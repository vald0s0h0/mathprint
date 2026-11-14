"""Musée des défauts (agents/forge/musee) : chaque défaut déjà rencontré est rejoué.
Un contrôle qui ne le reconnaît plus fait échouer la suite."""
import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.services import answer_mix, tikzfig

MUSEE = Path(__file__).resolve().parents[2] / "agents" / "forge" / "musee"
CASES = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(MUSEE.glob("*.json"))]
HAS_TEX = tikzfig.tectonic_binary() is not None


def test_the_museum_is_not_empty_and_every_case_is_well_formed():
    assert len(CASES) >= 4
    for case in CASES:
        assert case["nom"] and case["constat"] and case["type"] in ("figure", "melange")
        assert "attendu" in case


@pytest.mark.parametrize("case", [c for c in CASES if c["type"] == "figure"], ids=lambda c: c["nom"])
def test_figure_defects_are_still_caught(case, tmp_path, monkeypatch):
    if not HAS_TEX:
        pytest.skip("Tectonic absent (seul le Mac compile)")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(tikzfig, "STORE_DIR", tmp_path / "tikz")
    problems = tikzfig.inspect({"tikz": case["tikz"]})
    for fragment in case["attendu"]:
        assert any(fragment in p for p in problems), (fragment, problems)


@pytest.mark.parametrize("case", [c for c in CASES if c["type"] == "melange"], ids=lambda c: c["nom"])
def test_mixing_defects_are_still_caught(case):
    shape = case["shape"]
    for seed in range(12):
        mixed = answer_mix.mix_shape(shape, random.Random(seed), 250, 9)
        assert mixed["grading"] == shape["grading"] and mixed["expected"] == shape["expected"]
