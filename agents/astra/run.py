#!/usr/bin/env python3
"""Astra — commande de la pipeline (lancée par l'agent Codex `gpt-6-astra`).

Depuis la racine du repo, avec le Python du venv backend :

    backend/.venv/bin/python agents/astra/run.py prepare --chapter "Fonctions affines"
    backend/.venv/bin/python agents/astra/run.py prepare --chapter "6e Angles"
    backend/.venv/bin/python agents/astra/run.py validate  [RUN]
    backend/.venv/bin/python agents/astra/run.py figures   [RUN]
    backend/.venv/bin/python agents/astra/run.py preview   [RUN] [--no-guides]
    backend/.venv/bin/python agents/astra/run.py persist   [RUN] [--replace]
    backend/.venv/bin/python agents/astra/run.py chapters

RUN = identifiant ou dossier du run (défaut : le plus récent). Procédure
complète et règles de rédaction : agents/astra/ASTRA.md.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parents[1] / "backend"
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_BACKEND))
# l'app résout `sqlite:///./mathprint.db` relativement au CWD : on écrit dans la
# MÊME base qu'elle (cf. agents/cli-exos/run.py)
os.chdir(_BACKEND)

import astra                                    # noqa: E402
from app.db import SessionLocal                 # noqa: E402


def _cmd_chapters(args, db):
    for grade in [args.grade] if args.grade else astra.grades():
        print(f"== {grade}")
        for ch in astra.chapters(grade):
            comps = astra.chapter_competencies(db, grade, ch["code"])
            print(f"{ch['code']:>3}  {ch['name']:<45} leçon p.{ch['lesson'][0]}-{ch['lesson'][1]}"
                  f"  exercices p.{ch['exercises'][0]}-{ch['exercises'][1]}  "
                  f"({', '.join(c.short_id or c.code for c in comps)})")


def _cmd_prepare(args, db):
    run = astra.prepare(db, args.chapter, grade=args.grade, pages=args.pages,
                        lesson=args.lesson, dpi=args.dpi)
    payload = astra.load_json(run / "payload.json")
    lesson = [p["id"] for p in payload["pages"] if p["role"] == "lesson"]
    exos = [p["id"] for p in payload["pages"] if p["role"] == "exercises"]
    print(f"Run : {payload['run_id']}\nDossier : {run}")
    print(f"Chapitre : {payload['grade']} {payload['chapter']['code']} — {payload['chapter']['name']}")
    print("Compétences : " + " ; ".join(f"{c['code']} {c['label']}" for c in payload["competencies"]))
    print(f"Pages LEÇON ({len(lesson)}) : {', '.join(lesson)}")
    print(f"Pages EXERCICES ({len(exos)}) : {', '.join(exos)}")
    print(f"\nÉtape suivante : lis chaque image de {run / 'pages'} (leçon puis exercices),"
          f" puis écris {run / astra.OUTPUT_FILE} (contrat : agents/astra/schema.md).")


def _print_report(rep):
    for e in rep.errors:
        print(f"  ERREUR  {e}")
    for w in rep.warnings:
        print(f"  réserve {w}")
    n = len(rep.exercises)
    bad = sum(1 for e in rep.exercises.values() if e["errors"])
    print(f"\n{n} exercice(s), {n - bad} valide(s), {len(rep.errors)} erreur(s), "
          f"{len(rep.warnings)} réserve(s).")


def _cmd_validate(args, db):
    run = astra.resolve_run(args.run)
    rep, _ = astra.validate(db, run)
    _print_report(rep)
    print(f"Rapport : {run / 'report.json'}")
    return 0 if rep.ok else 1


def _cmd_figures(args, db):
    run = astra.resolve_run(args.run)
    rep, _ = astra.validate(db, run)
    figs = astra.build_figures(run)
    for (num, kind), path in sorted(figs.items()):
        print(f"  n°{num} {kind:<7} {path}")
    if not figs:
        print("Aucune figure.")
    fig_errors = [e for e in rep.errors if "figure" in e]
    for e in fig_errors:
        print(f"  ERREUR  {e}")
    print("\nOuvre CHAQUE image pour vérifier cadrage, masques et lisibilité.")
    return 1 if fig_errors else 0


def _cmd_preview(args, db):
    run = astra.resolve_run(args.run)
    out = astra.preview(db, run, guides=not args.no_guides, variant=args.variant)
    rep = astra.load_json(run / "report.json")
    print(f"Aperçu : {out / 'preview.pdf'}")
    for png in sorted(out.glob("page-*.png")):
        print(f"  {png}")
    print(f"Index des cartes : {out / 'index.txt'}")
    if not rep["ok"]:
        print(f"ATTENTION : {len(rep['errors'])} erreur(s) de validation — les variantes "
              "refusées n'apparaissent pas dans l'aperçu.")
    return 0


def _cmd_persist(args, db):
    run = astra.resolve_run(args.run)
    res = astra.persist(db, run, replace=args.replace)
    for w in res["written"]:
        print(f"  écrit   {w}")
    for s in res["skipped"]:
        print(f"  ignoré  {s}")
    print(f"\n{len(res['written'])} brouillon(s) dans l'onglet Exercices "
          f"(extraction {res['extraction_id']}).")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="astra", description="Pipeline Astra (GPT-6 Astra via Codex).")
    ap.add_argument("--grade", default=None,
                    help="Niveau (3e, 6e…) ; défaut : déduit du chapitre, ou préfixe « 6e B3 ».")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("chapters", help="Liste les chapitres et leurs pages.")
    p = sub.add_parser("prepare", help="Images des pages du chapitre + payload.json.")
    p.add_argument("--chapter", required=True,
                   help="Nom du chapitre, code (B3) ou numéro du livre ; « 6e B3 » précise le niveau.")
    p.add_argument("--pages", help="Pages PDF des EXERCICES, ex. 76-80 (défaut : table).")
    p.add_argument("--lesson", help="Pages PDF de la LEÇON, ex. 74-75 (défaut : table).")
    p.add_argument("--dpi", type=int, default=astra.PAGE_DPI)
    for name, helptext in (("validate", "Valide astra_output.json."),
                           ("figures", "Produit les PNG des figures."),
                           ("preview", "PDF + PNG de relecture."),
                           ("persist", "Écrit les brouillons (onglet Exercices).")):
        q = sub.add_parser(name, help=helptext)
        q.add_argument("run", nargs="?", help="Identifiant ou dossier du run (défaut : dernier).")
        if name == "preview":
            q.add_argument("--no-guides", action="store_true")
            q.add_argument("--variant", choices=("base", "facile", "original"),
                           help="Sujet ne contenant que ce type de cartes")
        if name == "persist":
            q.add_argument("--replace", action="store_true",
                           help="Remplace les brouillons Astra existants des mêmes numéros.")
    args = ap.parse_args(argv)
    db = SessionLocal()
    try:
        return {"chapters": _cmd_chapters, "prepare": _cmd_prepare,
                "validate": _cmd_validate, "figures": _cmd_figures,
                "preview": _cmd_preview, "persist": _cmd_persist}[args.cmd](args, db) or 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
