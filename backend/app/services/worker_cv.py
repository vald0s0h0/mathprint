"""Worker vision par ordinateur (§6.1-§6.2, phase 1).

Raster PDF -> détection du QR principal (identité, HMAC) + des 3 fiduciels
AprilTag de coin (placement) -> homographie vers le référentiel A4 canonique
-> crops des zones -> filtre dropout (suppression du rouge saumon) ->
détection QCM et vide.

Le scan original n'est jamais modifié (RM-002) ; tous les dérivés sont
reproductibles. Une page non identifiée est bloquée, jamais devinée (RM-001).
"""
import io
import math
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


def iter_raster_pdf(path: str, dpi: int = DPI):
    """Rasterise le PDF une page à la fois (générateur) : une seule page BGR en
    mémoire. Indispensable sur le NAS (2 Go) — une classe entière rastérisée d'un
    coup à 200 dpi (~11 Mo/page) suffisait à faire tuer le conteneur API (§11.2)."""
    doc = pdfium.PdfDocument(path)
    try:
        for i in range(len(doc)):
            # Page et bitmap FERMÉS aussitôt : pdfium garde sinon chaque page
            # (et son image décodée) en cache jusqu'à la fermeture du document —
            # +650 Mo pour une classe de 29 pages, malgré la lecture page à page.
            page = doc[i]
            try:
                # rev_byteorder=True force un rendu RGB(A) quel que soit le défaut natif
                bitmap = page.render(scale=dpi / 72.0, rev_byteorder=True)
                try:
                    arr = bitmap.to_numpy()
                    code = cv2.COLOR_RGBA2BGR if arr.shape[2] == 4 else cv2.COLOR_RGB2BGR
                    out = cv2.cvtColor(arr, code)     # copie : indépendante du bitmap
                finally:
                    bitmap.close()
            finally:
                page.close()
            yield out
    finally:
        doc.close()


def raster_pdf(path: str, dpi: int = DPI) -> list[np.ndarray]:
    """Rasterise chaque page du PDF en BGR (liste complète en mémoire)."""
    return list(iter_raster_pdf(path, dpi))


def count_pages(path: str) -> int:
    """Nombre de pages d'un scan sans le rastériser (1 pour une image)."""
    if Path(path).suffix.lower() != ".pdf":
        return 1
    doc = pdfium.PdfDocument(path)
    try:
        return len(doc)
    finally:
        doc.close()


_HEIF_REGISTERED = False


def _ensure_heif_opener():
    """Enregistre le décodeur HEIC/HEIF de Pillow (bac à sable, §5b) une seule fois."""
    global _HEIF_REGISTERED
    if _HEIF_REGISTERED:
        return
    import pillow_heif
    pillow_heif.register_heif_opener()
    _HEIF_REGISTERED = True


def iter_raster_any(path: str):
    """Version générateur de `raster_any` : une page en mémoire à la fois."""
    if Path(path).suffix.lower() == ".pdf":
        yield from iter_raster_pdf(path)
    else:
        yield from raster_any(path)


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


def analyze_page(img: np.ndarray, forced_page_id: str | None = None) -> PageAnalysis:
    """Identifie la page par ses marqueurs et la recale sur le gabarit A4.

    `forced_page_id` : identité posée à la main par le professeur sur une page
    jusqu'ici bloquée (QR illisible mais élève reconnu à l'œil sur l'aperçu,
    cf. résolution des scans bloqués) — saute la lecture QR mais retente quand
    même le recalage aux 3 fiduciels de coin, seuls capables de dire si la page
    se laisse recaler malgré tout (elle reste `identified`, pas `registered`,
    si le recalage échoue : pas d'OCR possible sans zones fiables)."""
    res = PageAnalysis()
    res.blur = float(cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY),
                                   cv2.CV_64F).var())

    centers_px: dict[str, np.ndarray] = {}
    if forced_page_id:
        res.page_id = forced_page_id
    else:
        detections = _detect_qrcodes(img)
        for text, quad in detections:
            # Le vérificateur HMAC est l'unique autorité de format (MP1
            # historique, M2 compact et futures versions). Un filtre de
            # préfixe local avait précisément laissé la vision bloquée sur
            # MP1 après l'arrivée de M2.
            page_id = verify_page_payload(text)
            if page_id is None:
                if text.startswith(("MP", "M2:")):
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


def draw_expected_answer(crop: np.ndarray, *, zone_geo: dict, meta: dict,
                         expected: dict, response_type: str,
                         color: str = "#C62828") -> np.ndarray:
    """Superpose la réponse attendue sur un crop de correction manuelle.

    Le crop a été produit par :func:`crop_zone` depuis la même géométrie PDF :
    on peut donc convertir les points en pixels sans recalage supplémentaire.
    L'image source reste intacte. QCM : une case rouge pleine/vide à gauche de
    chaque case élève. Matching : les liaisons attendues sont tracées en rouge
    par-dessus les traits noirs/bleus de l'élève."""
    out = crop.copy()
    if out.size == 0:
        return out
    h_px, w_px = out.shape[:2]
    padding = float(zone_geo.get("padding_pt") or 0.0)
    span_w = max(1e-6, float(zone_geo.get("w_pt") or 0.0) + 2 * padding)
    span_h = max(1e-6, float(zone_geo.get("h_pt") or 0.0) + 2 * padding)
    sx, sy = w_px / span_w, h_px / span_h
    zx = float(zone_geo.get("x_pt") or 0.0)
    ztop = (float(zone_geo.get("y_pt") or 0.0)
            + float(zone_geo.get("h_pt") or 0.0) + padding)

    value = color.lstrip("#")
    if len(value) != 6:
        value = "C62828"
    rgb = tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    bgr = (rgb[2], rgb[1], rgb[0])

    def point(x_pt: float, y_pt: float) -> tuple[int, int]:
        return (int(round((x_pt - zx + padding) * sx)),
                int(round((ztop - y_pt) * sy)))

    if response_type.startswith("qcm"):
        correct = set(expected.get("correct") or [])
        for box in meta.get("boxes") or []:
            corr = box.get("correction_box") or {}
            if not all(k in corr for k in ("x_pt", "y_pt", "w_pt", "h_pt")):
                continue
            x0, y1 = point(float(corr["x_pt"]), float(corr["y_pt"]))
            x1, y0 = point(float(corr["x_pt"]) + float(corr["w_pt"]),
                           float(corr["y_pt"]) + float(corr["h_pt"]))
            thickness = max(2, int(round(min(sx, sy) * 0.8)))
            cv2.rectangle(out, (x0, y0), (x1, y1), bgr,
                          -1 if box.get("index") in correct else thickness,
                          lineType=cv2.LINE_AA)
    elif response_type == "matching":
        left = {p.get("index"): p for p in meta.get("left_points") or []}
        right = {p.get("index"): p for p in meta.get("right_points") or []}
        thickness = max(2, int(round(min(sx, sy) * 0.75)))
        radius = max(2, thickness + 1)
        for pair in expected.get("pairs") or []:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            lp, rp = left.get(pair[0]), right.get(pair[1])
            if not lp or not rp:
                continue
            p1 = point(float(lp["x_pt"]) + float(lp["w_pt"]) / 2,
                       float(lp["y_pt"]) + float(lp["h_pt"]) / 2)
            p2 = point(float(rp["x_pt"]) + float(rp["w_pt"]) / 2,
                       float(rp["y_pt"]) + float(rp["h_pt"]) / 2)
            cv2.line(out, p1, p2, bgr, thickness, lineType=cv2.LINE_AA)
            cv2.circle(out, p1, radius, bgr, -1, lineType=cv2.LINE_AA)
            cv2.circle(out, p2, radius, bgr, -1, lineType=cv2.LINE_AA)
    return out


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
BLANK_INK_THRESHOLD = 0.003
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


def qcm_decision_confidence(densities: list[float], thr: QcmThreshold) -> float:
    """Confiance conservative d'une décision QCM/grille.

    Elle n'est élevée que si toutes les cases sont hors de la bande ambiguë et
    si la géométrie existe. Le seuil adaptatif issu de deux groupes nettement
    séparés vaut 0,99 ; le seuil fixe, plus conservateur, 0,95. Ces valeurs ne
    prétendent pas recalibrer OpenCV en probabilité : elles rendent explicite le
    contrat de la pipeline (>= 0,90 ou professeur) au lieu du 1,0 artificiel.
    """
    if not densities:
        return 0.0
    lo, hi = thr.band
    if any(lo <= d < hi for d in densities):
        return 0.0
    return 0.99 if thr.adapted else 0.95


def blank_decision_confidence(density: float,
                              threshold: float = BLANK_INK_THRESHOLD) -> float:
    """Confiance CV qu'une zone est réellement blanche.

    La moitié basse du seuil historique est nettement blanche. Entre cette
    limite et le seuil, une trace pâle peut être une réponse : revue manuelle.
    """
    if density < 0 or threshold <= 0 or density >= threshold:
        return 0.0
    return 0.99 if density < threshold * 0.5 else 0.0


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


def grid_selection(boxes: list[dict], densities: list[float], thr: QcmThreshold
                   ) -> list[int] | None:
    """Sélection par ligne d'une grille à partir de densités déjà mesurées
    (cf. `detect_grid`) ; None si une case est ambiguë ou une ligne doublement
    cochée."""
    lo, hi = thr.band
    if any(lo <= d < hi for d in densities):
        return None
    rows: dict[int, list[tuple[int, float]]] = {}
    for b, d in zip(boxes, densities):
        rows.setdefault(int(b.get("row", 0)), []).append((int(b.get("col", 0)), d))
    n_rows = (max(rows) + 1) if rows else 0
    selected = [-1] * n_rows
    for ri, cells in rows.items():
        checked = [col for col, d in cells if d >= thr.value]
        if len(checked) > 1:
            return None                     # double coche sur une ligne -> revue
        if checked:
            selected[ri] = checked[0]
    return selected


def detect_grid(warped: np.ndarray, boxes: list[dict], thr: QcmThreshold
                ) -> tuple[list[int] | None, list[float]]:
    """Grille cochée (checkbox_grid) : une case cochée PAR LIGNE. Réutilise les
    densités QCM + le seuil (adaptatif) de la page — la grille est un QCM à choix
    unique par ligne. Retourne (sélection par ligne, densités) : `selection[i]` =
    indice de la colonne cochée à la ligne i (-1 = rien coché). None (→ revue) si
    une case est AMBIGUË (densité dans la bande) ou si une ligne porte PLUSIEURS
    coches (choix multiple interdit sur une ligne)."""
    densities = qcm_densities(warped, boxes)
    return grid_selection(boxes, densities, thr), densities


# --- 2e passe des cases à cocher -------------------------------------------
# La 1re passe (encre sombre, gris < 128, dans la fenêtre élargie) est prudente
# mais hésite sur deux cas fréquents relevés sur un vrai contrôle (6e, 29
# copies) : coche au stylo BLEU CLAIR ou au crayon (plus claire que 128), et
# contour saumon de la case restitué GRIS par certains copieurs (non retiré par
# le dropout, il « remplit » les cases vides). Une zone qui hésite repasse par
# deux mesures INDÉPENDANTES, chacune avec son seuil adaptatif de page :
#   - filtre bleu : encre sombre OU colorée (stylo bleu clair) ;
#   - contour exclu : encre sombre hors du contour imprimé (position connue) +
#     encre claire (crayon) à l'INTÉRIEUR de la case, où le papier est blanc.
# La lecture n'est acceptée que si les deux sont nettes ET identiques ; sinon
# OCRiser. Sur les 116 réponses à cases du contrôle : revues 29 -> 23, 0 erreur.
QCM2_COLOR_SAT = 60
QCM2_COLOR_VMAX = 235
QCM2_RING_PX = 4
QCM2_LIGHT_GRAY = 170


def _qcm_window(warped: np.ndarray, b: dict):
    win = b.get("detect") or _detect_window(b)
    crop = crop_zone(warped, win["x_pt"], win["y_pt"], win["w_pt"], win["h_pt"],
                     padding_pt=0)
    return win, crop


def _dark_or_colored(filtered: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    gray = cv2.cvtColor(filtered, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(filtered, cv2.COLOR_BGR2HSV)
    colored = (hsv[..., 1] > QCM2_COLOR_SAT) & (hsv[..., 2] < QCM2_COLOR_VMAX)
    return (gray < 128) | colored, gray


def qcm_densities_color(warped: np.ndarray, boxes: list[dict]) -> list[float]:
    """2e passe, filtre bleu : encre sombre OU colorée dans la fenêtre."""
    out = []
    for b in boxes:
        _win, crop = _qcm_window(warped, b)
        if crop.size == 0:
            out.append(0.0)
            continue
        ink, _gray = _dark_or_colored(dropout_filter(crop))
        out.append(float(ink.mean()))
    return out


def qcm_densities_ring(warped: np.ndarray, boxes: list[dict]) -> list[float]:
    """2e passe, contour exclu : encre sombre/colorée HORS du contour imprimé
    (±QCM2_RING_PX) + encre claire (crayon) à l'intérieur de la case."""
    out = []
    for b in boxes:
        win, crop = _qcm_window(warped, b)
        if crop.size == 0:
            out.append(0.0)
            continue
        dark, gray = _dark_or_colored(dropout_filter(crop))
        wx0, wy0 = pt_to_px(win["x_pt"], win["y_pt"] + win["h_pt"])
        bx0, by0 = pt_to_px(b["x_pt"], b["y_pt"] + b["h_pt"])
        bx1, by1 = pt_to_px(b["x_pt"] + b["w_pt"], b["y_pt"])
        p0 = (int(round(bx0 - wx0)), int(round(by0 - wy0)))
        p1 = (int(round(bx1 - wx0)), int(round(by1 - wy0)))
        r = QCM2_RING_PX
        ring = np.zeros(gray.shape, np.uint8)
        cv2.rectangle(ring, p0, p1, 1, 2 * r + 1)
        inner = np.zeros(gray.shape, bool)
        inner[max(0, p0[1] + r + 1):max(0, p1[1] - r),
              max(0, p0[0] + r + 1):max(0, p1[0] - r)] = True
        keep = ring == 0
        ink = (dark & keep) | (inner & (gray < QCM2_LIGHT_GRAY))
        out.append(float(ink.sum()) / max(1, int(keep.sum())))
    return out


def _qcm_selection(boxes: list[dict], densities: list[float], thr: QcmThreshold,
                   grid: bool) -> list[int] | None:
    if grid:
        return grid_selection(boxes, densities, thr)
    selected, _d, _default = select_qcm(boxes, densities, thr)
    return selected


def second_pass_selection(boxes: list[dict], grid: bool,
                          dens_color: list[float], thr_color: QcmThreshold,
                          dens_ring: list[float], thr_ring: QcmThreshold
                          ) -> list[int] | None:
    """Lecture de 2e passe : acceptée seulement si les deux mesures sont
    nettes et donnent la MÊME sélection ; None sinon (-> OCRiser)."""
    a = _qcm_selection(boxes, dens_color, thr_color, grid)
    b = _qcm_selection(boxes, dens_ring, thr_ring, grid)
    if a is None or b is None:
        return None
    return a if (sorted(a) if not grid else a) == (sorted(b) if not grid else b) else None


# --- Points à relier : courbe ajustée par paire + lecture globale ----------
# Un trait d'élève n'est pas une droite : à main levée il est bancal, ondulé,
# part ou arrive à côté du point, peut être repassé deux fois, et un trait
# effacé au blanco laisse des mini-traits aux croisements. Pour chaque paire
# candidate (point gauche/haut i, point droit/bas j — colonnes ou rangées), on
# redresse une bande de ±MATCH_BAND_PX autour de la corde i→j et on y cherche,
# par programmation dynamique, la courbe LISSE qui suit au mieux l'encre
# (équivalent d'un RANSAC « ancré » aux deux points, mais qui tolère la
# courbure : pente latérale bornée, écart à la corde pénalisé). Une liaison
# n'est retenue que si cette courbe est encrée presque partout, sans trou.
# La lecture est ensuite GLOBALE : affectation un-pour-un de meilleur score.
# Elle n'est certaine que si (1) elle explique toute l'encre — un second
# passage le long d'une liaison retenue est un trait doublé ; un trait le long
# d'une AUTRE paire (deux partenaires, trait en trop), une tache étendue ou une
# rature envoient en revue ; les mini-traits isolés (restes de blanco aux
# croisements) sont ignorés — et (2) aucune autre lecture complète, presque
# aussi bonne, n'explique aussi toute l'encre. Au croisement de deux traits
# (trois droites concourantes comprises), une lecture qui « tourne » d'un trait
# à l'autre quitte un trait qui, lui, continue tout droit : elle est pénalisée
# (bonne continuation). Deux traits bancals qui se frôlent ou se confondent se
# lisent des deux façons : revue.
# Les points imprimés sont des disques noirs : repérés par leur rayon inscrit
# (un trait de stylo ne fait pas 1,6 mm d'épaisseur), ils recalent localement
# la lecture puis sont effacés de l'encre. Les anciennes pastilles saumon sont
# déjà supprimées par le dropout : on garde alors leur position théorique.
# Seuils réglés sur un banc synthétique (traits règle/main levée/ondulés/crayon,
# doublés, blanco, concourants, recalage décalé, ratures ; points noirs,
# pastilles saumon ou grises, colonnes ou rangées) : aucune lecture fausse sur
# les cas légitimes. À recaler sur copies réelles : scripts/calibrate_matching.py.
MATCH_INK_HI = 220          # gris au-dessus : papier (liseré de blanco compris)
MATCH_INK_LO = 160          # gris en dessous : encre franche (stylo, crayon appuyé)
MATCH_DOT_DARK = 128        # un point imprimé est franchement noir
MATCH_DOT_SEARCH_PX = 14    # recherche du point autour de sa position (recalage ≤ 1,5 mm)
MATCH_DOT_MIN_R = 4.5       # rayon inscrit mini d'un point imprimé (~6 px ; un trait : ≤ 2,5,
                            # deux traits qui se rejoignent : ≤ 4)
MATCH_DOT_OUTLIER_PX = 5    # point trouvé trop loin du décalage commun -> ignoré
MATCH_DOT_QUORUM = 0.5      # recalage local seulement si au moins cette part des points...
MATCH_DOT_SPREAD_PX = 3     # ... est trouvée avec des décalages cohérents
MATCH_END_PX = 12           # extrémités ignorées (point imprimé, départ/arrivée imprécis)
MATCH_HALO_PX = 16          # tache isolée autour d'un point (anneau saumon photocopié en
                            # gris, halo) : effacée ; un trait qui en part déborde et reste
MATCH_BAND_PX = 24          # écart latéral maxi d'un trait bancal (~3 mm à 200 dpi)
MATCH_SMOOTH = 0.15         # coût d'un pas latéral de 1 px (préférence pour le droit)
MATCH_CENTER = 0.3          # coût d'un écart à la corde (départage, au carré)
MATCH_COVER = 0.85          # couverture mini d'une liaison retenue
MATCH_GAP_PX = 10           # plus long trou toléré (~1,3 mm : stylo qui saute)
MATCH_TURN_SKIP_PX = 12     # test de continuation : on ignore la tache du croisement...
MATCH_TURN_ARM_PX = 20      # ... et on compare les bras de part et d'autre
MATCH_TURN_DEG = 20.0       # la courbe tourne de plus que ça entre ses deux bras ...
MATCH_TURN_INK = 0.7        # ... alors que l'encre continue tout droit : changement de trait
MATCH_TURN_TOL_PX = 2       # tolérance latérale du prolongement
MATCH_TURN_RETURN_PX = 3    # la courbe revient sur le trait quitté : simple écart
MATCH_TURN_COST = 0.25      # pénalité de score d'une courbe qui change de trait
MATCH_MARGIN = 0.35         # avance mini sur toute lecture concurrente complète
MATCH_CENTROID_PX = 3       # recentrage de la courbe sur l'encre (± px)
MATCH_MAX_READINGS = 4000   # lectures énumérées (au-delà : revue)
MATCH_MAX_ALTERNATIVES = 64  # lectures alternatives vérifiées (au-delà : revue)
MATCH_OWN_COVER = 0.3       # encre propre mini d'une liaison (hors autres traits)...
MATCH_OWN_RUN = 0.2         # ... ou portion continue propre : sinon liaison « fantôme »
MATCH_CORRIDOR_PX = 4       # demi-largeur d'encre expliquée par une liaison retenue
MATCH_LANE_PX = 10          # le couloir déborde des rangées de points (pas des étiquettes)...
MATCH_LANE_V_PX = 30        # ... et des points extrêmes de chaque rangée (trait bombé, ~4 mm)
MATCH_DUP_BRIDGE = 0.6      # un second passage peut emprunter le premier trait (gain réduit)
MATCH_DUP_FRESH = 0.15      # ... mais doit suivre sa propre encre sur cette fraction
MATCH_DUP_END_PX = 10       # ... rejoindre le premier trait aux deux bouts
MATCH_DUP_NEAR_PX = 10      # ... ne jamais s'en écarter de plus (~1,3 mm)
MATCH_DUP_SPILL_PX = 15     # ... et expliquer TOUTE l'encre nouvelle qu'il emprunte
MATCH_DUP_CORRIDOR_PX = 7   # couloir d'un doublon : couvre aussi l'entre-deux traits
MATCH_RES_SHARE = 0.25      # trait inexpliqué : portion d'une autre paire hors couloirs ...
MATCH_RES_COVER = 0.5       # ... encrée à ce point -> revue
MATCH_RES_BLOB_PX = 36      # tache résiduelle plus étendue (~4,5 mm) -> revue
MATCH_RES_MASS_PX = 120     # ... ou plus encrée -> revue
MATCH_OVERDRAW_PX = 16      # rature : encre à moins de ça d'une liaison retenue ...
MATCH_OVERDRAW_WIN = 30     # ... sur une fenêtre de cette longueur ...
MATCH_OVERDRAW = 3.0        # ... au-delà de tant de largeurs de trait (doublé : ~2)
MATCH_HATCH_TRACKS = (5, 8, 11)  # pistes parallèles à une liaison (px de part et d'autre)
MATCH_HATCH_WIN = 40        # rature : sur une fenêtre de cette longueur ...
MATCH_HATCH_RUNS = 3        # ... au moins tant de petits traits traversent une piste
MATCH_DENSE_WIN = 41        # tache : encre en excès sur l'attendu, fenêtre de ce côté ...
MATCH_DENSE_PX = 130        # ... au-delà de tant de pixels -> revue (rature dense)
MATCH_BLANK_PX = 40         # traces (même pâles) sous ce total -> copie blanche


def _ink_strength(gray: np.ndarray) -> np.ndarray:
    """Encre ∈ [0, 1] : 0 sur le papier, 1 sur un trait franc. Progressif pour
    garder le crayon pâle sans seuil dur."""
    ink = (MATCH_INK_HI - gray.astype(np.float32)) / (MATCH_INK_HI - MATCH_INK_LO)
    return np.clip(ink, 0.0, 1.0)


def _locate_dots(gray: np.ndarray, nominal: list[np.ndarray]) -> tuple[list[np.ndarray], int]:
    """Centres réels des points noirs imprimés, cherchés autour de leur position
    théorique : le maximum de la distance au fond (rayon inscrit) désigne le
    disque, jamais un trait fin qui y aboutit. Un décalage commun (recalage de
    page imparfait) est appliqué aux points introuvables ; un point aberrant
    (tache d'encre voisine) est ramené à ce décalage. Sans quorum de points
    trouvés et cohérents (anciennes copies à pastilles saumon, où seule une
    jonction de traits ressemble à un point), positions théoriques. Retourne
    aussi le nombre de points retenus pour le recalage."""
    dark = (gray < MATCH_DOT_DARK).astype(np.uint8)
    h, w = gray.shape
    s = MATCH_DOT_SEARCH_PX
    found: list[np.ndarray | None] = []
    for c in nominal:
        x0, y0 = max(0, int(c[0]) - s), max(0, int(c[1]) - s)
        x1, y1 = min(w, int(c[0]) + s + 1), min(h, int(c[1]) + s + 1)
        if x1 - x0 < 3 or y1 - y0 < 3:
            found.append(None)
            continue
        dist = cv2.distanceTransform(dark[y0:y1, x0:x1], cv2.DIST_L2, 5)
        peak = float(dist.max())
        if peak < MATCH_DOT_MIN_R:
            found.append(None)
            continue
        ys, xs = np.nonzero(dist >= peak - 1.0)
        found.append(np.array([x0 + xs.mean(), y0 + ys.mean()]))
    shifts = [f - c for f, c in zip(found, nominal) if f is not None]
    if len(shifts) < max(3, math.ceil(MATCH_DOT_QUORUM * len(nominal))):
        return [c.copy() for c in nominal], 0     # anciennes pastilles saumon, ou faux points
    common = np.median(np.array(shifts), axis=0)
    spread = np.median(np.linalg.norm(np.array(shifts) - common, axis=1))
    if spread > MATCH_DOT_SPREAD_PX:
        return [c.copy() for c in nominal], 0
    out = []
    for f, c in zip(found, nominal):
        if f is None or np.linalg.norm(f - c - common) > MATCH_DOT_OUTLIER_PX:
            out.append(c + common)
        else:
            out.append(f)
    return out, len(shifts)


@dataclass
class _Link:
    """Courbe ajustée le long d'une paire (repère redressé de la corde)."""
    cover: float                 # fraction de la portion mesurée bordée d'encre
    gap: int                     # plus long trou (px)
    run: float                   # plus longue portion continue encrée (fraction)
    offset: float                # écart moyen à la corde (px)
    end_offset: float            # écart moyen aux deux bouts (px)
    path: np.ndarray             # points (x, y) de la courbe

    @property
    def score(self) -> float:
        return (self.cover - 0.5 * self.offset / MATCH_BAND_PX
                - 0.25 * self.end_offset / MATCH_BAND_PX)

    @property
    def present(self) -> bool:
        return self.cover >= MATCH_COVER and self.gap <= MATCH_GAP_PX


def _runs(flags: np.ndarray) -> tuple[int, int]:
    """(plus longue suite de vrais, plus longue suite de faux)."""
    best = {True: 0, False: 0}
    cur, prev = 0, None
    for f in flags.tolist():
        cur = cur + 1 if f == prev else 1
        prev = f
        best[f] = max(best[f], cur)
    return best[True], best[False]


def _turns_off_a_stroke(path: np.ndarray, ink: np.ndarray) -> bool:
    """Vrai si la courbe change de trait à un croisement : entre ses bras
    d'entrée et de sortie (pris hors de la tache du croisement, où plusieurs
    traits se confondent), elle tourne de plus de MATCH_TURN_DEG alors que
    l'encre continue tout droit des DEUX côtés — le trait qu'elle quitte se
    poursuit devant, celui qu'elle rejoint existait derrière — et elle ne
    revient jamais sur le trait quitté. Un trait bancal tourne moins ; un trait
    ondulé tourne, mais rien ne le prolonge ; une fourche (reste de blanco au
    départ d'un point) n'a qu'un côté ; une liaison qui emprunte un instant
    l'autre trait d'un croisement serré revient ensuite sur le sien."""
    skip, arm = MATCH_TURN_SKIP_PX, MATCH_TURN_ARM_PX
    reach = skip + arm
    n = len(path)
    if n < 2 * reach + 1:
        return False
    inked = cv2.dilate(ink, np.ones((3, 3), np.uint8)) >= 0.5   # ±1 px
    near = cv2.dilate(ink, np.ones((2 * MATCH_TURN_TOL_PX + 1,) * 2, np.uint8)) >= 0.5
    hgt, wid = inked.shape

    def inked_along(pts: np.ndarray, where: np.ndarray = inked) -> float:
        pts = np.rint(pts).astype(int)
        ok = (pts[:, 0] >= 0) & (pts[:, 1] >= 0) & (pts[:, 0] < wid) & (pts[:, 1] < hgt)
        return float(where[pts[ok, 1], pts[ok, 0]].sum()) / len(pts)

    def unit(v: np.ndarray) -> np.ndarray:
        return v / max(float(np.linalg.norm(v)), 1e-6)

    def meets(pts: np.ndarray, origin: np.ndarray, direction: np.ndarray, ahead: bool) -> bool:
        rel = pts - origin[None, :]
        along = rel @ direction
        across = np.abs(rel[:, 0] * direction[1] - rel[:, 1] * direction[0])
        return bool(np.any(((along > 0) if ahead else (along < 0))
                           & (across <= MATCH_TURN_RETURN_PX)))

    beyond = np.arange(2 * skip, 2 * skip + arm + 1, dtype=np.float64)[:, None]
    cos_max = math.cos(math.radians(MATCH_TURN_DEG))
    for c in range(reach, n - reach):
        d_in = unit(path[c - skip] - path[c - reach])
        d_out = unit(path[c + reach] - path[c + skip])
        if float(d_in @ d_out) > cos_max:
            continue
        if (inked_along(path[c - reach:c - skip + 1]) < MATCH_TURN_INK
                or inked_along(path[c + skip:c + reach + 1]) < MATCH_TURN_INK):
            continue                          # bras dans un trou : rien à prolonger
        if (inked_along(path[c - skip] + d_in * beyond, near) < MATCH_TURN_INK
                or inked_along(path[c + skip] - d_out * beyond, near) < MATCH_TURN_INK):
            continue
        if (meets(path[c + reach:], path[c - skip], d_in, ahead=True)
                or meets(path[:c - reach + 1], path[c + skip], d_out, ahead=False)):
            continue                          # aller-retour sur l'autre trait
        return True
    return False


def _fit_links(ink: np.ndarray, segments: list[tuple[np.ndarray, np.ndarray]]
               ) -> list[_Link | None]:
    """Ajuste une courbe lisse le long de chaque segment (a, b) — toutes les
    paires en une passe de programmation dynamique vectorisée.

    Repère redressé : t le long de la corde (pas 1 px, extrémités exclues), d
    en travers (±MATCH_BAND_PX). La courbe garde un d par t, se décale d'au plus
    1 px par pas (pente ≤ 45° par rapport à la corde : un trait qui croise ne se
    suit pas) et maximise l'encre traversée, moins un coût par décalage et par
    écart à la corde."""
    ds = np.arange(-MATCH_BAND_PX, MATCH_BAND_PX + 1, dtype=np.float32)
    K = len(ds)
    gains, covers, geos, inks = [], [], [], []
    for a, b in segments:
        length = float(np.hypot(*(b - a)))
        ts = np.arange(MATCH_END_PX, length - MATCH_END_PX + 1e-6, 1.0, dtype=np.float32)
        if len(ts) < 8:
            gains.append(None)
            covers.append(None)
            geos.append(None)
            inks.append(None)
            continue
        u = (b - a) / length
        nv = np.array([-u[1], u[0]])
        mx = (a[0] + u[0] * ts[:, None] + nv[0] * ds[None, :]).astype(np.float32)
        my = (a[1] + u[1] * ts[:, None] + nv[1] * ds[None, :]).astype(np.float32)
        m = cv2.remap(ink, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                      borderValue=0)
        tol = m.copy()               # ±1 px en travers pour juger « encré »
        tol[:, 1:] = np.maximum(tol[:, 1:], m[:, :-1])
        tol[:, :-1] = np.maximum(tol[:, :-1], m[:, 1:])
        gains.append(m - MATCH_CENTER * (ds / MATCH_BAND_PX) ** 2)
        covers.append(tol >= 0.5)
        geos.append((a, u, nv, ts))
        inks.append(m)
    live = [k for k, g in enumerate(gains) if g is not None]
    out: list[_Link | None] = [None] * len(segments)
    if not live:
        return out
    T = max(len(gains[k]) for k in live)
    P = len(live)
    G = np.zeros((P, T, K), np.float32)
    for p, k in enumerate(live):
        G[p, :len(gains[k])] = gains[k]
    back = np.zeros((P, T, K), np.int8)
    last = {p: len(gains[k]) - 1 for p, k in enumerate(live)}
    finals = {p: G[p, 0].copy() for p in last if last[p] == 0}
    V = G[:, 0].copy()
    neg = np.full((P, 1), -np.inf, np.float32)
    for t in range(1, T):
        from_lo = np.concatenate([neg, V[:, :-1]], axis=1) - MATCH_SMOOTH   # depuis d-1
        from_hi = np.concatenate([V[:, 1:], neg], axis=1) - MATCH_SMOOTH    # depuis d+1
        stack = np.stack([from_lo, V, from_hi])
        arg = stack.argmax(axis=0)
        V = np.take_along_axis(stack, arg[None], axis=0)[0] + G[:, t]
        back[:, t] = (arg - 1).astype(np.int8)   # -1 : venu de d-1, +1 : de d+1
        for p in [p for p, e in last.items() if e == t]:
            finals[p] = V[p].copy()
    for p, k in enumerate(live):
        n_t = last[p] + 1
        idx = np.empty(n_t, np.int64)
        j = int(np.argmax(finals[p]))
        for t in range(n_t - 1, -1, -1):
            idx[t] = j
            j += int(back[p, t, j])
        cov = covers[k][np.arange(n_t), idx]
        on, off = _runs(cov)
        # recentrage sur l'encre : le coût d'écart à la corde tire la courbe
        # vers le bord du trait, les tests géométriques veulent son milieu
        r = MATCH_CENTROID_PX
        win = np.clip(idx[:, None] + np.arange(-r, r + 1)[None, :], 0, K - 1)
        wts = inks[k][np.arange(n_t)[:, None], win]
        wts = np.where(wts >= 0.5, wts, 0.0)
        tot = wts.sum(axis=1)
        offs = np.where(tot > 0, (wts * ds[win]).sum(axis=1) / np.maximum(tot, 1e-6), ds[idx])
        a, u, nv, ts = geos[k]
        path = a[None, :] + u[None, :] * ts[:, None] + nv[None, :] * offs[:, None]
        out[k] = _Link(cover=float(cov.mean()), gap=off, run=on / n_t,
                       offset=float(np.abs(offs[cov]).mean()) if cov.any() else MATCH_BAND_PX,
                       end_offset=float((abs(offs[0]) + abs(offs[-1])) / 2), path=path)
    return out


def _matchings(weights: dict[tuple[int, int], float]
               ) -> list[tuple[float, list[tuple[int, int]]]] | None:
    """Toutes les affectations un-pour-un parmi les paires candidates, par
    poids total décroissant (quelques paires par exercice : exhaustif). None
    au-delà de MATCH_MAX_READINGS (dessin trop chargé pour être certain)."""
    cands = sorted(weights, key=lambda p: -weights[p])
    found: list[tuple[float, list[tuple[int, int]]]] = []

    def walk(k: int, used_l: frozenset, used_r: frozenset, total: float, chosen: list):
        if len(found) > MATCH_MAX_READINGS:
            return
        if k == len(cands):
            found.append((total, list(chosen)))
            return
        i, j = cands[k]
        if i not in used_l and j not in used_r:
            chosen.append((i, j))
            walk(k + 1, used_l | {i}, used_r | {j}, total + weights[(i, j)], chosen)
            chosen.pop()
        walk(k + 1, used_l, used_r, total, chosen)

    walk(0, frozenset(), frozenset(), 0.0, [])
    if len(found) > MATCH_MAX_READINGS:
        return None
    return sorted(found, key=lambda f: -f[0])


def _corridor(shape: tuple[int, int], paths: list[np.ndarray],
              half: int = MATCH_CORRIDOR_PX) -> np.ndarray:
    mask = np.zeros(shape, np.uint8)
    for path in paths:
        cv2.polylines(mask, [np.round(path).astype(np.int32)], False, 255, 2 * half + 1)
    return mask > 0


def _doubled(ink: np.ndarray, base: np.ndarray, explained: np.ndarray, first: np.ndarray,
             a: np.ndarray, b: np.ndarray) -> np.ndarray | None:
    """Second passage le long d'une liaison retenue (trait repassé) : courbe
    qui suit de l'encre NOUVELLE et peut emprunter le premier trait là où les
    deux se confondent, mais reste près de lui, le rejoint aux deux bouts et
    explique toute l'encre nouvelle qu'elle touche — un trait vers un autre
    point (second partenaire) qui longe la liaison puis s'en écarte n'est pas
    un doublon. `base` : couloirs des seules liaisons retenues (l'encre
    « nouvelle » s'y mesure, pour que deux doublons ne se partagent pas un
    trait en trop) ; `explained` : couloirs déjà expliqués (doublons compris)."""
    bridged = ink.copy()
    bridged[explained] *= MATCH_DUP_BRIDGE
    lk = _fit_links(bridged, [(a, b)])[0]
    if lk is None or not lk.present:
        return None
    gaps = np.linalg.norm(lk.path[:, None, :] - first[None, :, :], axis=2).min(axis=1)
    if gaps.max() > MATCH_DUP_NEAR_PX or max(gaps[0], gaps[-1]) > MATCH_DUP_END_PX:
        return None
    fresh = ink.copy()
    fresh[explained] = 0.0
    pts = np.rint(lk.path).astype(int)
    h, w = ink.shape
    ok = (pts[:, 0] >= 0) & (pts[:, 1] >= 0) & (pts[:, 0] < w) & (pts[:, 1] < h)
    on_fresh = np.zeros(len(pts), bool)
    on_fresh[ok] = cv2.dilate(fresh, np.ones((3, 3), np.uint8))[pts[ok, 1], pts[ok, 0]] >= 0.5
    if on_fresh.mean() < MATCH_DUP_FRESH:
        return None
    unexplained = ink.copy()
    unexplained[base] = 0.0
    strokes = (unexplained >= 0.5).astype(np.uint8)
    _n, labels = cv2.connectedComponents(strokes, connectivity=8)
    along = _corridor(ink.shape, [lk.path], MATCH_DUP_CORRIDOR_PX)
    for c in np.unique(labels[along & (strokes > 0)]):
        if c == 0:
            continue
        ys, xs = np.nonzero((labels == c) & ~along)
        if len(ys) and np.hypot(np.ptp(xs), np.ptp(ys)) > MATCH_DUP_SPILL_PX:
            return None
    return lk.path


def _across(ink: np.ndarray, path: np.ndarray) -> np.ndarray:
    """Pixels d'encre en travers d'une liaison (±MATCH_OVERDRAW_PX), point par
    point le long de sa courbe."""
    chord = path[-1] - path[0]
    nv = np.array([-chord[1], chord[0]]) / max(float(np.linalg.norm(chord)), 1e-6)
    offs = np.arange(-MATCH_OVERDRAW_PX, MATCH_OVERDRAW_PX + 1, dtype=np.float64)
    pts = path[:, None, :] + offs[None, :, None] * nv[None, None, :]
    vals = cv2.remap(ink, pts[..., 0].astype(np.float32), pts[..., 1].astype(np.float32),
                     cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return (vals >= 0.5).sum(axis=1).astype(np.float64)


def _overdraw(profiles: list[np.ndarray]) -> float:
    """Densité d'encre maximale sur une fenêtre glissante, en largeurs de
    trait : ~1 pour un trait, ~2 pour un trait repassé, bien plus sous une
    rature (gribouillis, hachures). La largeur de référence est la médiane sur
    TOUTES les liaisons : une rature qui couvre la moitié d'un trait court ne
    la gonfle pas."""
    if not profiles:
        return 0.0
    width = max(float(np.median(np.concatenate(profiles))), 2.5)
    worst = 0.0
    for across in profiles:
        win = min(MATCH_OVERDRAW_WIN, len(across))
        worst = max(worst, float((np.convolve(across, np.ones(win), mode="valid")
                                  / (win * width)).max()))
    return worst


def _hatches(ink: np.ndarray, path: np.ndarray, crossers: list[np.ndarray]) -> int:
    """Traits en trop qui barrent une liaison : sur des pistes parallèles à
    elle, nombre maximal de traversées d'encre sur MATCH_HATCH_WIN px, moins
    les autres liaisons retenues qui y passent (un croisement normal compte
    pour 0). ~0 pour un trait seul ou croisé, 1 pour un reste de blanco ou une
    petite marque, plusieurs sous des hachures ou un gribouillis — y compris
    posé sur un croisement, où les couloirs des autres traits l'effaceraient.
    `ink` est privée de la liaison elle-même (et de ses doublons) ; `crossers`
    : couloirs des autres liaisons retenues."""
    chord = path[-1] - path[0]
    nv = np.array([-chord[1], chord[0]]) / max(float(np.linalg.norm(chord)), 1e-6)
    hgt, wid = ink.shape
    worst = 0
    for off in MATCH_HATCH_TRACKS:
        for side in (-1, 1):
            track = path + side * off * nv[None, :]
            vals = cv2.remap(ink, track[:, 0].astype(np.float32)[None, :],
                             track[:, 1].astype(np.float32)[None, :], cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=0)[0]
            inked = vals >= 0.5
            inked[1:-1] |= inked[:-2] & inked[2:]          # bouche les trous d'1 px
            starts = np.nonzero(inked & ~np.concatenate([[False], inked[:-1]]))[0]
            if len(starts) <= worst:
                continue
            pts = np.clip(np.rint(track).astype(int), 0, [wid - 1, hgt - 1])
            passes = [np.concatenate([[0], np.cumsum(m[pts[:, 1], pts[:, 0]])]) for m in crossers]
            n = len(track)
            for a, first in enumerate(starts):
                last = min(n, first + MATCH_HATCH_WIN)
                runs = int(np.searchsorted(starts, last) - a)
                crossed = sum(1 for cs in passes if cs[last] - cs[first] > 0)
                worst = max(worst, runs - crossed)
    return worst


def _unexplained(ink: np.ndarray, lane: np.ndarray, L: list, R: list,
                 links: dict, reading: list[tuple[int, int]]) -> dict:
    """Encre que `reading` n'explique pas : on retire le couloir de chaque
    liaison et de ses seconds passages (traits doublés). Mesure ensuite le plus
    fort trait résiduel le long d'une AUTRE paire, la plus grande tache
    résiduelle — morceaux recollés par-dessus les couloirs retirés — en étendue
    et en encre (un mini-trait isolé, reste de blanco à un croisement, passe
    sous les deux seuils), et l'encre accumulée sur une liaison (rature)."""
    owned = [(p, links[p].path) for p in reading]
    base = mask = _corridor(ink.shape, [path for _p, path in owned])
    for p in reading:
        for _ in range(2):
            again = _doubled(ink, base, mask, links[p].path, L[p[0]], R[p[1]])
            if again is None:
                break
            owned.append((p, again))
            mask = mask | _corridor(ink.shape, [again], MATCH_DUP_CORRIDOR_PX)
    residual = ink.copy()
    residual[mask] = 0.0
    residual[~lane] = 0.0
    hatches = 0
    for p in reading:
        own = np.zeros(ink.shape, bool)
        crossers = []
        for q, path in owned:
            half = MATCH_CORRIDOR_PX if path is links[q].path else MATCH_DUP_CORRIDOR_PX
            corr = _corridor(ink.shape, [path], half)
            if q == p:
                own |= corr
            else:
                crossers.append(corr)
        rest = ink.copy()
        rest[own | ~lane] = 0.0
        hatches = max(hatches, _hatches(rest, links[p].path, crossers))
    # trait le long d'une AUTRE paire : on juge sa courbe sur ses seules
    # portions libres (hors couloirs) — un trait croisé trois fois n'y perd rien
    loose = cv2.dilate(residual, np.ones((3, 3), np.uint8)) >= 0.5
    hgt, wid = ink.shape
    line_cover = 0.0
    for k, lk in links.items():
        if k in reading:
            continue
        pts = np.rint(lk.path).astype(int)
        pts = pts[(pts[:, 0] >= 0) & (pts[:, 1] >= 0) & (pts[:, 0] < wid) & (pts[:, 1] < hgt)]
        free = ~mask[pts[:, 1], pts[:, 0]]
        if len(pts) and free.mean() >= MATCH_RES_SHARE:
            line_cover = max(line_cover, float(loose[pts[free, 1], pts[free, 0]].mean()))
    inked = (residual >= 0.5).astype(np.uint8)
    marks = cv2.dilate(inked, np.ones((2 * MATCH_CORRIDOR_PX + 3,) * 2, np.uint8))
    n_cc, labels, stats, _cent = cv2.connectedComponentsWithStats(marks, connectivity=8)
    mass = np.bincount(labels[inked > 0], minlength=n_cc)
    pad = 2 * (MATCH_CORRIDOR_PX + 1)   # la dilatation élargit chaque tache
    blob = max((float(np.hypot(stats[k, cv2.CC_STAT_WIDTH] - pad,
                               stats[k, cv2.CC_STAT_HEIGHT] - pad))
                for k in range(1, n_cc)), default=0.0)
    heavy = int(mass[1:].max()) if n_cc > 1 else 0
    profiles = []
    for p in reading:
        alone = ink.copy()       # sans les autres liaisons (leurs croisements)
        alone[_corridor(ink.shape, [path for q, path in owned if q != p])] = 0.0
        profiles.append(_across(alone, links[p].path))
    overdraw = _overdraw(profiles)
    # encre en excès sur l'attendu : chaque liaison (et ses doublons) redessinée
    # à la largeur de trait mesurée. Un gribouillis posé sur un croisement est
    # noyé dans les couloirs (les courbes errent dans la tache), pas ici.
    width = max(2, int(round(float(np.median(np.concatenate(profiles))))) if profiles else 2)
    expected = np.zeros(ink.shape, np.uint8)
    for _q, path in owned:
        cv2.polylines(expected, [np.round(path).astype(np.int32)], False, 255, width + 2)
    excess = ((ink >= 0.5) & (expected == 0) & lane).astype(np.float32)
    dense = float(cv2.boxFilter(excess, -1, (MATCH_DENSE_WIN,) * 2, normalize=False,
                                borderType=cv2.BORDER_CONSTANT).max()) if excess.any() else 0.0
    return {"line_cover": line_cover, "blob": blob, "mass": heavy,
            "overdraw": overdraw, "hatches": hatches, "dense": dense,
            "traces": int((residual > 0).sum()),
            "clear": (line_cover < MATCH_RES_COVER and blob < MATCH_RES_BLOB_PX and heavy < MATCH_RES_MASS_PX
                      and overdraw < MATCH_OVERDRAW and hatches < MATCH_HATCH_RUNS
                      and dense < MATCH_DENSE_PX)}


def detect_matching(warped: np.ndarray, left_points: list[dict], right_points: list[dict],
                    diag: dict | None = None) -> tuple[list[list[int]] | None, float]:
    """Lecture d'un exercice « points à relier » (§ points à relier).

    Confiance HAUTE (0,97) seulement si la lecture est nette : chaque liaison
    retenue est une courbe encrée de bout en bout, toute l'encre est expliquée
    et aucune autre lecture ne l'explique aussi bien. Sinon 0,5 -> OCRiser,
    avec la meilleure lecture pré-remplie pour le professeur. Une zone sans
    aucune trace est une copie blanche lue avec certitude ([], 0,97) — jamais
    une liaison devinée (RM-005). `diag` reçoit, si fourni, le motif de la
    décision (journalisé avec la tentative, pour les recalibrages)."""
    diag = diag if diag is not None else {}
    if not left_points or not right_points:
        return None, 0.0

    def center(p: dict) -> np.ndarray:
        return np.array(pt_to_px(p["x_pt"] + p["w_pt"] / 2, p["y_pt"] + p["h_pt"] / 2))

    nominal = [center(p) for p in left_points + right_points]
    xs = [c[0] for c in nominal]
    ys = [c[1] for c in nominal]
    pad = max(MATCH_BAND_PX, MATCH_LANE_V_PX) + MATCH_DOT_SEARCH_PX + 8
    x0, y0 = max(0, int(min(xs) - pad)), max(0, int(min(ys) - pad))
    x1, y1 = int(max(xs) + pad), int(max(ys) + pad)
    band = warped[y0:y1, x0:x1]
    if band.size == 0:
        return None, 0.0
    gray = cv2.cvtColor(dropout_filter(band), cv2.COLOR_BGR2GRAY) if band.ndim == 3 else band
    origin = np.array([x0, y0], dtype=float)
    dots, n_found = _locate_dots(gray, [c - origin for c in nominal])
    L, R = dots[:len(left_points)], dots[len(left_points):]
    ink = _ink_strength(gray)
    dot_mask = np.zeros(gray.shape, np.uint8)
    halo = np.zeros(gray.shape, np.uint8)
    for c in dots:
        cv2.circle(dot_mask, (int(round(c[0])), int(round(c[1]))), MATCH_END_PX, 255, -1)
        cv2.circle(halo, (int(round(c[0])), int(round(c[1]))), MATCH_HALO_PX, 255, -1)
    ink[dot_mask > 0] = 0.0
    traced = (ink > 0).astype(np.uint8)
    _n, labels = cv2.connectedComponents(traced, connectivity=8)
    escaping = np.unique(labels[(traced > 0) & (halo == 0)])
    trapped = (traced > 0) & ~np.isin(labels, escaping)
    ink[trapped] = 0.0
    # couloir de tracé : entre les deux rangées de points (colonnes gauche/droite
    # ou rangées haut/bas), hors étiquettes, posées au-delà de chaque rangée
    axis = np.mean(R, axis=0) - np.mean(L, axis=0)
    axis = axis / max(float(np.linalg.norm(axis)), 1e-6)
    across = np.array([-axis[1], axis[0]])
    a0 = min(float(c @ axis) for c in L) - MATCH_LANE_PX
    a1 = max(float(c @ axis) for c in R) + MATCH_LANE_PX
    s0 = min(float(c @ across) for c in dots) - MATCH_LANE_V_PX
    s1 = max(float(c @ across) for c in dots) + MATCH_LANE_V_PX
    corners = [axis * a + across * b for a, b in ((a0, s0), (a1, s0), (a1, s1), (a0, s1))]
    lane_u8 = np.zeros(gray.shape, np.uint8)
    if a1 > a0:
        cv2.fillConvexPoly(lane_u8, np.round(np.array(corners)).astype(np.int32), 1)
    lane = lane_u8 > 0
    diag["dots_found"] = n_found

    keys = [(i, j) for i in range(len(L)) for j in range(len(R))]
    links = {k: lk for k, lk in zip(keys, _fit_links(ink, [(L[i], R[j]) for i, j in keys]))
             if lk is not None}
    # une courbe qui change de trait à un croisement est pénalisée, jamais
    # exclue d'office : là où deux traits se confondent, le test se trompe
    weights = {k: lk.score - (MATCH_TURN_COST if _turns_off_a_stroke(lk.path, ink) else 0.0)
               for k, lk in links.items() if lk.present and lk.score > 0}
    readings = _matchings(weights)
    if readings is None:
        diag["reason"] = "too_many_readings"
        return None, 0.0
    chosen = readings[0][1]
    pairs = [[int(i), int(j)] for i, j in sorted(chosen)]
    check = _unexplained(ink, lane, L, R, links, chosen)
    diag.update(residual_cover=round(check["line_cover"], 3),
                residual_blob=round(check["blob"], 1), residual_mass=check["mass"],
                overdraw=round(check["overdraw"], 2), hatches=check["hatches"],
                dense=round(check["dense"]))

    if not chosen:
        blank = check["traces"] < MATCH_BLANK_PX
        diag["reason"] = "blank" if blank else "unread_ink"
        return ([], 0.97) if blank else (None, 0.0)
    if not check["clear"]:
        diag["reason"] = ("unexplained_line" if check["line_cover"] >= MATCH_RES_COVER
                          else "unexplained_mark")
        return pairs, 0.5
    # liaison « fantôme » : toute son encre appartient déjà aux autres traits
    # (traits qui frôlent deux points libres) — l'élève ne l'a pas tracée
    for p in chosen:
        others = [links[q].path for q in chosen if q != p]
        own = ink.copy()
        own[_corridor(ink.shape, others)] = 0.0
        lk = _fit_links(own, [(L[p[0]], R[p[1]])])[0]
        if lk is None or (lk.cover < MATCH_OWN_COVER and lk.run < MATCH_OWN_RUN):
            diag["reason"] = "borrowed_ink"
            return pairs, 0.5
    # une autre lecture complète, presque aussi bonne, qui explique aussi toute
    # l'encre ? (traits bancals qui se frôlent ou se confondent : chacun peut se
    # lire comme la suite de l'autre). Au croisement franc de deux traits, la
    # lecture qui « tourne » cumule écart à la corde et changement de trait.
    best = readings[0][0]
    checked = 0
    for total, alt in readings[1:]:
        if best - total >= MATCH_MARGIN:
            break                             # lectures triées : toutes les suivantes aussi
        if len(alt) < len(chosen) or set(alt) == set(chosen):
            continue
        checked += 1
        if checked > MATCH_MAX_ALTERNATIVES:
            diag["reason"] = "too_many_readings"
            return pairs, 0.5
        if _unexplained(ink, lane, L, R, links, alt)["clear"]:
            diag.update(reason="ambiguous_reading",
                        alternative=[[int(i), int(j)] for i, j in sorted(alt)])
            return pairs, 0.5
    diag["reason"] = "clear"
    return pairs, 0.97


def encode_png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("Échec encodage PNG")
    return buf.tobytes()
