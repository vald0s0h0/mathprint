"""Tests : filtre CV (dropout, QCM, seuils adaptatifs)."""
import numpy as np


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


# --- Blanchiment du fond (photos iPhone : papier non blanc, éclairage inégal) ---

def test_flatten_background_lifts_dark_paper_keeps_ink():
    """Papier gris sombre (photo mal éclairée) : le fond monte au blanc — donc
    une zone vide cesse d'être comptée comme encre — et une coche reste sombre."""
    from app.services import worker_cv as W
    img = np.full((300, 300, 3), 120, dtype=np.uint8)   # papier gris, pas blanc
    img[140:160, 140:160] = 20                          # coche saturée
    out = W.flatten_background(img)
    assert out[10:40, 10:40].mean() > 230               # papier -> quasi blanc
    assert out[150, 150].mean() < 90                    # la coche reste sombre
    assert W.ink_ratio(img[10:60, 10:60]) > 0.9          # avant : tout < 128 = « encre »
    assert W.ink_ratio(out[10:60, 10:60]) < 0.05         # après : zone vide ~ 0


def test_flatten_background_flattens_illumination_gradient():
    """Dégradé d'éclairage (sombre à gauche, clair à droite) aplani des deux côtés."""
    from app.services import worker_cv as W
    grad = np.linspace(90, 200, 320, dtype=np.float32)
    img = np.repeat(grad[None, :], 320, axis=0)
    img = np.stack([img, img, img], axis=-1).astype(np.uint8)
    out = W.flatten_background(img)
    assert out[:, 30:50].mean() > 220
    assert out[:, -50:-30].mean() > 220


def test_flatten_background_preserves_white_page_and_mark():
    """Idempotence : une page déjà blanche n'est pas abîmée, la marque survit."""
    from app.services import worker_cv as W
    img = np.full((300, 300, 3), 255, dtype=np.uint8)
    img[140:160, 140:160] = 25
    out = W.flatten_background(img)
    assert out[10:40, 10:40].mean() > 245
    assert out[150, 150].mean() < 80


# --- Seuil QCM adaptatif par page ---

def test_adapt_qcm_threshold_splits_two_clear_groups():
    from app.services.worker_cv import adapt_qcm_threshold
    thr = adapt_qcm_threshold([0.001, 0.002, 0.15, 0.16, 0.5])
    assert thr.adapted
    assert 0.002 < thr.value < 0.15


def test_cv_confidence_is_high_only_outside_ambiguity_band():
    from app.services.worker_cv import (adapt_qcm_threshold,
                                        blank_decision_confidence,
                                        qcm_decision_confidence)
    clear = [0.001, 0.002, 0.15, 0.16]
    thr = adapt_qcm_threshold(clear)
    assert qcm_decision_confidence(clear, thr) >= 0.90

    ambiguous = [0.02, 0.03, 0.04]
    thr2 = adapt_qcm_threshold(ambiguous)
    assert qcm_decision_confidence(ambiguous, thr2) < 0.90

    assert blank_decision_confidence(0.0005) >= 0.90
    assert blank_decision_confidence(0.0025) < 0.90


def test_adapt_qcm_threshold_adapts_to_faint_checks():
    """Coches fines (~0,024) sous le seuil par défaut mais nettement au-dessus des
    cases vides (~0,002) : le seuil de page DESCEND pour les capter."""
    from app.services.worker_cv import adapt_qcm_threshold, QCM_THRESHOLD
    thr = adapt_qcm_threshold([0.001, 0.002, 0.003, 0.024, 0.026, 0.028])
    assert thr.adapted
    assert 0.003 < thr.value < QCM_THRESHOLD


def test_adapt_qcm_threshold_all_empty_keeps_default_no_review():
    from app.services.worker_cv import adapt_qcm_threshold, select_qcm, QCM_THRESHOLD
    dens = [0.001, 0.002, 0.003, 0.004]
    thr = adapt_qcm_threshold(dens)
    assert not thr.adapted and thr.value == QCM_THRESHOLD
    boxes = [{"index": i} for i in range(4)]
    selected, _, _ = select_qcm(boxes, dens, thr)
    assert selected == []                                # tout vide, pas de revue


def test_adapt_qcm_threshold_all_checked_keeps_default():
    from app.services.worker_cv import adapt_qcm_threshold, QCM_THRESHOLD
    thr = adapt_qcm_threshold([0.12, 0.15, 0.2, 0.5])
    assert not thr.adapted and thr.value == QCM_THRESHOLD


def test_select_qcm_poorly_isolated_goes_to_manual_review():
    """Continuum enjambant le seuil, sans coupure nette -> bande large -> None."""
    from app.services.worker_cv import adapt_qcm_threshold, select_qcm
    dens = [0.02, 0.03, 0.04, 0.05]
    thr = adapt_qcm_threshold(dens)
    assert not thr.adapted
    boxes = [{"index": i} for i in range(4)]
    selected, _, _ = select_qcm(boxes, dens, thr)
    assert selected is None


def test_select_qcm_adaptive_overrides_default():
    from app.services.worker_cv import adapt_qcm_threshold, select_qcm
    dens = [0.001, 0.002, 0.003, 0.024, 0.026, 0.028]
    thr = adapt_qcm_threshold(dens)
    boxes = [{"index": i} for i in range(6)]
    selected, _, default_sel = select_qcm(boxes, dens, thr)
    assert default_sel == []                             # le défaut rate les coches fines
    assert selected == [3, 4, 5]                          # l'adaptatif les prend
