#!/usr/bin/env python3
"""Forge : commandes de la pipeline, depuis la racine du dépôt.

    backend/.venv/bin/python agents/forge/run.py prepare fr 3e A1.2 --modele claude-opus-5-5
    backend/.venv/bin/python agents/forge/run.py prepare fr 3e A1 --problemes --modele gpt-6.1-sol
    backend/.venv/bin/python agents/forge/run.py prepare fr 3e A1.2 --complement 10 --modele ...
    backend/.venv/bin/python agents/forge/run.py prepare fr 3e C1.2 --traces 4 --modele ...
    backend/.venv/bin/python agents/forge/run.py suivant [RUN]
    backend/.venv/bin/python agents/forge/run.py check   [RUN]
    backend/.venv/bin/python agents/forge/run.py render  [RUN]
    backend/.venv/bin/python agents/forge/run.py goldfish [RUN] [--slots s03 s07]
    backend/.venv/bin/python agents/forge/run.py persist [RUN] [--replace] [--slots s03 s07]
    backend/.venv/bin/python agents/forge/run.py reprendre
    backend/.venv/bin/python agents/forge/run.py ecartes [fr-3e-A4]
    backend/.venv/bin/python agents/forge/run.py fiche fr 3e C2 [--matiere svt] [--sources cours.md]

RUN = identifiant ou dossier du run (défaut : le plus récent). Procédure :
agents/forge/FORGE.md. Sorties courtes volontairement : ce texte entre dans le
contexte du modèle, et chaque ligne se relit à chaque envoi.
"""
from __future__ import annotations

import argparse
import json
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
    # La fiche de la leçon s'écrit avant, par `forge contexte` : sans elle, pas d'exercices.
    target = forge.resolve_target(db, args.programme, args.cycle, args.cible, args.matiere, args.serie)
    fiche = forge.fiche_path(args.programme, args.cycle, target["chapter"][0].chapter_code,
                             args.matiere, args.serie)
    if not fiche.exists():
        serie = f" --serie {args.serie}" if args.serie else ""
        matiere = f" --matiere {args.matiere}" if args.matiere != forge.SUBJECT_DEFAULT else ""
        print(f"FICHE ABSENTE : {fiche}\nÉcris-la d'abord, dans une session à part :\n"
              f"  forge contexte {args.programme} {args.cycle} {target['chapter'][0].chapter_code}{serie}{matiere}")
        return 1
    runs = forge.prepare(db, args.programme, args.cycle, args.cible, model=args.modele,
                         problems=args.problemes, subject=args.matiere, pilot=args.pilote,
                         complement=complement, traces=args.traces, serie=args.serie,
                         volume="vingt" if args.vingt else "formule", suite=args.suite)
    if not runs:
        print("Rien à générer pour cette cible : une compétence « Traiter une situation » se "
              "travaille par les problèmes de la leçon (--problemes).")
        return 0
    for run in runs:
        payload = forge.load_json(run / "payload.json")
        context = payload["context"]
        print(f"Run : {run.name}")
        grid = forge.load_json(run / "grid.json")
        print(f"  Fiche : {context['fiche']}")
        print(f"  {len(grid)} famille(s) à écrire" + (" (suite du socle)" if payload.get("suite") else
              (" (amorce : `--suite` pour le reste du socle)" if payload.get("volume") == "formule" else "")))
        profile = payload.get("trace_profile")
        if profile:
            traces = sum(s["response_type"] in forge.DRAW_TYPES
                         for s in forge.load_json(run / "grid.json"))
            print(f"  Tracés : {profile['profile']} ({profile['source']}), {traces} sur {len(grid)}")
    first = forge.load_json(runs[0] / "payload.json")["context"]
    print("Lis dans cet ordre, sans rien modifier :")
    for path in first["read_order"]:
        print(f"  {path}")
    return 0


def _cmd_next(args, db):
    run = forge.resolve_run(args.run)
    refreshed = forge.refresh_grid(run)
    if refreshed:
        print(refreshed)
    slots = forge.remaining_slots(run)
    if not slots:
        print("Tous les emplacements sont écrits. Lance `check`.")
        return 0
    size = args.lot
    print(f"{run.name} : {len(slots)} emplacement(s) restant(s). Lot à écrire, une famille par emplacement, dans {run / 'familles'} :")
    for slot in slots[:size]:
        code = slot.get("competency") or ", ".join(slot.get("competencies", []))
        print(f"  {slot['slot']} {slot['difficulty']} {slot['response_type']} taille {slot['size']}"
              f"{' figure' if slot.get('figure') else ''} [{code}]")
        if slot["response_type"] in forge.DRAW_TYPES:
            print(f"    {forge.trace_brief(slot)}")
        if slot.get("archetype"):
            import plan
            print(f"    archétype {slot['archetype']} : {plan.ARCHETYPES.get(slot['archetype'], '')}")
        if slot.get("idee"):
            print(f"    idée {slot['idee']['id']} : {slot['idee']['mecanisme']}")
            if slot["idee"].get("ne_pas_reprendre"):
                print(f"      à ne pas reprendre : {slot['idee']['ne_pas_reprendre']}")
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
    _print_limited(forge.lot_hints(summary, kind) + forge.trace_lines(summary))
    for slot in summary.get("field_whitespace", {}):
        print(f"réserve {slot} : zone d’écriture trop large par rapport aux réponses (voir render.json)")
    simulation = summary["simulation"]
    print(f"{len(summary['cards'])} carte(s) rendue(s) dans {run / 'cards'}. "
          f"Remplissage simulé : médiane {simulation['median_fill']:.0%}, "
          f"10 % des pires {simulation['p10_fill']:.0%}.")
    return 0


def _cmd_goldfish(args, db):
    import goldfish
    run = forge.resolve_run(args.run)
    try:
        state = goldfish.run(run, slots=args.slots, cli=args.cli, session_dir=args.session_dir, model=args.modele)
    except goldfish.GoldfishError as error:
        _record_failure(args, db, str(error))
        raise
    verdicts = state["verdicts"]
    if state.get("pending"):
        print("Relecture en attente de réponses indépendantes : " + ", ".join(state["pending"]))
        return 2
    if args.slots:
        # Re-contrôle ciblé : seules ces cartes ont été relues.
        verdicts = {k: v for k, v in verdicts.items() if k in args.slots}
        import hashlib
        fresh = {k: v for k, v in verdicts.items() if v.get("image_sha256") == hashlib.sha256((run / "cards" / f"{k}.png").read_bytes()).hexdigest()}
        if len(fresh) != len(args.slots):
            print("Relecture non terminée : remplir les requêtes de session puis relancer Goldfish.")
            return 2
    lines = []
    for key in sorted(verdicts):
        entry = verdicts[key]
        if entry.get("flag"):
            action = "RÉÉCRIRE" if entry.get("rewrite") else (entry.get("severity") or "note")
            lines.append(f"{key} {action} : " + " | ".join(entry.get("notes") or []))
    _print_limited(lines)
    print(f"{len(verdicts)} carte(s) relue(s), {len(lines)} signalée(s).")
    return 0


def _record_failure(args, db, reason):
    from app.models import IndigoExercise
    import rework
    run = forge.resolve_run(args.run)
    refs = {f"{run.name}/{key}" for key in args.slots or []}
    for row in db.query(IndigoExercise).filter(IndigoExercise.source_number.in_(refs)).all():
        if (row.raw_ocr_json or {}).get("review_verdict") == "dislike":
            rework.failed(db, row, reason)


def _cmd_persist(args, db):
    run = forge.resolve_run(args.run)
    try:
        result = forge.persist(db, run, replace=args.replace, slots=args.slots, reprise=args.reprise)
    except SystemExit as error:
        db.rollback()
        _record_failure(args, db, str(error))
        raise
    print(f"{len(result['written'])} brouillon(s) dans l'onglet Exercices "
          f"(extraction {result['extraction_id']}).")
    print("À relire : " + ", ".join(result["review"]))
    rendered = forge.load_json(run / "render.json") if (run / "render.json").exists() else {}
    for line in forge.trace_lines(rendered):
        print(line)
    return 0


def _cmd_disliked(args, db):
    cards = forge.disliked(db, args.filtre, programme=getattr(args, "programme", None), cycle=getattr(args, "cycle", None),
                           domaine=getattr(args, "domaine", None), subject=getattr(args, "matiere", None))
    if getattr(args, "export", None):
        import rework
        forge.dump_json(Path(args.export), rework.export(db, cards))
        print("Manifeste : " + str(args.export))
    for card in cards:
        origin = "" if card["forge"] else " [hors forge : à reprendre à la main]"
        note = " | ".join(card["notes"]) or "aucun signalement"
        print(f"{card['ref']} ({card['competency']}, {card['status']}){origin} : {note}")
        if card["comment"]:
            print("  Commentaire admin : " + card["comment"])
        if card["goldfish"]:
            print("  Goldfish : " + json.dumps(card["goldfish"], ensure_ascii=False))
    print(f"{len(cards)} carte(s) à reprendre.")
    return 0


def _cmd_resume(args, db):
    runs = forge.unfinished_runs()
    if not runs:
        print("Aucun run inachevé.")
        return 0
    for run in runs:
        print(f"{run.name} : reste {len(forge.remaining_slots(run))} emplacement(s)")
    return 0


def _cmd_calendar(args, db):
    """Bases du déclencheur « calendrier » : ce qui commence bientôt, pour les niveaux
    qui ont des classes. Ne lance rien : la génération reste une commande."""
    from datetime import date

    import plan
    from app.models import Competency, CompetencyFramework, SchoolClass
    today = date.today()
    start = date.fromisoformat(args.rentree) if args.rentree else plan.default_start(today)
    week = plan.school_week(today, start)
    classes: dict[str, int] = {}
    for row in db.query(SchoolClass).all():
        classes[row.grade_level] = classes.get(row.grade_level, 0) + 1
    lessons = []
    frameworks = (db.query(CompetencyFramework)
                  .filter_by(program_id=args.programme, subject_id=args.matiere)
                  .filter(CompetencyFramework.status != "archived").all())
    for framework in frameworks:
        serie = framework.path_id.split("-", 1)[-1] if framework.path_id else None
        seen = set()
        for comp in db.query(Competency).filter_by(framework_id=framework.id).order_by(Competency.order_index):
            if comp.chapter_code in seen or comp.chapter_week is None:
                continue
            seen.add(comp.chapter_code)
            fiche = forge.fiche_path(args.programme, framework.grade_level, comp.chapter_code,
                                     args.matiere, serie)
            lessons.append({"level": framework.grade_level, "serie": serie, "code": comp.chapter_code,
                            "name": comp.chapter_name, "week": comp.chapter_week,
                            "classes": classes.get(framework.grade_level, 0), "fiche": fiche.exists()})
    print(f"Semaine {week} de l'année scolaire (rentrée le {start.isoformat()}). "
          f"Leçons qui commencent d'ici {args.semaines} semaine(s) :")
    rows = plan.upcoming(lessons, week, args.semaines)
    for lesson in rows:
        serie = f" {lesson['serie']}" if lesson["serie"] else ""
        state = "fiche prête" if lesson["fiche"] else "FICHE À ÉCRIRE"
        active = f"{lesson['classes']} classe(s)" if lesson["classes"] else "aucune classe"
        print(f"  sem. {lesson['week']:>2}  {lesson['level']}{serie} {lesson['code']} "
              f"{lesson['name']} — {active}, {state}")
    if not rows:
        print("  aucune")
    return 0


def _cmd_exhaustion(args, db):
    """Déclencheur « épuisement » : les compétences où une classe n'a presque plus
    d'exercices inédits, avec la commande de complément."""
    from app.services import bank_health
    rows = bank_health.exhaustion(db)
    if not rows:
        print("Aucune compétence épuisée.")
        return 0
    for row in rows:
        print(f"  [{row['severity']}] {row['programme']} {row['cycle']} {row['target']} "
              f"({row['difficulty']}) {row['label']} — {row['students_out']} élève(s) sans inédit, "
              f"médiane {row['median_unseen']:g} inédit(s)\n      {row['command']}")
    return 0


def _cmd_trust(args, db):
    """Indice de confiance par format : ce qui a été relu, et ce qu'il reste à relire."""
    import confiance
    known = confiance.streaks(db, args.matiere)
    print(f"Confiance des formats ({args.matiere}). Un format jamais relu est relu en entier.")
    for rtype in forge.LEAF_TYPES + (forge.COMPOSITE,):
        entry = known.get(rtype)
        if entry:
            print(f"  {rtype:<17} {entry['reviewed']:>4} relue(s), {entry['streak']:>4} acceptée(s) "
                  f"d'affilée → {round(entry['rate'] * 100)} % à relire")
        else:
            print(f"  {rtype:<17}    0 relue → 100 % à relire")
    return 0


def _read_sources(paths: list[str]) -> list[str]:
    """Textes des sources (.md, .txt) ; un dossier est lu en entier. Un chemin relatif
    se lit depuis le dossier de lancement, car ce script se place dans backend/."""
    texts = []
    for raw in paths or []:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            base = Path(os.environ.get("PWD") or _HERE.parents[1])
            path = base / path if (base / path).exists() else _HERE.parents[1] / path
        if not path.exists():
            raise SystemExit(f"Source introuvable : {raw}")
        files = sorted(path.rglob("*")) if path.is_dir() else [path]
        for item in files:
            if item.is_file() and item.suffix.lower() in (".md", ".txt", ".markdown"):
                texts.append(item.read_text(encoding="utf-8", errors="replace"))
            elif item == path:
                print(f"Source ignorée (ni .md ni .txt) : {raw}")
    return texts


def _cmd_fiche(args, db):
    """Contrôle par script de la fiche d'une leçon et de ses fiches-idées."""
    import contexte
    target = forge.resolve_target(db, args.programme, args.cycle, args.lecon, args.matiere, args.serie)
    chapter = target["chapter"][0].chapter_code
    fiche = forge.fiche_path(args.programme, args.cycle, chapter, args.matiere, args.serie)
    if not fiche.exists():
        print(f"FICHE ABSENTE : {fiche}")
        return 1
    codes = [forge.comp_code(c) for c in target["chapter"]]
    sources = _read_sources(args.sources)
    if args.matiere == forge.SUBJECT_DEFAULT and args.programme == "fr":
        indigo, _ = forge.source_text(args.cycle, chapter)
        if indigo:
            sources.append(indigo)
    problems = contexte.fiche_problems(fiche.read_text(encoding="utf-8"), codes, sources)
    ideas = fiche.with_suffix(".idees.yaml")
    if ideas.exists():
        problems += contexte.idees_problems(ideas.read_text(encoding="utf-8"), codes, sources)
    print(f"{fiche} ({', '.join(codes)}, {len(sources)} source(s))")
    for problem in problems:
        print(f"  {problem}")
    print(f"{len(problems)} problème(s)." if problems else "Fiche conforme.")
    return 1 if problems else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="forge", description="Pipeline forge.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    prep = sub.add_parser("prepare", help="Prépare le contexte et les grilles.")
    prep.add_argument("programme", help="Programme, ex. fr")
    prep.add_argument("cycle", help="Cycle, ex. 3e")
    prep.add_argument("cible", help="Compétence (A1.2) ou chapitre (A1)")
    prep.add_argument("--modele", required=True, help="Modèle réellement utilisé, ex. claude-opus-5-5")
    prep.add_argument("--matiere", default=forge.SUBJECT_DEFAULT)
    prep.add_argument("--serie", help="Série du lycée ivoirien : A, A1, A2, C, D")
    prep.add_argument("--suite", action="store_true", help="Le reste du socle, après l'amorce")
    prep.add_argument("--vingt", action="store_true", help="Ancienne grille fixe de 20 cartes")
    prep.add_argument("--problemes", action="store_true", help="Les 20 problèmes du chapitre")
    prep.add_argument("--pilote", action="store_true", help="Toutes les cartes à relire")
    prep.add_argument("--complement", type=int, help="Second run : nombre d'exercices à ajouter")
    prep.add_argument("--traces", type=int, help="Run de géométrie à tracer : nombre de cartes")
    prep.add_argument("--facile", type=int, help="Second run : nombre de faciles")
    prep.add_argument("--moyen", type=int, help="Second run : nombre de moyens")
    prep.add_argument("--difficile", type=int, help="Second run : nombre de difficiles")
    for name, helptext in (("check", "Contrôles gratuits."), ("render", "Rendu réel et blanc."),
                           ("persist", "Écrit les brouillons."), ("suivant", "Prochain lot à écrire."),
                           ("goldfish", "Relecture sans contexte.")):
        cmd = sub.add_parser(name, help=helptext)
        cmd.add_argument("run", nargs="?")
        if name == "persist":
            cmd.add_argument("--reprise", type=Path, help="Manifeste de reprise à vérifier")
            cmd.add_argument("--replace", action="store_true")
            cmd.add_argument("--slots", nargs="*", help="Remplace seulement ces emplacements")
        if name == "suivant":
            cmd.add_argument("--lot", type=int, default=5)
        if name == "goldfish":
            cmd.add_argument("--slots", nargs="*")
            cmd.add_argument("--cli", choices=("claude", "codex"))
            cmd.add_argument("--session-dir", type=Path, help="Transport de session indépendante : requêtes/réponses JSON")
            cmd.add_argument("--modele", help="Modèle indépendant utilisé dans la session")
    sub.add_parser("reprendre", help="Liste les runs inachevés.")
    cal = sub.add_parser("calendrier", help="Leçons qui commencent bientôt, et celles sans fiche.")
    cal.add_argument("programme")
    cal.add_argument("--matiere", default=forge.SUBJECT_DEFAULT)
    cal.add_argument("--semaines", type=int, default=3, help="Horizon, en semaines")
    cal.add_argument("--rentree", help="Date de la rentrée, AAAA-MM-JJ")
    sub.add_parser("epuisement", help="Compétences dont une classe n'a plus d'inédits.")
    trust = sub.add_parser("confiance", help="Part des cartes à relire, par format.")
    trust.add_argument("--matiere", default=forge.SUBJECT_DEFAULT)
    ecartes = sub.add_parser("ecartes", help="Liste les cartes au pouce baissé.")
    ecartes.add_argument("filtre", nargs="?", default="", help="Morceau du run, ex. fr-3e-A4")
    for option in ("programme", "cycle", "domaine", "matiere"):
        ecartes.add_argument("--" + option)
    ecartes.add_argument("--export", type=Path, help="Exporte un manifeste de reprise")
    fiche = sub.add_parser("fiche", help="Contrôle la fiche d'une leçon (forge contexte).")
    fiche.add_argument("programme", help="Programme, ex. fr")
    fiche.add_argument("cycle", help="Niveau, ex. 3e")
    fiche.add_argument("lecon", help="Leçon (C2) ou une de ses compétences")
    fiche.add_argument("--serie", help="Série du lycée ivoirien : A, A1, A2, C, D")
    fiche.add_argument("--matiere", default=forge.SUBJECT_DEFAULT)
    fiche.add_argument("--sources", nargs="*", default=[], help="Sources texte ou Markdown")
    args = parser.parse_args(argv)
    db = SessionLocal()
    try:
        return {"prepare": _cmd_prepare, "suivant": _cmd_next, "check": _cmd_check,
                "render": _cmd_render, "goldfish": _cmd_goldfish, "persist": _cmd_persist,
                "reprendre": _cmd_resume, "ecartes": _cmd_disliked,
                "calendrier": _cmd_calendar, "epuisement": _cmd_exhaustion,
                "confiance": _cmd_trust, "fiche": _cmd_fiche}[args.cmd](args, db) or 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
