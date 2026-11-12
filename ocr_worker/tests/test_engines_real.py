"""Test de fumée RÉEL de chaque moteur sur 5 découpes du banc (lot 0).

Désactivé par défaut (modèles de plusieurs Go) :
    OCR_REAL=1 HF_HUB_OFFLINE=1 ocr_worker/.venv/bin/python -m pytest ocr_worker/tests/test_engines_real.py -s
Il vérifie le contrat (lecture non vide, scores, déchargement), pas l'exactitude.
"""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.skipif(os.environ.get("OCR_REAL") != "1", reason="OCR_REAL=1 requis")

from ocr_worker.engines import build_engine
from ocr_worker.engines.base import Crop, device_gb, rss_gb

BENCH = ROOT / "data" / "ocr_bench"


def _crops(n=5):
    from PIL import Image
    manifest = json.loads((BENCH / "manifest.json").read_text())
    items = [m for m in manifest if m["src"] == "synth" and m["zone_class"] == "num_line"][:n]
    return items, [Crop(image=Image.open(m["path"]), zone_class="num_line", crop_id=f"c{i}")
                   for i, m in enumerate(items)]


@pytest.mark.parametrize("name", ["apple_vision", "trocr", "unimumer", "lighton"])
def test_engine_smoke(name):
    eng = build_engine(name, {"impl": name, "backend": "mps"}, "mps")
    if eng is None:
        pytest.skip("indisponible ici")
    items, crops = _crops()
    eng.load()
    try:
        reads = eng.read(crops)
        assert len(reads) == 5
        for m, r in zip(items, reads):
            print(f"{name}: vérité={m['truth']!r} lu={r.raw_text!r} lp={r.logprob_mean} {r.latency_ms:.0f} ms")
            assert isinstance(r.raw_text, str) and r.latency_ms is not None
        assert sum(1 for r in reads if r.raw_text.strip()) >= 3, "moins de 3 lectures non vides sur 5"
        if eng.supports_score:
            truth = items[0]["truth"]
            sc = eng.score(crops[0], [truth, "999999"])
            print(f"{name}: scores {truth!r} -> {sc}")
            assert len(sc) == 2 and all(x <= 0 for x in sc) and sc[0] > sc[1]
        else:
            assert eng.score(crops[0], ["1"]) is None
        d = eng.describe()
        assert d["family"] and d["backend"]
        print(f"{name}: RSS {rss_gb():.2f} Go, appareil {device_gb():.2f} Go, {d}")
    finally:
        eng.unload()


def test_finetuned_model_directory_loads_as_a_trocr_ft_engine():
    """Sortie de `finetune_trocr.py` (safetensors local) chargée par le moteur sous un nom distinct."""
    model_dir = ROOT / "data" / "trocr_ft_smoke" / "model"
    if not model_dir.exists():
        pytest.skip("lancer d'abord finetune_trocr.py --smoke")
    eng = build_engine("trocr_ft", {"impl": "trocr", "model": str(model_dir), "dtype": "fp16", "backend": "mps"}, "mps")
    items, crops = _crops(3)
    eng.load()
    try:
        reads = eng.read(crops)
        assert eng.name == "trocr_ft" and eng.family == "encdec" and all(isinstance(r.raw_text, str) for r in reads)
        print([ (m["truth"], r.raw_text) for m, r in zip(items, reads)])
    finally:
        eng.unload()
