"""Indigo — PRÉ-DÉCOUPAGE des énoncés ET des corrigés par NUMÉRO (LLM au choix).

Le découpage géométrique (services.indigo._segment_by_numbers /
_segment_corrections_by_numbers) rate parfois une frontière quand l'OCR a
fusionné deux exercices (numéro manqué, colonnes mal lues, énoncé/corrigé à
cheval sur deux blocs) : deux exercices du manuel deviennent alors UN seul. Le
NUMÉRO imprimé dans le badge circulaire est la SOURCE DE VÉRITÉ — pour le
manuel ÉLÈVE comme pour le manuel PROFESSEUR, même politique.

Ici on donne au fournisseur choisi (cf. services.indigo_llm) TOUT le texte
OCRisé des pages (dans l'ordre de lecture) + la PLAGE de numéros attendue (ex.
34→67, bornes incluses), et on lui demande de RE-DÉCOUPER proprement : un objet
par numéro, en RECOPIANT le contenu VERBATIM.
Il ne reformule pas, ne résout pas, n'ajoute rien — la mise au propre réelle est
l'étape suivante (indigo_gemini.adapt_batch, par lots de 5 à 7). Sortie :
{numéro -> texte brut découpé} (énoncé OU corrigé selon la fonction appelée).

Dégradation gracieuse : si l'appel échoue ou ne renvoie rien d'exploitable, on
retourne {} et l'appelant retombe sur le découpage géométrique (contraint par la
plage de numéros), qui reste testé et fonctionnel hors ligne.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..models import Competency
from . import indigo_llm, prompts
from . import statement as statement_mod
from .gemini_gen import _competency_name

logger = logging.getLogger("app.indigo")

SEGMENT_PROMPT_VERSION = "indigo-segment-1"
SEGMENT_CORRECTIONS_PROMPT_VERSION = "indigo-segment-corr-1"


def _system_statements() -> str:
    """Prompt de découpage des ÉNONCÉS — éditable dans
    prompts/indigo/segmentation.txt (chargé paresseusement)."""
    return prompts.load("indigo", "segmentation")


# Même politique que _system_statements(), appliquée au manuel du PROFESSEUR : le numéro
# (celui de l'EXERCICE corrigé, pas un numéro de question interne au corrigé)
# fait toujours autorité, recopie verbatim, aucune résolution par le modèle — la
# résolution éventuelle (corrigé absent/illisible) reste le travail de
# indigo_gemini.adapt_batch, jamais de cette étape de découpage.
def _system_corrections() -> str:
    """Prompt de découpage des CORRIGÉS (manuel prof) — éditable dans
    prompts/indigo/segmentation_corrections.txt (chargé paresseusement)."""
    return prompts.load("indigo", "segmentation_corrections")


def _call(db: Session, system: str, payload: dict, correlation_id: str) -> dict | None:
    """Découpage par le fournisseur choisi dans l'onglet (Anthropic Sonnet ou
    DeepSeek pro, cf. services.indigo_llm). Une sortie tronquée/en échec retombe en
    JSON invalide → l'appelant (segment_statements) rend {} et le pipeline repasse
    au découpage géométrique."""
    return indigo_llm.call(db, "segment", system, payload, correlation_id)


def _segment(db: Session, system: str, log_label: str, id_prefix: str,
            competency: Competency, grade: str, page_texts: list[tuple[int, str]],
            expected_numbers: list[int], correlation_id: str | None = None) -> dict[str, str]:
    """Re-découpe des textes (énoncés ou corrigés) par numéro via Claude Haiku —
    logique PARTAGÉE entre `segment_statements` et `segment_corrections`, seul
    le prompt (`system`) change. `page_texts` = [(index de page PDF 0-based,
    texte OCR en ordre de lecture), …]. `expected_numbers` = la plage attendue
    (liste d'entiers croissante). Retourne {numéro -> texte brut}, ou {} si
    rien d'exploitable (l'appelant retombe alors sur la géométrie)."""
    if not expected_numbers or not any(t.strip() for _p, t in page_texts):
        return {}
    lo, hi = expected_numbers[0], expected_numbers[-1]
    payload = {
        "grade_level": grade,
        "competency_label": _competency_name(competency),
        "expected_numbers": [str(n) for n in expected_numbers],
        "number_range": {"from": lo, "to": hi},
        "pages": [{"page": p + 1, "text": t} for p, t in page_texts if t.strip()],
    }
    cid = correlation_id or f"indigo-{id_prefix}-{competency.code}-{lo}-{hi}"
    try:
        data = _call(db, system, payload, cid)
    except Exception:
        logger.exception("Indigo : pré-découpage Claude (%s) en échec (%s) — repli géométrie",
                         log_label, competency.code)
        return {}

    allowed = {str(n) for n in expected_numbers}
    out: dict[str, str] = {}
    for it in (data or {}).get("exercises") or []:
        if not isinstance(it, dict):
            continue
        num = str(it.get("number") or "").strip()
        txt = str(it.get("statement") or "").strip()
        if num in allowed and txt and num not in out:
            # ceinture + bretelles : jamais le numéro en tête (le texte ne
            # commence pas par un chiffre — cf. statement.strip_leading_number)
            out[num] = statement_mod.strip_leading_number(txt, num)
    logger.info("Indigo/segment : %s — %s/%s %s re-découpé(s) par Claude",
                competency.code, len(out), len(expected_numbers), log_label)
    return out


def segment_statements(db: Session, competency: Competency, grade: str,
                       page_texts: list[tuple[int, str]], expected_numbers: list[int],
                       correlation_id: str | None = None) -> dict[str, str]:
    """Re-découpe les ÉNONCÉS (manuel élève) par numéro via Claude Haiku."""
    return _segment(db, _system_statements(), "exercice(s)", "seg", competency, grade,
                    page_texts, expected_numbers, correlation_id)


def segment_corrections(db: Session, competency: Competency, grade: str,
                        page_texts: list[tuple[int, str]], expected_numbers: list[int],
                        correlation_id: str | None = None) -> dict[str, str]:
    """Re-découpe les CORRIGÉS (manuel professeur) par numéro via Claude Haiku — même
    politique que `segment_statements` (le numéro de l'exercice fait autorité,
    recopie verbatim), pour les mêmes raisons : l'OCR fusionne parfois deux
    corrigés voisins ou coupe un corrigé en deux blocs."""
    return _segment(db, _system_corrections(), "corrigé(s)", "segcorr", competency, grade,
                    page_texts, expected_numbers, correlation_id)
