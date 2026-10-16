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


def _matching_page(lines, n=4):
    """Page blanche + pastilles 4×4 à la géométrie réelle d'une copie 6e
    (pastilles 6,2 pt, colonnes à 46 pt, lignes à 18,4 pt) ; `lines` = traits
    ((i, j), (dx1, dy1), (dx2, dy2)) tracés de la pastille gauche i (+ décalage
    px) à la droite j (+ décalage px)."""
    import cv2
    from app.services import worker_cv as W
    from app.services.pdfgen import PAGE_H

    img = np.full((int(PAGE_H * W.SCALE), 1700, 3), 255, dtype=np.uint8)

    def dot(x_pt, y_pt):
        return {"x_pt": x_pt, "y_pt": y_pt, "w_pt": 6.24, "h_pt": 6.24}

    left = [dot(393.0, 557.0 - 18.4 * k) for k in range(n)]
    right = [dot(438.9, 557.0 - 18.4 * k) for k in range(n)]

    def c(p):
        return W.pt_to_px(p["x_pt"] + p["w_pt"] / 2, p["y_pt"] + p["h_pt"] / 2)
    for (i, j), (ax, ay), (bx, by) in lines:
        (lx, ly), (rx, ry) = c(left[i]), c(right[j])
        cv2.line(img, (int(lx + ax), int(ly + ay)), (int(rx + bx), int(ry + by)),
                 (40, 30, 20), 3)
    return img, left, right


def test_detect_matching_reads_a_straight_drawn_line():
    """Régression OpenCV 5 (HoughLinesP en (N, 4)) : la lecture plantait
    (« cannot unpack non-iterable numpy.int32 ») sur tout exercice « relier »."""
    from app.services import worker_cv as W

    img, left, right = _matching_page([((0, 1), (0, 0), (0, 0))], n=2)
    pairs, conf = W.detect_matching(img, left, right)
    assert pairs == [[0, 1]] and conf >= 0.9


def test_detect_matching_crossing_lines_drawn_off_the_dots():
    """Cas réels : traits qui se croisent, partent à côté de la pastille ou la
    dépassent — l'ancienne heuristique (extrémités près des pastilles) les
    envoyait presque tous en revue."""
    from app.services import worker_cv as W

    img, left, right = _matching_page([
        ((0, 1), (6, -5), (12, 4)),      # arrive en dépassant la pastille
        ((1, 3), (8, 6), (-14, -3)),     # s'arrête avant la pastille
        ((2, 0), (5, 4), (4, -6)),
        ((3, 2), (0, 0), (0, 0)),
    ])
    pairs, conf = W.detect_matching(img, left, right)
    assert sorted(pairs) == [[0, 1], [1, 3], [2, 0], [3, 2]]
    assert conf >= 0.9


def test_detect_matching_blank_answer_is_read_with_confidence():
    from app.services import worker_cv as W

    img, left, right = _matching_page([])
    assert W.detect_matching(img, left, right) == ([], 0.97)


def test_detect_matching_two_partners_for_one_dot_goes_to_review():
    """L'élève relie la même pastille à deux réponses : jamais un choix deviné."""
    from app.services import worker_cv as W

    img, left, right = _matching_page([
        ((0, 1), (0, 0), (0, 0)), ((0, 3), (0, 0), (0, 0)),
        ((2, 0), (0, 0), (0, 0)), ((3, 2), (0, 0), (0, 0)),
    ])
    _pairs, conf = W.detect_matching(img, left, right)
    assert conf < 0.9


def _qcm_page(marks, outline=175):
    """3 cases QCM à la géométrie réelle ; `marks` = {index: (couleur BGR, forme)}
    avec forme 'x' (croix) ou 'fill' (case noircie) ; `outline` = gris du
    contour imprimé resté visible (saumon restitué sans couleur)."""
    import cv2
    from app.services import worker_cv as W
    from app.services.pdfgen import PAGE_H

    img = np.full((int(PAGE_H * W.SCALE), 1700, 3), 255, dtype=np.uint8)
    boxes = []
    for k in range(3):
        b = {"index": k, "x_pt": 44.2 + 110 * k, "y_pt": 270.0, "w_pt": 5.67, "h_pt": 5.67,
             "detect": {"x_pt": 41.4 + 110 * k, "y_pt": 267.1, "w_pt": 9.92, "h_pt": 11.34}}
        boxes.append(b)
        x0, y0 = W.pt_to_px(b["x_pt"], b["y_pt"] + b["h_pt"])
        x1, y1 = W.pt_to_px(b["x_pt"] + b["w_pt"], b["y_pt"])
        cv2.rectangle(img, (int(x0), int(y0)), (int(x1), int(y1)), (outline,) * 3, 1)
        if k in marks:
            color, shape = marks[k]
            if shape == "x":
                cv2.line(img, (int(x0) - 2, int(y0) - 2), (int(x1) + 2, int(y1) + 2), color, 2)
                cv2.line(img, (int(x0) - 2, int(y1) + 2), (int(x1) + 2, int(y0) - 2), color, 2)
            else:
                cv2.rectangle(img, (int(x0), int(y0)), (int(x1), int(y1)), color, -1)
    return img, boxes


def test_second_pass_reads_a_light_blue_cross():
    """Croix au stylo BLEU CLAIR (gris ~170) : invisible pour l'encre sombre de
    la 1re passe, lue par la 2e passe (filtre bleu ET contour exclu concordent)."""
    from app.services import worker_cv as W

    img, boxes = _qcm_page({1: ((220, 160, 100), "x")})
    first = W.qcm_densities(img, boxes)
    assert first[1] < W.QCM_THRESHOLD                       # 1re passe aveugle
    dc, dr = W.qcm_densities_color(img, boxes), W.qcm_densities_ring(img, boxes)
    sel = W.second_pass_selection(boxes, False, dc, W.adapt_qcm_threshold(dc),
                                  dr, W.adapt_qcm_threshold(dr))
    assert sel == [1]


def test_second_pass_confirms_blank_with_light_outline():
    from app.services import worker_cv as W

    img, boxes = _qcm_page({})
    dc, dr = W.qcm_densities_color(img, boxes), W.qcm_densities_ring(img, boxes)
    sel = W.second_pass_selection(boxes, False, dc, W.adapt_qcm_threshold(dc),
                                  dr, W.adapt_qcm_threshold(dr))
    assert sel == []


def test_second_pass_disagreement_on_dark_printed_outline_goes_to_review():
    """Contour imprimé resté gris FONCÉ : l'encre sombre croit toutes les cases
    cochées, la mesure « contour exclu » n'en voit aucune — désaccord, donc
    aucune lecture devinée (None -> OCRiser)."""
    from app.services import worker_cv as W

    img, boxes = _qcm_page({}, outline=110)
    dc, dr = W.qcm_densities_color(img, boxes), W.qcm_densities_ring(img, boxes)
    sel = W.second_pass_selection(boxes, False, dc, W.adapt_qcm_threshold(dc),
                                  dr, W.adapt_qcm_threshold(dr))
    assert sel is None
