"""Affinage de TrOCR (français) sur la vérité du PROFESSEUR — jamais sur des sorties de moteurs.

Entrée : un dossier produit par `backend/scripts/ocr_export_dataset.py` (images + metadata.jsonl, séparation
apprentissage / évaluation PAR ÉLÈVE). Sortie : un modèle local + `report.json` + `PROMOTION.txt`.

Garde-fous :
- local seulement : `HF_HUB_OFFLINE=1`, aucun téléversement, les poids (qui contiennent des traces d'écriture
  d'élèves) restent sur la machine ;
- refuse de s'entraîner sous `--min-train` paires (2 000) sauf `--smoke` ; le jeu synthétique n'est accepté
  qu'en `--smoke` (test de la boucle, jamais promu) ;
- le modèle n'est « promu » que s'il fait mieux que la base SUR LE JEU D'ÉVALUATION (exactitude, erreur de
  caractères, virgules perdues) avec au moins `--min-eval` paires ; la décision finale reste humaine.
Les virgules décimales sont surpondérées : c'est la faute grave connue de TrOCR (lot 0).

Pour l'utiliser ensuite : ajouter un moteur OBSERVATEUR à `config.toml` (nom distinct, jamais `trocr`) :
    [engines.trocr_ft]  impl = "trocr"  model = "<out>/model"  dtype = "fp16"  backend = "mps"
puis dans le plan (`ocr_engine_plan.families` : "trocr_ft": "encdec" ; classe : `observe`). Le rapport
(`/admin/lecture-ocr`) compare alors `trocr` et `trocr_ft` sur les mêmes découpes avant toute promotion.

Usage :
    ocr_worker/.venv/bin/python ocr_worker/scripts/finetune_trocr.py --dataset data/ocr_dataset --out data/trocr_ft_YYYYMMDD
    ... --smoke --max-steps 20      # test de la boucle sur le jeu synthétique
"""
import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

ALLOWED_SOURCES = ("teacher_ocr", "audit")


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def cer(pred: str, ref: str) -> float:
    """Taux d'erreur de caractères (Levenshtein / longueur de la référence)."""
    a, b = norm(pred), norm(ref)
    if not b:
        return 0.0 if not a else 1.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] / len(b)


def comma_lost(pred: str, ref: str) -> bool:
    """Virgule décimale perdue : la référence en a une, pas la lecture, et les chiffres sont les mêmes."""
    d = lambda s: re.sub(r"\D", "", s or "")
    return "," in ref and "," not in pred and d(pred) == d(ref)


def metrics(preds: list[str], refs: list[str]) -> dict:
    n = len(refs)
    with_comma = [(p, r) for p, r in zip(preds, refs) if "," in r]
    return {"n": n,
            "exact": round(sum(norm(p) == norm(r) for p, r in zip(preds, refs)) / n, 4) if n else None,
            "cer": round(sum(cer(p, r) for p, r in zip(preds, refs)) / n, 4) if n else None,
            "comma_cases": len(with_comma),
            "comma_lost_rate": round(sum(comma_lost(p, r) for p, r in with_comma) / len(with_comma), 4) if with_comma else None}


def promotion(base: dict, ft: dict, *, min_eval: int, smoke: bool) -> dict:
    reasons = []
    if smoke:
        return {"verdict": "smoke_only", "reasons": ["test de la boucle : jamais promu"]}
    if (ft["n"] or 0) < min_eval:
        reasons.append(f"jeu d'évaluation trop petit ({ft['n']} < {min_eval})")
    if ft["exact"] is None or ft["exact"] < base["exact"]:
        reasons.append("exactitude inférieure à la base")
    if ft["cer"] is None or ft["cer"] > base["cer"]:
        reasons.append("erreur de caractères supérieure à la base")
    if (ft["comma_lost_rate"] or 0) > (base["comma_lost_rate"] or 0):
        reasons.append("plus de virgules perdues que la base")
    return {"verdict": "promote" if not reasons else "not_promoted", "reasons": reasons}


def load_rows(dataset: Path, zone_class: str | None, smoke: bool) -> tuple[list, list]:
    rows = [json.loads(l) for l in (dataset / "metadata.jsonl").read_text().splitlines() if l.strip()]
    bad = {r["truth_source"] for r in rows} - set(ALLOWED_SOURCES)
    if bad and not (smoke and bad == {"synthetic"}):
        raise SystemExit(f"sources refusées : {sorted(bad)} (seules {ALLOWED_SOURCES} sont admises ; "
                         "`synthetic` seulement avec --smoke)")
    if zone_class:
        rows = [r for r in rows if r["zone_class"] == zone_class]
    for r in rows:
        r["path"] = dataset / r["image"]
    return [r for r in rows if r["split"] == "train"], [r for r in rows if r["split"] == "eval"]


def sample_weights(rows: list) -> list[float]:
    """Les virgules, fractions et exposants comptent triple : ce sont les fautes graves à corriger."""
    return [3.0 if re.search(r"[,/^]", r["text"]) else 1.0 for r in rows]


def decode(model, proc, rows, device, bs=8, max_new_tokens=48) -> list[str]:
    import torch
    from PIL import Image
    out = []
    model.eval()
    with torch.no_grad():
        for i in range(0, len(rows), bs):
            imgs = [Image.open(r["path"]).convert("RGB") for r in rows[i:i + bs]]
            pv = proc(images=imgs, return_tensors="pt").pixel_values.to(device)
            ids = model.generate(pv, max_new_tokens=max_new_tokens, num_beams=1, do_sample=False)
            out += proc.batch_decode(ids, skip_special_tokens=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--base", default="agomberto/trocr-large-handwritten-fr")
    ap.add_argument("--processor", default="microsoft/trocr-large-handwritten")
    ap.add_argument("--class", dest="zone_class"); ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=2e-5); ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--max-steps", type=int); ap.add_argument("--freeze-encoder", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--min-train", type=int, default=2000); ap.add_argument("--min-eval", type=int, default=200)
    ap.add_argument("--device", default="mps"); ap.add_argument("--smoke", action="store_true"); ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    train, ev = load_rows(Path(a.dataset), a.zone_class, a.smoke)
    if len(train) < a.min_train and not a.smoke:
        raise SystemExit(f"{len(train)} paires d'apprentissage < {a.min_train} : refus (trop peu pour un affinage utile ; "
                         "--smoke pour tester la boucle)")
    if not train or not ev:
        raise SystemExit("jeux d'apprentissage et d'évaluation tous deux requis (séparation par élève)")
    import torch
    from PIL import Image
    from transformers import TrOCRProcessor, VisionEncoderDecoderModel
    from ocr_worker.engines.base import ensure_device
    ensure_device(a.device)
    random.seed(a.seed); torch.manual_seed(a.seed)
    proc = TrOCRProcessor.from_pretrained(a.processor)
    model = VisionEncoderDecoderModel.from_pretrained(a.base, use_safetensors=False).to(a.device)
    t0 = time.time()
    base_m = metrics(decode(model, proc, ev, a.device), [r["text"] for r in ev])
    print("base :", base_m, flush=True)

    if a.freeze_encoder:
        for p in model.encoder.parameters():
            p.requires_grad = False
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=a.lr, weight_decay=0.01)
    steps_per_epoch = max(1, len(train) // a.batch)
    total = min(a.max_steps or 10**9, a.epochs * steps_per_epoch)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / max(1, int(0.05 * total))) * max(0.0, 1 - s / total))
    weights = sample_weights(train)
    tok = proc.tokenizer
    model.train()
    for step in range(total):
        batch = random.choices(train, weights=weights, k=a.batch)
        pv = proc(images=[Image.open(r["path"]).convert("RGB") for r in batch], return_tensors="pt").pixel_values.to(a.device)
        labels = tok([r["text"] for r in batch], padding="max_length", max_length=48, truncation=True, return_tensors="pt").input_ids
        labels[labels == tok.pad_token_id] = -100
        loss = model(pixel_values=pv, labels=labels.to(a.device)).loss
        loss.backward()
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        opt.step(); sched.step(); opt.zero_grad(set_to_none=True)
        if step % 10 == 0 or step == total - 1:
            print(f"pas {step + 1}/{total} perte {loss.item():.4f}", flush=True)
    ft_m = metrics(decode(model, proc, ev, a.device), [r["text"] for r in ev])
    print("affiné :", ft_m, flush=True)
    verdict = promotion(base_m, ft_m, min_eval=a.min_eval, smoke=a.smoke)
    out = Path(a.out)
    (out / "model").mkdir(parents=True, exist_ok=True)
    model.save_pretrained(out / "model"); proc.save_pretrained(out / "model")
    report = {"base": base_m, "finetuned": ft_m, "promotion": verdict, "train": len(train), "eval": len(ev),
              "steps": total, "seconds": round(time.time() - t0), "args": vars(a)}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    (out / "PROMOTION.txt").write_text(
        f"Décision : {verdict['verdict']}\n" + "\n".join(f"- {r}" for r in verdict["reasons"]) +
        "\n\nCe fichier est un avis : la promotion en production reste une décision humaine, après comparaison "
        "de `trocr` et `trocr_ft` sur les mêmes découpes dans le rapport « Lecture OCR ».\n")
    print("décision :", verdict, "->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
