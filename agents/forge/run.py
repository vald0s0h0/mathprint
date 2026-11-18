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
    backend/.venv/bin/python agents/forge/run.py relecture [RUN] [--slots s03 s07]
    backend/.venv/bin/python agents/forge/run.py persist [RUN] [--replace] [--slots s03 s07]
    backend/.venv/bin/python agents/forge/run.py bilan [RUN]
    backend/.venv/bin/python agents/forge/run.py qualite [--matiere math]
    backend/.venv/bin/python agents/forge/run.py reprendre
    backend/.venv/bin/python agents/forge/run.py ecartes [fr-3e-A4]
    backend/.venv/bin/python agents/forge/run.py lecon fr 3e C2 [--verifier] [--accepter F3] [--sources cours.md]

RUN = identifiant ou dossier du run (défaut : le plus récent). Procédure :
agents/forge/CONSIGNE-EXERCICES.md et CONSIGNE-LECON.md. Sorties courtes volontairement : ce texte entre dans le
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
    # La leçon s'écrit avant, par `forge lecon` : sans leçon conforme et vérifiée,
    # pas d'exercices.
    target = forge.resolve_target(db, args.programme, args.cycle, args.cible, args.matiere, args.serie)
    blocked = _lesson_gate(args, target)
    if blocked:
        return 1
    runs = forge.prepare(db, args.programme, args.cycle, args.cible, model=args.modele,
                         problems=args.problemes, subject=args.matiere, pilot=args.pilote,
                         complement=complement, traces=args.traces, serie=args.serie,
                         volume="vingt" if args.vingt else "formule", suite=args.suite)
    if not runs:
        print("Rien à générer pour cette cible : une compétence « Traiter une situation » se "
              "travaille par les problèmes de la leçon (--problemes).")
        return 0
    total = 0
    for run in runs:
        payload = forge.load_json(run / "payload.json")
        grid = forge.load_json(run / "grid.json")
        total += len(grid)
        archetypes = " ".join(s.get("archetype") or "?" for s in grid)
        traces = sum(s["response_type"] in forge.DRAW_TYPES for s in grid)
        print(f"{run.name} : {len(grid)} famille(s) [{archetypes}]"
              + (f", {traces} tracé(s)" if traces else ""))
    first = forge.load_json(runs[0] / "payload.json")["context"]
    print(f"{total} famille(s) en tout, {len(runs)} run(s). Un seul agent les écrit toutes, "
          "compétence après compétence, sans sous-agent.")
    print("Lis ce fichier une seule fois, en entier, sans le modifier :")
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


def _cmd_review(args, db):
    import goldfish
    import relecture
    run = forge.resolve_run(args.run)
    state = relecture.run(run, slots=args.slots, session_dir=args.session_dir, model=args.modele)
    if state.get("pending"):
        print("Relecture en attente de réponses indépendantes : " + ", ".join(state["pending"]))
        return 2
    lines = []
    for key in sorted(state["verdicts"]):
        entry = state["verdicts"][key]
        if args.slots and key not in args.slots:
            continue
        if entry.get("ecartee"):
            lines.append(f"{key} ÉCARTÉE (bloquée {entry['attempts']} fois) : {entry.get('critique')}")
        elif entry.get("blocked"):
            lines.append(f"{key} BLOQUÉE, à réécrire une fois : {entry.get('critique')}"
                         + (f" [{' ; '.join(entry['notes'])}]" if entry.get("notes") else ""))
        elif entry.get("remarques"):
            lines.append(f"{key} remarque pour l'humain : {entry['remarques']}")
    _print_limited(lines)
    blocked = sum(1 for v in state["verdicts"].values() if v.get("blocked") and not v.get("ecartee"))
    print(f"{len(state['verdicts'])} famille(s) relue(s), {blocked} bloquée(s), "
          f"{len(relecture.ecartees(run))} écartée(s).")
    return 1 if blocked else 0


def _cmd_validate(args, db):
    """Contrôle, rendu, goldfish, relecture : la boucle de vérification en une
    commande et une sortie courte. S'arrête à la première étape qui demande d'agir."""
    import goldfish
    import relecture
    run = forge.resolve_run(args.run)
    report, contracts = forge.check(db, run)
    if not report.ok:
        _print_limited([f"ERREUR {e}" for e in report.errors])
        print(f"check : {len(report.errors)} erreur(s). Corrige ces familles, puis relance `valider`.")
        return 1
    summary = forge.render(db, run)
    blocked = sorted(set(summary.get("writing") or []) | set(summary.get("leading") or [])
                     | set(summary.get("traces") or []) | set(summary.get("geometry") or []))
    height_blocked = {group for group, measure in (summary.get("family_heights") or {}).items()
                      if measure["too_different"] and (not args.slots or group in args.slots)}
    blocked = sorted(set(blocked) | height_blocked)
    hints = forge.lot_hints(summary, forge.load_json(run / "grid.json")[0]["kind"])
    if blocked:
        _print_limited(hints)
        print(f"render : mise en page refusée pour {', '.join(blocked)}. Réécris ces cartes, puis relance `valider`.")
        return 1
    state = goldfish.run(run, slots=args.slots)
    flagged = {k: v for k, v in state["verdicts"].items()
               if v.get("severity") == "blocker" and (not args.slots or k.split(".")[0] in args.slots)}
    if flagged:
        _print_limited([f"{k} {'RÉÉCRIRE' if v.get('rewrite') else 'bloquée'} : " + " | ".join(v.get("notes") or [])
                        for k, v in sorted(flagged.items())])
        print("goldfish : corrige ces cartes (vérifie d'abord la réponse du lecteur), puis relance `valider`.")
        return 1
    relecture.run(run, slots=args.slots)
    pending = relecture.problems(run)
    if pending:
        _print_limited(pending)
        print("relecture : réécris une fois chaque carte bloquée avec sa critique, puis relance `valider`.")
        return 1
    dropped = relecture.ecartees(run)
    print(f"{len(contracts)} carte(s) valides et relues"
          + (f", écartée(s) : {', '.join(sorted(dropped))}" if dropped else "") + ".")
    if args.slots:
        print("Reprise ciblée : lance `persist` avec --slots et --reprise.")
        return 0
    try:
        result = forge.persist(db, run)
    except SystemExit as error:
        db.rollback()
        print(f"persist : {error}")
        return 1
    print(f"{len(result['written'])} brouillon(s) écrits. À relire : " + (", ".join(result["review"]) or "aucune"))
    return 0


def _tokens_line(label: str, usage: list[dict]) -> str:
    import mesure
    total = mesure.total(usage)
    return (f"  {label} : {total['calls']} appel(s), entrée {total['input']:,} + cache lu {total['cache_read']:,}"
            f" + cache écrit {total['cache_write']:,}, sortie {total['output']:,}").replace(",", " ")


def _cmd_report(args, db):
    """Bilan d'un run : ce qui est écrit, écarté, à relire, et ce que le run a consommé."""
    import mesure
    import relecture
    run = forge.resolve_run(args.run)
    grid = forge.load_json(run / "grid.json")
    review = relecture.load_state(run) if relecture.state_path(run).exists() else {"verdicts": {}, "usage": []}
    gold = forge.load_json(run / "goldfish.json") if (run / "goldfish.json").exists() else {}
    dropped = sorted(k for k, v in review["verdicts"].items() if v.get("ecartee"))
    print(f"{run.name} : {len(grid)} famille(s) prévue(s), {len(forge.remaining_slots(run))} à écrire, "
          f"{len(dropped)} écartée(s).")
    for key in dropped:
        print(f"  écartée {key} : {review['verdicts'][key].get('critique')}")
    for key, entry in sorted(review["verdicts"].items()):
        if entry.get("remarques") and not entry.get("ecartee"):
            print(f"  à regarder {key} : {entry['remarques']}")
    proposed = [p for ex in forge.load_slots(run).values() for p in ex.get("proposed_errors") or []]
    for item in proposed:
        print(f"  code proposé : {item.get('code')} — {item.get('label')}")
    print("Tokens :")
    print(_tokens_line("goldfish", gold.get("usage") or []))
    print(_tokens_line("relecture", review.get("usage") or []))
    author = mesure.author_usage(run.name)
    if author["calls"]:
        print(_tokens_line(f"rédacteur ({author['files']} transcription(s) de sous-agent)", author["usage"]))
    else:
        print("  rédacteur : aucune transcription de sous-agent ne cite ce run")
    return 0


def _cmd_quality(args, db):
    """Lot 0 : les avis humains sur les cartes de la forge, par cause et par format."""
    import confiance
    from app.models import IndigoExercise
    by_cause: dict[str, int] = {}
    by_format: dict[str, list[int]] = {}
    for row in db.query(IndigoExercise).all():
        raw = row.raw_ocr_json or {}
        if raw.get("pipeline") != forge.PIPELINE or (raw.get("subject") or "math") != args.matiere:
            continue
        twin = (row.grading_json or {}).get("twin")
        if twin and not twin.get("principal"):
            continue
        verdict = raw.get("review_verdict")
        if verdict not in ("like", "dislike"):
            continue
        counts = by_format.setdefault(row.response_type, [0, 0])
        counts[0 if verdict == "like" else 1] += 1
        if verdict == "dislike":
            cause = raw.get("review_cause") or "sans cause"
            by_cause[cause] = by_cause.get(cause, 0) + 1
    print(f"Avis humains sur la forge ({args.matiere}), une famille comptée une fois :")
    for rtype, (likes, dislikes) in sorted(by_format.items()):
        total = likes + dislikes
        print(f"  {rtype:<17} {total:>4} relue(s), {dislikes:>3} refusée(s) ({round(100 * dislikes / total)} %)")
    print("Refus par cause :")
    for cause, count in sorted(by_cause.items(), key=lambda item: -item[1]):
        label = confiance.CAUSES.get(cause, (cause, ""))
        print(f"  {label[0]} : {count}" + (f" → corriger {label[1]}" if label[1] else ""))
    if not by_cause:
        print("  aucun")
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
    if result.get("ecartees"):
        print("Écartées par la relecture pédagogique, non écrites : " + ", ".join(result["ecartees"]))
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
        if card.get("cause"):
            import confiance
            label, level = confiance.CAUSES.get(card["cause"], (card["cause"], ""))
            print(f"  Cause : {label}" + (f" → corriger aussi {level}" if level else ""))
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
            fiche = forge.lecon_path(args.programme, framework.grade_level, comp.chapter_code,
                                     args.matiere, serie)
            lessons.append({"level": framework.grade_level, "serie": serie, "code": comp.chapter_code,
                            "name": comp.chapter_name, "week": comp.chapter_week,
                            "classes": classes.get(framework.grade_level, 0), "fiche": fiche.exists()})
    print(f"Semaine {week} de l'année scolaire (rentrée le {start.isoformat()}). "
          f"Leçons qui commencent d'ici {args.semaines} semaine(s) :")
    rows = plan.upcoming(lessons, week, args.semaines)
    for lesson in rows:
        serie = f" {lesson['serie']}" if lesson["serie"] else ""
        state = "leçon prête" if lesson["fiche"] else "LEÇON À ÉCRIRE"
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
    """Indice de confiance par format, et par format dans chaque archétype."""
    import confiance
    known = confiance.streaks(db, args.matiere)
    print(f"Confiance des formats ({args.matiere}). Un format jamais relu est relu en entier.")
    for key in sorted(k for k in known if "/" in k):
        entry = known[key]
        print(f"  {key:<24} {entry['reviewed']:>4} relue(s), {entry['streak']:>4} acceptée(s) "
              f"d'affilée → {round(entry['rate'] * 100)} % à relire")
    print("Par format, tous archétypes :")
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


def _lesson_args(args) -> str:
    serie = f" --serie {args.serie}" if args.serie else ""
    matiere = f" --matiere {args.matiere}" if args.matiere != forge.SUBJECT_DEFAULT else ""
    return serie + matiere


def _lesson_gate(args, target) -> bool:
    """Refuse un run sans leçon conforme : absente, à l'ancien format ou pas vérifiée."""
    import lecon
    chapter = target["chapter"][0].chapter_code
    path = forge.lecon_path(args.programme, args.cycle, chapter, args.matiere, args.serie)
    command = f"forge lecon {args.programme} {args.cycle} {chapter}{_lesson_args(args)}"
    if not path.exists():
        print(f"LEÇON ABSENTE : {path}\nÉcris-la d'abord, dans une session à part :\n  {command}")
        return True
    text = path.read_text(encoding="utf-8")
    codes = [forge.comp_code(c) for c in target["chapter"]]
    problems = lecon.structure_problems(text, codes, args.matiere)
    if problems:
        print(f"LEÇON À RÉÉCRIRE : {path}\n  " + "\n  ".join(problems)
              + f"\nRéécris-la dans une session à part :\n  {command} --refaire")
        return True
    blocking = lecon.blocking_facts(text, lecon.load_verif(path))
    if blocking:
        print(f"LEÇON À VÉRIFIER : {path}\n  " + "\n  ".join(blocking)
              + f"\nLance la vérification ou fais trancher l'humain :\n"
              f"  backend/.venv/bin/python agents/forge/run.py lecon {args.programme} {args.cycle} {chapter}"
              f"{_lesson_args(args)} --verifier")
        return True
    return False


def _cmd_lesson(args, db):
    """Contrôle par script d'une leçon, vérification de ses faits par un modèle neuf,
    et accord de l'humain sur un fait contesté."""
    import goldfish
    import lecon
    target = forge.resolve_target(db, args.programme, args.cycle, args.lecon, args.matiere, args.serie)
    chapter = target["chapter"][0].chapter_code
    path = forge.lecon_path(args.programme, args.cycle, chapter, args.matiere, args.serie)
    if not path.exists():
        print(f"LEÇON ABSENTE : {path}")
        return 1
    if args.accepter:
        done = lecon.accept(path, args.accepter)
        print("Maintenu(s) par l'humain : " + (", ".join(done) or "aucun"))
    codes = [forge.comp_code(c) for c in target["chapter"]]
    sources = _read_sources(args.sources)
    if args.matiere == forge.SUBJECT_DEFAULT and args.programme == "fr":
        indigo, _ = forge.source_text(args.cycle, chapter)
        if indigo:
            sources.append(indigo)
    upper = [p.read_text(encoding="utf-8") for p in forge.upper_levels(args.matiere, args.programme)]
    text = path.read_text(encoding="utf-8")
    problems = lecon.lecon_problems(text, codes, sources, upper, args.matiere)
    print(f"{path} ({', '.join(codes)}, {len(sources)} source(s))")
    for problem in problems:
        print(f"  {problem}")
    if problems:
        print(f"{len(problems)} problème(s) : corrige la leçon avant de la faire vérifier.")
        return 1
    if args.verifier:
        referential = forge.referential_text(args.programme, args.cycle, args.serie, target["chapter"],
                                             target.get("all"))

        def ask(system, prompt):
            goldfish.USAGE.clear()
            answer = goldfish.ask("claude", system, prompt, [], model=args.modele)
            return answer, (goldfish.USAGE[-1] if goldfish.USAGE else {})
        lecon.verify(path, referential, ask, model=args.modele)
    status = lecon.fact_status(text, lecon.load_verif(path))
    unsure = [k for k, f in status.items() if f["incertain"]]
    contested = [(k, f) for k, f in status.items() if f["verdict"] not in (None, "juste", lecon.ACCEPTED)]
    unverified = [k for k, f in status.items() if f["verdict"] is None]
    print(f"{len(status)} fait(s), {len(unverified)} non vérifié(s), {len(contested)} contesté(s), "
          f"{len(unsure)} incertain(s) selon l'auteur.")
    for key, fact in contested:
        print(f"  {key} {fact['verdict']} : {fact['raison']}\n      {fact['text']}")
    if unsure:
        print("  Incertains à relire : " + ", ".join(unsure))
    if contested:
        print("Corrige ces faits dans la leçon (puis --verifier), ou maintiens-les avec --accepter F…")
    blocking = lecon.blocking_facts(text, lecon.load_verif(path))
    print("Leçon conforme et vérifiée." if not blocking else "Leçon pas encore utilisable par la forge.")
    return 1 if blocking else 0


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
    prep.add_argument("--suite", action="store_true", help="Une nouvelle série, différente de la banque")
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
                           ("goldfish", "Lecture par un élève sans contexte."),
                           ("relecture", "Relecture pédagogique indépendante."),
                           ("valider", "Contrôle, rendu, goldfish et relecture, d'une traite."),
                           ("bilan", "Bilan du run et tokens consommés.")):
        cmd = sub.add_parser(name, help=helptext)
        cmd.add_argument("run", nargs="?")
        if name == "persist":
            cmd.add_argument("--reprise", type=Path, help="Manifeste de reprise à vérifier")
            cmd.add_argument("--replace", action="store_true")
            cmd.add_argument("--slots", nargs="*", help="Remplace seulement ces emplacements")
        if name == "suivant":
            cmd.add_argument("--lot", type=int, default=5)
        if name == "valider":
            cmd.add_argument("--slots", nargs="*")
        if name == "relecture":
            cmd.add_argument("--slots", nargs="*")
            cmd.add_argument("--session-dir", type=Path, help="Transport de session indépendante : requêtes/réponses JSON")
            cmd.add_argument("--modele", default="opus", help="Modèle du relecteur (défaut : opus)")
        if name == "goldfish":
            cmd.add_argument("--slots", nargs="*")
            cmd.add_argument("--cli", choices=("claude", "codex"))
            cmd.add_argument("--session-dir", type=Path, help="Transport de session indépendante : requêtes/réponses JSON")
            cmd.add_argument("--modele", help="Modèle indépendant utilisé dans la session")
    sub.add_parser("reprendre", help="Liste les runs inachevés.")
    cal = sub.add_parser("calendrier", help="Leçons qui commencent bientôt, et celles sans contexte.")
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
    lesson = sub.add_parser("lecon", help="Contrôle et vérifie le contexte d'une leçon (forge lecon).")
    lesson.add_argument("programme", help="Programme, ex. fr")
    lesson.add_argument("cycle", help="Niveau, ex. 3e")
    lesson.add_argument("lecon", help="Leçon (C2) ou une de ses compétences")
    lesson.add_argument("--serie", help="Série du lycée ivoirien : A, A1, A2, C, D")
    lesson.add_argument("--matiere", default=forge.SUBJECT_DEFAULT)
    lesson.add_argument("--sources", nargs="*", default=[], help="Sources texte ou Markdown")
    lesson.add_argument("--verifier", action="store_true", help="Fait juger les faits par un modèle neuf")
    lesson.add_argument("--modele", default="opus", help="Modèle du vérificateur (défaut : opus)")
    lesson.add_argument("--accepter", nargs="*", default=[], help="Faits contestés que l'humain maintient")
    quality = sub.add_parser("qualite", help="Avis humains sur la forge, par cause et par format.")
    quality.add_argument("--matiere", default=forge.SUBJECT_DEFAULT)
    args = parser.parse_args(argv)
    db = SessionLocal()
    try:
        return {"prepare": _cmd_prepare, "suivant": _cmd_next, "check": _cmd_check,
                "render": _cmd_render, "goldfish": _cmd_goldfish, "persist": _cmd_persist,
                "reprendre": _cmd_resume, "ecartes": _cmd_disliked,
                "calendrier": _cmd_calendar, "epuisement": _cmd_exhaustion,
                "confiance": _cmd_trust, "lecon": _cmd_lesson, "relecture": _cmd_review,
                "valider": _cmd_validate,
                "bilan": _cmd_report, "qualite": _cmd_quality}[args.cmd](args, db) or 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
