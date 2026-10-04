"""Astra — cœur de la pipeline (préparation, validation, figures, aperçu, persistance).

Astra est l'agent Codex `gpt-6-astra` lancé depuis le chat VSCode : il LIT
lui-même les images des pages du manuel et ÉCRIT `astra_output.json`. Ce module
fait tout le reste, de façon déterministe, en réutilisant le backend MathPrint :

  prepare  : chapitre → pages (chapters_<niveau>.json) → images PNG + payload.json
  validate : astra_output.json → contrat MathPrint (validateur partagé, sympy,
             barème codé) + règles Astra (CV seulement, guides) → report.json
  figures  : découpes (bbox + masques blanchis) et figures geo/chart → PNG
  preview  : PDF de relecture (pdfgen, le rendu RÉEL des copies) + PNG par page
  persist  : brouillons IndigoExercise Base + Facile (onglet Exercices)

Aucun appel LLM ici : Astra, c'est la session Codex. Voir ASTRA.md.
"""
from __future__ import annotations

import json
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

MODEL = "gpt-6-astra"
PROMPT_VERSION = "astra-v3-figure-coverage-guides"
PIPELINE = "astra"
VARIANTS = ("base", "facile")
VARIANT_LEVEL = {"facile": 1, "base": 2}
VARIANT_LABEL = {"base": "Base", "facile": "Facile", "original": "Problème"}


def is_problem(ex: dict) -> bool:
    return ex.get("badge") in ("probleme", "enigme")


def exercise_variants(ex: dict) -> tuple:
    return ("original",) if is_problem(ex) else VARIANTS


def exercise_level(ex: dict, kind: str) -> int:
    return int(ex["difficulty"]) if is_problem(ex) else VARIANT_LEVEL[kind]
# réponses corrigées par VISION PAR ORDINATEUR uniquement : cocher, relier
LEAF_TYPES = ("qcm_single", "qcm_multiple", "checkbox_grid", "matching")
COMPOSITE = "composite"
BADGES = ("exercice", "flash", "expert", "probleme", "enigme")
CALCULATORS = ("interdite", "autorisee", "necessaire")
GUIDE_MIN_WORDS, GUIDE_MAX_WORDS = 4, 45
MATCH_MIN, MATCH_MAX = 2, 6
PAGE_DPI = 220                          # ≈ résolution native du manuel (2560 px par double page)
OUTPUT_FILE = "astra_output.json"
ST_DONE = "cli_astra_done"              # statut d'extraction HORS du worker de l'app

_ANSWER_TOKEN_RE = re.compile(r"\{\{\s*(blank|blank_right|mini)\s*\}\}")


def fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s or "")
                   if not unicodedata.combining(c)).lower().strip()


def runs_dir() -> Path:
    from app.config import settings
    d = Path(settings.data_dir) / "astra" / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_run(run: str | None) -> Path:
    """Dossier d'un run : chemin, identifiant, ou (défaut) le plus récent."""
    if run:
        p = Path(run)
        if p.is_dir():
            return p.resolve()
        p = runs_dir() / run
        if p.is_dir():
            return p
        raise SystemExit(f"Run introuvable : {run}")
    runs = sorted((d for d in runs_dir().iterdir() if (d / "payload.json").exists()),
                  key=lambda d: d.stat().st_mtime)
    if not runs:
        raise SystemExit("Aucun run Astra : commence par `run.py prepare --chapter …`.")
    return runs[-1]


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


# ================================================================== PREPARE
def grades() -> list[str]:
    """Niveaux dotés d'une table de chapitres (chapters_<niveau>.json)."""
    return sorted(p.stem.split("_", 1)[1] for p in HERE.glob("chapters_*.json"))


def chapters(grade: str = "3e") -> list[dict]:
    path = HERE / f"chapters_{grade}.json"
    if not path.exists():
        raise SystemExit(f"Pas de table de chapitres pour {grade} ({path.name}).")
    return load_json(path)["chapters"]


# « 6e B3 », « 6eme Angles », « 3e: Thalès » : niveau en tête de la demande
_GRADE_PREFIX_RE = re.compile(r"^([3-6])\s*(?:e|eme)\b[\s:/-]*")


def find_chapter(query: str, grade: str | None = None) -> dict:
    """Chapitre par nom (accents/casse ignorés, correspondance partielle), par
    code (« B3 ») ou par numéro du livre (« 8 ») ; renvoie le chapitre avec son
    niveau sous la clé "grade".

    Sans niveau (ni `grade`, ni préfixe « 6e … » dans la demande), on cherche
    dans TOUS les manuels : les codes A1, B3… existent en 3e comme en 6e, une
    demande qui désigne deux chapitres est refusée plutôt que devinée."""
    q = fold(query)
    m = _GRADE_PREFIX_RE.match(q)
    if m:
        asked = f"{m.group(1)}e"
        if grade and grade != asked:
            raise SystemExit(f"Niveaux contradictoires : {query!r} avec --grade {grade}.")
        grade, q = asked, q[m.end():].strip()
    pool = [(g, ch) for g in ([grade] if grade else grades()) for ch in chapters(g)]
    hits = [(g, ch) for g, ch in pool
            if q in (fold(ch["code"]), fold(ch["name"]), str(ch.get("book_chapter", "")))]
    if not hits:
        hits = [(g, ch) for g, ch in pool
                if q and not q.isdigit() and (q in fold(ch["name"]) or fold(ch["name"]) in q)]
    if len(hits) == 1:
        g, ch = hits[0]
        return {**ch, "grade": g}
    names = ", ".join(f"« {g} {c['name']} » ({c['code']})" for g, c in (hits or pool))
    raise SystemExit(f"Chapitre ambigu ou inconnu : {query!r}. Précise au besoin le niveau "
                     f"(ex. « 6e B3 ») et choisis parmi : {names}")


def chapter_competencies(db, grade: str, chapter_code: str) -> list:
    from app.models import Competency, CompetencyFramework
    fw_ids = [f.id for f in db.query(CompetencyFramework).all()
              if getattr(f, "grade_level", "") == grade]
    return (db.query(Competency)
            .filter(Competency.framework_id.in_(fw_ids),
                    Competency.chapter_code == chapter_code)
            .order_by(Competency.order_index).all())


def _span(value: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", value or "")
    if not m:
        raise SystemExit(f"Plage de pages illisible : {value!r} (ex. 76-80)")
    a, b = int(m.group(1)), int(m.group(2) or m.group(1))
    if b < a:
        raise SystemExit(f"Plage inversée : {value!r}")
    return a, b


def prepare(db, chapter_query: str, *, grade: str | None = None, pages: str | None = None,
            lesson: str | None = None, dpi: int = PAGE_DPI) -> Path:
    """Rastérise les pages du chapitre (double page → deux pages imprimées) et
    écrit le payload qu'Astra lira. Retourne le dossier du run."""
    import fitz
    from PIL import Image

    from app.config import settings
    ch = find_chapter(chapter_query, grade)
    grade = ch["grade"]
    comps = chapter_competencies(db, grade, ch["code"])
    if not comps:
        raise SystemExit(f"Aucune compétence en base pour le chapitre {ch['code']} ({grade}).")
    ex_span = _span(pages) if pages else tuple(ch["exercises"])
    le_span = _span(lesson) if lesson else tuple(ch["lesson"])
    pdf = Path(settings.indigo_manuals[grade]["eleve"])
    if not pdf.exists():
        raise SystemExit(f"Manuel élève introuvable : {pdf}")
    doc = fitz.open(str(pdf))
    run_id = f"{grade}-{ch['code'].lower()}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    run = runs_dir() / run_id
    (run / "pages").mkdir(parents=True)
    entries = []
    for role, (a, b) in (("lesson", le_span), ("exercises", ex_span)):
        for p in range(a, b + 1):
            if not 1 <= p <= doc.page_count:
                raise SystemExit(f"Page {p} hors du manuel (1–{doc.page_count}).")
            pix = doc[p - 1].get_pixmap(dpi=dpi)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            half = img.width // 2
            for side, box, printed in (("left", (0, 0, half, img.height), 2 * p - 2),
                                       ("right", (half, 0, img.width, img.height), 2 * p - 1)):
                pid = f"p{printed:03d}"
                crop = img.crop(box)
                crop.save(run / "pages" / f"{pid}.png", dpi=(dpi, dpi))
                entries.append({"id": pid, "file": f"pages/{pid}.png", "role": role,
                                "pdf_page": p, "side": side, "printed_page": printed,
                                "width_px": crop.width, "height_px": crop.height})
    payload = {
        "run_id": run_id, "grade": grade, "dpi": dpi,
        "chapter": {"code": ch["code"], "name": ch["name"]},
        "competencies": [{"code": c.short_id or c.code, "label": c.label, "id": c.id}
                         for c in comps],
        "pages": entries, "output_file": OUTPUT_FILE,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    dump_json(run / "payload.json", payload)
    return run


# ================================================================= VALIDATE
@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    exercises: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings,
                "exercises": self.exercises}


def _words(text: str) -> int:
    return len(str(text or "").split())


def normalize_leaf(part: dict) -> dict:
    """Question simple : QCM/grille par le normaliseur Multipass, relier ici."""
    from app.services import indigo_multipass as mp
    from app.services import statement as st
    rtype = str((part or {}).get("response_type") or "").strip()
    if rtype != "matching":
        out = mp._normalize_leaf(part)
        if "figure" in part:
            out["figure"] = part["figure"]
        _keep_symbolic_check(part, out)
        return out
    out = dict(part)
    out["response_type"] = "matching"
    out["statement"] = st.repair_latex_control_chars(str(out.get("statement") or "").strip())
    out["left"] = [str(x).strip() for x in (out.get("left") or [])]
    out["right"] = [str(x).strip() for x in (out.get("right") or [])]
    out["pairs"] = [[mp._as_int(p[0]), mp._as_int(p[1])] for p in (out.get("pairs") or [])
                    if isinstance(p, (list, tuple)) and len(p) == 2]
    return out


def _keep_symbolic_check(before: dict, after: dict) -> None:
    """Le normaliseur Multipass neutralise tout `check` « value » dont
    l'expression garde un symbole libre (il visait les NOMS inventés,
    « count_class_45_50 »). En calcul littéral, `expand((7*x+3)*(4*x+5))` est au
    contraire LA vérification utile : on la rétablit quand ses seules variables
    sont des lettres qui figurent dans les propositions elles-mêmes."""
    from app.services import indigo_check
    check = (before or {}).get("check")
    if not (isinstance(check, dict) and check.get("kind") == "value"
            and (after.get("check") or {}).get("kind") == "none"):
        return
    try:
        want = indigo_check._eval(check.get("expr"))
    except ValueError:
        return
    names = {str(sym) for sym in getattr(want, "free_symbols", set())}
    if not names or any(len(n) != 1 for n in names):
        return
    in_choices = set()
    for c in after.get("choices") or []:
        val = indigo_check._value_of(str(c))
        in_choices |= {str(sym) for sym in getattr(val, "free_symbols", set())}
    if names <= in_choices:
        after["check"] = dict(check)


def normalize_variant(variant: dict) -> dict:
    from app.services import statement as st
    v = dict(variant or {})
    v["response_type"] = str(v.get("response_type") or "").strip()
    questions = v.get("questions") if isinstance(v.get("questions"), list) else v.get("parts")
    if v["response_type"] != COMPOSITE and isinstance(questions, list) and len(questions) >= 2:
        v["response_type"] = COMPOSITE
    if v["response_type"] != COMPOSITE:
        v.pop("questions", None)
        v.pop("parts", None)
        return {**v, **normalize_leaf(v)}
    v["statement"] = st.repair_latex_control_chars(str(v.get("statement") or "").strip())
    v.pop("parts", None)
    v["questions"] = [normalize_leaf(q) for q in (questions or []) if isinstance(q, dict)]
    return v


def parts(variant: dict) -> list[dict]:
    if variant.get("response_type") == COMPOSITE:
        return variant.get("questions") or []
    return [variant]


def _unprintable(text: str) -> str:
    """Caractères HORS formule que la police des copies (Helvetica, encodage
    cp1252) ne sait pas imprimer : ils sortiraient en « ? » (①, ✓, →, −…).
    Une formule $...$ passe par le moteur mathématique, elle n'est pas concernée."""
    from app.services import mathrender
    bad = []
    for content, is_math in mathrender.split_math_spans(text or ""):
        if is_math:
            continue
        for ch in content:
            if ch == "\u202f":      # espace fine insécable (services/typography) : imprimée en espace
                continue
            try:
                ch.encode("cp1252")
            except UnicodeEncodeError:
                bad.append(ch)
    return "".join(dict.fromkeys(bad))


def _texts(variant: dict) -> list[str]:
    out = [variant.get("statement") or ""]
    for p in parts(variant):
        if p is not variant:
            out.append(p.get("statement") or "")
        out += [str(c) for c in (p.get("choices") or [])]
        out += [str(c) for c in (p.get("cols") or [])]
        out += [str((r or {}).get("label") or "") for r in (p.get("rows") or [])]
        out += [str(x) for x in (p.get("left") or []) + (p.get("right") or [])]
    return out


def _lint_matching(part: dict) -> list[str]:
    from app.services import indigo_check
    probs = indigo_check.lint_statement(part.get("statement"), has_figure=True,
                                        min_len=indigo_check.STATEMENT_MIN_PART, part=True)
    left, right, pairs = part.get("left") or [], part.get("right") or [], part.get("pairs") or []
    if not MATCH_MIN <= len(left) <= MATCH_MAX or not MATCH_MIN <= len(right) <= MATCH_MAX:
        probs.append(f"relier : {len(left)} × {len(right)} éléments, attendu {MATCH_MIN} à "
                     f"{MATCH_MAX} de chaque côté")
    for side, items in (("gauche", left), ("droite", right)):
        if any(not x or len(x) > 80 for x in items):
            probs.append(f"relier : élément vide ou > 80 caractères à {side}")
        if len({fold(x) for x in items}) != len(items):
            probs.append(f"relier : deux éléments identiques à {side}")
    if not pairs:
        probs.append("relier : aucune paire `pairs`")
    lefts = [a for a, _ in pairs]
    rights = [b for _, b in pairs]
    if any(a is None or not 0 <= a < len(left) for a in lefts) or \
            any(b is None or not 0 <= b < len(right) for b in rights):
        probs.append("relier : indice de paire hors bornes")
    if len(set(lefts)) != len(lefts) or len(set(rights)) != len(rights):
        probs.append("relier : chaque élément ne se relie qu'une fois")
    if pairs and len(pairs) < len(left):
        probs.append("relier : chaque élément de gauche doit avoir sa paire")
    return probs


def _leaf_bareme(part: dict) -> float | None:
    from app.services import scoring
    if part.get("response_type") == "matching":
        return scoring.QCM_BOX_POINTS * max(1, len(part.get("pairs") or []))
    try:
        return scoring.qcm_bareme(part.get("response_type"),
                                  {"choices": part.get("choices") or [],
                                   "rows": part.get("rows") or []})
    except ValueError:
        return None


def to_raw_part(part: dict) -> dict:
    rtype = part.get("response_type")
    raw = {"response_type": rtype, "statement": part.get("statement") or "",
           "bareme_points": _leaf_bareme(part)}
    if part.get("figure"):
        fig = part["figure"]
        raw["figure"] = ({"type": fig["kind"], "params": fig["spec"]}
                         if fig.get("kind") in ("geo", "chart") else fig)
    if rtype == "checkbox_grid":
        raw["answer"] = {"type": "grid", "cols": part.get("cols") or [],
                         "rows": part.get("rows") or []}
    elif rtype == "matching":
        raw["answer"] = {"type": "matching", "left": part.get("left") or [],
                         "right": part.get("right") or [], "pairs": part.get("pairs") or []}
    else:
        raw["choices"] = part.get("choices") or []
        raw["answer"] = {"type": "choice", "correct": part.get("correct") or []}
    return raw


def to_raw(variant: dict) -> dict:
    """Variante Astra → schéma du validateur partagé. Pas de `correction` : les
    guides sont DANS l'énoncé (lignes {{aide}})."""
    if variant.get("response_type") == COMPOSITE:
        return {"response_type": COMPOSITE, "kind": "application",
                "statement": variant.get("statement") or "", "correction": "",
                "answer": {"type": "composite",
                           "parts": [to_raw_part(p) for p in parts(variant)]}}
    return {**to_raw_part(variant), "kind": "application", "correction": ""}


def variant_guides(variant: dict) -> list[str]:
    from app.services import statement as st
    texts = [variant.get("statement") or ""] + [p.get("statement") or ""
                                                for p in parts(variant)
                                                if variant.get("response_type") == COMPOSITE]
    return [g for t in texts for g in st.guide_texts(st.normalize(t))]


def _guide_problems(kind: str, variant: dict) -> tuple[list[str], list[str]]:
    from app.services import indigo_multipass as mp
    errors, warnings = [], []
    guides = variant_guides(variant)
    if kind == "facile" and not guides:
        errors.append("aucun encadré guide : la version Facile accompagne l'élève en "
                      "difficulté (au moins un « {{aide}} … » bien placé)")
    seen = set()
    for g in guides:
        n = _words(g)
        if not GUIDE_MIN_WORDS <= n <= GUIDE_MAX_WORDS:
            errors.append(f"guide de {n} mots (attendu {GUIDE_MIN_WORDS}–{GUIDE_MAX_WORDS}) : "
                          f"« {g[:60]} »")
        if _ANSWER_TOKEN_RE.search(g) or "{{figure}}" in g:
            errors.append(f"marqueur interdit dans un guide : « {g[:60]} »")
        key = mp._plain(g)
        if key in seen:
            errors.append(f"guide répété dans l'exercice : « {g[:60]} »")
        seen.add(key)
        leak = _guide_leak(g, variant)
        if leak:
            errors.append(leak)
    return errors, warnings


def _guide_leak(guide: str, variant: dict) -> str:
    """Le guide contient-il une bonne réponse (proposition juste, paire) ?"""
    from app.services import indigo_multipass as mp
    flat = mp._plain(guide)
    for part in parts(variant):
        answers = []
        if part.get("response_type") in ("qcm_single", "qcm_multiple"):
            ch = part.get("choices") or []
            answers = [ch[i] for i in (part.get("correct") or []) if 0 <= i < len(ch)]
        for ans in answers:
            a = mp._plain(ans)
            if a and len(a) >= 1 and re.search(rf"(?<!\w){re.escape(a)}(?!\w)", flat):
                return (f"le guide « {guide[:50]} » donne la bonne réponse « {ans} » : "
                        "il doit expliquer la démarche, jamais le résultat")
    return ""


def _editorial_problems(variant: dict) -> list[str]:
    """Refuse les rubriques éditoriales, sans interdire leur emploi dans un récit."""
    from app.services import statement as st
    problems = []
    scopes = [variant] + (parts(variant) if variant.get("response_type") == COMPOSITE else [])
    heading = re.compile(
        r"^(?:bilan|automatismes?|questions? flash|ceinture (?:jaune|verte|noire)|"
        r"(?:exercice|niveau) (?:base|facile))(?=\s|[,:.—-]|$)|"
        r"^(?:probleme|enigme|exercice)\s*[,:.—-]")
    for scope in scopes:
        text = st.strip_guides(scope.get("statement") or "")
        if any(heading.match(fold(line)) for line in text.splitlines()):
            problems.append("rubrique éditoriale dans l'énoncé (Bilan, Automatismes, type d'exercice, ceinture ou niveau) : utilise les métadonnées")
        if re.search(r"\bobserve\b[^.!?]*[.!?]\s*(?:\{\{figure\}\}\s*)?observe\b", fold(text)):
            problems.append("consigne d'observation répétée : une seule invitation à observer suffit")
    return problems


def _figure_coverage_problems(variant: dict, common_figure: dict | None) -> list[str]:
    """Contrôle les items déclarés et les candidats numérotés des figures geo.

    L'inventaire d'une image reste à vérifier visuellement. On ne prétend pas
    déduire les tâches d'une image à partir de ses pixels ou de tous ses points.
    """
    from app.services import indigo_multipass as mp
    from app.services import statement as st

    def response_text(scopes):
        return "\n".join(mp._plain(st.strip_guides(t)) for s in scopes for t in _texts(s))

    scopes = parts(variant)
    bindings = [(common_figure, scopes, "figure commune")]
    if variant.get("response_type") == COMPOSITE:
        # Une figure peut servir à plusieurs questions successives.
        for i, q in enumerate(scopes):
            if q.get("figure"):
                end = next((j for j in range(i + 1, len(scopes)) if scopes[j].get("figure")), len(scopes))
                bindings.append((q["figure"], scopes[i:end], f"figure de la question {chr(97+i)}"))
    problems = []
    for fig, related, where in bindings:
        if not isinstance(fig, dict):
            continue
        text = response_text(related)
        items = fig.get("items") or []
        if not isinstance(items, list) or any(not isinstance(x, str) for x in items):
            continue  # signalé par _figure_problems
        # Dans les panneaux de constructions geo, « 1 » sous le dessin signifie
        # « Figure 1 ». Ne pas confondre avec des nombres inscrits sur un dessin.
        candidates = [c for s in related for c in (s.get("choices") or [])
                      if re.fullmatch(r"Figure\s+\d+", c, re.I)]
        if fig.get("kind") == "geo" and candidates:
            numbers = [re.fullmatch(r"(?:Figure\s+)?(\d+)", str(t.get("text", "")), re.I)
                       for t in (fig.get("spec") or {}).get("texts", []) if isinstance(t, dict)]
            items = list(dict.fromkeys([*items, *(f"Figure {m[1]}" for m in numbers if m)]))
        for item in items:
            label = mp._plain(item)
            haystack = response_text([{"choices": candidates}]) if re.fullmatch(r"figure\s+\d+", label, re.I) and candidates else text
            if label and not re.search(rf"(?<!\w){re.escape(label)}(?!\w)", haystack, re.I):
                problems.append(f"{where} : élément visible « {item} » sans question/réponse correspondante ; complète les réponses ou adapte la figure")
    return problems


def _figure_problems(fig, payload: dict, where: str) -> list[str]:
    from app.services import figures
    if fig is None:
        return []
    if not isinstance(fig, dict):
        return [f"{where} : objet attendu"]
    if "items" in fig and (not isinstance(fig["items"], list) or not fig["items"]
                          or any(not isinstance(x, str) or not x.strip() for x in fig["items"])
                          or len({fold(x) for x in fig["items"]}) != len(fig["items"])):
        return [f"{where} : items doit contenir des libellés non vides et distincts"]
    kind = fig.get("kind")
    if kind == "crop":
        pages = {p["id"]: p for p in payload["pages"]}
        page = pages.get(fig.get("page"))
        if page is None:
            return [f"{where} : page inconnue {fig.get('page')!r} (pages : {', '.join(pages)})"]
        probs = []
        box = fig.get("bbox_px")
        if not _is_box(box) or not _box_in(box, page):
            probs.append(f"{where} : bbox_px [x0,y0,x1,y1] invalide ou hors de la page "
                         f"({page['width_px']}×{page['height_px']} px)")
        elif (box[2] - box[0]) < 40 or (box[3] - box[1]) < 40:
            probs.append(f"{where} : découpe minuscule ({box[2]-box[0]}×{box[3]-box[1]} px)")
        for i, m in enumerate(fig.get("masks") or []):
            if not _is_box(m) or not _box_in(m, page):
                probs.append(f"{where} : masks[{i}] invalide ou hors page")
        return probs
    if kind in ("geo", "chart"):
        err = figures.figure_error({"type": kind, "params": fig.get("spec")})
        return [f"{where} : {err}"] if err else []
    return [f"{where} : kind « crop », « geo » ou « chart » attendu, reçu {kind!r}"]


def _is_box(b) -> bool:
    return (isinstance(b, (list, tuple)) and len(b) == 4
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in b)
            and b[2] > b[0] and b[3] > b[1])


def _box_in(b, page) -> bool:
    return b[0] >= 0 and b[1] >= 0 and b[2] <= page["width_px"] and b[3] <= page["height_px"]


def variant_figure(ex: dict, kind: str):
    """Figure d'une variante : la sienne si elle en déclare une (`figure`,
    `null` = aucune), sinon celle de l'exercice."""
    v = (ex.get("variants") or {}).get(kind) or {}
    return v["figure"] if "figure" in v else ex.get("figure")


def validate(db, run: Path) -> tuple[Report, dict]:
    """Valide astra_output.json. Retourne (rapport, contrats validés par n°/niveau)."""
    from app.models import Competency
    from app.services import exercise_gen, indigo_check, scoring
    from app.services import indigo_multipass as mp
    from app.services import statement as st

    payload = load_json(run / "payload.json")
    out_path = run / OUTPUT_FILE
    rep = Report()
    if not out_path.exists():
        rep.errors.append(f"{OUTPUT_FILE} absent du run {run.name}")
        return rep, {}
    try:
        data = load_json(out_path)
    except json.JSONDecodeError as exc:
        rep.errors.append(f"{OUTPUT_FILE} n'est pas du JSON valide : {exc}")
        return rep, {}
    comps = {c["code"]: c for c in payload["competencies"]}
    exercises = data.get("exercises")
    if not isinstance(exercises, list) or not exercises:
        rep.errors.append("`exercises` doit être une liste non vide")
        return rep, {}
    contracts: dict = {}
    seen_numbers = set()
    for i, ex in enumerate(exercises):
        num = str((ex or {}).get("source_number") or f"#{i}")
        tag = f"n°{num}"
        e_err, e_warn = [], []
        if num in seen_numbers:
            e_err.append("numéro d'exercice en double dans la sortie")
        seen_numbers.add(num)
        if not isinstance(ex, dict):
            rep.errors.append(f"{tag} : objet attendu")
            continue
        code = ex.get("competency_code") or (next(iter(comps)) if is_problem(ex) else None)
        if is_problem(ex):
            if ex.get("difficulty") not in (1, 2, 3):
                e_err.append("problème : difficulty attendu 1 (vert), 2 (orange) ou 3 (noir)")
            if set(ex.get("variants") or {}) != {"original"}:
                e_err.append("problème : une seule version original, aucun dérivé Base/Facile")
            if not ex.get("title"):
                e_err.append("problème : titre obligatoire")
        if code not in comps:
            e_err.append(f"competency_code {code!r} hors du chapitre ({', '.join(comps)})")
        if ex.get("badge", "exercice") not in BADGES:
            e_err.append(f"badge {ex.get('badge')!r} ∉ {BADGES}")
        if ex.get("calculator", "autorisee") not in CALCULATORS:
            e_err.append(f"calculator {ex.get('calculator')!r} ∉ {CALCULATORS}")
        pages = {p["id"] for p in payload["pages"]}
        if ex.get("source_page") not in pages:
            e_err.append(f"source_page {ex.get('source_page')!r} : id de page du payload attendu "
                         "(ex. « p076 »)")
        src_box = ex.get("source_bbox_px")
        if src_box is not None:
            page = next((p for p in payload["pages"] if p["id"] == ex.get("source_page")), None)
            if page and (not _is_box(src_box) or not _box_in(src_box, page)):
                e_err.append("source_bbox_px invalide ou hors de la page")
        else:
            e_warn.append("source_bbox_px absent : la relecture n'aura pas l'extrait du manuel")
        e_err += _figure_problems(ex.get("figure"), payload, "figure")
        variants = ex.get("variants") or {}
        comp = db.get(Competency, comps[code]["id"]) if code in comps else None
        normalized = {}
        for kind in exercise_variants(ex):
            v_raw = variants.get(kind)
            label = VARIANT_LABEL[kind]
            if not isinstance(v_raw, dict):
                e_err.append(f"{label} : variante absente")
                continue
            if "figure" in v_raw:
                e_err += [f"{label} : {p}" for p in
                          _figure_problems(v_raw.get("figure"), payload, "figure")]
            has_fig = variant_figure(ex, kind) is not None
            v = normalize_variant(v_raw)
            normalized[kind] = v
            probs = _editorial_problems(v)
            probs += _figure_coverage_problems(v, variant_figure(ex, kind))
            if v["response_type"] == COMPOSITE:
                qs = parts(v)
                if not 2 <= len(qs) <= 8:
                    probs.append(f"{len(qs)} question(s) dans le composite, attendu 2 à 8")
                probs += indigo_check.lint_statement(st.strip_guides(v.get("statement")),
                                                     has_figure=has_fig, min_len=0)
            else:
                qs = [v]
            for j, q in enumerate(qs):
                where = f"question {chr(97 + j)}. : " if v["response_type"] == COMPOSITE else ""
                if q.get("response_type") not in LEAF_TYPES:
                    probs.append(f"{where}format « {q.get('response_type')} » interdit — "
                                 "uniquement cocher (qcm_single, qcm_multiple, checkbox_grid) "
                                 "ou relier (matching)")
                    continue
                q_for_check = {**q, "statement": st.strip_guides(q.get("statement") or "")}
                qfig = q.get("figure") if v["response_type"] == COMPOSITE else None
                probs += [where + p for p in _figure_problems(qfig, payload, "figure")]
                q_has_fig = has_fig or qfig is not None
                if q["response_type"] == "matching":
                    probs += [where + p for p in _lint_matching(q_for_check)]
                else:
                    probs += [where + p for p in indigo_check.verify(
                        q_for_check, has_figure=q_has_fig, part=v["response_type"] == COMPOSITE)]
            bad = "".join(dict.fromkeys(_unprintable(t) for t in _texts(v)))
            if bad:
                probs.append(f"caractère(s) non imprimable(s) hors formule « {bad} » : la "
                             "police des copies les imprimerait « ? » — écris-les en toutes "
                             "lettres ou dans une formule $...$ (ex. « rectangle 1 », $-3$)")
            g_err, g_warn = _guide_problems(kind, v)
            probs += g_err
            e_warn += [f"{label} : {w}" for w in g_warn]
            if not any("|---" in t for t in _texts(v)):
                # Les figures de questions ont déjà une portée explicite. Ne pas
                # les chercher seulement dans le contexte global du composite.
                question_figs = any(q.get("figure") for q in parts(v)) if v["response_type"] == COMPOSITE else False
                if has_fig or not question_figs:
                    e_warn += [f"{label} : {n}" for n in mp._figure_notes(v, has_figure=has_fig)]
            if not probs and comp is not None:
                raw = to_raw(v)
                for j, (q, rp) in enumerate(zip(parts(v), (raw.get("answer") or {}).get("parts") or [])):
                    if (q.get("figure") or {}).get("kind") == "crop":
                        path = figure_file(run, num, f"{kind}-q{j}")
                        render_figure(run, payload, q["figure"], path)
                        rp["figure"] = {"type": "image", "params": {"path": str(path)}}
                # empreintes VIERGES par variante : Base et Facile d'un même
                # exercice partagent légitimement leur contexte ; les vrais
                # clones sont repérés plus bas (_family_key)
                valid = exercise_gen._validate_exercise(
                    raw, comp, db, set(), allow_geometry_text=True, require_correction=False)
                if valid is None:
                    probs.append("refusé par le validateur MathPrint — " +
                                 exercise_gen.diagnose_rejection(raw, comp,
                                                                 require_correction=False))
                else:
                    if valid["response_type"] in ("qcm_single", "qcm_multiple", "checkbox_grid"):
                        try:
                            valid["grading"] = scoring.with_qcm_bareme(valid["grading"],
                                                                       valid["response_type"])
                        except ValueError as exc:
                            probs.append(str(exc))
                    contracts.setdefault(num, {})[kind] = valid
            e_err += [f"{label} : {p}" for p in probs]
        if len(normalized) == 2 and mp._family_key(normalized["base"]) == \
                mp._family_key(normalized["facile"]):
            e_err.append("Base et Facile sont identiques : la version Facile doit réellement "
                         "étayer (guides, découpage, données plus simples)")
        rep.exercises[num] = {"errors": e_err, "warnings": e_warn,
                              "bareme": {k: (c["grading"].get("bareme_points"))
                                         for k, c in contracts.get(num, {}).items()}}
        rep.errors += [f"{tag} — {e}" for e in e_err]
        rep.warnings += [f"{tag} — {w}" for w in e_warn]
        if e_err:
            contracts.pop(num, None)
    guide_uses: dict[str, set[str]] = {}
    for ex in exercises:
        for kind, variant in (ex.get("variants") or {}).items():
            for guide in variant_guides(variant):
                for line in guide.splitlines():
                    if line.strip():
                        guide_uses.setdefault(fold(line), set()).add(str(ex["source_number"]))
    for guide, numbers in guide_uses.items():
        if len(numbers) >= 3:
            rep.warnings.append(f"Guide identique dans {len(numbers)} sources ({', '.join(sorted(numbers))}) : vérifie sa pertinence pour chaque tâche : « {guide[:90]} »")
    for s in data.get("skipped") or []:
        if not (isinstance(s, dict) and s.get("source_number") and s.get("reason")):
            rep.errors.append("skipped : chaque entrée porte source_number et reason")
    dump_json(run / "report.json", rep.as_dict())
    return rep, contracts


# ================================================================== FIGURES
def _safe(num) -> str:
    return re.sub(r"[^\w-]", "_", str(num))


def figure_file(run: Path, num: str, kind: str | None) -> Path:
    return run / "figures" / f"{_safe(num)}-{kind or 'commun'}.png"


def render_crop(run: Path, payload: dict, fig: dict, dest: Path) -> Path:
    """Découpe une figure de la page : cadre `bbox_px`, `masks` blanchis (texte
    résiduel hors champ), marges blanches rognées. Le PNG porte le dpi des
    pages : imprimé à la taille du manuel (cf. pdfgen._figure_image)."""
    from PIL import Image, ImageChops, ImageDraw
    page = next(p for p in payload["pages"] if p["id"] == fig["page"])
    img = Image.open(run / page["file"]).convert("RGB")
    draw = ImageDraw.Draw(img)
    for m in fig.get("masks") or []:
        draw.rectangle([int(m[0]), int(m[1]), int(m[2]) - 1, int(m[3]) - 1], fill="white")
    x0, y0, x1, y1 = (int(v) for v in fig["bbox_px"])
    crop = img.crop((x0, y0, x1, y1))
    # rognage du blanc (seuil doux : le fond du manuel n'est pas blanc pur)
    gray = crop.convert("L").point(lambda v: 255 if v > 235 else 0)
    bbox = ImageChops.invert(gray).getbbox()
    if bbox:
        pad = 6
        crop = crop.crop((max(0, bbox[0] - pad), max(0, bbox[1] - pad),
                          min(crop.width, bbox[2] + pad), min(crop.height, bbox[3] + pad)))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dpi = int(payload.get("dpi") or PAGE_DPI)
    crop.save(dest, dpi=(dpi, dpi))
    return dest


def render_figure(run: Path, payload: dict, fig: dict, dest: Path) -> Path:
    from app.services import figures
    if fig.get("kind") == "crop":
        return render_crop(run, payload, fig, dest)
    png = figures.render_figure({"type": fig["kind"], "params": fig.get("spec")})
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(png)
    return dest


def build_figures(run: Path) -> dict[tuple[str, str], Path]:
    """PNG de chaque figure utilisée, par (n°, niveau)."""
    payload = load_json(run / "payload.json")
    data = load_json(run / OUTPUT_FILE)
    out: dict[tuple[str, str], Path] = {}
    for ex in data.get("exercises") or []:
        num = str(ex.get("source_number"))
        for kind in exercise_variants(ex):
            for j, q in enumerate(parts((ex.get("variants") or {}).get(kind) or {})):
                if q.get("figure") and (ex.get("variants") or {}).get(kind, {}).get("response_type") == COMPOSITE:
                    dest = figure_file(run, num, f"{kind}-q{j}")
                    render_figure(run, payload, q["figure"], dest)
                    out[(num, f"{kind}-q{j}")] = dest
            fig = variant_figure(ex, kind)
            if fig is None or _figure_problems(fig, payload, "figure"):
                continue
            own = "figure" in ((ex.get("variants") or {}).get(kind) or {})
            dest = figure_file(run, num, kind if own else None)
            if not dest.exists() or dest.stat().st_mtime < (run / OUTPUT_FILE).stat().st_mtime:
                render_figure(run, payload, fig, dest)
            out[(num, kind)] = dest
        if ex.get("source_bbox_px") and ex.get("source_page"):
            dest = run / "figures" / f"{_safe(num)}-source.png"
            try:
                render_crop(run, payload, {"page": ex["source_page"],
                                           "bbox_px": ex["source_bbox_px"]}, dest)
                out[(num, "source")] = dest
            except Exception:  # noqa: BLE001 — l'extrait source n'est qu'une aide de relecture
                pass
    return out


# ================================================================== PREVIEW
def _render_row(num: str, kind: str, valid: dict, ex: dict, fig_path: Path | None):
    """Ligne de banque SIMULÉE (sans base) pour generation.render_shape."""
    from app.services import indigo_fields
    from app.services import statement as st
    statement, expected, grading = indigo_fields.adapt_fields(
        valid["statement"], valid["response_type"], valid.get("expected"), valid.get("grading"))
    statement = st.place_figure_marker(statement, fig_path is not None,
                                       at_end=valid["response_type"] == COMPOSITE)
    return SimpleNamespace(
        id=f"{num}-{kind}", statement=statement, correction="",
        difficulty_level=exercise_level(ex, kind), response_type=valid["response_type"],
        grading_json=grading, expected_json=expected, source="indigo",
        figure_json={"type": "image", "params": {"path": str(fig_path)}} if fig_path else None,
        raw_extract_json={"indigo": {"badge_type": ex.get("badge", "exercice"),
                                     "title": ex.get("title") or "",
                                     "calculator": ex.get("calculator", "autorisee")}})


def preview(db, run: Path, *, variant: str, guides: bool = True) -> Path:
    """Sujet séparé d'un seul type de cartes (base, facile ou original), à la
    demande. Le document récapitulatif de TOUTES les cartes n'est plus produit."""
    import fitz
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    from app.services import generation, pdfgen
    rep, contracts = validate(db, run)
    if not rep.ok:
        raise SystemExit(f"Aperçu refusé : {len(rep.errors)} erreur(s) de validation. "
                         "Corrige le run pour produire un PDF complet.")
    figs = build_figures(run)
    data = load_json(run / OUTPUT_FILE)
    payload = load_json(run / "payload.json")
    items, index = [], []
    for ex in data.get("exercises") or []:
        num = str(ex.get("source_number"))
        for kind in exercise_variants(ex):
            if kind != variant:
                continue
            valid = contracts.get(num, {}).get(kind)
            if valid is None:
                continue
            row = _render_row(num, kind, valid, ex, figs.get((num, kind)))
            shape = generation.render_shape(
                row, pdfgen.GUIDES_INCLUDE if guides else pdfgen.GUIDES_NONE)
            items.append({**shape, "item_id": row.id})
            index.append(f"{len(items):>3}  n°{num}  {VARIANT_LABEL[kind]}")
    if not items:
        raise SystemExit("Rien à prévisualiser : aucune variante valide (lance `validate`).")
    out_dir = run / f"subject-{variant}"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir()
    pdf_path = out_dir / "preview.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    pages_meta = [{"page_id": f"p{i}", "payload": f"MP1|p{i}|0"} for i in range(len(items) + 2)]
    # rendu page par page : la mise en page réelle des copies, sans placement imposé
    subject_label = "Problèmes" if variant == "original" else VARIANT_LABEL.get(variant, "Astra")
    pdfgen.render_copy(c, student_name="Nom : __________________", class_name=payload["grade"],
                       title=f"{payload['chapter']['name']} — {subject_label}", assessment_type="training",
                       items=items, pages_meta=pages_meta, font_size=9)
    c.save()
    doc = fitz.open(str(pdf_path))
    for i, page in enumerate(doc):
        page.get_pixmap(dpi=110).save(str(out_dir / f"page-{i + 1:02d}.png"))
    (out_dir / "index.txt").write_text(
        "Ordre des cartes (numéro de carte → exercice)\n" + "\n".join(index) + "\n",
        encoding="utf-8")
    return out_dir


# ================================================================== PERSIST
def persist(db, run: Path, *, replace: bool = False) -> dict:
    from app.models import Competency, IndigoExercise, IndigoExtraction
    from app.services import indigo, indigo_fields
    from app.services import statement as st

    rep, contracts = validate(db, run)
    if not rep.ok:
        raise SystemExit(f"Validation en échec ({len(rep.errors)} erreur(s)) : corrige "
                         f"{OUTPUT_FILE} puis relance `validate`. Rien n'a été écrit.")
    figs = build_figures(run)
    payload = load_json(run / "payload.json")
    data = load_json(run / OUTPUT_FILE)
    comps = {c["code"]: c["id"] for c in payload["competencies"]}
    pages = {p["id"]: p for p in payload["pages"]}
    now = datetime.now(timezone.utc)

    existing = [r for r in db.query(IndigoExercise).filter(
        IndigoExercise.competency_id.in_(list(comps.values()))).all()
        if (r.raw_ocr_json or {}).get("pipeline") == PIPELINE]
    by_key: dict[tuple[str, str], list] = {}
    for r in existing:
        by_key.setdefault((r.competency_id, r.source_number), []).append(r)

    extraction = IndigoExtraction(
        grade_level=payload["grade"], status=ST_DONE, progress=100,
        targets_json=[{"kind": PIPELINE, "chapter": payload["chapter"],
                       "pages": [p["id"] for p in payload["pages"]], "run": run.name}],
        log_text=f"Astra ({MODEL}) — run {run.name}", created_at=now, updated_at=now)
    db.add(extraction)
    db.flush()
    written, skipped = [], []
    notes_by_num = {num: e.get("warnings") or [] for num, e in rep.exercises.items()}
    for order, ex in enumerate(data.get("exercises") or []):
        num = str(ex["source_number"])
        cid = comps[ex.get("competency_code") or next(iter(comps))]
        old = ([r for r in existing if r.source_number == num]
               if is_problem(ex) else by_key.get((cid, num)) or [])
        if old:
            if not replace:
                skipped.append(f"n°{num} : déjà en brouillon Astra (relance avec --replace)")
                continue
            if any(r.status == "validated" for r in old):
                skipped.append(f"n°{num} : déjà VALIDÉ dans l'onglet Exercices — non remplacé")
                continue
            for r in old:
                for rel in (r.crop_path, r.figure_path):
                    if rel:
                        indigo.crop_abs_path(rel).unlink(missing_ok=True)
                db.delete(r)
            db.flush()
        page = pages[ex["source_page"]]
        base_id = None
        for kind in exercise_variants(ex):
            valid = contracts[num][kind]
            fig = variant_figure(ex, kind)
            fig_png = figs.get((num, kind))
            statement, expected, grading = indigo_fields.adapt_fields(
                valid["statement"], valid["response_type"], valid.get("expected"),
                valid.get("grading"))
            statement = st.place_figure_marker(statement, fig_png is not None,
                                               at_end=valid["response_type"] == COMPOSITE)
            row = IndigoExercise(
                extraction_id=extraction.id, competency_id=cid, grade_level=payload["grade"],
                source_page=page["pdf_page"] - 1, source_number=num, order_index=order,
                badge_type=ex.get("badge", "exercice"), difficulty=exercise_level(ex, kind),
                title=(ex.get("title") or "")[:200], tags_json=list(ex.get("tags") or []),
                calculator=ex.get("calculator", "autorisee"),
                statement=statement, response_type=valid["response_type"],
                expected_json=expected, grading_json=grading,
                correction_solution=str(((ex.get("variants") or {}).get(kind) or {})
                                        .get("solution") or ""),
                correction_guide="",
                payload_json={"response_type": valid["response_type"], "statement": statement,
                              "expected": expected, "grading": grading, "kind": "probleme" if is_problem(ex) else "application"},
                variant_kind=kind, derived_from_id=None if is_problem(ex) else base_id, status="draft",
                model=MODEL, prompt_version=PROMPT_VERSION, created_at=now, updated_at=now)
            db.add(row)
            db.flush()
            src = figs.get((num, "source"))
            if src is not None:
                rel = f"indigo/drafts/{row.id}.png"
                indigo.crop_abs_path(rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, indigo.crop_abs_path(rel))
                row.crop_path = rel
                b = ex["source_bbox_px"]
                row.crop_box_json = {"page_index": page["pdf_page"] - 1, "x0": b[0], "y0": b[1],
                                     "x1": b[2], "y1": b[3], "raster_dpi": payload["dpi"],
                                     "img_w": page["width_px"], "img_h": page["height_px"],
                                     "pipeline": PIPELINE, "half": page["side"]}
            if fig_png is not None:
                rel = f"indigo/drafts/{row.id}_fig.png"
                indigo.crop_abs_path(rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(fig_png, indigo.crop_abs_path(rel))
                row.has_figure = row.figure_required = True
                row.figure_path = rel
                if fig.get("kind") == "crop":
                    b = fig["bbox_px"]
                    fpage = pages[fig["page"]]
                    row.figure_box_json = {
                        "page_index": fpage["pdf_page"] - 1, "x0": b[0], "y0": b[1],
                        "x1": b[2], "y1": b[3], "raster_dpi": payload["dpi"],
                        "img_w": fpage["width_px"], "img_h": fpage["height_px"],
                        "half": fpage["side"],
                        "masks": [dict(zip(("x0", "y0", "x1", "y1"), m))
                                  for m in fig.get("masks") or []]}
            label = VARIANT_LABEL[kind]
            notes = [n for n in notes_by_num.get(num, [])
                     if not n.startswith(tuple(f"{v} :" for v in VARIANT_LABEL.values()))
                     or n.startswith(f"{label} :")]
            row.raw_ocr_json = {"pipeline": PIPELINE, "model": MODEL, "run": run.name,
                                "chapter_code": ex.get("chapter_code"),
                                "difficulty_source": ex.get("difficulty_source"),
                                "source_statement": ex.get("source_statement") or "",
                                "figure_spec": fig if fig and fig.get("kind") != "crop" else None,
                                "review_notes": notes, "review_blocking": [], "adapted": True}
            if kind == "base":
                base_id = row.id
            written.append(f"n°{num} {label} → {row.id}")
    extraction.stats_json = {"pipeline": PIPELINE, "exercises": len(written),
                             "skipped": skipped, "run": run.name,
                             "skipped_by_astra": data.get("skipped") or []}
    db.commit()
    return {"extraction_id": extraction.id, "written": written, "skipped": skipped}
