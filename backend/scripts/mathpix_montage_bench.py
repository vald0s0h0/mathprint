"""Banc des planches Mathpix : lecture UNITAIRE de N découpes, puis lecture en PLANCHES des
mêmes découpes, et comparaison. Critère pour activer `ocr_montage_enabled` : 0 écart.

Écart = une découpe que la planche a ATTRIBUÉE (ligne unique dans sa bande) mais dont la
surface diffère de la lecture unitaire. Une découpe non attribuée (sans ligne, ligne
ambiguë ou à cheval) est un REPLI : relue seule en production, donc sans risque d'erreur
(mais elle fait perdre l'économie : le taux de repli est mesuré).

COÛT : N appels unitaires + ceil(N / taille) planches, 0,004 € l'appel. Pour N = 60 et des
planches de 12 : 65 appels, environ 0,26 €.

PAR DÉFAUT AUCUN APPEL RÉSEAU : le script assemble les planches, vérifie la géométrie,
affiche le coût prévu et s'arrête. L'appel réel exige `--yes`.

Usage (depuis la racine du dépôt) :
    backend/.venv/bin/python backend/scripts/mathpix_montage_bench.py [--n 60] [--size 12]
    backend/.venv/bin/python backend/scripts/mathpix_montage_bench.py --n 60 --yes   # payant
Sources de découpes (dans l'ordre) : --dir <dossier de PNG>, sinon data/ocr_bench/ (banc du
lot 0), sinon data/assessments/*/scans/derived/ocr/.
"""
import argparse
import glob
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT / "backend")                      # la base SQLite est relative à backend/
sys.path.insert(0, str(ROOT / "backend"))

COST = 0.004


def find_crops(directory: str | None, n: int) -> list[Path]:
    if directory:
        paths = sorted(Path(directory).glob("*.png"))
    else:
        bench = ROOT / "data" / "ocr_bench" / "manifest.json"
        if bench.exists():
            paths = [Path(m["path"]) for m in json.loads(bench.read_text())]
        else:
            paths = [Path(p) for p in sorted(glob.glob(str(ROOT / "data/assessments/*/scans/derived/ocr/*.png")))]
    return paths[:n]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--size", type=int, default=12, help="découpes par planche")
    ap.add_argument("--dir", default=None)
    ap.add_argument("--yes", action="store_true", help="autorise les appels Mathpix (payants)")
    ap.add_argument("--out", default=None, help="écrit le détail JSON")
    ap.add_argument("--units", default=None, help="réutilise les lectures unitaires d'un --out précédent (économise N appels)")
    a = ap.parse_args()

    from app.services import ocr_montage as M
    from app.services.ocr_surface import to_surface

    paths = find_crops(a.dir, a.n)
    if not paths:
        print("aucune découpe trouvée")
        return 2
    chunks = [paths[i:i + a.size] for i in range(0, len(paths), a.size)]
    calls = len(paths) + len(chunks)
    if a.units:
        calls = len(chunks)
    print(f"{len(paths)} découpes, {len(chunks)} planche(s) de {a.size} : {calls} appel(s) payant(s), "
          f"coût prévu {calls * COST:.2f} €")

    montages = []
    for chunk in chunks:
        m = M.build_montage([p.read_bytes() for p in chunk])
        gaps = [b[1] - a_[3] for a_, b in zip(m.boxes, m.boxes[1:])]
        assert m.width <= M.MAX_WIDTH_PX and (not gaps or min(gaps) >= M.MIN_GAP_PX)
        montages.append(m)
    print(f"géométrie vérifiée : largeur max {max(m.width for m in montages)} px, "
          f"écart min {M.MIN_GAP_PX} px")
    if not a.yes:
        print("simulation seulement (aucun appel). Ajoutez --yes pour lancer la comparaison réelle.")
        return 0

    from app.db import SessionLocal
    from app.services import providers
    db = SessionLocal()
    rows, fallbacks, diffs = [], 0, []
    try:
        unit = {}
        known = {r["crop"]: r["unit"] for r in json.loads(Path(a.units).read_text())} if a.units else {}
        for p in paths:
            if p.name in known:
                unit[p] = (known[p.name], 1.0)
                continue
            r = providers.mathpix_ocr(db, p.read_bytes(), f"bench-unit-{p.stem[:8]}")
            unit[p] = (r["text"], float(r["confidence"]))
        for chunk, m in zip(chunks, montages):
            res = providers.mathpix_montage_ocr(db, m.png, f"bench-montage-{chunk[0].stem[:8]}")
            for p, asg in zip(chunk, M.assign_lines(m.boxes, res["lines"])):
                u_text, u_conf = unit[p]
                if asg.status != "ok":
                    fallbacks += 1
                    rows.append({"crop": p.name, "status": asg.status, "unit": u_text})
                    continue
                m_text = asg.lines[0]["text"]
                same_text = m_text == u_text
                same_surface = to_surface(m_text, "text_line") == to_surface(u_text, "text_line")
                rows.append({"crop": p.name, "status": "ok", "unit": u_text, "montage": m_text,
                             "same_text": same_text, "same_surface": same_surface,
                             "conf_unit": u_conf, "conf_montage": asg.lines[0]["confidence"]})
                if not same_surface:
                    diffs.append(rows[-1])
        db.commit()
    finally:
        db.close()
    ok = sum(1 for r in rows if r["status"] == "ok")
    print(f"attribuées {ok}/{len(paths)} · replis {fallbacks} · ÉCARTS {len(diffs)}")
    for d in diffs:
        print(f"  ÉCART {d['crop']}: unitaire={d['unit']!r} planche={d['montage']!r}")
    print("RÉSULTAT :", "0 écart : l'activation est justifiée" if not diffs else "écarts : NE PAS activer")
    if a.out:
        Path(a.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    return 0 if not diffs else 1


if __name__ == "__main__":
    raise SystemExit(main())
