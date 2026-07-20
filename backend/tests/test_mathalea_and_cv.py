"""Tests : conversion MathALÉA -> contrat interne, nettoyage LaTeX, filtre CV."""
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models as _models  # noqa: F401 (enregistre toutes les tables sur Base)
from app.db import Base
from app.services import mathalea_client
from app.services.mathalea_client import MathaleaUnavailable, _expected_from_mathalea, latex_to_text
from app.services.grading import grade


@pytest.fixture
def mock_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_generate_mock_mode_never_hits_network(mock_db):
    # settings.mock_mode par défaut = True et aucune ProviderConfig en base
    # -> mode mock actif ; ne doit jamais tenter de vraie requête HTTP
    data = mathalea_client.generate("builtin:x", seed=1, db=mock_db)
    assert data["provider_version"] == "mock"
    assert data["grading"]["comparator"] == "numeric"


def test_generate_without_db_param_uses_real_path_not_mock(monkeypatch):
    # generate() sans `db` (comme avant le correctif) ne doit PAS basculer
    # silencieusement en mock — seul un `db` explicite active le mock
    called = {}

    def fake_with_deadline(fn, *a, **kw):
        called["hit"] = True
        raise MathaleaUnavailable("simulé : service injoignable")

    monkeypatch.setattr(mathalea_client, "_with_deadline", fake_with_deadline)
    with pytest.raises(MathaleaUnavailable):
        mathalea_client.generate("builtin:x", seed=1)
    assert called.get("hit")


def test_with_deadline_gives_up_after_configured_timeout(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "mathalea_call_timeout_s", 0.2)

    def hangs_forever(*a, **kw):
        time.sleep(10)

    started = time.monotonic()
    with pytest.raises(MathaleaUnavailable):
        mathalea_client._with_deadline(hangs_forever)
    # doit abandonner après ~0.2s, jamais attendre les 10s de l'appel simulé
    assert time.monotonic() - started < 2.0


def test_latex_cleanup():
    assert latex_to_text(r"$3 + \ldots = 7$") == "3 + …… = 7"
    assert latex_to_text(r"$\dfrac{2}{5} \times 10$") == "2/5 × 10"
    assert latex_to_text(r"Calcul<br>suivant : $5{,}5$") == "Calcul suivant : 5,5"
    assert "begin" not in latex_to_text(r"\begin{array}{|c|c|}1 & 2\end{array}")


def test_expected_mapping_number():
    exp, grad = _expected_from_mathalea({"format": "calcul", "values": [5]})
    assert exp == {"type": "integer", "value": 5}
    r = grade(exp, grad, "5", 0.95)
    assert r["score"] == 1


def test_expected_mapping_decimal_string():
    exp, grad = _expected_from_mathalea({"format": "calcul", "values": ["5,6"]})
    assert exp["type"] == "decimal"
    r = grade(exp, grad, "5,6", 0.95)
    assert r["score"] == 1


def test_expected_mapping_fraction():
    exp, grad = _expected_from_mathalea({"format": "fraction", "values": [{"fraction": [3, 4]}]})
    r = grade(exp, grad, "6/8", 0.9)   # équivalence, pas égalité de chaînes
    assert r["score"] == 1


def test_expected_mapping_text_expression():
    exp, grad = _expected_from_mathalea({"format": "calcul", "values": ["2\\times3\\times5"]})
    r = grade(exp, grad, "2×3×5", 0.9)
    assert r["score"] == 1  # normalisation commune \times/× -> *


def test_expected_missing_goes_manual():
    exp, grad = _expected_from_mathalea(None)
    r = grade(exp, grad, "n'importe quoi", 0.9)
    assert r["tier"] == "D"


def test_dropout_preserves_blue_ink_removes_salmon():
    from app.services.worker_cv import dropout_filter, ink_ratio
    img = np.full((60, 60, 3), 255, dtype=np.uint8)
    img[10:20, 10:50] = (196, 183, 245)   # BGR du rouge saumon #F5B7A8 (cadre)
    img[35:45, 10:50] = (120, 40, 20)     # encre bleu foncé (élève)
    out = dropout_filter(img)
    assert (out[15, 30] == 255).all(), "le cadre saumon doit être supprimé"
    assert (out[40, 30] != 255).any(), "l'encre bleue doit être conservée"
    assert ink_ratio(out) > 0.05


def _qcm_box(idx, x_pt, y_pt, *, detect=True):
    from app.services.pdfgen import QCM_BOX, QCM_DETECT_MARGIN
    b = {"index": idx, "x_pt": x_pt, "y_pt": y_pt, "w_pt": QCM_BOX, "h_pt": QCM_BOX}
    if detect:
        dm = QCM_DETECT_MARGIN
        b["detect"] = {"x_pt": x_pt - dm, "y_pt": y_pt - dm,
                       "w_pt": QCM_BOX + 2 * dm, "h_pt": QCM_BOX + 2 * dm}
    return b


def _paint_mark(img, x_pt, y_pt, half=10):
    """Coche bleue foncée centrée sur la case (déborde volontairement la petite
    case de 2 mm mais reste dans la fenêtre de détection élargie)."""
    from app.services import worker_cv as W
    from app.services.pdfgen import QCM_BOX
    cx, cy = W.pt_to_px(x_pt + QCM_BOX / 2, y_pt + QCM_BOX / 2)
    cx, cy = int(cx), int(cy)
    img[cy - half:cy + half, cx - half:cx + half] = (120, 40, 20)


def test_detect_qcm_reads_overflowing_mark_in_enlarged_window():
    """La coche qui déborde la case (2 mm) est captée par la fenêtre élargie ;
    une case vide reste non sélectionnée."""
    from app.services import worker_cv as W
    img = np.full((500, 500, 3), 255, dtype=np.uint8)
    marked = _qcm_box(0, 100, 780)
    empty = _qcm_box(1, 100, 760)
    _paint_mark(img, 100, 780)
    selected, densities = W.detect_qcm(img, [marked, empty])
    assert selected == [0], (selected, densities)
    assert densities[0] > densities[1]


def test_detect_qcm_fallback_window_when_meta_lacks_detect():
    """Copies imprimées avant l'évolution : la méta ne porte pas `detect` et la
    case stockée était dégénérée (largeur négative) ; le centre reste correct,
    donc la fenêtre reconstruite capte quand même la coche."""
    from app.services import worker_cv as W
    from app.services.pdfgen import QCM_BOX
    img = np.full((500, 500, 3), 255, dtype=np.uint8)
    inner = 1.1 / 25.4 * 72  # ancienne marge intérieure (pt)
    # ancienne géométrie dégénérée : x/y décalés, largeur négative, PAS de "detect"
    old = {"index": 0, "x_pt": 100 + inner, "y_pt": 780 + inner,
           "w_pt": QCM_BOX - 2 * inner, "h_pt": QCM_BOX - 2 * inner}
    _paint_mark(img, 100, 780)   # coche centrée sur la vraie case
    selected, _ = W.detect_qcm(img, [old])
    assert selected == [0]
