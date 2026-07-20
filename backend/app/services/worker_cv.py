"""Worker vision par ordinateur (§6.1-§6.2, phase 1).

Raster PDF -> détection du QR principal (identité, HMAC) + des 3 fiduciels
AprilTag de coin (placement) -> homographie vers le référentiel A4 canonique
-> crops des zones -> filtre dropout (suppression du rouge saumon) ->
détection QCM et vide.

Le scan original n'est jamais modifié (RM-002) ; tous les dérivés sont
reproductibles. Une page non identifiée est bloquée, jamais devinée (RM-001).
"""
import io
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium

from .pdfgen import (FIDUCIAL_DICT, FIDUCIAL_IDS, MARGIN, PAGE_H, PAGE_W,
                     QCM_BOX, QCM_DETECT_MARGIN, QCM_DETECT_MARGIN_R, QR_MAIN, QR_MINI)
from .security import verify_page_payload

_FIDUCIAL_ROLE_BY_ID = {v: k for k, v in FIDUCIAL_IDS.items()}
_ARUCO_DETECTOR = cv2.aruco.ArucoDetector(FIDUCIAL_DICT, cv2.aruco.DetectorParameters())

DPI = 200
SCALE = DPI / 72.0  # points PDF -> pixels

# centres canoniques des 4 marqueurs, en points PDF (origine bas-gauche)
CANONICAL_CENTERS_PT = {
    "MAIN": (PAGE_W - MARGIN - QR_MAIN / 2, PAGE_H - MARGIN - QR_MAIN / 2),
    "TL": (MARGIN + QR_MINI / 2, PAGE_H - MARGIN - QR_MINI / 2),
    "BL": (MARGIN + QR_MINI / 2, MARGIN + QR_MINI / 2),
    "BR": (PAGE_W - MARGIN - QR_MINI / 2, MARGIN + QR_MINI / 2),
}


def pt_to_px(x_pt: float, y_pt: float) -> tuple[float, float]:
    """Points PDF (origine bas-gauche) -> pixels image (origine haut-gauche)."""
    return x_pt * SCALE, (PAGE_H - y_pt) * SCALE


@dataclass
class PageAnalysis:
    page_id: str | None = None
    status: str = "blocked"          # identified | registered | blocked
    marker_count: int = 0
    reprojection_error_px: float = -1.0
    blur: float = 0.0
    warped: np.ndarray | None = None
    warnings: list[str] = field(default_factory=list)


def raster_pdf(path: str, dpi: int = DPI) -> list[np.ndarray]:
    """Rasterise chaque page du PDF en BGR. Page par page (mémoire NAS, §11.2)."""
    doc = pdfium.PdfDocument(path)
    pages = []
    try:
        for i in range(len(doc)):
            # rev_byteorder=True force un rendu RGB(A) quel que soit le défaut natif
            bitmap = doc[i].render(scale=dpi / 72.0, rev_byteorder=True)
            arr = bitmap.to_numpy()
            code = cv2.COLOR_RGBA2BGR if arr.shape[2] == 4 else cv2.COLOR_RGB2BGR
            pages.append(cv2.cvtColor(arr, code))
    finally:
        doc.close()
    return pages


_HEIF_REGISTERED = False


def _ensure_heif_opener():
    """Enregistre le décodeur HEIC/HEIF de Pillow (bac à sable, §5b) une seule fois."""
    global _HEIF_REGISTERED
    if _HEIF_REGISTERED:
        return
    import pillow_heif
    pillow_heif.register_heif_opener()
    _HEIF_REGISTERED = True


def raster_any(path: str) -> list[np.ndarray]:
    """Rasterise un PDF (page par page) ou décode une image (JPEG/PNG/HEIC) en
    une seule page BGR — dispatch par extension (§5b, images acceptées)."""
    if Path(path).suffix.lower() == ".pdf":
        return raster_pdf(path)
    from PIL import Image
    if Path(path).suffix.lower() in (".heic", ".heif"):
        _ensure_heif_opener()
    with Image.open(path) as im:
        arr = np.array(im.convert("RGB"))
    return [cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)]


def _detect_qrcodes(img: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """Retourne [(payload, quad 4x2 px)] pour tous les QR décodés."""
    detector = cv2.QRCodeDetector()
    out: list[tuple[str, np.ndarray]] = []
    ok, texts, quads, _ = detector.detectAndDecodeMulti(img)
    if ok and quads is not None:
        for text, quad in zip(texts, quads):
            if text:
                out.append((text, quad.reshape(4, 2)))
    return out


def _detect_fiducials_in(img: np.ndarray) -> dict[str, np.ndarray]:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    corners, ids, _ = _ARUCO_DETECTOR.detectMarkers(gray)
    out: dict[str, np.ndarray] = {}
    if ids is not None:
        for quad, tag_id in zip(corners, ids.flatten()):
            role = _FIDUCIAL_ROLE_BY_ID.get(int(tag_id))
            if role:
                out[role] = quad.reshape(4, 2).mean(axis=0)
    return out


def detect_fiducials(img: np.ndarray) -> dict[str, np.ndarray]:
    """Détecte les 3 fiduciels AprilTag de coin (placement : translation,
    rotation, échelle — §5.4). Pleine image d'abord, puis repli par ROI de
    coin sur-échantillonnée si un tag manque (petite taille apparente)."""
    found = _detect_fiducials_in(img)
    h, w = img.shape[:2]
    rw, rh = int(w * 0.22), int(h * 0.16)
    rois = {"TL": (0, 0), "BL": (0, h - rh), "BR": (w - rw, h - rh)}
    for role, (x0, y0) in rois.items():
        if role in found:
            continue
        roi = img[y0:y0 + rh, x0:x0 + rw]
        up = 3
        big = cv2.resize(roi, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
        local = _detect_fiducials_in(big)
        if role in local:
            found[role] = local[role] / up + np.array([x0, y0], dtype=np.float32)
    return found


def analyze_page(img: np.ndarray) -> PageAnalysis:
    """Identifie la page par ses marqueurs et la recale sur le gabarit A4."""
    res = PageAnalysis()
    res.blur = float(cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY),
                                   cv2.CV_64F).var())

    detections = _detect_qrcodes(img)
    centers_px: dict[str, np.ndarray] = {}
    for text, quad in detections:
        if text.startswith("MP1|"):
            page_id = verify_page_payload(text)
            if page_id is None:
                res.warnings.append("qr_hmac_invalid")
                continue
            res.page_id = page_id
            centers_px["MAIN"] = quad.mean(axis=0)

    # seconde lecture du QR principal sur ROI haut-droit sur-échantillonnée
    if res.page_id is None:
        h, w = img.shape[:2]
        roi = img[0:int(h * 0.20), int(w * 0.65):w]
        big = cv2.resize(roi, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
        text, quad, _ = cv2.QRCodeDetector().detectAndDecode(big)
        if text.startswith("MP1|"):
            page_id = verify_page_payload(text)
            if page_id:
                res.page_id = page_id
                center = quad.reshape(-1, 2).mean(axis=0) / 2.5
                centers_px["MAIN"] = center + np.array([w * 0.65, 0], dtype=np.float32)

    # fiduciels de coin : purement géométriques (§5.4)
    for role, center in detect_fiducials(img).items():
        centers_px.setdefault(role, center)

    res.marker_count = len(centers_px)
    if res.page_id is None:
        res.warnings.append("main_qr_missing_or_invalid")
        return res
    res.status = "identified"

    if len(centers_px) < 3:
        res.warnings.append("not_enough_markers")
        return res

    roles = list(centers_px.keys())
    src = np.array([centers_px[r] for r in roles], dtype=np.float32)
    dst = np.array([pt_to_px(*CANONICAL_CENTERS_PT[r]) for r in roles], dtype=np.float32)

    w_px, h_px = int(PAGE_W * SCALE), int(PAGE_H * SCALE)
    if len(roles) >= 4:
        matrix, _ = cv2.findHomography(src, dst)
        proj = cv2.perspectiveTransform(src.reshape(-1, 1, 2), matrix).reshape(-1, 2)
    else:
        matrix = cv2.getAffineTransform(src[:3], dst[:3])
        matrix = np.vstack([matrix, [0, 0, 1]])
        proj = (matrix @ np.hstack([src, np.ones((len(src), 1))]).T).T[:, :2]
        res.warnings.append("affine_fallback")

    res.reprojection_error_px = float(np.abs(proj - dst).max())
    if res.reprojection_error_px > 1.5 / 25.4 * DPI:  # > 1,5 mm (§12.2)
        res.warnings.append("reprojection_error_high")
        return res

    warped = cv2.warpPerspective(img, matrix, (w_px, h_px),
                                 flags=cv2.INTER_LINEAR,
                                 borderValue=(255, 255, 255))
    # Blanchiment du fond : les scans sont des photos iPhone (papier ni blanc ni
    # uniformément éclairé). On normalise UNE fois par page — le reste du pipeline
    # (détection QCM à seuils absolus, crops Mathpix, aperçu) retrouve le fond
    # blanc qu'il suppose. L'original n'est jamais modifié (RM-002) ; `warped` est
    # déjà un dérivé.
    res.warped = flatten_background(warped)
    res.status = "registered"
    return res


def crop_zone(warped: np.ndarray, x_pt: float, y_pt: float, w_pt: float, h_pt: float,
              padding_pt: float = 3.0) -> np.ndarray:
    x0, y1 = pt_to_px(x_pt - padding_pt, y_pt - padding_pt)
    x1, y0 = pt_to_px(x_pt + w_pt + padding_pt, y_pt + h_pt + padding_pt)
    h, w = warped.shape[:2]
    x0, x1 = max(0, int(x0)), min(w, int(x1))
    y0, y1 = max(0, int(y0)), min(h, int(y1))
    return warped[y0:y1, x0:x1]


def dropout_filter(crop: np.ndarray) -> np.ndarray:
    """Supprime les teintes rouge/orangé claires (cadres, lignes guides) ;
    conserve l'encre noire et bleue de l'élève (§5.3)."""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    red_hue = (h <= 18) | (h >= 165)
    mask = red_hue & (s > 30) & (v > 90)
    out = crop.copy()
    out[mask] = (255, 255, 255)
    return out


def ink_ratio(crop_filtered: np.ndarray) -> float:
    """Fraction de pixels d'encre (sombres) après dropout."""
    gray = cv2.cvtColor(crop_filtered, cv2.COLOR_BGR2GRAY)
    dark = (gray < 128).sum()
    return float(dark) / max(1, gray.size)


# Blanchiment du fond (photos iPhone : papier jauni, éclairage inégal). On DIVISE
# chaque canal par une carte de fond (illumination) estimée en basse résolution :
# le papier -> blanc PARTOUT (suit teinte ET dégradé), l'encre reste
# proportionnellement sombre — on ne SEUILLE jamais, donc une coche pâle survit.
# Le fond sous une case saturée est reconstruit depuis le papier voisin (fermeture
# morphologique à noyau large) -> une case entièrement noircie reste noire. Limite
# connue : un aplat sombre PLUS GRAND que le noyau (~12 mm) verrait son centre
# blanchi ; hors de portée d'une case QCM entourée de blanc et de texte.
_FLATTEN_DOWN = 8            # sous-échantillonnage pour estimer le fond (vitesse)
_FLATTEN_KERNEL_MM = 12.0    # noyau de fermeture, > plus gros aplat d'encre à garder


def flatten_background(bgr: np.ndarray) -> np.ndarray:
    """Retire le saumon (dropout) puis blanchit le fond par division par la carte
    d'illumination. Idempotent sur une page déjà blanche (le fond y vaut ~255).
    Une seule passe par page — sert la détection QCM ET l'OCR Mathpix."""
    no_salmon = dropout_filter(bgr)
    h, w = no_salmon.shape[:2]
    down = _FLATTEN_DOWN
    sw, sh = max(1, w // down), max(1, h // down)
    small = cv2.resize(no_salmon, (sw, sh), interpolation=cv2.INTER_AREA)
    k = max(3, int(round(_FLATTEN_KERNEL_MM / 25.4 * DPI / down)))
    k += (k + 1) % 2  # impair
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    out = np.empty_like(no_salmon)
    for ch in range(no_salmon.shape[2]):
        # fermeture : bouche les creux sombres (encre) plus petits que le noyau
        # -> enveloppe claire = niveau du papier, par canal (corrige aussi le jauni)
        bg_small = cv2.morphologyEx(small[..., ch], cv2.MORPH_CLOSE, kernel)
        bg_small = cv2.GaussianBlur(bg_small, (0, 0), sigmaX=max(1.0, k / 2.0))
        bg = cv2.resize(bg_small, (w, h), interpolation=cv2.INTER_LINEAR)
        bg = np.maximum(bg.astype(np.float32), 1.0)
        norm = no_salmon[..., ch].astype(np.float32) * 255.0 / bg
        out[..., ch] = np.clip(norm, 0, 255).astype(np.uint8)
    return out


def _detect_window(b: dict) -> dict:
    """Fenêtre de détection reconstruite autour du CENTRE d'une case, pour les
    copies dont la méta ne porte pas encore `detect` (le centre reste correct
    même si la case stockée était dégénérée). Même fenêtre que pdfgen : case +
    marge large (gauche/haut/bas), réduite à droite (label du choix)."""
    cx = b["x_pt"] + b["w_pt"] / 2
    cy = b["y_pt"] + b["h_pt"] / 2
    left = QCM_BOX / 2 + QCM_DETECT_MARGIN
    return {"x_pt": cx - left, "y_pt": cy - (QCM_BOX / 2 + QCM_DETECT_MARGIN),
            "w_pt": QCM_BOX + QCM_DETECT_MARGIN + QCM_DETECT_MARGIN_R,
            "h_pt": QCM_BOX + 2 * QCM_DETECT_MARGIN}


QCM_THRESHOLD = 0.035
# Seuil ADAPTATIF par page : les élèves cochent de façons très différentes (trait
# fin / gros trait / aplat noir), ce qui déplace la frontière vide↔coché d'une
# copie à l'autre. On l'ajuste À LA PAGE, mais SEULEMENT si les densités de toutes
# ses cases se séparent nettement en deux groupes (vides ~ blanc, cochées ~ encre)
# — sinon le défaut absolu prime (cas « tout vide » / « tout coché » indécidables
# par un tri), et un nuage qui enjambe le seuil sans coupure franche (« mal
# isolé ») part en correction manuelle.
_QCM_ADAPT_MIN = 3             # trop peu de cases -> pas d'adaptation
_QCM_ADAPT_GAP_MIN = 0.02      # écart absolu mini entre les deux groupes
_QCM_ADAPT_SEP = 3.0           # l'écart doit valoir >= 3x la dispersion intra-groupe
_QCM_ADAPT_EMPTY_CEIL = 0.03   # le groupe bas doit friser le blanc (vraies cases vides)


@dataclass
class QcmThreshold:
    """Seuil QCM d'une page. `band` = [lo, hi) : une case dont la densité y tombe
    est AMBIGUË -> zone envoyée en correction manuelle."""
    value: float
    band: tuple[float, float]
    adapted: bool
    default: float = QCM_THRESHOLD


def qcm_densities(warped: np.ndarray, boxes: list[dict]) -> list[float]:
    """Densité d'encre dans la FENÊTRE ÉLARGIE de chaque case (§4.3). La case
    imprimée (2 mm) est plus petite que la tolérance de recalage ET que le geste
    de l'élève : on lit `box["detect"]` (posée par pdfgen), ou à défaut une
    fenêtre reconstruite autour du centre (copies d'avant l'évolution)."""
    densities = []
    for b in boxes:
        win = b.get("detect") or _detect_window(b)
        crop = crop_zone(warped, win["x_pt"], win["y_pt"], win["w_pt"], win["h_pt"],
                         padding_pt=0)
        densities.append(0.0 if crop.size == 0 else ink_ratio(dropout_filter(crop)))
    return densities


def adapt_qcm_threshold(pooled: list[float],
                        default: float = QCM_THRESHOLD) -> QcmThreshold:
    """Seuil de décision pour UNE page, à partir des densités de TOUTES ses cases
    QCM. Trie, cherche le plus grand écart : si les deux groupes sont bien
    distincts (écart net + groupe bas proche du blanc), pose le seuil au milieu de
    l'écart (il PRIME sur le défaut, avec une bande d'ambiguïté étroite car on est
    sûr). Sinon garde le défaut ; si le nuage enjambe le seuil sans coupure nette,
    élargit la bande d'ambiguïté -> les cases limites passent en revue."""
    ds = sorted(pooled)
    narrow = (default * 0.5, default)          # bande « défaut » (comportement antérieur)
    if len(ds) < _QCM_ADAPT_MIN:
        return QcmThreshold(default, narrow, False)
    # Frontière vide->coché : le plus grand écart dont le groupe BAS frise encore le
    # blanc (vraies cases vides). Le plus grand écart TOUT COURT tomberait parfois
    # DANS le groupe coché (un aplat saturé à 0,5 loin des coches à 0,15) et
    # scinderait les cochées à tort ; on ne considère donc que les coupures qui
    # laissent en bas un groupe proche du papier (mean <= EMPTY_CEIL). Absence de
    # tel groupe = pas de cases vides sur la page (« tout coché ») -> pas d'adaptation.
    candidates = []
    for i in range(len(ds) - 1):
        lower = ds[:i + 1]
        if sum(lower) / len(lower) <= _QCM_ADAPT_EMPTY_CEIL:
            candidates.append((ds[i + 1] - ds[i], i))
    if candidates:
        gap, gi = max(candidates)
        lo, hi = ds[gi], ds[gi + 1]
        # On jauge l'écart contre la dispersion du groupe BAS (censé serré près du
        # blanc) ; le groupe HAUT peut légitimement s'étaler sans casser la séparation.
        spread_low = ds[gi] - ds[0]
        if gap >= _QCM_ADAPT_GAP_MIN and gap >= _QCM_ADAPT_SEP * max(spread_low, 1e-6):
            t = (lo + hi) / 2.0
            # bande étroite au cœur de l'écart : aucune case n'y tombe -> pleine confiance
            return QcmThreshold(t, (t - 0.25 * gap, t + 0.25 * gap), True)
    # non séparable : nuage serré (tout vide / tout coché) -> le défaut tranche seul ;
    # nuage étalé enjambant le seuil -> bande large -> revue des cases limites
    if ds[0] < default <= ds[-1] and (ds[-1] - ds[0]) >= _QCM_ADAPT_GAP_MIN:
        return QcmThreshold(default, (default * 0.5, default * 1.8), False)
    return QcmThreshold(default, narrow, False)


def select_qcm(boxes: list[dict], densities: list[float], thr: QcmThreshold
               ) -> tuple[list[int] | None, list[float], list[int]]:
    """Décision d'une zone avec le seuil (adaptatif) de la page. Le seuil adaptatif
    PRIME ; renvoie aussi la sélection au seuil par DÉFAUT (traçabilité). None si
    au moins une case est ambiguë (densité dans la bande) -> correction manuelle."""
    lo, hi = thr.band
    default_sel = [b["index"] for b, d in zip(boxes, densities) if d >= thr.default]
    if any(lo <= d < hi for d in densities):
        return None, densities, default_sel
    selected = [b["index"] for b, d in zip(boxes, densities) if d >= thr.value]
    return selected, densities, default_sel


def detect_qcm(warped: np.ndarray, boxes: list[dict],
               threshold: float = QCM_THRESHOLD) -> tuple[list[int] | None, list[float]]:
    """Détection QCM à seuil fixe (une zone isolée, sans contexte de page).
    Conservée pour les appels directs et les tests ; le pipeline passe désormais
    par `qcm_densities` + `adapt_qcm_threshold` + `select_qcm` (seuil par page)."""
    densities = qcm_densities(warped, boxes)
    selected = [b["index"] for b, d in zip(boxes, densities) if d >= threshold]
    borderline = [d for d in densities if threshold * 0.5 <= d < threshold]
    if borderline and not selected:
        return None, densities
    return selected, densities


def detect_matching(warped: np.ndarray, left_points: list[dict], right_points: list[dict]
                    ) -> tuple[list[list[int]] | None, float]:
    """Détection heuristique v1 du trait manuscrit reliant une pastille gauche
    à une pastille droite (§ points à relier). Combine segments Hough (traits
    droits) et extrémités de composantes connexes (approximation des traits
    courbes) ; toute pastille dont la connexion est ambiguë (plusieurs
    partenaires détectés côté gauche ou côté droit) est EXCLUE du résultat
    plutôt que devinée (RM-005). Retourne (paires acceptées, confiance) ;
    None si rien d'exploitable. À affiner une fois de vrais scans manuscrits
    disponibles — le seuil d'accroche (snap_radius) et le filtrage des petits
    contours sont les premiers paramètres à recalibrer."""
    if not left_points or not right_points:
        return None, 0.0

    def _center_px(pt: dict) -> tuple[float, float]:
        return pt_to_px(pt["x_pt"] + pt["w_pt"] / 2, pt["y_pt"] + pt["h_pt"] / 2)

    left_centers = [_center_px(p) for p in left_points]
    right_centers = [_center_px(p) for p in right_points]
    xs = [c[0] for c in left_centers + right_centers]
    ys = [c[1] for c in left_centers + right_centers]
    margin = 40
    x0, x1 = max(0, int(min(xs) - margin)), int(max(xs) + margin)
    y0, y1 = max(0, int(min(ys) - margin)), int(max(ys) + margin)
    band = warped[y0:y1, x0:x1]
    if band.size == 0:
        return None, 0.0

    gray = cv2.cvtColor(dropout_filter(band), cv2.COLOR_BGR2GRAY)
    ink_mask = (gray < 150).astype(np.uint8) * 255

    snap_radius = 22.0  # px, tolérance d'accroche sur une pastille

    def _nearest(px: float, py: float, centers: list[tuple[float, float]]) -> int | None:
        best_i, best_d = None, snap_radius
        for i, (cx, cy) in enumerate(centers):
            d = ((px - (cx - x0)) ** 2 + (py - (cy - y0)) ** 2) ** 0.5
            if d < best_d:
                best_i, best_d = i, d
        return best_i

    candidates: dict[tuple[int, int], int] = {}

    def _register(ax: float, ay: float, bx: float, by: float) -> None:
        li = _nearest(ax, ay, left_centers)
        ri = _nearest(bx, by, right_centers)
        if li is None or ri is None:
            li = _nearest(bx, by, left_centers)
            ri = _nearest(ax, ay, right_centers)
        if li is None or ri is None:
            return
        candidates[(li, ri)] = candidates.get((li, ri), 0) + 1

    lines = cv2.HoughLinesP(ink_mask, 1, np.pi / 180, threshold=25,
                            minLineLength=30, maxLineGap=8)
    for seg in (lines if lines is not None else []):
        x_a, y_a, x_b, y_b = seg[0]
        _register(float(x_a), float(y_a), float(x_b), float(y_b))

    contours, _ = cv2.findContours(ink_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for cnt in contours:
        if cv2.contourArea(cnt) < 15:
            continue
        pts = cnt.reshape(-1, 2)
        leftmost = pts[pts[:, 0].argmin()]
        rightmost = pts[pts[:, 0].argmax()]
        _register(float(leftmost[0]), float(leftmost[1]),
                  float(rightmost[0]), float(rightmost[1]))

    if not candidates:
        return None, 0.0

    left_partner: dict[int, set[int]] = {}
    right_partner: dict[int, set[int]] = {}
    for li, ri in candidates:
        left_partner.setdefault(li, set()).add(ri)
        right_partner.setdefault(ri, set()).add(li)

    pairs = [[li, ri] for li, ri in candidates
             if len(left_partner[li]) == 1 and len(right_partner[ri]) == 1]
    if not pairs:
        return None, 0.0
    confidence = len(pairs) / max(len(left_points), len(right_points))
    return pairs, confidence


def encode_png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("Échec encodage PNG")
    return buf.tobytes()
