"""Forge : préparation, grilles, contrôles, rendu, compactage, persistance.

Le modèle (Opus ou GPT Sol, en session d'agent) lit le contexte préparé ici et
écrit un fichier JSON par emplacement. Tout le reste est déterministe et gratuit :
aucun appel à un modèle dans ce module. Voir FORGE.md et schema.md.
"""
from __future__ import annotations

import json
import random
import re
import shutil
import statistics
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

PIPELINE = "forge"
VERSION = "forge-1.0"
PROMPT_VERSION = "forge-noyau-1"
SUBJECT_DEFAULT = "math"

LEVEL_NAMES = {1: "facile", 2: "moyen", 3: "difficile"}
VARIANT_BY_LEVEL = {1: "facile", 2: "base", 3: "difficile"}
CV_TYPES = ("qcm_single", "qcm_multiple", "checkbox_grid", "matching")
WRITTEN_TYPES = ("short_text", "multi_blank", "table_fill", "multiline_text")
LEAF_TYPES = CV_TYPES + WRITTEN_TYPES
COMPOSITE = "composite"

# Hauteur totale, bande de micro-correction et espacement compris : la plus petite
# carte possible (un QCM d'une ligne) mesure environ 35 mm.
SIZE_LIMITS_MM = (("XS", 52), ("S", 66), ("M", 90), ("L", 120), ("XL", 10_000))
SIZE_TOLERANCE = 2
EXERCISE_COMPOSITE_MAX = 4          # 20 % de 20
PART_MAX_EXERCISE, PART_MAX_PROBLEM = 3, 5
FIGURE_MIN, FIGURE_MAX = 4, 14
PROBLEM_FIGURE_MIN = 8
TIME_BOUNDS = {1: (20, 180), 2: (45, 360), 3: (90, 720)}
PROBLEM_TIME_BOUNDS = {1: (90, 300), 2: (180, 540), 3: (300, 900)}
POINTS_PER_MINUTE = (0.6, 1.5)
PROBLEM_POINTS_PER_MINUTE = (0.3, 1.2)
# L'interligne et les marges normales ne comptent pas : seul l'excès au-delà de
# NORMAL_GAP_MM dans chaque bande vide est du blanc perdu.
EMPTY_RATIO_MAX, EMPTY_RUN_MAX_MM, SIDE_VOID_MAX, NORMAL_GAP_MM = 0.15, 12.0, 0.25, 6.0
FIGURE_SCALES = (1.0, 0.85, 0.7)
FIGURE_MIN_RATIO = 0.7
SIMULATIONS = 200
FILL_MEDIAN_MIN, FILL_P10_MIN = 0.90, 0.80
COPY_RATIO_MAX, COPY_MIN_WORDS = 0.30, 10
SHINGLE = 5
_NOISE_RE = re.compile(r"QUITTER LE PLEIN|Hachette Livre|photocopie non autoris|^←$|^!\[img")
_PRINT_ENCODING = "cp1252"


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text or "")
                   if not unicodedata.combining(c)).lower().strip()


def data_dir() -> Path:
    from app.config import settings
    path = Path(settings.data_dir) / "forge"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def write_stable(path: Path, text: str) -> None:
    """N'écrit que si le contenu change : un octet modifié invaliderait le cache
    du modèle pour tout ce qui suit dans son contexte."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")


def size_class(height_mm: float) -> str:
    return next(name for name, limit in SIZE_LIMITS_MM if height_mm < limit)


# ================================================================== CIBLE
def framework_for(db, programme: str, cycle: str, subject: str = SUBJECT_DEFAULT):
    """Le référentiel fixe programme, cycle et matière : le filtre est explicite,
    pour ne jamais prendre une compétence d'un autre programme de même niveau."""
    from app.models import CompetencyFramework
    frameworks = (db.query(CompetencyFramework)
                  .filter_by(program_id=programme, grade_level=cycle,
                             subject_id=subject, path_id=None)
                  .filter(CompetencyFramework.status != "archived").all())
    frameworks.sort(key=lambda f: f.program_year or 0, reverse=True)
    return frameworks[0] if frameworks else None


def resolve_target(db, programme: str, cycle: str, target: str, subject: str = SUBJECT_DEFAULT) -> dict:
    """(référentiel, chapitre, compétence visée ou None). Une cible ambiguë ou
    inconnue arrête la commande plutôt que d'être devinée."""
    from app.models import Competency
    framework = framework_for(db, programme, cycle, subject)
    if framework is None:
        raise SystemExit(f"Aucun référentiel {programme} {cycle} {subject}.")
    comps = (db.query(Competency).filter_by(framework_id=framework.id)
             .order_by(Competency.order_index).all())
    wanted = fold(target)
    hit = [c for c in comps if fold(c.short_id or c.code) == wanted or fold(c.code) == wanted]
    if len(hit) == 1:
        chapter = [c for c in comps if c.chapter_code == hit[0].chapter_code]
        return {"framework": framework, "chapter": chapter, "competency": hit[0]}
    chapter = [c for c in comps if fold(c.chapter_code) == wanted]
    if chapter:
        return {"framework": framework, "chapter": chapter, "competency": None}
    known = ", ".join(sorted({c.chapter_code for c in comps if c.chapter_code}))
    raise SystemExit(f"Cible {target!r} inconnue en {programme} {cycle}. Chapitres : {known}")


def comp_code(comp) -> str:
    return comp.short_id or comp.code


# ================================================================== GRILLES
def exercise_grid(code: str, n_figures: int) -> list[dict]:
    """Les 20 emplacements d'une compétence : 8 faciles, 8 moyens, 4 difficiles ;
    14 réponses CV et 6 rédactions ; tailles variées. Déterministe : le même
    appel redonne la même grille, y compris après une reprise."""
    plan = (
        # (niveau, format, taille)
        (1, "qcm_single", "XS"), (1, "qcm_single", "XS"), (1, "checkbox_grid", "XS"),
        (1, "qcm_single", "XS"), (1, "qcm_multiple", "S"), (1, "matching", "S"),
        (1, "qcm_single", "S"), (1, "checkbox_grid", "M"),
        (2, "qcm_single", "XS"), (2, "short_text", "S"), (2, "qcm_multiple", "S"),
        (2, "multi_blank", "S"), (2, "matching", "M"), (2, "checkbox_grid", "M"),
        (2, "qcm_multiple", "M"), (2, "table_fill", "L"),
        (3, "matching", "M"), (3, "multiline_text", "L"), (3, "multiline_text", "L"),
        (3, "table_fill", "XL"),
    )
    n_figures = max(FIGURE_MIN, min(FIGURE_MAX, n_figures))
    # Figures réparties sur les grandes cartes d'abord, à toutes les difficultés.
    rng = random.Random(f"{code}/figures")
    order = sorted(range(len(plan)), key=lambda i: (plan[i][2] in ("XS",), rng.random()))
    with_figure = set(order[:n_figures])
    return [{"slot": f"s{i + 1:02d}", "kind": "exercise", "competency": code, "level": level,
             "difficulty": LEVEL_NAMES[level], "response_type": rtype, "size": size,
             "figure": i in with_figure}
            for i, (level, rtype, size) in enumerate(plan)]


def problem_grid(codes: list[str]) -> list[dict]:
    """Les 20 problèmes d'un chapitre : chacun mobilise au moins 2 compétences, et
    chaque compétence revient dans plusieurs problèmes."""
    levels = [1] * 8 + [2] * 8 + [3] * 4
    parts = {1: 2, 2: 3, 3: 4}
    rng = random.Random(f"{','.join(codes)}/problemes")
    with_figure = set(rng.sample(range(20), PROBLEM_FIGURE_MIN))
    slots = []
    pool = list(codes)
    for i, level in enumerate(levels):
        wanted = max(2, min(len(pool), parts[level])) if len(pool) >= 2 else 1
        chosen = [pool[(i + k) % len(pool)] for k in range(wanted)]
        slots.append({"slot": f"p{i + 1:02d}", "kind": "problem", "competencies": chosen,
                      "level": level, "difficulty": LEVEL_NAMES[level],
                      "response_type": COMPOSITE, "parts": parts[level], "size": "L",
                      "figure": i in with_figure})
    return slots


def size_targets(grid: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for slot in grid:
        out[slot["size"]] = out.get(slot["size"], 0) + 1
    return out


# ================================================================== CONTEXTE
def chapter_table(cycle: str) -> dict:
    path = REPO / "agents" / "astra" / f"chapters_{cycle}.json"
    if not path.exists():
        return {}
    return {c["code"]: c for c in load_json(path)["chapters"]}


def _block_text(block: dict) -> str:
    text = str(block.get("content") or "").strip()
    return "" if not text or _NOISE_RE.search(text) else text


def clean_page(page: dict) -> str:
    """Texte seul d'une page d'index : sans coordonnées, sans l'interface de la
    liseuse. Le JSON brut pèserait autant que l'image de la page."""
    return "\n".join(t for t in (_block_text(b) for b in page.get("blocks") or []) if t)


def _index(cycle: str, which: str) -> dict:
    from app.config import settings
    path = Path(settings.data_dir) / "indigo" / "index" / cycle / f"{which}.json"
    return load_json(path).get("pages") or {} if path.exists() else {}


def source_text(cycle: str, code: str) -> tuple[str, dict]:
    """(texte du chapitre, statistiques) à partir de l'OCR déjà payé : leçon et
    exercices du manuel élève, chapitre du livre du professeur. Vide si le niveau
    n'a ni index ni table de pages (d'autres manuels viendront par ce format)."""
    entry = chapter_table(cycle).get(code)
    if not entry:
        return "", {"pages": 0}
    student = _index(cycle, "eleve")
    teacher = _index(cycle, "prof")
    out: list[str] = []
    pages = 0
    for role, span in (("LEÇON", entry.get("lesson")), ("EXERCICES", entry.get("exercises"))):
        if not span:
            continue
        for page_number in range(span[0], span[1] + 1):
            page = student.get(str(page_number - 1))
            text = clean_page(page) if page else ""
            if text:
                out.append(f"\n[{role} — page PDF {page_number}]\n{text}")
                pages += 1
    wanted = fold(entry["name"])
    for key in sorted(teacher, key=int):
        page = teacher[key]
        if wanted and wanted == fold(str(page.get("chapter") or "")):
            text = clean_page(page)
            if text:
                out.append(f"\n[LIVRE DU PROFESSEUR — page {key}]\n{text}")
                pages += 1
    return "\n".join(out).strip(), {"pages": pages, "chars": sum(map(len, out))}


def figure_pages(cycle: str, code: str, limit: int = 4) -> list[tuple[int, str]]:
    """Moitiés de doubles pages les plus riches en figures : les seules images
    utiles, car l'OCR perd les dessins."""
    entry = chapter_table(cycle).get(code)
    if not entry:
        return []
    student = _index(cycle, "eleve")
    scored = []
    for key in ("lesson", "exercises"):
        span = entry.get(key)
        for page_number in range(span[0], span[1] + 1) if span else ():
            page = student.get(str(page_number - 1)) or {}
            width = (page.get("dims") or {}).get("width") or 1
            counts = {"left": 0, "right": 0}
            for block in page.get("blocks") or []:
                if str(block.get("content") or "").startswith("![img"):
                    middle = (block.get("top_left_x", 0) + block.get("bottom_right_x", 0)) / 2
                    counts["left" if middle < width / 2 else "right"] += 1
            scored += [(n, page_number, side) for side, n in counts.items() if n]
    scored.sort(key=lambda s: (-s[0], s[1], s[2]))
    return sorted((p, s) for _n, p, s in scored[:limit])


def render_figure_pages(cycle: str, code: str, dest: Path) -> list[str]:
    import fitz

    from app.config import settings
    manuals = getattr(settings, "indigo_manuals", {}) or {}
    pdf = Path((manuals.get(cycle) or {}).get("eleve") or "")
    if not pdf.is_file():
        return []
    dest.mkdir(parents=True, exist_ok=True)
    files = []
    with fitz.open(str(pdf)) as doc:
        for page_number, side in figure_pages(cycle, code):
            name = f"page{page_number:03d}-{side}.png"
            path = dest / name
            if not path.exists():
                page = doc[page_number - 1]
                rect = page.rect
                half = rect.width / 2
                clip = fitz.Rect(rect.x0 if side == "left" else rect.x0 + half, rect.y0,
                                 rect.x0 + half if side == "left" else rect.x1, rect.y1)
                page.get_pixmap(dpi=130, clip=clip).save(str(path))
            files.append(str(path))
    return files


def published_records() -> list[dict]:
    path = REPO / "backend" / "app" / "data" / "indigo" / "exercises.json"
    return load_json(path).get("exercises") or [] if path.exists() else []


def published_examples(cycle: str, code: str, limit: int = 8) -> list[str]:
    """Exemples texte de la compétence, s'il en existe (8 compétences seulement)."""
    rows = [r for r in published_records() if r.get("grade_level") == cycle
            and r.get("competency_code") == code and r.get("variant_kind") != "facile"]
    rows.sort(key=lambda r: str(r.get("id")))
    return [str(r.get("statement") or "") for r in rows[:limit]]


def figure_share(cycle: str, code: str) -> float:
    rows = [r for r in published_records() if r.get("grade_level") == cycle
            and r.get("competency_code") == code]
    return sum(bool(r.get("has_figure")) for r in rows) / len(rows) if rows else 0.2


def build_context(run: Path, *, programme: str, cycle: str, subject: str, chapter_comps: list,
                  competency, problems: bool) -> dict:
    """Écrit le contexte du run, du plus stable au plus variable. Les blocs
    réutilisés vivent hors du run pour rester identiques d'un run à l'autre."""
    code = chapter_comps[0].chapter_code
    base = data_dir()
    tag = f"{programme}-{cycle}-{code}"
    source_path = base / "sources" / f"{tag}.md"
    text, stats = source_text(cycle, code)
    write_stable(source_path, text)
    images = render_figure_pages(cycle, code, base / "sources" / tag)
    referential = "\n".join(
        f"- {comp_code(c)} — {c.label}" + (f" : {c.description}" if c.description else "")
        for c in chapter_comps)
    referential_path = base / "sources" / f"{tag}-referentiel.md"
    write_stable(referential_path,
                 f"# Référentiel {programme} {cycle} — {chapter_comps[0].chapter_name}\n{referential}\n")
    fiche = base / "fiches" / f"{tag}.md"
    fiche.parent.mkdir(parents=True, exist_ok=True)
    read_order = [
        str(HERE / "FORGE.md"), str(HERE / "schema.md"),
        str(HERE / "matieres" / subject / "charte.md"),
        str(REPO / "backend" / "app" / "data" / "misconceptions" / f"{subject}.json"),
        str(HERE / "matieres" / subject / "etalons.json"),
        str(HERE / "programmes" / f"{programme}.md"),
        str(referential_path),
        str(fiche),
    ]
    pack = None
    if competency is not None and not problems:
        examples = published_examples(cycle, comp_code(competency))
        pack = base / "packs" / f"{programme}-{cycle}-{comp_code(competency)}.md"
        lines = [f"# Compétence {comp_code(competency)} — {competency.label}",
                 "Exemples sources, à ne jamais recopier : leur niveau et leur intention seulement.", ""]
        lines += [f"## Exemple {i + 1}\n{s}\n" for i, s in enumerate(examples)]
        write_stable(pack, "\n".join(lines))
        read_order.append(str(pack))
    return {"read_order": read_order, "fiche": str(fiche), "fiche_ready": fiche.exists(),
            "source": str(source_path), "source_stats": stats, "figure_images": images,
            "referential": str(referential_path)}


def _new_run_dir(name: str) -> Path:
    """Dossier neuf, même pour deux préparations dans la même seconde."""
    root = data_dir() / "runs"
    run = root / name
    index = 2
    while run.exists():
        run = root / f"{name}-{index}"
        index += 1
    (run / "slots").mkdir(parents=True)
    return run


def complement_grid(code: str, counts: dict[int, int], *, problem_codes: list[str] | None = None) -> list[dict]:
    """Second run : seulement ce qui manque, dans les difficultés demandées. Mêmes
    formats que la grille complète, repris dans l'ordre pour rester variés."""
    full = problem_grid(problem_codes) if problem_codes else exercise_grid(code, 4)
    slots = []
    for level, count in sorted(counts.items()):
        pool = [dict(s) for s in full if s["level"] == level]
        for i in range(max(0, count)):
            slot = dict(pool[i % len(pool)])
            slot["slot"] = f"{'p' if problem_codes else 's'}{len(slots) + 1:02d}"
            slots.append(slot)
    return slots


def split_complement(total: int) -> dict[int, int]:
    """Sans précision : même proportion que la grille complète (40/40/20)."""
    hard = round(total * 0.2)
    easy = (total - hard + 1) // 2
    return {1: easy, 2: total - hard - easy, 3: hard}


def existing_signatures(db, comp_ids: list[str], problems: bool) -> list[str]:
    """Mémoire légère du second run : une ligne par exercice existant, sans son
    énoncé complet. Le modèle varie sans relire toute la banque."""
    from app.models import GeneratedExercise
    query = db.query(GeneratedExercise).filter(GeneratedExercise.competency_id.in_(comp_ids),
                                               GeneratedExercise.status == "active")
    query = query.filter(GeneratedExercise.kind == "probleme") if problems else query.filter(
        GeneratedExercise.kind != "probleme")
    lines = []
    for row in query.order_by(GeneratedExercise.difficulty_level, GeneratedExercise.id).all():
        grading = row.grading_json or {}
        values = ",".join(re.findall(r"\d+(?:[.,]\d+)?", row.statement or "")[:5])
        lines.append(f"- {LEVEL_NAMES.get(row.difficulty_level, row.difficulty_level)} | {row.response_type} | "
                     f"{grading.get('family_id') or 'sans famille'} | {' '.join((row.statement or '').split()[:6])}… | {values}")
    return lines


def prepare(db, programme: str, cycle: str, target: str, *, model: str, problems: bool = False,
            subject: str = SUBJECT_DEFAULT, pilot: bool = False,
            complement: dict[int, int] | None = None) -> list[Path]:
    """Prépare un run par compétence (un chapitre en donne plusieurs), ou un run
    de problèmes pour le chapitre."""
    resolved = resolve_target(db, programme, cycle, target, subject)
    chapter_comps = resolved["chapter"]
    competency = resolved["competency"]
    if problems and competency is not None:
        raise SystemExit("--problemes s'applique à un chapitre, pas à une compétence.")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    targets = [None] if problems else ([competency] if competency else chapter_comps)
    runs = []
    for comp in targets:
        suffix = comp_code(comp) if comp else f"{chapter_comps[0].chapter_code}-problemes"
        run = _new_run_dir(f"{programme}-{cycle}-{suffix}-{stamp}")
        context = build_context(run, programme=programme, cycle=cycle, subject=subject,
                                chapter_comps=chapter_comps, competency=comp, problems=problems)
        if complement:
            codes = [comp_code(c) for c in chapter_comps]
            grid = complement_grid(comp_code(comp) if comp else "", complement,
                                   problem_codes=codes if problems else None)
            signatures = existing_signatures(
                db, [c.id for c in chapter_comps] if problems else [comp.id], problems)
            memory = run / "existants.md"
            memory.write_text("# Exercices déjà en banque : ne reprends ni leur structure ni leurs valeurs\n"
                              + "\n".join(signatures) + "\n", encoding="utf-8")
            context["read_order"].append(str(memory))
        elif problems:
            grid = problem_grid([comp_code(c) for c in chapter_comps])
        else:
            share = figure_share(cycle, comp_code(comp))
            grid = exercise_grid(comp_code(comp), round(20 * share))
        dump_json(run / "grid.json", grid)
        dump_json(run / "payload.json", {
            "run_id": run.name, "programme": programme, "cycle": cycle, "subject": subject,
            "model": model, "version": VERSION, "prompt_version": PROMPT_VERSION,
            "mode": "problems" if problems else "exercises", "pilot": pilot,
            "complement": bool(complement),
            "chapter": {"code": chapter_comps[0].chapter_code, "name": chapter_comps[0].chapter_name},
            "competency": comp_code(comp) if comp else None,
            "competencies": [{"code": comp_code(c), "label": c.label, "id": c.id}
                             for c in chapter_comps],
            "context": context,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        runs.append(run)
    return runs


def resolve_run(run: str | None) -> Path:
    root = data_dir() / "runs"
    if run:
        path = Path(run)
        if path.is_dir():
            return path.resolve()
        path = root / run
        if path.is_dir():
            return path
        raise SystemExit(f"Run introuvable : {run}")
    runs = sorted((d for d in root.iterdir() if (d / "payload.json").exists()),
                  key=lambda d: d.stat().st_mtime) if root.exists() else []
    if not runs:
        raise SystemExit("Aucun run : commence par `run.py prepare`.")
    return runs[-1]


def remaining_slots(run: Path) -> list[dict]:
    """Emplacements sans fichier : point de reprise après une coupure."""
    return [s for s in load_json(run / "grid.json") if not (run / "slots" / f"{s['slot']}.json").exists()]


def unfinished_runs() -> list[Path]:
    root = data_dir() / "runs"
    if not root.exists():
        return []
    return [d for d in sorted(root.iterdir())
            if (d / "grid.json").exists() and remaining_slots(d)]


# ================================================================== NORMALISATION
def _ids(items: list[dict]) -> list[str]:
    return [str(x.get("id") or "") for x in items]


def _message(node) -> dict | None:
    text = (node or {}).get("feedback") if isinstance(node, dict) else None
    return {"text": str(text), "error": node.get("error")} if text else None


def _option_feedback(items: list[dict]) -> dict:
    """Message de chaque option fausse. Une option juste passe par `correct`."""
    return {str(x["id"]): {"text": str(x.get("feedback") or ""), "error": x.get("error")}
            for x in items if not x.get("correct") and x.get("id")}


GUIDE_POSITIONS = ("apres_enonce", "avant_question", "apres_question")
GUIDE_WORDS = (4, 30)
GUIDE_MAX_LEVEL = 2          # facile et moyen ; un difficile n'a pas de guide


def _guides(node: dict) -> list[dict]:
    return [g for g in node.get("guides") or [] if isinstance(g, dict)]


_FINAL_FIELD_RE = re.compile(r"^(.*\S)\s+(\{\{blank\}\}[^{}\n]*)$", re.DOTALL)


def place_guides(statement: str, guides: list[dict]) -> str:
    """Énoncé avec ses guides à leur place, marqués `{{aide}}` :
    - `apres_enonce` et `avant_question` : devant la dernière ligne, donc entre le contexte
      et la question ;
    - `apres_question` : après la question et avant la zone de réponse, y compris quand
      cette réponse est une case écrite à la fin de la question."""
    from app.services import statement as st
    before = [f"{st.GUIDE_TOKEN} {g['text']}" for g in guides if g.get("position") != "apres_question"]
    after = [f"{st.GUIDE_TOKEN} {g['text']}" for g in guides if g.get("position") == "apres_question"]
    if not guides:
        return statement
    lines = statement.split("\n")
    field = None
    if after and (match := _FINAL_FIELD_RE.match(lines[-1])):
        lines[-1], field = match.group(1), match.group(2)
    head, last = lines[:-1], lines[-1]
    return "\n".join([*head, *before, last, *after, *([field] if field else [])])


def _guide_problems(node: dict, *, composite_root: bool = False, part: bool = False) -> list[str]:
    from app.services import forge_contract as fc
    problems = []
    logical = [ln for ln in str(node.get("statement") or "").split("\n") if ln.strip()]
    for guide in node.get("guides") or []:
        if not isinstance(guide, dict) or not str(guide.get("text") or "").strip():
            problems.append("guide sans texte")
            continue
        text, position = str(guide["text"]), guide.get("position")
        if position not in GUIDE_POSITIONS:
            problems.append(f"guide : position {position!r} ∉ {GUIDE_POSITIONS}")
        if composite_root and position != "apres_enonce":
            problems.append("guide du contexte commun : position apres_enonce")
        if part and position == "apres_enonce":
            problems.append("guide de sous-question : avant_question ou apres_question")
        if not composite_root and not part and position in ("apres_enonce", "avant_question") \
                and len(logical) < 2:
            problems.append("guide avant la question : sépare le contexte et la question par un "
                            "saut de ligne, ou place-le en apres_question")
        words = len(text.split())
        if not GUIDE_WORDS[0] <= words <= GUIDE_WORDS[1]:
            problems.append(f"guide de {words} mots : entre {GUIDE_WORDS[0]} et {GUIDE_WORDS[1]}")
        if fc._POSITION_RE.search(text):
            problems.append(f"guide citant une position : « {text[:50]} »")
    return problems


def _answer_texts(leaf: dict) -> list[str]:
    """Ce que le guide ne doit pas écrire : la réponse ou la bonne proposition."""
    out: list = []
    rtype = leaf.get("response_type")
    if rtype in ("qcm_single", "qcm_multiple"):
        out += [c.get("text") for c in leaf.get("choices") or [] if isinstance(c, dict) and c.get("correct")]
    elif rtype == "short_text":
        out.append(leaf.get("answer"))
    elif rtype == "multi_blank":
        out += leaf.get("blanks") or []
    elif rtype == "table_fill":
        out += [c for row in leaf.get("cells") or [] for c in row if isinstance(c, dict) and not c.get("given")]
    return out


def _guide_leaks(leaf: dict, guides: list[dict]) -> list[str]:
    """La réponse écrite telle quelle dans le guide. Les petits nombres et les mots
    courants sont ignorés : un guide peut citer 10, 100 ou « dixièmes » sans rien donner.
    Une valeur déjà imprimée dans l'énoncé n'est pas une fuite."""
    from app.services import handwriting
    statement = fold(handwriting.plain(leaf.get("statement") or "")).replace(" ", "")
    leaks = []
    for guide in guides:
        text = fold(handwriting.plain(guide.get("text") or "")).replace(" ", "")
        for value in _answer_texts(leaf):
            written = fold(handwriting.label(value)).replace(" ", "").replace("\u202f", "")
            significant = len(re.sub(r"\D", "", written)) >= 2 and written not in ("10", "100", "1000")
            words = len(written) >= 6 and not any(ch.isdigit() for ch in written)
            if (significant or words) and written in text and written not in statement:
                leaks.append(f"guide donnant la réponse « {handwriting.label(value)} »")
    return leaks


def leaf_to_raw(leaf: dict, level: int) -> tuple[dict, dict]:
    """Question du schéma forge → schéma du validateur partagé + contrat forge."""
    from app.services import forge_contract as fc
    rtype = leaf.get("response_type")
    route, route_problem = fc.resolve_route(rtype, leaf.get("route")) if rtype in LEAF_TYPES \
        else (fc.ROUTE_CV, None)
    extra = {"route": route, "route_problem": route_problem, "option_ids": [],
             "right_ids": [], "answer_form": leaf.get("answer_form") or "any"}
    raw = {"response_type": rtype, "statement": str(leaf.get("statement") or ""),
           "kind": "application", "correction": "", "bareme_points": 1}
    options: dict = {}
    if rtype in ("qcm_single", "qcm_multiple"):
        items = [c for c in leaf.get("choices") or [] if isinstance(c, dict)]
        raw["choices"] = [str(c.get("text") or "") for c in items]
        raw["answer"] = {"type": "choice",
                         "correct": [i for i, c in enumerate(items) if c.get("correct")]}
        extra["option_ids"] = _ids(items)
        options = _option_feedback(items)
    elif rtype == "checkbox_grid":
        rows = [r for r in leaf.get("rows") or [] if isinstance(r, dict)]
        raw["answer"] = {"type": "grid", "cols": leaf.get("cols") or [],
                         "rows": [{"label": str(r.get("label") or ""), "correct": r.get("correct")}
                                  for r in rows]}
        extra["option_ids"] = _ids(rows)
        options = {str(r["id"]): {"text": str(r.get("feedback") or ""), "error": r.get("error")}
                   for r in rows if r.get("id")}
    elif rtype == "matching":
        right = [r for r in leaf.get("right") or [] if isinstance(r, dict)]
        positions = {str(r.get("id")): i for i, r in enumerate(right)}
        pairs = [[p[0], positions.get(str(p[1]))] for p in leaf.get("pairs") or []
                 if isinstance(p, (list, tuple)) and len(p) == 2]
        traps = len(right) - len(pairs)
        statement = raw["statement"]
        if traps > 0:
            statement = statement.rstrip() + "\n" + (
                "Attention : 1 proposition ne sert pas." if traps == 1
                else f"Attention : {traps} propositions ne servent pas.")
        raw["statement"] = statement
        raw["answer"] = {"type": "matching", "left": [str(x) for x in leaf.get("left") or []],
                         "right": [str(r.get("text") or "") for r in right], "pairs": pairs}
        extra["right_ids"] = _ids(right)
        extra["option_ids"] = _ids(right)
        extra["traps"] = traps
        options = {str(r["id"]): {"text": str(r.get("feedback") or ""), "error": r.get("error")}
                   for r in right if r.get("id")}
    elif rtype == "short_text":
        raw["answer"] = leaf.get("answer") or {}
    elif rtype == "multi_blank":
        raw["answer"] = {"type": "blanks", "values": leaf.get("blanks") or []}
    elif rtype == "table_fill":
        raw["answer"] = {"type": "table", "rows": leaf.get("rows_count"), "cols": leaf.get("cols_count"),
                         "cells": leaf.get("cells") or [], "col_labels": leaf.get("col_labels"),
                         "row_labels": leaf.get("row_labels"),
                         "unordered": bool(leaf.get("unordered"))}
    elif rtype == "multiline_text":
        raw["answer"] = {"type": "rubric", "steps": leaf.get("steps") or []}
        expected_errors = [e for e in leaf.get("expected_errors") or [] if isinstance(e, dict)]
        extra["option_ids"] = _ids(expected_errors)
        options = {str(e["id"]): {"text": str(e.get("feedback") or ""), "error": e.get("error")}
                   for e in expected_errors if e.get("id")}
    wrong = [{"value": w.get("value"), "cell": w.get("cell"), "text": str(w.get("feedback") or ""),
              "error": w.get("error")}
             for w in leaf.get("wrong_answers") or [] if isinstance(w, dict)]
    lines = fc.FEEDBACK_LINES.get(route, 2)
    feedback = {"lines": lines, "options": options, "wrong_answers": wrong,
                "correct": {"text": str(leaf.get("correct_feedback") or "")},
                "blank": {"text": str(leaf.get("blank_feedback") or "")},
                "fallback": {"text": str(leaf.get("fallback_feedback") or "")}}
    if level == 1 and not feedback["correct"]["text"]:
        feedback.pop("correct")
    extra["feedback"] = feedback
    extra["message_ids"] = ([str(c.get("id")) for c in leaf.get("choices") or []
                             if isinstance(c, dict) and not c.get("correct")]
                            if rtype in ("qcm_single", "qcm_multiple") else list(extra["option_ids"]))
    extra["llm_brief"] = leaf.get("llm_brief")
    raw["statement"] = place_guides(raw["statement"], _guides(leaf))
    return raw, extra


def figure_to_json(figure) -> dict | None:
    if not figure:
        return None
    return {"type": figure.get("kind"), "params": figure.get("spec")}


def to_raw(exercise: dict, level: int) -> tuple[dict, list[dict]]:
    if exercise.get("response_type") == COMPOSITE:
        parts_raw, extras = [], []
        for part in exercise.get("parts") or []:
            raw, extra = leaf_to_raw(part, level)
            raw["competency_code"] = part.get("competency")
            if part.get("figure"):
                raw["figure"] = figure_to_json(part["figure"])
            parts_raw.append(raw)
            extras.append(extra)
        return {"response_type": COMPOSITE, "kind": "application",
                "statement": place_guides(str(exercise.get("statement") or ""), _guides(exercise)),
                "correction": "", "answer": {"type": "composite", "parts": parts_raw}}, extras
    raw, extra = leaf_to_raw(exercise, level)
    return raw, [extra]


# ================================================================== CONTRÔLES
@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    slots: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings, "slots": self.slots}


def _unprintable(text: str) -> str:
    from app.services import mathrender
    bad = []
    for content, is_math in mathrender.split_math_spans(text or ""):
        if is_math:
            continue
        for char in content:
            if char == " ":
                continue
            try:
                char.encode(_PRINT_ENCODING)
            except UnicodeEncodeError:
                bad.append(char)
    return "".join(dict.fromkeys(bad))


def _leaves(exercise: dict) -> list[dict]:
    return (exercise.get("parts") or []) if exercise.get("response_type") == COMPOSITE else [exercise]


def _printed_texts(leaf: dict) -> list[str]:
    out = [str(leaf.get("statement") or "")]
    out += [str(g.get("text") or "") for g in _guides(leaf)]
    for key in ("choices", "right"):
        out += [str(x.get("text") or "") for x in leaf.get(key) or [] if isinstance(x, dict)]
    out += [str(x) for x in leaf.get("left") or []] + [str(x) for x in leaf.get("cols") or []]
    out += [str(r.get("label") or "") for r in leaf.get("rows") or [] if isinstance(r, dict)]
    out += [str(x) for x in (leaf.get("col_labels") or []) + (leaf.get("row_labels") or [])]
    return out


def _value_of(text: str):
    from app.services import indigo_check
    try:
        return indigo_check._value_of(text)
    except Exception:  # noqa: BLE001 - une proposition en toutes lettres n'a pas de valeur
        return None


def _same_value(left, right) -> bool:
    import sympy
    try:
        return sympy.simplify(left - right) == 0
    except Exception:  # noqa: BLE001 - valeurs de natures différentes
        return False


def math_problems(leaf: dict) -> list[str]:
    """Justesse : la réponse et chaque distracteur sont recalculés. Un distracteur
    déclare le calcul qui l'explique, donc l'erreur est vérifiée et pas supposée."""
    from app.services import indigo_check
    problems = []
    rtype = leaf.get("response_type")
    computed = None
    if leaf.get("answer_expr"):
        try:
            computed = indigo_check._eval(leaf["answer_expr"])
        except ValueError as exc:
            problems.append(f"answer_expr illisible : {exc}")
    elif not leaf.get("no_check_reason") and rtype in ("qcm_single", "qcm_multiple", "short_text"):
        problems.append("answer_expr obligatoire si le résultat se calcule ; sinon no_check_reason")
    if rtype in ("qcm_single", "qcm_multiple"):
        correct_values = []
        for choice in leaf.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            value = _value_of(choice.get("text"))
            if choice.get("correct"):
                if value is not None:
                    correct_values.append(value)
                if computed is not None and value is not None and rtype == "qcm_single" \
                        and not _same_value(value, computed):
                    problems.append(f"la réponse marquée juste « {choice.get('text')} » ne vaut pas "
                                    f"{leaf.get('answer_expr')}")
                continue
            if computed is not None and value is not None and _same_value(value, computed):
                problems.append(f"le distracteur « {choice.get('text')} » vaut aussi la bonne réponse")
            calc = choice.get("calc")
            if calc and value is not None:
                try:
                    if not _same_value(indigo_check._eval(calc), value):
                        problems.append(f"le distracteur « {choice.get('text')} » ne vaut pas son "
                                        f"calcul déclaré {calc}")
                except ValueError as exc:
                    problems.append(f"calc illisible « {calc} » : {exc}")
    if rtype == "short_text" and computed is not None:
        answer = leaf.get("answer") or {}
        value = answer.get("value")
        if isinstance(value, (list, tuple)) and len(value) == 2:
            value = f"{value[0]}/{value[1]}"
        expected = None
        try:
            expected = indigo_check._eval(str(value).replace(",", "."))
        except ValueError:
            pass
        if expected is not None and not _same_value(expected, computed):
            problems.append(f"answer.value {value!r} ne vaut pas {leaf.get('answer_expr')}")
    if rtype in ("checkbox_grid",) and leaf.get("check"):
        problems += indigo_check.check_math({"response_type": rtype,
                                             "cols": leaf.get("cols") or [],
                                             "rows": leaf.get("rows") or [],
                                             "check": leaf["check"]})
    return problems


def _matching_problems(leaf: dict) -> list[str]:
    right = [r for r in leaf.get("right") or [] if isinstance(r, dict)]
    left = leaf.get("left") or []
    pairs = leaf.get("pairs") or []
    problems = []
    if not 2 <= len(left) <= 6 or not 2 <= len(right) <= 6:
        problems.append(f"relier : {len(left)} × {len(right)} éléments, attendu 2 à 6 de chaque côté")
    if len(pairs) != len(left):
        problems.append("relier : chaque élément de gauche a exactement un lien (pas d'intrus à gauche)")
    ids = {str(r.get("id")) for r in right}
    linked = [str(p[1]) for p in pairs if isinstance(p, (list, tuple)) and len(p) == 2]
    if len(set(linked)) != len(linked):
        problems.append("relier : un point de droite ne reçoit qu'un seul lien")
    if any(x not in ids for x in linked):
        problems.append("relier : pairs désigne un identifiant inconnu à droite")
    if any(not r.get("feedback") for r in right):
        problems.append("relier : chaque point de droite a son message")
    return problems


def _figure_problems(figure) -> list[str]:
    from app.services import figures
    if figure is None:
        return []
    if not isinstance(figure, dict):
        return ["figure : objet attendu"]
    if figure.get("kind") not in figures.DECLARATIVE_TYPES:
        return [f"figure : kind {figure.get('kind')!r} ; geo, chart, template ou svg attendu "
                "(jamais de découpe d'une page de manuel)"]
    error = figures.figure_error(figure_to_json(figure))
    return [error] if error else []


_LABELLED_BLANK_RE = re.compile(r"[^\s{}.?!]\s*:\s*\{\{blank\}\}")


def _leaf_problems(leaf: dict, level: int, subject: str) -> tuple[list[str], dict, dict]:
    from app.services import forge_contract as fc
    from app.services import indigo_check
    problems: list[str] = []
    rtype = leaf.get("response_type")
    if rtype not in LEAF_TYPES:
        return [f"format {rtype!r} ∉ {LEAF_TYPES}"], {}, {}
    raw, extra = leaf_to_raw(leaf, level)
    if extra.get("route_problem"):
        problems.append(extra["route_problem"])
    if extra["route"] != fc.default_route(rtype) and not leaf.get("route_reason"):
        problems.append("route modifiée sans route_reason")
    problems += math_problems(leaf)
    bad = "".join(_unprintable(t) for t in _printed_texts(leaf))
    if bad:
        problems.append(f"caractère non imprimable hors formule « {bad} » : écris-le dans $...$")
    if rtype in ("qcm_single", "qcm_multiple"):
        items = leaf.get("choices") or []
        if any(not (c or {}).get("id") for c in items):
            problems.append("chaque choix a un id stable")
        if any(not c.get("correct") and not c.get("error") for c in items if isinstance(c, dict)):
            problems.append("chaque distracteur déclare son code d'erreur")
        problems += indigo_check.lint_statement(raw["statement"], has_figure=True, min_len=0, part=True)
    if rtype == "matching":
        problems += _matching_problems(leaf)
    problems += _guide_leaks(leaf, _guides(leaf))
    if rtype == "multi_blank" and len(_LABELLED_BLANK_RE.findall(raw["statement"])) >= 2:
        problems.append("cases étiquetées (« Abscisse de A : … ») : utilise table_fill, plus lisible")
    if rtype in ("short_text", "multi_blank", "table_fill"):
        problem = fc.form_problem(raw["statement"], extra["answer_form"])
        if problem:
            problems.append(problem)
    if extra["route"] in (fc.ROUTE_LLM, fc.ROUTE_OCR_LLM) or rtype == "multiline_text":
        if rtype == "multiline_text" or leaf.get("llm_brief"):
            problems += fc.brief_problems(leaf.get("llm_brief"))
    problems += fc.feedback_problems(extra["feedback"], option_ids=extra["message_ids"], level=level,
                                     subject=subject)
    return problems, raw, extra


def _copy_shingles(text: str) -> list[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9]+", fold(text))
    return [tuple(words[i:i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)]


def copy_corpus(payload: dict) -> tuple[set, list[set]]:
    """Texte des sources du chapitre : suites de mots et listes de nombres."""
    path = Path(payload["context"]["source"])
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    numbers = []
    for block in re.split(r"\n\s*\n|\n(?=\d+\s)", text):
        values = set(re.findall(r"\d+(?:[,.]\d+)?", block))
        if len(values) >= 3:
            numbers.append(values)
    return set(_copy_shingles(text)), numbers


def copy_problem(statement: str, corpus: tuple[set, list[set]]) -> str | None:
    words, numbers = corpus
    shingles = _copy_shingles(statement)
    if len(shingles) + SHINGLE - 1 >= COPY_MIN_WORDS and shingles:
        ratio = sum(s in words for s in shingles) / len(shingles)
        if ratio > COPY_RATIO_MAX:
            return f"énoncé trop proche du manuel ({ratio:.0%} de suites de {SHINGLE} mots communes)"
    values = set(re.findall(r"\d+(?:[,.]\d+)?", statement))
    significant = {v for v in values if len(v) >= 2}
    if len(significant) >= 3 and any(significant <= block for block in numbers):
        return "mêmes valeurs numériques qu'un exercice du manuel : change les données"
    return None


def apply_bareme(contract: dict, level: int, seconds: int, *, problem: bool) -> dict:
    from app.services import scoring
    contract = dict(contract)
    grading = dict(contract.get("grading") or {})
    if contract["response_type"] == COMPOSITE:
        parts = grading.get("parts") or []
        leaf_values = [scoring.forge_bareme(p["response_type"], level) for p in parts]
        total = (scoring.forge_bareme("", level, problem=True) if problem
                 else min(scoring.BAREME_MAX, max(leaf_values, default=1.0)))
        shares = scoring.split_bareme(total, leaf_values)
        new_parts = []
        for part, share in zip(parts, shares):
            part_grading = {**(part.get("grading") or {}), "bareme_points": share}
            new_parts.append({**part, "grading": part_grading})
        grading["parts"] = new_parts
        grading["bareme_points"] = total
        expected = dict(contract.get("expected") or {})
        expected["parts"] = new_parts
        contract["expected"] = expected
    else:
        grading["bareme_points"] = scoring.forge_bareme(contract["response_type"], level, seconds)
    contract["grading"] = grading
    return contract


def build_contract(db, exercise: dict, slot: dict, comp, subject: str, seen: set) -> tuple[dict | None, list[str]]:
    """Exercice forge → contrat validé par le validateur partagé, avec son
    barème, ses micro-corrections et sa route. (contrat, problèmes)."""
    from app.services import exercise_gen, forge_contract as fc
    level = slot["level"]
    problems: list[str] = []
    composite = exercise.get("response_type") == COMPOSITE
    leaves = _leaves(exercise)
    if composite:
        limit = PART_MAX_PROBLEM if slot["kind"] == "problem" else PART_MAX_EXERCISE
        if not 2 <= len(leaves) <= limit:
            problems.append(f"{len(leaves)} partie(s), attendu 2 à {limit}")
    leaf_extras = []
    for i, leaf in enumerate(leaves):
        where = f"partie {chr(97 + i)} : " if composite else ""
        found, _raw, extra = _leaf_problems(leaf, level, subject)
        problems += [where + p for p in found]
        leaf_extras.append(extra)
        if composite:
            problems += [where + p for p in _figure_problems(leaf.get("figure"))]
            if slot["kind"] == "problem" and leaf.get("competency") not in slot.get("competencies", []):
                problems.append(f"{where}competency {leaf.get('competency')!r} hors de ce problème "
                                f"({', '.join(slot.get('competencies', []))})")
    problems += _figure_problems(exercise.get("figure"))
    if problems:
        return None, problems
    raw, extras = to_raw(exercise, level)
    valid = exercise_gen._validate_exercise(raw, comp, db, seen, allow_geometry_text=True,
                                            require_correction=False)
    if valid is None:
        return None, ["refusé par le validateur Kiro & Nina : "
                      + exercise_gen.diagnose_rejection(raw, comp, require_correction=False)]
    layout = exercise.get("figure_layout")
    if composite:
        parts = (valid.get("expected") or {}).get("parts") or []
        new_parts = []
        for part, extra, leaf in zip(parts, extras, leaves):
            grading = fc.attach(part.get("grading"), feedback=extra["feedback"], route=extra["route"],
                                llm_brief=extra.get("llm_brief"), answer_form=extra["answer_form"],
                                option_ids=extra["option_ids"], right_ids=extra["right_ids"],
                                competency_code=leaf.get("competency"))
            new_parts.append({**part, "grading": grading})
        valid["expected"] = {**valid["expected"], "parts": new_parts}
        valid["grading"] = {**valid["grading"], "parts": new_parts}
        if layout:
            valid["grading"]["figure_layout"] = layout
        if sum(len(_guides(x)) for x in [exercise] + leaves):
            valid["grading"]["guides_placed"] = True
    else:
        extra = extras[0]
        valid["grading"] = fc.attach(valid.get("grading"), feedback=extra["feedback"], route=extra["route"],
                                     llm_brief=extra.get("llm_brief"), answer_form=extra["answer_form"],
                                     option_ids=extra["option_ids"], right_ids=extra["right_ids"],
                                     figure_layout=layout)
        if _guides(exercise):
            valid["grading"]["guides_placed"] = True
    seconds = int(exercise.get("estimated_seconds") or 0)
    return apply_bareme(valid, level, seconds, problem=slot["kind"] == "problem"), []


def _slot_problems(exercise: dict, slot: dict, payload: dict) -> list[str]:
    problems = []
    if exercise.get("slot") != slot["slot"]:
        problems.append(f"slot {exercise.get('slot')!r} différent de {slot['slot']}")
    wanted = slot["response_type"]
    swap = exercise.get("slot_swap") or {}
    if exercise.get("response_type") != wanted and not swap.get("reason"):
        problems.append(f"format {exercise.get('response_type')!r} au lieu de {wanted} sans "
                        "slot_swap.reason")
    written = [x.get("response_type") in WRITTEN_TYPES for x in _leaves(exercise)]
    if slot["level"] == 1 and slot["kind"] == "exercise" and any(written):
        problems.append("un facile n'a pas de réponse à rédiger")
    bounds = (PROBLEM_TIME_BOUNDS if slot["kind"] == "problem" else TIME_BOUNDS)[slot["level"]]
    seconds = exercise.get("estimated_seconds")
    if not isinstance(seconds, int) or not bounds[0] <= seconds <= bounds[1]:
        problems.append(f"estimated_seconds {seconds!r} : attendu un entier entre {bounds[0]} et "
                        f"{bounds[1]} pour un {slot['difficulty']}")
    if exercise.get("figure_layout") not in (None, "above", "below", "left", "right",
                                             "grid_left", "grid_right"):
        problems.append("figure_layout : above, below, left, right, grid_left ou grid_right")
    if not exercise.get("micro_skill"):
        problems.append("micro_skill obligatoire")
    if not exercise.get("family_id"):
        problems.append("family_id obligatoire")
    if slot["kind"] == "problem" and not exercise.get("title"):
        problems.append("un problème a un titre")
    composite = exercise.get("response_type") == COMPOSITE
    problems += _guide_problems(exercise, composite_root=composite)
    if composite:
        for part in _leaves(exercise):
            problems += _guide_problems(part, part=True)
    count = sum(len(_guides(x)) for x in ([exercise] + _leaves(exercise) if composite else [exercise]))
    if slot["level"] <= GUIDE_MAX_LEVEL and not count:
        problems.append("guide obligatoire sur un facile ou un moyen : champ guides")
    if slot["level"] > GUIDE_MAX_LEVEL and count:
        problems.append("pas de guide sur un difficile")
    if slot.get("figure") and not exercise.get("figure") and not swap.get("reason") \
            and not any(p.get("figure") for p in _leaves(exercise)):
        problems.append("cet emplacement attend une figure (ou slot_swap.reason)")
    return problems


def load_slots(run: Path) -> dict[str, dict]:
    out = {}
    for path in sorted((run / "slots").glob("*.json")):
        try:
            out[path.stem] = load_json(path)
        except json.JSONDecodeError as exc:
            out[path.stem] = {"_json_error": str(exc)}
    return out


def _proposed_errors(exercises: dict) -> list[dict]:
    seen, out = set(), []
    for exercise in exercises.values():
        for item in exercise.get("proposed_errors") or []:
            code = (item or {}).get("code")
            if code and code not in seen:
                seen.add(code)
                out.append(item)
    return out


def _lot_problems(exercises: dict, grid: list[dict], contracts: dict) -> tuple[list[str], list[str]]:
    """Quotas et équité du lot : les erreurs bloquent, les réserves s'affichent."""
    from app.services import scoring
    errors, warnings = [], []
    by_slot = {s["slot"]: s for s in grid}
    done = {k: v for k, v in contracts.items() if k in by_slot}
    composite = sum(1 for k, c in done.items()
                    if c["response_type"] == COMPOSITE and by_slot[k]["kind"] == "exercise")
    if composite > EXERCISE_COMPOSITE_MAX:
        errors.append(f"{composite} composites, maximum {EXERCISE_COMPOSITE_MAX} sur 20")
    figures_count = sum(1 for k in done if exercises[k].get("figure")
                        or any(p.get("figure") for p in _leaves(exercises[k])))
    needed = sum(1 for s in grid if s.get("figure"))
    if len(done) == len(grid) and figures_count < min(needed, FIGURE_MIN):
        errors.append(f"{figures_count} figure(s), minimum {min(needed, FIGURE_MIN)}")
    families: dict[str, list[str]] = {}
    for key in done:
        families.setdefault(fold(str(exercises[key].get("family_id"))), []).append(key)
    for family, keys in families.items():
        if len(keys) > 2:
            warnings.append(f"{len(keys)} exercices de même structure « {family} » : {', '.join(keys)}")
    by_format: dict[tuple[str, int], list[float]] = {}
    per_level: dict[int, list[float]] = {}
    for key, contract in done.items():
        level = by_slot[key]["level"]
        points = float(contract["grading"].get("bareme_points") or 0)
        by_format.setdefault((contract["response_type"], level), []).append(points)
        per_level.setdefault(level, []).append(points)
        seconds = int(exercises[key].get("estimated_seconds") or 0)
        rate = points / (seconds / 60) if seconds else 0
        low, high = PROBLEM_POINTS_PER_MINUTE if by_slot[key]["kind"] == "problem" else POINTS_PER_MINUTE
        if seconds and not low <= rate <= high:
            warnings.append(f"{key} : {points:g} point(s) pour {seconds} s, soit {rate:.1f} point/min "
                            f"(repère {low}–{high})")
    formats = {f for f, _ in by_format}
    for name in formats:
        easy, hard = by_format.get((name, 1)), by_format.get((name, 3))
        if easy and hard and max(easy) > min(hard):
            errors.append(f"barème inéquitable sur {name} : un facile vaut plus qu'un difficile")
    if per_level.get(1) and per_level.get(3):
        ratio = statistics.median(per_level[3]) / statistics.median(per_level[1])
        if not 2.0 <= ratio <= 4.0 and by_slot and next(iter(by_slot.values()))["kind"] == "exercise":
            warnings.append(f"médiane difficile / facile = {ratio:.1f} (repère : environ 3)")
    longest = total = 0
    for key in done:
        leaf = exercises[key]
        if leaf.get("response_type") not in ("qcm_single", "qcm_multiple"):
            continue
        items = [c for c in leaf.get("choices") or [] if isinstance(c, dict)]
        if len(items) >= 3:
            total += 1
            best = max(items, key=lambda c: len(str(c.get("text") or "")))
            longest += bool(best.get("correct"))
    if total >= 5 and longest / total > 0.6:
        warnings.append(f"bonne réponse la plus longue dans {longest}/{total} QCM : un élève peut le repérer")
    kinds = {s["kind"] for s in grid}
    if "problem" in kinds and len(done) == len(grid):
        covered = {p.get("competency") for k in done for p in _leaves(exercises[k])}
        wanted = {c for s in grid for c in s.get("competencies", [])}
        missing = sorted(wanted - covered)
        if missing:
            errors.append(f"compétence(s) du chapitre non couvertes : {', '.join(missing)}")
    return errors, warnings


def leak_conflicts(exercises: dict, contracts: dict) -> list[tuple[str, str, str]]:
    """Une carte affiche-t-elle ce qu'une autre demande ? Comparaison des
    `leak_keys` déclarées avec les `gives`, et des réponses avec les énoncés."""
    from app.services import mathrender
    conflicts = []
    texts = {k: fold(mathrender.strip_math(" ".join(_printed_texts(exercises[k]))))
             for k in contracts}
    for key in contracts:
        answers = set(map(fold, exercises[key].get("leak_keys") or []))
        for other in contracts:
            if other == key:
                continue
            common = answers & set(map(fold, exercises[other].get("gives") or []))
            if common:
                conflicts.append((key, other, sorted(common)[0]))
                continue
            value = (exercises[key].get("answer") or {}).get("value")
            flat = str(value) if value is not None else ""
            if len(flat) >= 3 and re.search(rf"(?<![\w,.]){re.escape(fold(flat))}(?![\w,.])", texts[other]):
                conflicts.append((key, other, flat))
    return conflicts


def check(db, run: Path) -> tuple[Report, dict]:
    """Tous les contrôles gratuits du run. Écrit report.json et contracts.json."""
    from app.models import Competency
    payload = load_json(run / "payload.json")
    grid = load_json(run / "grid.json")
    exercises = load_slots(run)
    by_code = {c["code"]: c for c in payload["competencies"]}
    corpus = copy_corpus(payload)
    report = Report()
    contracts: dict = {}
    seen: set = set()
    for slot in grid:
        key = slot["slot"]
        if key not in exercises:
            continue
        exercise = exercises[key]
        if "_json_error" in exercise:
            found = [f"JSON invalide : {exercise['_json_error']}"]
        else:
            found = _slot_problems(exercise, slot, payload)
            code = slot.get("competency") or (slot.get("competencies") or [None])[0]
            comp = db.get(Competency, by_code[code]["id"]) if code in by_code else None
            if comp is None:
                found.append(f"compétence {code!r} absente")
            else:
                contract, more = build_contract(db, exercise, slot, comp, payload["subject"], seen)
                found += more
                if contract is not None and not found:
                    contracts[key] = contract
            for leaf in _leaves(exercise):
                problem = copy_problem(str(leaf.get("statement") or ""), corpus)
                if problem:
                    found.append(problem)
                    contracts.pop(key, None)
        report.slots[key] = {"errors": found}
        report.errors += [f"{key} : {p}" for p in found]
    missing = [s["slot"] for s in grid if s["slot"] not in exercises]
    if missing:
        report.warnings.append(f"Reste à écrire : {', '.join(missing)}")
    valid_exercises = {k: exercises[k] for k in contracts}
    lot_errors, lot_warnings = _lot_problems(valid_exercises, grid, contracts)
    report.errors += lot_errors
    report.warnings += lot_warnings
    conflicts = leak_conflicts(valid_exercises, contracts)
    for a, b, what in conflicts:
        report.warnings.append(f"fuite possible : la réponse de {a} (« {what} ») figure dans {b} ; "
                               "ces deux cartes seront incompatibles dans un même sujet")
    dump_json(run / "contracts.json", contracts)
    dump_json(run / "conflicts.json", conflicts)
    proposed = _proposed_errors({k: v for k, v in exercises.items() if "_json_error" not in v})
    dump_json(run / "proposed_errors.json", proposed)
    dump_json(run / "report.json", report.as_dict())
    return report, contracts


# ================================================================== RENDU
def _place_figure(statement: str, figure, rtype: str) -> str:
    """Un énoncé d'une seule ligne passe avant sa figure : sinon le numéro de la carte
    resterait seul au-dessus d'elle."""
    from app.services import statement as st
    return st.place_figure_marker(statement, figure is not None,
                                  at_end=rtype == COMPOSITE or "\n" not in statement.strip())


def preview_exercise(contract: dict, exercise: dict, slot: dict) -> dict:
    """Carte telle qu'elle s'imprimera : typographie appliquée d'abord, car les
    retours à la ligne en dépendent, et marqueur de figure placé comme en banque."""
    from app.services import indigo_fields
    from app.services import statement as st
    from app.services import typography
    statement, expected, grading = indigo_fields.adapt_fields(
        contract["statement"], contract["response_type"], contract.get("expected"), contract.get("grading"))
    figure = figure_to_json(exercise.get("figure"))
    statement = _place_figure(statement, figure, contract["response_type"])
    record = typography.apply_to_record({
        "statement": statement, "response_type": contract["response_type"], "expected": expected,
        "grading": grading, "title": exercise.get("title") or ""})
    return {"statement": record["statement"], "response_type": contract["response_type"],
            "expected": record["expected"], "grading": record["grading"],
            "figure": figure,
            "difficulty": slot["level"], "title": record["title"],
            "kind": "probleme" if slot["kind"] == "problem" else "application",
            "is_problem": slot["kind"] == "problem"}


def card_height_mm(ex: dict) -> float:
    from app.services import card_preview, pdfgen
    item = card_preview.shape(ex)
    tpl = pdfgen.DEFAULT_TEMPLATES["exercise"]
    fs = float(tpl.get("font_size", 9))
    return pdfgen.estimate_item_height(item, fs, fs, tpl) / pdfgen.mm


def whitespace(png: bytes) -> dict:
    """Blanc d'une carte, lu sur les pixels (NumPy, aucun modèle). Tout ce qui
    n'est pas blanc compte comme encre : les cadres de réponse et lignes de
    rédaction sont imprimés, donc une zone voulue n'est pas du vide."""
    import io

    import numpy as np
    from PIL import Image
    with Image.open(io.BytesIO(png)) as image:
        gray = np.asarray(image.convert("L"), dtype=np.uint8)
        dpi = float((image.info.get("dpi") or (180,))[0] or 180)
    mm_px = dpi / 25.4
    margin = int(2.0 * mm_px)
    inner = gray[margin:-margin or None, margin:-margin or None]
    if inner.size == 0:
        return {"empty_ratio": 0.0, "empty_run_mm": 0.0, "side_void": 0.0}
    ink = inner < 235
    rows = ink.any(axis=1)
    best = run = excess = 0
    normal = int(NORMAL_GAP_MM * mm_px)
    for has_ink in rows:
        if has_ink:
            excess += max(0, run - normal)
            run = 0
        else:
            run += 1
            best = max(best, run)
    excess += max(0, run - normal)
    width = inner.shape[1]
    # Vide latéral : rangées longues où l'encre n'occupe qu'une petite part de la
    # largeur, typiquement une figure seule au milieu de la carte.
    spans = np.where(rows, (width - ink.argmax(axis=1) - ink[:, ::-1].argmax(axis=1)) / width, 1.0)
    # Un élément centré a des marges égales : c'est voulu. Seul le vide d'un seul côté
    # compte (figure ou tableau collé à un bord).
    left, right = ink.argmax(axis=1) / width, ink[:, ::-1].argmax(axis=1) / width
    narrow = (spans < 0.65) & (np.abs(left - right) > 0.15)
    min_run = int(15 * mm_px)
    void, count, acc = 0.0, 0, 0.0
    for value, is_narrow in zip(spans, narrow):
        if is_narrow:
            count += 1
            acc += 1 - value
        else:
            if count >= min_run:
                void += acc
            count, acc = 0, 0.0
    if count >= min_run:
        void += acc
    return {"empty_ratio": round(excess / len(rows), 3),
            "empty_run_mm": round(best / mm_px, 1),
            "side_void": round(float(void / len(rows)), 3)}


def whitespace_excess(metrics: dict) -> list[str]:
    out = []
    if metrics["empty_ratio"] > EMPTY_RATIO_MAX:
        out.append(f"{metrics['empty_ratio']:.0%} de hauteur perdue en bandes vides")
    if metrics["empty_run_mm"] > EMPTY_RUN_MAX_MM:
        out.append(f"bande vide de {metrics['empty_run_mm']:g} mm")
    if metrics["side_void"] > SIDE_VOID_MAX:
        out.append(f"{metrics['side_void']:.0%} de vide à côté d'un élément étroit")
    return out


def _with_layout(ex: dict, layout: str | None, scale: float) -> dict:
    grading = {**(ex.get("grading") or {}), "figure_layout": layout, "figure_scale": scale}
    if layout is None:
        grading.pop("figure_layout")
    if scale == 1.0:
        grading.pop("figure_scale")
    return {**ex, "grading": grading}


def _figure_stays_readable(ex: dict, layout: str | None, scale: float) -> bool:
    """Une figure trop réduite rend ses étiquettes illisibles : elle garde sa largeur
    minimale, et au moins 70 % de sa taille prévue, en comptant la réduction que la
    colonne impose déjà à côté du texte."""
    from app.services import figkit, pdfgen
    natural = pdfgen._figure_image(ex.get("figure"), 93 * pdfgen.mm, 90 * pdfgen.mm)
    if not natural:
        return False
    limit = (pdfgen.COL_W - 2 * pdfgen.CARD_PAD) * pdfgen._FIG_SIDE_MAX_RATIO
    side = str(layout or "").endswith(("left", "right"))
    final_w = min(natural[1], limit) * scale if side else natural[1] * scale
    return final_w >= figkit.MIN_WIDTH_MM * pdfgen.mm and final_w / natural[1] >= FIGURE_MIN_RATIO


def best_layout(ex: dict) -> tuple[str | None, float, float]:
    """Compactage gratuit : essaie les mises en page et les réductions de figure,
    mesure chacune avec le vrai code et garde la plus basse."""
    current = (ex.get("grading") or {}).get("figure_layout")
    best = (current, 1.0)
    best_h = card_height_mm(_with_layout(ex, current, 1.0))
    if not ex.get("figure"):
        return current, 1.0, best_h
    # Une zone d'écriture garde toute la largeur : jamais de grille pour elle.
    from app.services import pdfgen
    grids = () if ex.get("response_type") in pdfgen.FIGURE_NO_GRID_RESPONSES else ("grid_right", "grid_left")
    for layout in (None, "right", "left", *grids):
        for scale in FIGURE_SCALES:
            if (layout, scale) == best or not _figure_stays_readable(ex, layout, scale):
                continue
            height = card_height_mm(_with_layout(ex, layout, scale))
            # À taille égale, passer du dessous au côté remplit le vide autour de la
            # figure : un petit gain suffit. Réduire une figure en demande un vrai.
            beside = best[0] is None and layout in ("right", "left") and scale == best[1]
            if height < best_h - (0.5 if beside else 3.0):
                best, best_h = (layout, scale), height
    return best[0], best[1], best_h


def simulate(heights_mm: list[float], tiers: list[int], *, n: int = SIMULATIONS,
             seed: str = "forge") -> dict:
    """Sujets d'une page tirés au hasard, avec le vrai code de placement : une
    carte au hasard, puis la plus grande qui entre encore, comme le remplissage
    réel. Le remplissage mesuré est absolu : aucune banque de référence n'est
    nécessaire."""
    from app.services import pdfgen
    if not heights_mm:
        return {"median_fill": 0.0, "p10_fill": 0.0, "runs": 0}
    rng = random.Random(seed)
    heights = [h * pdfgen.mm for h in heights_mm]
    capacity = pdfgen.column_capacity(0) + pdfgen.column_capacity(1)
    fills = []
    leftovers = []
    for _ in range(n):
        remaining = list(range(len(heights)))
        rng.shuffle(remaining)
        chosen: list[int] = []
        while remaining:
            pick = None
            hs = [heights[i] for i in chosen]
            ts = [tiers[i] for i in chosen]
            free = pdfgen.free_space(hs, 1, ts) if chosen else [pdfgen.column_capacity(0),
                                                                pdfgen.column_capacity(1)]
            gap = max(free)
            fitting = [i for i in remaining if heights[i] <= gap]
            if not fitting:
                break
            if not chosen:
                pick = fitting[0]
            else:
                pick = max(fitting, key=lambda i: heights[i])
            if pdfgen.pack_placement(hs + [heights[pick]], ts + [tiers[pick]])[2] > 1:
                remaining.remove(pick)
                continue
            chosen.append(pick)
            remaining.remove(pick)
        used = sum(heights[i] for i in chosen)
        fills.append(used / capacity)
        hs = [heights[i] for i in chosen]
        leftovers.append(max(pdfgen.free_space(hs, 1, [tiers[i] for i in chosen])) / pdfgen.mm)
    fills.sort()
    return {"median_fill": round(statistics.median(fills), 3),
            "p10_fill": round(fills[int(0.1 * len(fills))], 3),
            "median_leftover_mm": round(statistics.median(leftovers), 1), "runs": n}


def render(db, run: Path) -> dict:
    """Rendu réel de chaque carte valide : PNG, hauteur, blanc, compactage gratuit,
    simulation de sujets. Aucun modèle n'est appelé."""
    from app.services import card_preview, pdfgen
    report, contracts = check(db, run)
    exercises = load_slots(run)
    grid = {s["slot"]: s for s in load_json(run / "grid.json")}
    out_dir = run / "cards"
    # À part : ces images montrent les réponses, et le goldfish lit le dossier cards.
    written_dir = run / "ecrit"
    for folder in (out_dir, written_dir):
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir()
    cards: dict = {}
    layouts: dict = {}
    writing: dict = {}
    leading: list[str] = []
    for key, contract in contracts.items():
        exercise, slot = exercises[key], grid[key]
        ex = preview_exercise(contract, exercise, slot)
        chosen, scale, height = best_layout(ex)
        layouts[key] = {"figure_layout": chosen, "figure_scale": scale}
        ex = _with_layout(ex, chosen, scale)
        png = card_preview.render(ex)
        (out_dir / f"{key}.png").write_bytes(png)
        metrics = whitespace(png)
        excess = whitespace_excess(metrics)
        written_png, fields = card_preview.written_preview(ex)
        if fields:
            (written_dir / f"{key}.png").write_bytes(written_png)
        tight = [f["problem"] for f in fields if f and f["problem"]]
        if tight:
            writing[key] = tight
        if ex["response_type"] != COMPOSITE:
            fs = float(pdfgen.DEFAULT_TEMPLATES["exercise"].get("font_size", 9))
            if pdfgen.broken_leading(pdfgen._exercise_layout(card_preview.shape(ex), fs, fs)[0]):
                leading.append(key)
        cards[key] = {"height_mm": round(height, 1), "size": size_class(height),
                      "size_target": slot["size"], "whitespace": metrics, "excess": excess,
                      "figure_layout": chosen, "figure_scale": scale,
                      "points": contract["grading"].get("bareme_points"),
                      "tier": pdfgen.reading_tier(slot["level"], slot["kind"] == "problem"),
                      "fits_column": height * pdfgen.mm <= pdfgen.column_capacity(1)}
    dump_json(run / "layouts.json", layouts)
    actual: dict[str, int] = {}
    for card in cards.values():
        actual[card["size"]] = actual.get(card["size"], 0) + 1
    targets = size_targets(list(grid.values()))
    size_gaps = {name: {"cible": targets.get(name, 0), "mesure": actual.get(name, 0)}
                 for name, _ in SIZE_LIMITS_MM
                 if abs(targets.get(name, 0) - actual.get(name, 0)) > SIZE_TOLERANCE}
    simulation = simulate([c["height_mm"] for c in cards.values()],
                          [c["tier"] for c in cards.values()], seed=run.name)
    summary = {"cards": cards, "size_gaps": size_gaps, "simulation": simulation,
               "writing": writing, "leading": leading,
               "too_tall": [k for k, c in cards.items() if not c["fits_column"]],
               "excess": {k: c["excess"] for k, c in cards.items() if c["excess"]},
               "complete": len(contracts) == len(grid)}
    dump_json(run / "render.json", summary)
    return summary


def lot_hints(summary: dict, kind: str) -> list[str]:
    """Ce que le rapport demande de corriger, en clair."""
    hints = []
    for key, problems in summary.get("writing", {}).items():
        hints.append(f"ÉCRITURE TROP SERRÉE {key} : " + " ; ".join(problems))
    if summary.get("leading"):
        hints.append("INTERLIGNE CASSÉ par une case dans une phrase repliée : "
                     + ", ".join(summary["leading"]) + " (mets la phrase à trous sur sa propre ligne)")
    if summary["too_tall"]:
        hints.append(f"Carte trop haute pour une colonne : {', '.join(summary['too_tall'])}")
    for key, reasons in summary["excess"].items():
        hints.append(f"Blanc anormal {key} : {', '.join(reasons)}")
    for name, values in summary["size_gaps"].items():
        hints.append(f"Taille {name} : {values['mesure']} au lieu de {values['cible']} (réserve)")
    simulation = summary["simulation"]
    if summary["complete"] and kind == "exercise" and simulation["runs"]:
        if simulation["median_fill"] < FILL_MEDIAN_MIN or simulation["p10_fill"] < FILL_P10_MIN:
            hints.append(f"Remplissage simulé {simulation['median_fill']:.0%} (10 % des pires : "
                         f"{simulation['p10_fill']:.0%}), blanc restant médian "
                         f"{simulation.get('median_leftover_mm')} mm : ajoute de petites cartes")
    return hints


# ================================================================== PERSISTANCE
def review_sample(run: Path, flagged: set[str], keys: list[str], pilot: bool) -> list[str]:
    """Cartes à relire : toutes en pilote ; sinon les signalées, plus 3 au hasard."""
    if pilot:
        return list(keys)
    rng = random.Random(run.name)
    others = [k for k in keys if k not in flagged]
    return sorted(flagged & set(keys)) + sorted(rng.sample(others, min(3, len(others))))


def _apply_layout(grading: dict, layout: dict | None) -> dict:
    """La disposition retenue au rendu remplace celle de l'auteur : la carte imprimée
    doit être celle qui a été mesurée et contrôlée."""
    if layout is None:
        return grading
    kept = {k: v for k, v in grading.items() if k not in ("figure_layout", "figure_scale")}
    return {**kept, **{k: v for k, v in layout.items() if v not in (None, 1.0)}}


def persist(db, run: Path, *, replace: bool = False) -> dict:
    """Brouillons dans l'onglet Exercices. Rien n'est publié ici."""
    from app.models import Competency, IndigoExercise, IndigoExtraction
    from app.services import indigo, indigo_fields, typography

    report, contracts = check(db, run)
    if not report.ok:
        raise SystemExit(f"Contrôles en échec ({len(report.errors)}) : rien n'a été écrit.")
    payload = load_json(run / "payload.json")
    grid = {s["slot"]: s for s in load_json(run / "grid.json")}
    if set(contracts) != set(grid):
        raise SystemExit(f"Run incomplet : {sorted(set(grid) - set(contracts))}")
    rendered = load_json(run / "render.json") if (run / "render.json").exists() else {}
    if "writing" not in rendered:
        raise SystemExit("Lance `render` d'abord : le contrôle d'écriture n'a pas tourné.")
    if rendered["writing"] or rendered.get("leading"):
        raise SystemExit("Cartes refusées à la mise en page : " + ", ".join(
            sorted(set(rendered["writing"]) | set(rendered.get("leading") or []))))
    exercises = load_slots(run)
    layouts = load_json(run / "layouts.json") if (run / "layouts.json").exists() else {}
    goldfish = load_json(run / "goldfish.json") if (run / "goldfish.json").exists() else {}
    conflicts = load_json(run / "conflicts.json") if (run / "conflicts.json").exists() else []
    by_code = {c["code"]: c["id"] for c in payload["competencies"]}
    now = datetime.now(timezone.utc)
    existing = [r for r in db.query(IndigoExercise).filter(
        IndigoExercise.competency_id.in_(list(by_code.values()))).all()
        if (r.raw_ocr_json or {}).get("pipeline") == PIPELINE
        and (r.raw_ocr_json or {}).get("run") == run.name]
    if existing:
        if not replace:
            raise SystemExit("Ce run est déjà en brouillon (relance avec --replace).")
        if any(r.status == "validated" for r in existing):
            raise SystemExit("Ce run contient des exercices déjà validés : non remplacé.")
        for row in existing:
            db.delete(row)
        db.flush()
    extraction = IndigoExtraction(
        grade_level=payload["cycle"], status="cli_forge_done", progress=100,
        targets_json=[{"kind": PIPELINE, "chapter": payload["chapter"], "run": run.name,
                       "programme": payload["programme"], "version": VERSION}],
        log_text=f"Forge {VERSION} ({payload['model']}) — run {run.name}", created_at=now, updated_at=now)
    db.add(extraction)
    db.flush()
    flagged = {k for k, v in (goldfish.get("verdicts") or {}).items() if v.get("flag")}
    written = []
    for order, key in enumerate(sorted(contracts)):
        contract, exercise, slot = contracts[key], exercises[key], grid[key]
        code = slot.get("competency") or slot["competencies"][0]
        statement, expected, grading = indigo_fields.adapt_fields(
            contract["statement"], contract["response_type"], contract.get("expected"), contract.get("grading"))
        figure = exercise.get("figure")
        statement = _place_figure(statement, figure, contract["response_type"])
        grading = _apply_layout(grading, layouts.get(key))
        # Le brouillon s'imprime déjà comme la carte relue : sans ces insécables, un « ? »
        # peut partir seul à la ligne dans l'onglet Exercices. La publication la rejoue
        # sans rien changer (typographie idempotente).
        typed = typography.apply_to_record({"statement": statement, "response_type": contract["response_type"],
                                            "expected": expected, "grading": grading,
                                            "title": exercise.get("title") or ""})
        statement, expected, grading = typed["statement"], typed["expected"], typed["grading"]
        # Fuites entre cartes : la composition d'un sujet lit ces clés, sans LLM.
        grading = {**grading, "family_id": exercise.get("family_id"),
                   "leak_keys": sorted(set(exercise.get("leak_keys") or [])
                                       | {what for first, _other, what in conflicts if first == key}),
                   "gives": sorted(set(exercise.get("gives") or [])
                                   | {what for _first, other, what in conflicts if other == key})}
        problem = slot["kind"] == "problem"
        row = IndigoExercise(
            extraction_id=extraction.id, competency_id=by_code[code], grade_level=payload["cycle"],
            source_page=0, source_number=f"{run.name}/{key}", order_index=order,
            badge_type="probleme" if problem else "exercice", difficulty=slot["level"],
            title=str(exercise.get("title") or "")[:200], tags_json=[exercise.get("family_id")],
            statement=statement,
            response_type=contract["response_type"], expected_json=expected, grading_json=grading,
            correction_solution="", correction_guide="",
            payload_json={"response_type": contract["response_type"], "statement": statement,
                          "expected": expected, "grading": grading,
                          "kind": "probleme" if problem else "application"},
            variant_kind="original" if problem else VARIANT_BY_LEVEL[slot["level"]],
            status="draft", model=payload["model"], prompt_version=PROMPT_VERSION,
            created_at=now, updated_at=now)
        db.add(row)
        db.flush()
        if figure:
            from app.services import figures
            rel = f"indigo/drafts/{row.id}_fig.png"
            target = indigo.crop_abs_path(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(figures.render_figure(figure_to_json(figure)))
            row.has_figure = row.figure_required = True
            row.figure_path = rel
        row.raw_ocr_json = {
            "pipeline": PIPELINE, "source_version": VERSION, "model": payload["model"], "run": run.name,
            "programme": payload["programme"], "chapter_code": payload["chapter"]["code"],
            "estimated_seconds": int(exercise.get("estimated_seconds") or 0),
            "family_id": exercise.get("family_id"), "micro_skill": exercise.get("micro_skill"),
            "leak_keys": exercise.get("leak_keys") or [], "gives": exercise.get("gives") or [],
            "competencies": slot.get("competencies") or [code],
            "figure_spec": figure,
            "quality": {"goldfish": (goldfish.get("verdicts") or {}).get(key)},
            "review_notes": list((goldfish.get("verdicts") or {}).get(key, {}).get("notes") or []),
            "review_blocking": [], "adapted": True}
        written.append(f"{key} → {row.id}")
    extraction.stats_json = {"pipeline": PIPELINE, "version": VERSION, "exercises": len(written),
                             "run": run.name, "review": review_sample(
                                 run, flagged, sorted(contracts), bool(payload.get("pilot")))}
    db.commit()
    return {"extraction_id": extraction.id, "written": written,
            "review": extraction.stats_json["review"]}

