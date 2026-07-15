"""Adaptateur MathALÉA (§3.3) : interroge le service Node headless
(mathalea-service) et convertit sa sortie vers le contrat interne
(GeneratedItem : énoncé, correction, réponse attendue, barème).

La version MathALÉA est épinglée (clone versionné) ; chaque instantané stocke
la seed et la version exacte (RM-014).
"""
import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FutureTimeout

import httpx
from sqlalchemy.orm import Session

from ..config import settings

logger = logging.getLogger(__name__)


class MathaleaUnavailable(Exception):
    pass


def _base_url() -> str:
    return settings.mathalea_url


def _mock_enabled(db: Session | None) -> bool:
    if db is None:
        return False
    from .runtime_settings import mock_enabled
    return mock_enabled(db)


# Même garde-fou que providers._post_with_deadline : le timeout httpx est par
# lecture socket, pas global — un service qui répond au compte-gouttes ou
# tarde à démarrer (cold start du process Node à la première requête) peut
# sinon bloquer le worker de génération indéfiniment (incident observé : job
# figé après "Copie 1/N", plus aucun log, worker mono-thread sans watchdog).
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mathalea-http")


def _with_deadline(fn, *args, **kwargs):
    total = settings.mathalea_call_timeout_s
    future = _POOL.submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=total)
    except _FutureTimeout:
        future.cancel()
        logger.warning("MathALÉA : aucune réponse complète après %ss — appel abandonné", total)
        raise MathaleaUnavailable(
            f"Service MathALÉA : pas de réponse après {total}s (appel abandonné)") from None


def _mock_generate(ref: str, seed: int) -> dict:
    """Exercice déterministe simulé — utilisé quand le mode mock est actif,
    pour ne jamais dépendre du service MathALÉA réel en dev/test (comme tous
    les autres fournisseurs, cf. providers._mock_enabled)."""
    h = int(hashlib.sha256(f"{ref}-{seed}".encode()).hexdigest(), 16)
    a, b = 2 + h % 20, 2 + (h // 20) % 20
    return {
        "statement": f"(mock MathALÉA {ref}) Calculer : ${a} + {b}$",
        "correction": f"${a} + {b} = {a + b}$",
        "expected": {"type": "integer", "value": a + b},
        "grading": {"max_score": 1, "comparator": "numeric", "tolerance": 0},
        "response_type": "short_text",
        "provider_version": "mock",
        "titre": ref,
    }


def health() -> dict | None:
    try:
        return _with_deadline(httpx.get, f"{_base_url()}/health", timeout=5).json()
    except Exception:
        return None


def catalog(grade: str | None = None) -> list[dict]:
    try:
        params = {"grade": grade} if grade else {}
        r = _with_deadline(httpx.get, f"{_base_url()}/catalog", params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except MathaleaUnavailable:
        raise
    except Exception as e:
        raise MathaleaUnavailable(f"Service MathALÉA injoignable : {e}")


def latex_to_text(s: str) -> str:
    """Nettoyage LaTeX -> texte imprimable par le gabarit reportlab."""
    s = re.sub(r"<br\s*/?>", "  ", s)
    s = re.sub(r"<[^>]+>", "", s)  # autres balises HTML résiduelles
    s = re.sub(r"\\begin\{[^}]*\}(\[[^\]]*\])?", " ", s)
    s = re.sub(r"\\end\{[^}]*\}", " ", s)
    s = s.replace("&", " ").replace("\\\\", "  ")
    s = s.replace("\\ldots", "……").replace("\\dots", "……")
    s = s.replace("\\times", "×").replace("\\div", "÷").replace("\\cdot", "·")
    s = s.replace("\\%", "%").replace("\\euro", "€").replace("^\\circ", "°")
    s = s.replace("\\degree", "°").replace("\\pi", "π")
    s = re.sub(r"\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}", r"\1/\2", s)
    s = re.sub(r"\\text(?:bf|it)?\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\num(?:print)?\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\(?:,|;|!|:|quad|qquad)", " ", s)
    s = s.replace("{,}", ",")
    s = s.replace("$", "")
    s = re.sub(r"\\[a-zA-Z]+", "", s)  # commandes restantes non gérées
    s = s.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", s).strip()


def latex_to_tagged(s: str) -> str:
    """Conversion MathALÉA -> texte balisé $...$ (contrat exgen-3).

    Les spans mathématiques sont CONSERVÉS en LaTeX (validés par mathrender)
    au lieu d'être aplatis en texte ; si un span n'est pas dans le
    sous-ensemble accepté, on retombe sur l'aplatissement complet."""
    from . import mathrender

    txt = re.sub(r"<br\s*/?>", "  ", s)
    txt = re.sub(r"<[^>]+>", "", txt)
    txt = re.sub(r"\\begin\{[^}]*\}(\[[^\]]*\])?", " ", txt)
    txt = re.sub(r"\\end\{[^}]*\}", " ", txt)
    txt = txt.replace("\\\\", "  ").replace("&", " ")

    parts = []
    for content, is_math in mathrender.split_math_spans(txt):
        if not is_math:
            # nettoyage des commandes LaTeX résiduelles hors math
            t = re.sub(r"\\text(?:bf|it)?\{([^{}]*)\}", r"\1", content)
            t = re.sub(r"\\num(?:print)?\{([^{}]*)\}", r"\1", t)
            t = t.replace(r"\ldots", "……").replace(r"\dots", "……")
            t = t.replace(r"\%", "%").replace(r"\euro", "€")
            if re.search(r"\\[a-zA-Z]+", t):
                return latex_to_text(s)  # commandes inconnues hors math : aplatir
            parts.append(t)
        else:
            content = re.sub(r"\\num(?:print)?\{([^{}]*)\}", r"\1", content)
            clean = mathrender.sanitize_latex(content)
            if clean is None:
                return latex_to_text(s)
            parts.append(f"${clean}$")
    return re.sub(r"[ \t]+", " ", "".join(parts)).strip()


def _expected_from_mathalea(exp: dict | None) -> tuple[dict, dict]:
    """Convertit autoCorrection MathALÉA -> (expected_json, grading_json)."""
    if not exp or not exp.get("values"):
        # pas de réponse structurée : validation obligatoire (§3.3)
        return ({"type": "text", "value": None},
                {"max_score": 1, "comparator": "manual"})
    v = exp["values"][0]
    if isinstance(v, dict) and "fraction" in v:
        n, d = v["fraction"]
        return ({"type": "rational", "value": [int(n), int(d)]},
                {"max_score": 1, "comparator": "rational_equiv"})
    if isinstance(v, (int, float)):
        if float(v).is_integer():
            return ({"type": "integer", "value": int(v)},
                    {"max_score": 1, "comparator": "numeric", "tolerance": 0})
        return ({"type": "decimal", "value": v},
                {"max_score": 1, "comparator": "numeric", "tolerance": 0})
    s = str(v)
    if re.fullmatch(r"-?\d+(?:[.,]\d+)?", s.strip()):
        num = float(s.replace(",", "."))
        if num.is_integer():
            return ({"type": "integer", "value": int(num)},
                    {"max_score": 1, "comparator": "numeric", "tolerance": 0})
        return ({"type": "decimal", "value": num},
                {"max_score": 1, "comparator": "numeric", "tolerance": 0})
    return ({"type": "text", "value": s},
            {"max_score": 1, "comparator": "text_equal"})


def generate(ref: str, seed: int, nb_questions: int = 1, *,
            db: Session | None = None) -> dict:
    """Génère un exercice. Retourne le contrat interne :
    {statement, correction, expected, grading, response_type, version}.
    En mode mock (db fourni), ne contacte jamais le service réel."""
    if _mock_enabled(db):
        return _mock_generate(ref, seed)
    try:
        r = _with_deadline(httpx.post, f"{_base_url()}/generate",
                           json={"ref": ref, "seed": seed, "nbQuestions": nb_questions},
                           timeout=30)
        r.raise_for_status()
        data = r.json()
    except MathaleaUnavailable:
        raise
    except httpx.HTTPStatusError as e:
        raise MathaleaUnavailable(f"Génération {ref} en échec : {e.response.text[:200]}")
    except Exception as e:
        raise MathaleaUnavailable(f"Service MathALÉA injoignable : {e}")
    if "error" in data:
        raise MathaleaUnavailable(f"Génération {ref} : {data['error']}")

    questions = data.get("questions") or []
    corrections = data.get("corrections") or []
    expecteds = data.get("expected") or []
    if not questions:
        raise MathaleaUnavailable(f"{ref} : aucune question générée")

    consigne = latex_to_tagged(data.get("consigne") or "")
    statement = latex_to_tagged(questions[0])
    if consigne and consigne not in statement:
        statement = f"{consigne} {statement}"
    correction = latex_to_tagged(corrections[0]) if corrections else ""
    expected, grading = _expected_from_mathalea(expecteds[0] if expecteds else None)

    return {
        "statement": statement,
        "correction": correction,
        "expected": expected,
        "grading": grading,
        "response_type": "short_text" if grading["comparator"] != "manual" else "multiline_text",
        "provider_version": data.get("mathaleaVersion", "?"),
        "titre": data.get("titre", ref),
    }
