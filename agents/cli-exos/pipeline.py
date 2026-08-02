"""Pipeline `cli-exos` — les 3 étapes LLM, câblées sur le CLI Claude (abonnement).

Miroir fonctionnel des étapes LLM d'Indigo, mais :
  • découpage  → `app.services.indigo_segment`  ⇒ ici via CLI (Sonnet) ;
  • génération → `app.services.indigo_gemini`   ⇒ ici via CLI (Sonnet) ;
  • vérification→ `app.services.indigo_verify`   ⇒ ici via CLI (Opus).

On NE ré-implémente PAS ce qui n'est pas LLM : l'OCR (Mistral), le découpage
géométrique, les crops, la lecture couleur (CV) et la persistance restent ceux
d'Indigo (`app.services.indigo`). On réutilise aussi le validateur/finaliseur
partagé (`indigo_gemini._finalize`, `exercise_gen.format_contract`) et le moteur
de champs : la sortie du CLI doit passer EXACTEMENT le même contrôle qu'Indigo,
sinon elle serait rejetée en silence. Les prompts, eux, sont propres à cette
pipeline (fichiers `prompts/*.txt`, éditables), distincts de ceux d'Indigo.

Ce module s'importe APRÈS que `run.py` a mis `backend/` sur `sys.path` (il dépend
de `app.services.*`).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import llm_cli
from app.services import exercise_gen, indigo_gemini, indigo_verify
from app.services import statement as statement_mod
from app.services.gemini_gen import _competency_name, _competency_tree

logger = logging.getLogger("cli-exos.pipeline")

# Répartition des modèles demandée : Sonnet (découpage), Sonnet (génération),
# Opus (vérification). Alias CLI (toujours à jour côté abonnement), surchargeables.
SEGMENT_MODEL = os.environ.get("CLI_EXOS_MODEL_SEGMENT", "sonnet")
GENERATE_MODEL = os.environ.get("CLI_EXOS_MODEL_GENERATE", "sonnet")
REVIEW_MODEL = os.environ.get("CLI_EXOS_MODEL_REVIEW", "opus")
REVIEW_ENABLED = os.environ.get("CLI_EXOS_REVIEW", "1") != "0"

PROMPT_VERSION = "cli-exos-1"
# prompts ÉDITABLES, désormais à la racine du repo (rangés par pipeline) :
# prompts/cli-exos/{decoupage,generation,verification}.txt — cf. prompts/indigo/…
# pour la pipeline Indigo (API). Surchargeable par CLI_EXOS_PROMPTS_DIR.
_PROMPTS_DIR = Path(os.environ.get(
    "CLI_EXOS_PROMPTS_DIR",
    Path(__file__).resolve().parents[2] / "prompts" / "cli-exos"))

# Lots (repris d'Indigo) : génération 5–7, relecture 20–40.
choose_batch_size = indigo_gemini.choose_batch_size
choose_review_batch_size = indigo_verify.choose_review_batch_size

# Glue de schéma (NON éditable — doit rester alignée sur `indigo_gemini._finalize`) :
# force les champs supplémentaires que le validateur attend en plus du contrat.
_SCHEMA_ADDENDUM = (
    "\n\nAJOUT AU SCHÉMA (obligatoire) : chaque objet exercice porte EN PLUS "
    "\"source_number\":str (le numéro imprimé dans le manuel), "
    "\"correction_solution\":str (la vraie solution — cf. « LES DEUX CHAMPS DE "
    "CORRECTION »/« CE QUE TU TRAQUES ») et \"needs_figure\":bool (cf. « BESOIN "
    "DE FIGURE »). N'utilise ni le champ \"figure\" ni \"source_blocks\".")


# ------------------------------------------------------------------- prompts

def _load_prompt(name: str) -> str:
    """Lit un prompt (re-lu à chaque run : éditer le .txt suffit, pas de cache)."""
    return (_PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")


def _subst(text: str, competency, db, grade: str) -> str:
    return (text
            .replace("§GRADE§", grade)
            .replace("§COMPETENCY§", _competency_name(competency))
            .replace("§CHAPTER§", f"{competency.chapter_code} {competency.chapter_name}".strip())
            .replace("§DOMAIN§", f"{competency.domain_code} {competency.domain_name}".strip())
            .replace("§COMPETENCY_TREE§", _competency_tree(db, competency)))


def _short(competency) -> str:
    return competency.short_id or competency.code


# ------------------------------------------------------------- étape 1 : découpage

def _segment(db, competency, grade: str, page_texts, expected_numbers,
             corpus: str, id_prefix: str) -> dict[str, str]:
    """Re-découpe par numéro un ensemble de pages (énoncés OU corrigés), via le CLI
    Sonnet. Dégradation gracieuse : renvoie {} en cas d'échec (l'appelant retombe
    sur le découpage géométrique)."""
    if not expected_numbers or not any(t.strip() for _p, t in page_texts):
        return {}
    lo, hi = expected_numbers[0], expected_numbers[-1]
    payload = {
        "grade_level": grade,
        "competency_label": _competency_name(competency),
        "corpus": corpus,
        "expected_numbers": [str(n) for n in expected_numbers],
        "number_range": {"from": lo, "to": hi},
        "pages": [{"page": p + 1, "text": t} for p, t in page_texts if t.strip()],
    }
    system = _load_prompt("decoupage")
    try:
        data = llm_cli.claude_cli_json(
            system, payload, model=SEGMENT_MODEL,
            correlation_id=f"cli-{id_prefix}-{competency.code}-{lo}-{hi}")
    except Exception:
        logger.exception("cli-exos : découpage (%s) en échec (%s) — repli géométrie",
                         corpus, competency.code)
        return {}
    allowed = {str(n) for n in expected_numbers}
    out: dict[str, str] = {}
    for it in (data or {}).get("exercises") or []:
        if not isinstance(it, dict):
            continue
        num = str(it.get("number") or "").strip()
        txt = str(it.get("statement") or "").strip()
        if num in allowed and txt and num not in out:
            out[num] = statement_mod.strip_leading_number(txt, num)
    logger.info("cli-exos/découpage : %s — %s/%s %s", competency.code,
                len(out), len(expected_numbers), corpus)
    return out


def segment_statements(db, competency, grade, page_texts, expected_numbers) -> dict[str, str]:
    return _segment(db, competency, grade, page_texts, expected_numbers,
                    "énoncés", "seg")


def segment_corrections(db, competency, grade, page_texts, expected_numbers) -> dict[str, str]:
    return _segment(db, competency, grade, page_texts, expected_numbers,
                    "corrigés", "segcorr")


# ------------------------------------------------------- collecte (géométrie + LLM)

def collect_exercises(db, competency, grade, eleve, eleve_pages, expected, log) -> list[dict]:
    """Un exercice par NUMÉRO : la géométrie (crop/figure) localise, le découpage
    Sonnet recoupe proprement l'énoncé (le numéro fait autorité). Reprend la
    logique d'`indigo._collect_exercises`, mais avec le découpage CLI."""
    from app.services import indigo
    exp_set = set(expected)
    geom: dict[str, dict] = {}
    for page in eleve:
        for ex in indigo._segment_by_numbers(page, competency, exp_set):
            geom.setdefault(ex["number"], ex)
    log(f"{_short(competency)} : découpage des énoncés (Sonnet)…")
    page_texts = [(p["source_page"], indigo._ordered_text(p)) for p in eleve]
    seg_text = segment_statements(db, competency, grade, page_texts, expected)
    out: list[dict] = []
    for n in expected:
        num = str(n)
        g = geom.get(num)
        clean = (seg_text.get(num) or "").strip()
        if g is not None:
            if clean:
                g["text"] = statement_mod.strip_leading_number(clean, num)
            out.append(g)
        elif clean:
            out.append(indigo._placeholder_exercise(num, clean, eleve_pages))
        else:
            log(f"  · n°{num} absent des pages fournies ({competency.code}) — ignoré")
    return out


def collect_corrections(db, competency, grade, prof, expected, log) -> dict[str, str]:
    """Corrigés du manuel prof, un par NUMÉRO — même politique que côté élève."""
    from app.services import indigo
    exp_set = set(expected)
    geom: dict[str, str] = {}
    for page in prof:
        for num, txt in indigo._segment_corrections_by_numbers(page, exp_set).items():
            geom.setdefault(num, txt)
    if not prof:
        return geom
    log(f"{_short(competency)} : découpage des corrigés (Sonnet)…")
    page_texts = [(p["source_page"], indigo._ordered_text(p)) for p in prof]
    seg_text = segment_corrections(db, competency, grade, page_texts, expected)
    out = dict(geom)
    out.update(seg_text)   # texte Sonnet préféré au flatten géométrique
    return out


# ------------------------------------------------------------- étape 2 : génération

def _gen_system(db, competency, grade: str) -> str:
    intro = _subst(_load_prompt("generation"), competency, db, grade)
    contract = exercise_gen.format_contract(intro, geometry_rules=exercise_gen._GEOMETRY_RULES)
    return contract + _SCHEMA_ADDENDUM


def generate_batch(db, competency, grade: str, manuals: list[dict]) -> dict[str, dict]:
    """Met au propre un LOT d'exercices en un appel CLI (Sonnet). `manuals` =
    [{number, statement, correction, has_figure}, …]. Retourne
    {source_number -> contrat interne validé} ; un exercice absent/refusé est
    simplement omis (repli géré par l'appelant)."""
    system = _gen_system(db, competency, grade)
    payload = {
        "grade_level": grade,
        "competency_code": competency.code,
        "competency_label": _competency_name(competency),
        "exercises_to_adapt": [
            {"number": str(m.get("number", "")), "statement": m.get("statement", ""),
             "correction": m.get("correction", ""), "has_figure": bool(m.get("has_figure"))}
            for m in manuals],
    }
    nums = ",".join(str(m.get("number", "?")) for m in manuals)
    try:
        data = llm_cli.claude_cli_json(system, payload, model=GENERATE_MODEL,
                                       correlation_id=f"cli-{competency.code}-{nums}")
    except Exception:
        logger.exception("cli-exos : lot de génération échoué (%s)", competency.code)
        data = None
    items = (data or {}).get("exercises") or []

    by_number: dict[str, dict] = {}
    for raw in items:
        if isinstance(raw, dict):
            by_number.setdefault(str(raw.get("source_number") or "").strip(), raw)

    out: dict[str, dict] = {}
    for m in manuals:
        num = str(m.get("number", "")).strip()
        raw = by_number.get(num)
        if raw is None:                        # repli positionnel si numéro manquant
            idx = manuals.index(m)
            raw = items[idx] if idx < len(items) and isinstance(items[idx], dict) else None
        if raw is None:
            logger.warning("cli-exos : pas de sortie pour l'exercice n°%s (%s)",
                           num, competency.code)
            continue
        valid = indigo_gemini._finalize(raw, competency, db, m)
        if valid is not None:
            out[num] = valid
    logger.info("cli-exos/génération : lot [%s] %s — %s/%s mis au propre",
                nums, competency.code, len(out), len(manuals))
    return out


def generate_one(db, competency, grade: str, manual: dict) -> dict | None:
    """2e chance en SOLO après un échec/refus en lot (un lot chargé dégrade parfois
    un exercice ; rejoué seul il passe souvent)."""
    try:
        out = generate_batch(db, competency, grade, [manual])
    except Exception:
        logger.exception("cli-exos : retry solo échoué (n°%s, %s)",
                         manual.get("number"), competency.code)
        return None
    return out.get(str(manual.get("number", "")).strip())


# ---------------------------------------------------------- étape 3 : vérification

def _review_system(db, competency, grade: str) -> str:
    intro = _subst(_load_prompt("verification"), competency, db, grade)
    contract = exercise_gen.format_contract(intro, geometry_rules=exercise_gen._GEOMETRY_RULES)
    return contract + _SCHEMA_ADDENDUM


def _review_batch(db, competency, grade: str, batch: list[tuple]) -> dict[str, dict]:
    """Relit UN lot (Opus). `batch` = [(number, manual, valid), …]. Retourne
    {number -> valid RELU} — seuls les exercices dont la version relue re-passe le
    validateur (jamais de dégradation : l'appelant garde sinon la version générée).
    Réutilise `indigo_verify._review_item` (mise au format d'entrée du relecteur)."""
    items: list[dict] = []
    order: list[tuple[str, dict]] = []
    for number, manual, valid in batch:
        it = indigo_verify._review_item(number, manual, valid)
        if it is None:
            continue
        items.append(it)
        order.append((number, manual))
    if not items:
        return {}
    system = _review_system(db, competency, grade)
    payload = {
        "grade_level": grade,
        "competency_code": competency.code,
        "competency_label": _competency_name(competency),
        "exercises_to_review": items,
    }
    nums = ",".join(n for n, _m in order)
    try:
        data = llm_cli.claude_cli_json(system, payload, model=REVIEW_MODEL,
                                       correlation_id=f"cli-review-{competency.code}-{nums[:60]}")
    except Exception:
        logger.exception("cli-exos : lot de relecture échoué (%s) — versions générées conservées",
                         competency.code)
        return {}
    reviewed = (data or {}).get("exercises") or []

    by_number: dict[str, dict] = {}
    for raw in reviewed:
        if isinstance(raw, dict):
            by_number.setdefault(str(raw.get("source_number") or "").strip(), raw)

    out: dict[str, dict] = {}
    for idx, (number, manual) in enumerate(order):
        raw = by_number.get(number)
        if raw is None:
            raw = reviewed[idx] if idx < len(reviewed) and isinstance(reviewed[idx], dict) else None
        if raw is None:
            continue
        valid = indigo_gemini._finalize(raw, competency, db, manual)
        if valid is not None:
            out[number] = valid
    logger.info("cli-exos/relecture : lot [%s] %s — %s/%s corrigé(s)/confirmé(s)",
                nums[:60], competency.code, len(out), len(items))
    return out


def review(db, competency, grade: str, triples: list[tuple], progress_cb=None) -> dict[str, dict]:
    """Relecture finale (Opus) de tous les exercices générés d'une cible, par lots
    de 20 à 40. `triples` = [(row, manual, valid|None), …]. Retourne {number ->
    valid} pour chaque exercice généré : la version relue si elle re-passe le
    validateur, sinon la version générée (contrat brut `_raw` retiré au passage)."""
    ready = [(str(m.get("number", "")).strip(), m, v)
             for (_row, m, v) in triples if v is not None]
    result: dict[str, dict] = {num: v for num, m, v in ready}

    if not REVIEW_ENABLED or not ready:
        return {n: indigo_verify._strip_raw(v) for n, v in result.items()}

    size = choose_review_batch_size(len(ready))
    for i in range(0, len(ready), size):
        batch = ready[i:i + size]
        if progress_cb:
            progress_cb(f"{_short(competency)} : relecture "
                        f"{min(i + len(batch), len(ready))}/{len(ready)} (Opus)…")
        try:
            corrected = _review_batch(db, competency, grade, batch)
        except Exception:
            logger.exception("cli-exos/relecture : lot échoué (%s) — versions générées conservées",
                             competency.code)
            corrected = {}
        for number, valid in corrected.items():
            result[number] = valid

    return {n: indigo_verify._strip_raw(v) for n, v in result.items()}
