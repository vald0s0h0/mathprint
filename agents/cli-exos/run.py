#!/usr/bin/env python3
"""cli-exos — orchestrateur de la pipeline d'extraction d'exercices par le CLI Claude.

Même pipeline qu'« Indigo » (OCR Mistral → découpage géométrique → crops/CV →
3 étapes LLM → brouillons `IndigoExercise` → onglet Exercices), MAIS les 3 étapes
LLM passent par le CLI Claude Code (abonnement), jamais l'API Anthropic. Voir
README.md pour l'usage.

Lancement (depuis la racine du repo) :
    python agents/cli-exos/run.py --competency N1.2 --eleve 34-40 --prof 210-214 --numbers 34-67

On réutilise TOUTE la géométrie/persistance d'Indigo (`app.services.indigo`) : ce
script ne réécrit que la BOUCLE d'orchestration (~1 fonction), et remplace les
3 appels LLM par ceux de `pipeline` (CLI). `app.services.indigo` n'est jamais
modifié.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- bootstrap : rendre le backend importable + taper la MÊME base que l'app ----
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]
_BACKEND = _REPO / "backend"
sys.path.insert(0, str(_HERE))          # import pipeline / llm_cli
sys.path.insert(0, str(_BACKEND))       # import app.*
# l'app résout `sqlite:///./mathprint.db` relativement au CWD (elle tourne depuis
# backend/) : on s'y place pour écrire dans le MÊME fichier de base.
os.chdir(_BACKEND)

import llm_cli                                                   # noqa: E402
import pipeline                                                  # noqa: E402
from app.config import settings                                  # noqa: E402
from app.db import SessionLocal                                  # noqa: E402
from app.models import (Competency, CompetencyFramework,         # noqa: E402
                        IndigoExtraction)
from app.services import indigo, indigo_manual                   # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("cli-exos.run")

# statuts d'extraction PROPRES à cette pipeline : volontairement HORS de
# {pending, running} pour que le worker Indigo in-process de l'app (qui réclame
# status=="pending", et bascule "running"→"pending" au redémarrage) ne les
# happe JAMAIS — sinon un run CLI serait re-traité par la voie Gemini/API. Le
# frontend ne pose de bandeau que pour pending/running/failed : ces statuts sont
# donc aussi invisibles côté web (le terminal est l'UI de progression).
ST_RUNNING, ST_DONE, ST_FAILED = "cli_running", "cli_done", "cli_failed"


# ------------------------------------------------------------------- arguments

def _parse_target_spec(spec: str) -> dict:
    """« CODE:eleve=34-40:prof=210-214:numbers=34-67 » → dict de plages brutes."""
    parts = spec.split(":")
    kv = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            kv[k.strip()] = v.strip()
    return {"code": parts[0].strip(), "eleve": kv.get("eleve", ""),
            "prof": kv.get("prof", ""), "numbers": kv.get("numbers", "")}


def _build_args():
    ap = argparse.ArgumentParser(
        prog="cli-exos", description="Extraction d'exercices d'un manuel via le CLI Claude (abonnement).")
    ap.add_argument("--grade", default="3e", help="Niveau (défaut : 3e).")
    ap.add_argument("--competency", help="Code (ou short_id) de la compétence, ex. N1.2.")
    ap.add_argument("--eleve", default="", help="Pages du manuel ÉLÈVE, 1-based, ex. 34-40.")
    ap.add_argument("--prof", default="", help="Pages du manuel PROF, 1-based, ex. 210-214.")
    ap.add_argument("--numbers", default="", help="Plage des numéros d'exercices, ex. 34-67.")
    ap.add_argument("--target", action="append", default=[],
                    help="Cible complète répétable : CODE:eleve=..:prof=..:numbers=.. .")
    ap.add_argument("--list-competencies", action="store_true",
                    help="Liste les compétences disponibles (code — libellé) et quitte.")
    return ap.parse_args()


# ------------------------------------------------------------------- compétences

def _resolve_competency(db, grade: str, code: str):
    fw = db.query(CompetencyFramework).filter_by(grade_level=grade).first()
    if fw is None:
        return None
    return (db.query(Competency).filter_by(framework_id=fw.id, code=code).first()
            or db.query(Competency).filter_by(framework_id=fw.id, short_id=code).first())


def _list_competencies(db, grade: str) -> None:
    fw = db.query(CompetencyFramework).filter_by(grade_level=grade).first()
    if fw is None:
        print(f"Aucun référentiel pour le niveau {grade}.")
        return
    rows = (db.query(Competency).filter_by(framework_id=fw.id)
            .order_by(Competency.order_index).all())
    print(f"Compétences {grade} ({len(rows)}) — code · short_id · libellé :")
    for c in rows:
        print(f"  {c.code:<10} {c.short_id or '·':<10} {c.label}")


# ------------------------------------------------------------------- une cible

def _process_target(db, doc_eleve, doc_prof, grade: str, target: dict,
                    extraction_id: str, log) -> int:
    comp = db.get(Competency, target["competency_id"])
    if comp is None:
        log(f"⚠ compétence {target.get('competency_id')} introuvable — cible ignorée")
        return 0
    short = comp.short_id or comp.code
    eleve_pages = [int(p) for p in target.get("eleve_pages") or []]
    prof_pages = [int(p) for p in target.get("prof_pages") or []]
    expected = [int(n) for n in target.get("numbers") or []]
    if not expected:
        log(f"⚠ {short} : aucune plage de numéros (--numbers) — cible ignorée")
        return 0

    log(f"OCR élève ({short}) — pages {[p + 1 for p in eleve_pages]}…")
    eleve = indigo._ocr_pages(db, doc_eleve, eleve_pages, f"clix-eleve-{comp.code}")
    prof = []
    if doc_prof and prof_pages:
        log(f"OCR prof ({short}) — pages {[p + 1 for p in prof_pages]}…")
        prof = indigo._ocr_pages(db, doc_prof, prof_pages, f"clix-prof-{comp.code}")

    corrections = pipeline.collect_corrections(db, comp, grade, prof, expected, log)
    exercises = pipeline.collect_exercises(db, comp, grade, eleve, eleve_pages, expected, log)
    log(f"{short} : {len(exercises)} exercice(s) détecté(s), {len(corrections)} corrigé(s) prof")
    if not exercises:
        return 0

    # 1) crop + couleur (CV) en local, sans LLM — 100 % réutilisé d'Indigo
    prepared: list[tuple] = []
    for order, ex in enumerate(exercises):
        try:
            pr = indigo._prepare_exercise(doc_eleve, grade, comp, ex, corrections,
                                          extraction_id, order)
            if pr is not None:
                prepared.append(pr)
        except Exception:
            logger.exception("cli-exos : découpe de l'exercice n°%s échouée", ex.get("number"))
    log(f"{short} : {len(prepared)} exercice(s) découpé(s), génération (Sonnet)…")

    # 2) génération par lots de 5 à 7 (CLI Sonnet), 2e chance en solo
    triples: list[tuple] = []
    bs = pipeline.choose_batch_size(len(prepared))
    for i in range(0, len(prepared), bs):
        chunk = prepared[i:i + bs]
        adapted = pipeline.generate_batch(db, comp, grade, [m for _r, m in chunk])
        for row, manual in chunk:
            valid = adapted.get(str(manual["number"]))
            if valid is None:
                valid = pipeline.generate_one(db, comp, grade, manual)
            triples.append((row, manual, valid))
        log(f"{short} : {min(i + bs, len(prepared))}/{len(prepared)} généré(s)…")

    # 3) relecture finale (CLI Opus), par lots de 20 à 40
    reviewed = pipeline.review(db, comp, grade, triples, log)

    # 4) persistance — réutilise indigo._persist_exercise, puis marque la provenance
    made = adapted_ok = 0
    for row, manual, valid in triples:
        final = reviewed.get(str(manual["number"]).strip()) if valid is not None else None
        indigo._persist_exercise(db, row, manual, final)
        row.model = f"claude-code-cli:{pipeline.GENERATE_MODEL}/{pipeline.REVIEW_MODEL}"
        row.prompt_version = pipeline.PROMPT_VERSION
        row.raw_ocr_json = {**(row.raw_ocr_json or {}), "pipeline": "cli-exos"}
        made += 1
        adapted_ok += 1 if final is not None else 0
        if made % 10 == 0:
            db.commit()
    db.commit()
    fallback = made - adapted_ok
    log(f"{short} : {adapted_ok}/{made} exercice(s) adapté(s)"
        + (f", {fallback} en repli OCR brut (à corriger)" if fallback else ""))
    return made


# ------------------------------------------------------------------- entrée

def main() -> int:
    args = _build_args()
    grade = args.grade
    db = SessionLocal()
    try:
        try:
            db.query(CompetencyFramework).first()   # sonde : la base existe-t-elle ?
        except Exception:
            print("Base MathPrint introuvable ou non initialisée. Démarre l'application "
                  "au moins une fois (elle crée/migrera la base), puis relance.",
                  file=sys.stderr)
            return 2

        if args.list_competencies:
            _list_competencies(db, grade)
            return 0

        # garde-fou « jamais l'API » + abonnement présent
        if not llm_cli.is_available():
            print("Le binaire « claude » (CLI Claude Code) est introuvable. Installe-le "
                  "et connecte-toi à ton abonnement, ou définis CLAUDE_BIN=/chemin/claude.",
                  file=sys.stderr)
            return 2
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            if os.environ.get(var):
                print(f"Note : {var} est défini dans l'environnement ; il sera IGNORÉ "
                      "(les appels passent par l'abonnement, jamais l'API).")

        # cibles : forme simple (--competency …) et/ou forme répétable (--target …)
        specs: list[dict] = [_parse_target_spec(s) for s in args.target]
        if args.competency:
            specs.append({"code": args.competency, "eleve": args.eleve,
                          "prof": args.prof, "numbers": args.numbers})
        if not specs:
            print("Rien à faire : donne --competency (+ --eleve/--prof/--numbers) ou "
                  "au moins un --target. (--list-competencies pour voir les codes.)",
                  file=sys.stderr)
            return 2

        targets: list[dict] = []
        for s in specs:
            comp = _resolve_competency(db, grade, s["code"])
            if comp is None:
                print(f"Compétence « {s['code']} » introuvable pour {grade} "
                      "(voir --list-competencies).", file=sys.stderr)
                return 2
            if not s["eleve"].strip():
                print(f"Compétence « {s['code']} » : --eleve (pages élève) est requis.",
                      file=sys.stderr)
                return 2
            if not s["numbers"].strip():
                print(f"Compétence « {s['code']} » : --numbers (plage de numéros) est requis.",
                      file=sys.stderr)
                return 2
            t = indigo.normalize_target({
                "competency_id": comp.id, "eleve_page_range": s["eleve"],
                "prof_page_range": s["prof"], "number_range": s["numbers"]})
            t["competency_id"] = comp.id
            targets.append(t)

        doc_eleve = indigo_manual.open_doc(grade, "eleve")
        doc_prof = indigo_manual.open_doc(grade, "prof")
        if doc_eleve is None:
            man = settings.indigo_manuals.get(grade, {})
            print(f"Manuel élève {grade} introuvable ({man.get('eleve')!r}). "
                  "Place le PDF (cf. settings.indigo_manuals / dossier context/).",
                  file=sys.stderr)
            return 2

        # ligne d'extraction (traçabilité + lien exercices). Statut CLI = inerte
        # pour le worker Indigo (cf. commentaire ST_RUNNING plus haut).
        ext = IndigoExtraction(
            grade_level=grade, targets_json=targets, status=ST_RUNNING,
            progress_message="cli-exos : démarrage…",
            stats_json={"pipeline": "cli-exos", "models": {
                "segment": pipeline.SEGMENT_MODEL, "generate": pipeline.GENERATE_MODEL,
                "review": pipeline.REVIEW_MODEL}})
        db.add(ext)
        db.commit()

        log_lines: list[str] = []

        def log(msg: str) -> None:
            print(msg, flush=True)
            log_lines.append(msg)
            ext.progress_message = msg[:300]
            ext.log_text = "\n".join(log_lines[-300:])
            ext.updated_at = datetime.now(timezone.utc)
            db.commit()

        print("──────────────────────────────────────────────────────────")
        print(f"cli-exos · {grade} · {len(targets)} cible(s) · abonnement Claude (pas d'API)")
        print(f"modèles : découpage={pipeline.SEGMENT_MODEL}  génération={pipeline.GENERATE_MODEL}"
              f"  vérification={pipeline.REVIEW_MODEL}")
        print("──────────────────────────────────────────────────────────")

        total = 0
        try:
            for i, target in enumerate(targets):
                log(f"[cible {i + 1}/{len(targets)}]")
                total += _process_target(db, doc_eleve, doc_prof, grade, target, ext.id, log)
            ext.status = ST_DONE
            ext.progress = 100
            ext.progress_message = f"{total} exercice(s) extrait(s)"
            ext.stats_json = {**(ext.stats_json or {}), "exercises": total, "targets": len(targets)}
            ext.updated_at = datetime.now(timezone.utc)
            db.commit()
        except Exception as e:
            logger.exception("cli-exos : run échoué")
            db.rollback()
            ext.status = ST_FAILED
            ext.error_message = f"{type(e).__name__}: {e}"
            ext.updated_at = datetime.now(timezone.utc)
            db.commit()
            print(f"\n✗ Échec : {type(e).__name__}: {e}", file=sys.stderr)
            return 1

        print("──────────────────────────────────────────────────────────")
        print(f"✓ {total} exercice(s) en brouillon → onglet Exercices (valider / modifier / "
              "supprimer / publier).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
