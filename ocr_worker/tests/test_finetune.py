"""Logique de l'affinage de TrOCR (sans modèle) : métriques, décision de promotion, garde-fous des données."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("finetune", Path(__file__).resolve().parents[1] / "scripts" / "finetune_trocr.py")
ft = importlib.util.module_from_spec(spec); spec.loader.exec_module(ft)


def test_cer_and_comma_loss():
    assert ft.cer("179", "1,79") == pytest.approx(0.25) and ft.cer("1,79", "1,79") == 0.0
    assert ft.cer("", "") == 0.0 and ft.cer("x", "") == 1.0
    assert ft.comma_lost("179", "1,79") and not ft.comma_lost("1,79", "1,79")
    assert not ft.comma_lost("18", "1,79")            # d'autres chiffres : une autre faute, pas une virgule perdue


def test_metrics_count_commas():
    m = ft.metrics(["179", "24", "3,5"], ["1,79", "24", "3,5"])
    assert m["n"] == 3 and m["exact"] == pytest.approx(2 / 3, abs=1e-3)
    assert m["comma_cases"] == 2 and m["comma_lost_rate"] == 0.5


BASE = {"n": 300, "exact": 0.80, "cer": 0.10, "comma_lost_rate": 0.40}


def test_promotion_requires_better_everywhere_and_enough_data():
    good = {"n": 300, "exact": 0.90, "cer": 0.05, "comma_lost_rate": 0.05}
    assert ft.promotion(BASE, good, min_eval=200, smoke=False)["verdict"] == "promote"
    assert ft.promotion(BASE, {**good, "n": 50}, min_eval=200, smoke=False)["verdict"] == "not_promoted"
    assert ft.promotion(BASE, {**good, "comma_lost_rate": 0.5}, min_eval=200, smoke=False)["verdict"] == "not_promoted"
    assert ft.promotion(BASE, {**good, "exact": 0.7}, min_eval=200, smoke=False)["verdict"] == "not_promoted"
    assert ft.promotion(BASE, good, min_eval=200, smoke=True)["verdict"] == "smoke_only"   # le test de boucle ne promeut jamais


def _dataset(tmp_path, sources):
    rows = []
    for i, src in enumerate(sources):
        (tmp_path / f"i{i}.png").write_bytes(b"x")
        rows.append({"image": f"i{i}.png", "text": "1,79", "zone_class": "num_line", "group": f"g{i}",
                     "split": "train" if i % 2 else "eval", "truth_source": src})
    (tmp_path / "metadata.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    return tmp_path


def test_only_teacher_truth_is_accepted_for_training(tmp_path):
    tr, ev = ft.load_rows(_dataset(tmp_path, ["teacher_ocr", "audit", "audit", "teacher_ocr"]), None, smoke=False)
    assert len(tr) == 2 and len(ev) == 2
    with pytest.raises(SystemExit, match="sources refusées"):
        ft.load_rows(_dataset(tmp_path, ["teacher_ocr", "engine_consensus"]), None, smoke=False)
    with pytest.raises(SystemExit):                     # le jeu synthétique n'entraîne rien hors --smoke
        ft.load_rows(_dataset(tmp_path, ["synthetic", "synthetic"]), None, smoke=False)
    ft.load_rows(_dataset(tmp_path, ["synthetic", "synthetic"]), None, smoke=True)


def test_commas_fractions_and_powers_are_oversampled():
    w = ft.sample_weights([{"text": "1,79"}, {"text": "24"}, {"text": "3/6"}, {"text": "x^2"}])
    assert w == [3.0, 1.0, 3.0, 3.0]
