"""Commande `vague` : corrige par LLM, sur abonnement, ce qui attend (plan forge2 §10.2).

Usage (depuis la racine du dépôt, sur le Mac) :
    backend/.venv/bin/python backend/scripts/vague.py [corriger] [apprecier] [tout]
        [--moteur haiku|luna|terra] [--devoir ID] [--classe ID] [--essai]

    corriger   réponses écrites « correction en attente », par exercice, avec leur
               micro-correction rédigée ;
    apprecier  synthèse de l'appréciation des copies finalisées qui l'attendent ;
    tout       les deux, dans cet ordre (défaut).

`--essai` montre les paquets sans appeler le moteur ni rien écrire. Une vague
interrompue se relance telle quelle : un paquet écrit n'est jamais refait.
Voir docs/vague.md.
"""
import argparse
import os
import sys
from pathlib import Path

if "__file__" in globals():
    BACKEND = Path(__file__).resolve().parents[1]
    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))

from app.db import SessionLocal  # noqa: E402
from app.services import cli_llm, vague  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vague", description=__doc__.split("\n")[0])
    parser.add_argument("taches", nargs="*", choices=["corriger", "apprecier", "tout"])
    parser.add_argument("--moteur", default="haiku", choices=cli_llm.ENGINES)
    parser.add_argument("--devoir", help="identifiant du devoir (assessment)")
    parser.add_argument("--classe", help="identifiant de la classe")
    parser.add_argument("--essai", action="store_true", help="montrer sans rien écrire")
    args = parser.parse_args(argv)
    db = SessionLocal()
    try:
        bilan = vague.run(db, args.taches, moteur=args.moteur, devoir=args.devoir,
                          classe=args.classe, essai=args.essai)
    finally:
        if args.essai:
            db.rollback()
        db.close()
    print("\n" + ("Essai : rien n'a été écrit.\n" if args.essai else "") + bilan.texte())
    return 1 if bilan.arret else 0


if __name__ == "__main__":
    sys.exit(main())
