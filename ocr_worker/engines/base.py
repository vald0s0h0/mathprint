"""Interface commune des moteurs OCR du worker.

Règles (cahier des charges) : décodage glouton et déterministe, aucune correction
linguistique, texte brut renvoyé intact. Un moteur ne reçoit jamais la valeur
attendue : seulement l'image, la classe de zone et l'alphabet qui en découle.
"""
import gc
import re
from dataclasses import dataclass, field

# Alphabet d'une zone numérique : chiffres, virgule, point, moins (- et −), barre
# de fraction, parenthèses et espace. Le serveur peut en envoyer un autre.
NUM_CHARSET = "0123456789,.-−/() "

# Bornes de génération par classe (tokens) : une ligne ne demande pas plus.
MAX_NEW_TOKENS = {"num_line": 24, "math_line": 64, "text_line": 64, "multiline_line": 96}

# Sorties qui ne sont pas une transcription : description d'image, bloc de code,
# marqueurs de case. Jamais réécrites (un `□` n'est pas un `×`) : la lecture est
# marquée illisible et ne vote pas.
_SUSPECT = re.compile(r"<figure|```|\\boxed|\\square|\\bullet|□|\[ILLISIBLE\]|\$\$", re.IGNORECASE)


def looks_suspect(raw: str) -> bool:
    return bool(_SUSPECT.search(raw or ""))


@dataclass
class Crop:
    image: object                      # PIL.Image.Image
    zone_class: str = "num_line"
    charset: str = ""
    crop_id: str = ""
    sha256: str = ""


@dataclass
class Read:
    raw_text: str = ""
    illegible: bool = False
    confidence_raw: float | None = None
    logprob_mean: float | None = None
    logprob_min: float | None = None
    alternatives: list = field(default_factory=list)
    latency_ms: float | None = None


class Engine:
    name: str = ""
    family: str = ""
    version: str = "0"
    weights: str = ""
    backend: str = ""
    device: str = ""
    supports_score: bool = False

    def load(self) -> None: ...

    def unload(self) -> None:
        """Libère la mémoire : del + gc + torch.mps.empty_cache()."""
        free_memory()

    def read(self, crops: list[Crop]) -> list[Read]:
        raise NotImplementedError

    def score(self, crop: Crop, candidates: list[str]) -> list[float] | None:
        """Log-probabilité forcée de chaque candidat (forme brute), ou None si le
        moteur ne sait pas la calculer. Ne crée jamais de candidat."""
        return None

    def describe(self) -> dict:
        return {"engine_version": self.version, "weights": self.weights, "backend": self.backend,
                "device": self.device, "family": self.family}


def ensure_device(device: str) -> None:
    """Refuse un appareil indisponible au lieu de retomber en silence sur le processeur : une lecture
    étiquetée `cuda` ou `mps` qui aurait tourné ailleurs fausserait la comparaison entre machines."""
    import torch
    ok = {"mps": lambda: torch.backends.mps.is_available(), "cuda": lambda: torch.cuda.is_available(),
          "cpu": lambda: True}
    if device not in ok:
        raise RuntimeError(f"appareil inconnu : {device!r} (mps, cuda ou cpu)")
    if not ok[device]():
        raise RuntimeError(f"appareil {device!r} demandé mais indisponible sur cette machine")


def set_determinism(seed: int = 0) -> None:
    try:
        import torch
    except ImportError:
        return
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def free_memory() -> None:
    gc.collect()
    try:
        import torch
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def rss_gb() -> float:
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1e9
    except Exception:
        return 0.0


def device_gb() -> float:
    try:
        import torch
        if torch.backends.mps.is_available():
            return torch.mps.driver_allocated_memory() / 1e9
        if torch.cuda.is_available():
            return torch.cuda.memory_allocated() / 1e9
    except Exception:
        pass
    return 0.0


def logprob_stats(scores) -> tuple[float | None, float | None]:
    vals = [float(x) for x in scores]
    return (sum(vals) / len(vals), min(vals)) if vals else (None, None)
