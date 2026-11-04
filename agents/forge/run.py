#!/usr/bin/env python3
"""Forge : commandes de la pipeline, depuis la racine du dépôt.

    backend/.venv/bin/python agents/forge/run.py prepare fr 3e A1.2 --modele claude-opus-5-5
    backend/.venv/bin/python agents/forge/run.py prepare fr 3e A1 --problemes --modele gpt-6.1-sol
    backend/.venv/bin/python agents/forge/run.py prepare fr 3e A1.2 --complement 10 --modele ...
    backend/.venv/bin/python agents/forge/run.py suivant [RUN]
    backend/.venv/bin/python agents/forge/run.py check   [RUN]
    backend/.venv/bin/python agents/forge/run.py render  [RUN]
    backend/.venv/bin/python agents/forge/run.py goldfish [RUN] [--slots s03 s07]
    backend/.venv/bin/python agents/forge/run.py persist [RUN] [--replace]
    backend/.venv/bin/python agents/forge/run.py reprendre

RUN = identifiant ou dossier du run (défaut : le plus récent). Procédure :
agents/forge/FORGE.md. Sorties courtes volontairement : ce texte entre dans le
contexte du modèle, et chaque ligne se relit à chaque envoi.
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
# MÊME base qu'elle (cf. agents/astra/run.py)
os.chdir(_BACKEND)

import forge                                    # noqa: E402
from app.db import SessionLocal                 # noqa: E402

MAX_LINES = 30


def _print_limited(lines: list[str]) -> None:
    for line in lines[:MAX_LINES]:
        print(line)
    if len(lines) > MAX_LINES:
        print(f"... et {len(lines) - MAX_LINES} autre(s) : voir report.json")


def _cmd_prepare(args, db):
    complement = None
    if args.complement or args.facile or args.moyen or args.difficile:
        levels = {1: args.facile or 0, 2: args.moyen or 0, 3: args.difficile or 0}
        complement = ({k: v for k, v in levels.items() if v} if any(levels.values())
                      else forge.split_complement(args.complement))
    runs = forge.prepare(db, args.programme, args.cycle, args.cible, model=args.modele,
                         problems=args.problemes, subject=args.matiere, pilot=args.pilote,
                         complement=complement)
    for run in runs:
        payload = forge.load_json(run / "payload.json")
        context = payload["context"]
        print(f"Run : {run.name}")
        print(f"  Fiche : {'prête' if context['fiche_ready'] else 'À ÉCRIRE'} — {context['fiche']}")
    first = forge.load_json(runs[0] / "payload.json")["context"]
    if not first["fiche_ready"]:
        print(f"Sources de la fiche : {first['source']} "
              f"({first['source_stats'].get('pages', 0)} page(s))")
        for image in first["figure_images"]:
            print(f"  Image : {image}")
    print("Lis dans cet ordre, sans rien modifier :")
    for path in first["read_order"]:
        print(f"  {path}")
    return 0


def _cmd_next(args, db):
    run = forge.resolve_run(args.run)
    slots = forge.remaining_slots(run)
    if not slots:
        print("Tous les emplacements sont écrits. Lance `check`.")
        return 0
    size = args.lot
    print(f"{run.name} : {len(slots)} emplacement(s) restant(s). Lot à écrire dans {run / 'slots'} :")
    for slot in slots[:size]:
        code = slot.get("competency") or ", ".join(slot.get("competencies", []))
        print(f"  {slot['slot']} {slot['difficulty']} {slot['response_type']} taille {slot['size']}"
              f"{' figure' if slot.get('figure') else ''} [{code}]")
    return 0


def _cmd_check(args, db):
    run = forge.resolve_run(args.run)
    report, contracts = forge.check(db, run)
    _print_limited([f"ERREUR {e}" for e in report.errors] + [f"réserve {w}" for w in report.warnings])
    print(f"{len(contracts)} carte(s) valide(s), {len(report.errors)} erreur(s), "
          f"{len(report.warnings)} réserve(s).")
    return 0 if report.ok else 1


def _cmd_render(args, db):
    run = forge.resolve_run(args.run)
    summary = forge.render(db, run)
    kind = forge.load_json(run / "grid.json")[0]["kind"]
    _print_limited(forge.lot_hints(summary, kind))
    simulation = summary["simulation"]
    print(f"{len(summary['cards'])} carte(s) rendue(s) dans {run / 'cards'}. "
          f"Remplissage simulé : médiane {simulation['median_fill']:.0%}, "
          f"10 % des pires {simulation['p10_fill']:.0%}.")
    return 0


def _cmd_goldfish(args, db):
    import goldfish
    run = forge.resolve_run(args.run)
    state = goldfish.run(run, slots=args.slots, cli=args.cli)
    verdicts = state["verdicts"]
    lines = []
    for key in sorted(verdicts):
        entry = verdicts[key]
        if entry.get("flag"):
            action = "RÉÉCRIRE" if entry.get("rewrite") else (entry.get("severity") or "note")
            lines.append(f"{key} {action} : " + " | ".join(entry.get("notes") or []))
    _print_limited(lines)
    print(f"{len(verdicts)} carte(s) relue(s), {len(lines)} signalée(s).")
    return 0


def _cmd_persist(args, db):
    run = forge.resolve_run(args.run)
    result = forge.persist(db, run, replace=args.replace)
    print(f"{len(result['written'])} brouillon(s) dans l'onglet Exercices "
          f"(extraction {result['extraction_id']}).")
    print("À relire : " + ", ".join(result["review"]))
    return 0


def _cmd_resume(args, db):
    runs = forge.unfinished_runs()
    if not runs:
        print("Aucun run inachevé.")
        return 0
    for run in runs:
        print(f"{run.name} : reste {len(forge.remaining_slots(run))} emplacement(s)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="forge", description="Pipeline forge.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    prep = sub.add_parser("prepare", help="Prépare le contexte et les grilles.")
    prep.add_argument("programme", help="Programme, ex. fr")
    prep.add_argument("cycle", help="Cycle, ex. 3e")
    prep.add_argument("cible", help="Compétence (A1.2) ou chapitre (A1)")
    prep.add_argument("--modele", required=True, help="Modèle réellement utilisé, ex. claude-opus-5-5")
    prep.add_argument("--matiere", default=forge.SUBJECT_DEFAULT)
    prep.add_argument("--problemes", action="store_true", help="Les 20 problèmes du chapitre")
    prep.add_argument("--pilote", action="store_true", help="Toutes les cartes à relire")
    prep.add_argument("--complement", type=int, help="Second run : nombre d'exercices à ajouter")
    prep.add_argument("--facile", type=int, help="Second run : nombre de faciles")
    prep.add_argument("--moyen", type=int, help="Second run : nombre de moyens")
    prep.add_argument("--difficile", type=int, help="Second run : nombre de difficiles")
    for name, helptext in (("check", "Contrôles gratuits."), ("render", "Rendu réel et blanc."),
                           ("persist", "Écrit les brouillons."), ("suivant", "Prochain lot à écrire."),
                           ("goldfish", "Relecture sans contexte.")):
        cmd = sub.add_parser(name, help=helptext)
        cmd.add_argument("run", nargs="?")
        if name == "persist":
            cmd.add_argument("--replace", action="store_true")
        if name == "suivant":
            cmd.add_argument("--lot", type=int, default=5)
        if name == "goldfish":
            cmd.add_argument("--slots", nargs="*")
            cmd.add_argument("--cli", choices=("claude", "codex"))
    sub.add_parser("reprendre", help="Liste les runs inachevés.")
    args = parser.parse_args(argv)
    db = SessionLocal()
    try:
        return {"prepare": _cmd_prepare, "suivant": _cmd_next, "check": _cmd_check,
                "render": _cmd_render, "goldfish": _cmd_goldfish, "persist": _cmd_persist,
                "reprendre": _cmd_resume}[args.cmd](args, db) or 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
