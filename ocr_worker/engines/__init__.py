"""Fabrique des moteurs. Les imports lourds (torch, transformers) restent dans les modules des moteurs."""
from .base import Crop, Engine, Read  # noqa: F401

FAMILIES = {"trocr": "encdec", "apple_vision": "apple", "unimumer": "qwen35", "lighton": "pixtral_qwen3"}


def build_engine(name: str, cfg: dict, device: str = "mps") -> Engine | None:
    """Instancie le moteur `name` selon `cfg['impl']`, ou None s'il n'est pas disponible."""
    impl = cfg.get("impl", name)
    cfg = {"backend": device, **cfg}
    if impl == "fake":
        from .fake import FakeEngine
        return FakeEngine(name, cfg.get("family", FAMILIES.get(name, name)), cfg.get("table"),
                          supports_score=cfg.get("supports_score", True))
    if impl == "apple_vision":
        from . import apple_vision
        if not apple_vision.available():
            return None                       # hors macOS : désactivé automatiquement
        eng = apple_vision.AppleVisionEngine(cfg)
    elif impl == "trocr":
        from .trocr import TrocrEngine
        eng = TrocrEngine(cfg)
    elif impl == "unimumer":
        from .unimumer import UnimumerEngine
        eng = UnimumerEngine(cfg)
    elif impl == "lighton":
        from .lighton import LightonEngine
        eng = LightonEngine(cfg)
    else:
        raise ValueError(f"moteur inconnu : {impl!r}")
    eng.name = name
    return eng
