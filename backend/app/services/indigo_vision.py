"""Extraction visuelle des sources du mode « QCM multipass ».

UNE COLONNE d'exercices est l'unité de travail et la seule image d'un appel. Les
PDF du manuel contiennent deux pages imprimées par raster, à deux colonnes
chacune : quatre colonnes par raster, toujours aux mêmes abscisses. À l'échelle
de la double page, les petits badges ronds sont mal lus et des exercices voisins
sont fusionnés ; à l'échelle de la demi-page, deux colonnes se disputaient encore
l'ordre de lecture. Une colonne seule se lit de haut en bas, sans ambiguïté, et
donne au modèle une image beaucoup plus haute que large — donc un texte
mathématique nettement plus lisible à budget d'image égal.

Chaque colonne conserve la résolution native ; ses boîtes 0–1000 sont ensuite
remises dans le repère du raster complet pour les crops.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

from ..config import settings
from . import indigo_llm, indigo_manual, prompts
from . import statement as statement_mod

logger = logging.getLogger("app.indigo")

BOX_SCALE = 1000.0
MIN_BOX_SIDE = 3.0

# --------------------------------------------------------- bandeaux roses (CV)
# Un bandeau de compétence est une pastille arrondie d'un rose foncé UNIQUE dans
# le manuel : RVB (195, 25, 130), soit BGR (130, 25, 195) dans le raster.
# Le repérer en Python plutôt que de le demander au modèle n'est pas une
# optimisation, c'est une correction : à qui on montrait la pastille « Calculer
# une moyenne » en haut d'une colonne, le modèle répondait quand même la
# compétence de la colonne précédente — treize exercices rangés sous la mauvaise
# compétence, en silence. Une couleur exacte, elle, ne se laisse pas influencer.
BANNER_BGR = (130, 25, 195)
BANNER_TOLERANCE = 135          # distance L1 sur les trois canaux
# Une LIGNE de pastille est rose sur la quasi-totalité de sa largeur. Les barres
# d'un histogramme rose (exercice 35, page 87) plafonnent à 0,33 : le seuil haut
# les écarte, le seuil bas sert seulement à délimiter la bande.
BANNER_ROW_MIN, BANNER_ROW_PEAK = 0.30, 0.60
BANNER_MIN_HEIGHT = 10          # pixels


def _box(value: Any) -> list[float] | None:
    """Boîte 0–1000 canonique, ou ``None`` si elle est inexploitable."""
    if isinstance(value, dict):
        value = [value.get(k) for k in ("x0", "y0", "x1", "y1")]
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    x0, x1 = sorted((max(0.0, min(BOX_SCALE, x0)),
                     max(0.0, min(BOX_SCALE, x1))))
    y0, y1 = sorted((max(0.0, min(BOX_SCALE, y0)),
                     max(0.0, min(BOX_SCALE, y1))))
    if x1 - x0 < MIN_BOX_SIDE or y1 - y0 < MIN_BOX_SIDE:
        return None
    return [x0, y0, x1, y1]


def pixel_box(value: Any, width: int, height: int, *, pad: int = 0) -> tuple[int, int, int, int] | None:
    """Convertit une boîte Vision 0–1000 vers le raster réel."""
    box = _box(value)
    if box is None or width <= 0 or height <= 0:
        return None
    x0, y0, x1, y1 = box
    return (max(0, int(x0 * width / BOX_SCALE) - pad),
            max(0, int(y0 * height / BOX_SCALE) - pad),
            min(width, int(x1 * width / BOX_SCALE + 0.999) + pad),
            min(height, int(y1 * height / BOX_SCALE + 0.999) + pad))


def _page_contract(data: Any) -> dict:
    """Validation minimale déclenchant le retry correctif DeepSeek.

    Ne sont exigés que le NUMÉRO et l'ÉNONCÉ : sans eux, il n'y a pas
    d'exercice. Une page sans exercice est légitime.

    Le CROP d'une figure, lui, n'est plus exigé. Il l'était, et un exercice
    entier disparaissait faute d'un rectangle — pire, la colonne entière
    échouait après deux réparations et emportait ses voisins. Une image
    manquante ne rend pas un exercice inutilisable : le professeur l'ajoute à la
    relecture. On dégrade donc proprement (`has_figure` retombe à faux, la
    DESCRIPTION est conservée, elle porte les données du dessin) au lieu de
    perdre le texte que Vision a lu correctement.
    """
    if not isinstance(data, dict) or not isinstance(data.get("exercises"), list):
        raise ValueError("`exercises` doit être une liste")
    items = data["exercises"]
    for pos, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"exercises[{pos}] n'est pas un objet")
        if not str(item.get("number") or "").strip():
            raise ValueError(f"exercises[{pos}].number manque")
        if len(str(item.get("statement") or "").strip()) < 5:
            raise ValueError(f"exercises[{pos}].statement manque")
        if bool(item.get("has_figure")):
            crop = item.get("figure_crop")
            usable = (isinstance(crop, dict) and _box(crop.get("bbox")) is not None
                      and str(crop.get("page") or "") == "current")
            if not usable:
                item["has_figure"] = False
                item["figure_crop"] = None
                item["figure_missing_crop"] = True
    # Une boîte d'exercice sert au brouillon/admin, pas à comprendre le contenu.
    # Une coordonnée nulle ne doit donc jamais jeter tous les autres exercices
    # du lot. Elle se reconstruit du badge jusqu'au badge suivant. C'est
    # exactement ce que le découpage en COLONNES a rendu trivial : l'image ne
    # contient qu'une colonne, donc « l'exercice suivant » est simplement le
    # suivant dans la liste, sans avoir à deviner à quelle colonne il appartient.
    # Les crops de FIGURE restent, eux, strictement obligatoires.
    for pos, item in enumerate(items):
        if _box(item.get("exercise_bbox")) is not None:
            continue
        badge = _box(item.get("number_bbox"))
        if badge is None:
            item["exercise_bbox"] = [0, 0, 1000, 1000]
            item["exercise_bbox_repaired"] = True
            continue
        y0, y1 = max(0.0, badge[1] - 12), 1000.0
        for following in items[pos + 1:]:
            next_badge = _box((following or {}).get("number_bbox"))
            if next_badge is not None and next_badge[1] > y0:
                y1 = max(y0 + MIN_BOX_SIDE, next_badge[1] - 8)
                break
        item["exercise_bbox"] = [0.0, y0, BOX_SCALE, y1]
        item["exercise_bbox_repaired"] = True
    return data


def _page_box(value: Any, tile_x0: float = 0.0,
              tile_x1: float = 1.0) -> list[float] | None:
    """Remet une boîte locale à une COLONNE dans le repère de la double page."""
    box = _box(value)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    width = max(0.0, tile_x1 - tile_x0)
    return [BOX_SCALE * tile_x0 + width * x0, y0,
            BOX_SCALE * tile_x0 + width * x1, y1]


def _normalize(item: dict, page_index: int, title: str,
               *, tile_x0: float = 0.0, tile_x1: float = 1.0) -> dict:
    crop = item.get("figure_crop") if isinstance(item.get("figure_crop"), dict) else {}
    number = str(item.get("number") or "").strip()
    return {
        "number": number,
        "source_page": page_index,
        # titre CALCULÉ à partir des bandeaux relevés (§ _titles_by_banner), pas
        # rendu par le modèle : à lui de VOIR, à Python de trancher.
        "competency_title": str(title or "").strip(),
        "text": statement_mod.strip_leading_number(
            statement_mod.repair_latex_control_chars(str(item.get("statement") or "").strip()),
            number),
        "has_figure": bool(item.get("has_figure")),
        "figure_description": statement_mod.repair_latex_control_chars(
            str(item.get("figure_description") or "").strip()),
        "exercise_bbox": _page_box(item.get("exercise_bbox"), tile_x0, tile_x1),
        "number_bbox": _page_box(item.get("number_bbox"), tile_x0, tile_x1),
        "figure_bbox": _page_box(crop.get("bbox"), tile_x0, tile_x1),
        "figure_page": page_index,
        "exercise_bbox_repaired": bool(item.get("exercise_bbox_repaired")),
        # figure annoncée par le modèle mais sans rectangle exploitable : la
        # description reste, l'image sera ajoutée à la relecture.
        "figure_missing_crop": bool(item.get("figure_missing_crop")),
        "vision_extracted": True,
    }


def columns(width: int, count: int | None = None) -> list[tuple[int, int]]:
    """Bornes en pixels des colonnes d'exercices d'un raster de `width` pixels.

    Le découpage porte sur la BOÎTE DE CONTENU, pas sur le raster : celui-ci est
    une capture d'écran de lecteur PDF, et ses bords portent des flèches, des
    boutons et une vignette qui n'appartiennent pas à la page. Découper la
    largeur totale en quatre plaçait deux coupes au milieu des colonnes 1 et 4 ;
    les exercices coupés étaient alors soit lus deux fois, soit perdus (pages
    86-87 : trois de chaque). Avec la boîte, les trois coupes tombent dans les
    gouttières, où il n'y a par définition rien à couper.

    La mise en page ne varie pas d'une page à l'autre : les bornes sont donc des
    réglages (§ config), pas une détection refaite à chaque image."""
    count = int(settings.indigo_multipass_columns if count is None else count)
    if width <= 1 or count <= 1:
        return [(0, max(1, width))]
    x0 = max(0, min(width - 1, round(settings.indigo_multipass_page_x0 * width)))
    x1 = max(x0 + 1, min(width, round(settings.indigo_multipass_page_x1 * width)))
    inner = x1 - x0
    edges = [x0 + round(i * inner / count) for i in range(count + 1)]
    out: list[tuple[int, int]] = []
    for i in range(count):
        a, b = int(edges[i]), int(edges[i + 1])
        if b > a:
            out.append((a, b))
    return out or [(0, width)]


def extract_pages(db, doc, grade: str, page_indices: list[int], progress_cb=None) -> list[dict]:
    """Extrait tous les exercices dont le badge commence dans ``page_indices``.

    Les appels sont séquentiels et séparés par COLONNE, dans l'ordre de lecture
    (colonnes 1 à 4 du raster, c'est-à-dire page de gauche puis page de droite).
    On mémorise le dernier titre rose dans cet ordre : une section commencée en
    bas d'une colonne continue ainsi correctement sur la suivante.

    Les numéros d'exercices se suivent d'une colonne à l'autre. Ils sont donnés
    au modèle (`previous_number`) : c'est le repère le plus sûr pour savoir où
    commence un exercice quand une colonne débute au milieu d'un énoncé.
    """
    pages = sorted({int(i) for i in page_indices if 0 <= int(i) < doc.page_count})
    system = prompts.load("indigo", "multipass_extract")
    active_title = ""
    previous_number = ""
    out: list[dict] = []
    for pos, page_index in enumerate(pages, 1):
        raster = indigo_manual.raster_page(doc, page_index)
        width = int(raster.shape[1])
        bounds = columns(width)
        for index, (px0, px1) in enumerate(bounds, 1):
            if progress_cb:
                progress_cb(f"Vision DeepSeek : page {page_index + 1}, colonne "
                            f"{index}/{len(bounds)} ({pos}/{len(pages)})…")
            column = raster[:, px0:px1]
            # Les bandeaux sont LOCALISÉS ici, par leur couleur (§ find_banners).
            # Le modèle n'a plus qu'à les LIRE : c'est une tâche de lecture, pas
            # de jugement, et une lecture ne se laisse pas influencer par le
            # titre de la colonne précédente.
            found = [round(y0 * BOX_SCALE / max(1, column.shape[0]))
                     for y0, _ in find_banners(column)]
            payload = {
                "grade_level": grade,
                "current_page": page_index + 1,
                "column_index": index,
                "column_count": len(bounds),
                "banners": [{"y": y} for y in found],
                "previous_competency_title": active_title or None,
                "previous_number": previous_number or None,
                "image_order": ["current"],
            }
            try:
                data = indigo_llm.call_vision(
                    db, system, payload,
                    [indigo_manual.encode_png(column)],
                    f"indigo-vision-{grade}-{page_index + 1}-c{index}",
                    validator=_page_contract) or {}
            except Exception:
                # Une colonne illisible ne doit pas emporter les sept autres :
                # l'extraction continue et le journal dit laquelle a manqué.
                logger.exception("Indigo/Vision : page %s colonne %s illisible — "
                                 "colonne ignorée", page_index + 1, index)
                if progress_cb:
                    progress_cb(f"⚠ page {page_index + 1}, colonne {index} illisible "
                                "— colonne ignorée, l'extraction continue")
                continue
            items = [i for i in (data.get("exercises") or []) if isinstance(i, dict)]
            titles, active_title = _titles_by_banner(
                items, _read_banners(data.get("banners"), found), active_title)
            for item, title in zip(items, titles):
                out.append(_normalize(item, page_index, title,
                                      tile_x0=px0 / width, tile_x1=px1 / width))
                previous_number = out[-1]["number"] or previous_number
    out = _dedupe_by_number(out)
    logger.info("Indigo/Vision : %s — %s exercice(s) sur %s double-page(s), "
                "%s colonne(s) par page", grade, len(out), len(pages),
                settings.indigo_multipass_columns)
    return out


def find_banners(column) -> list[tuple[int, int]]:
    """Bandeaux roses d'une colonne : liste de (y0, y1) en PIXELS, de haut en bas.

    Détection par couleur, sans modèle : le rose des pastilles n'apparaît nulle
    part ailleurs en aplat large. Une bande n'est retenue que si UNE de ses
    lignes est rose sur au moins `BANNER_ROW_PEAK` de la largeur — le texte
    blanc creuse les lignes du milieu, mais le haut et le bas de la pastille
    sont pleins."""
    if column is None or getattr(column, "size", 0) == 0:
        return []
    distance = np.abs(column.astype(np.int32) - np.array(BANNER_BGR)).sum(axis=2)
    coverage = (distance < BANNER_TOLERANCE).mean(axis=1)
    bands: list[tuple[int, int]] = []
    start = None
    for y, value in enumerate(coverage > BANNER_ROW_MIN):
        if value and start is None:
            start = y
        elif not value and start is not None:
            bands.append((start, y))
            start = None
    if start is not None:
        bands.append((start, len(coverage)))
    return [(a, b) for a, b in bands
            if b - a >= BANNER_MIN_HEIGHT and coverage[a:b].max() >= BANNER_ROW_PEAK]


def _read_banners(rendered, positions: list[int]) -> list[dict]:
    """Recolle les titres LUS par le modèle aux positions TROUVÉES par la CV.

    L'ordre fait la correspondance : les positions viennent de la détection, les
    titres de la lecture. Si le modèle en rend moins que prévu, les bandeaux
    surnuméraires sont ignorés — mieux vaut un report que d'inventer un titre."""
    titles = [str((b or {}).get("title") or "").strip()
              for b in (rendered or []) if isinstance(b, dict)]
    return [{"y": y, "title": title}
            for y, title in zip(positions, titles) if title]


def _titles_by_banner(items: list[dict], banners, carried: str) -> tuple[list[str], str]:
    """Rattache chaque exercice au dernier BANDEAU situé au-dessus de lui.

    Le modèle rendait auparavant lui-même le titre de chaque exercice, et il
    s'ancrait sur le titre qu'on lui passait : sur la page 86, la pastille
    « Calculer une moyenne » était sous ses yeux, en haut de la colonne 4, et il
    recopiait quand même la compétence de la colonne précédente — treize
    exercices rangés sous la mauvaise compétence, sans que rien ne le signale.

    Il ne rend plus qu'une OBSERVATION (les bandeaux et leur hauteur) ; le
    rattachement, lui, est un calcul. Retourne (titres, titre actif en fin
    d'image), ce dernier servant de report à la colonne suivante."""
    found = []
    for banner in (banners or []):
        if not isinstance(banner, dict):
            continue
        title = str(banner.get("title") or "").strip()
        try:
            y = float(banner.get("y"))
        except (TypeError, ValueError):
            y = 0.0
        if title:
            found.append((max(0.0, min(BOX_SCALE, y)), title))
    found.sort()
    titles: list[str] = []
    active = carried
    for item in items:
        box = _box(item.get("number_bbox")) or _box(item.get("exercise_bbox"))
        # Sans repère vertical, l'exercice hérite du titre courant : c'est le
        # comportement le moins surprenant, et le plus souvent le bon.
        top = box[1] if box else None
        title = carried
        for y, name in found:
            if top is None or y <= top:
                title = name
        titles.append(title or carried)
        if titles[-1]:
            active = titles[-1]
    if found:
        active = found[-1][1]
    return titles, active


def _dedupe_by_number(items: list[dict]) -> list[dict]:
    """Un numéro = un exercice. Filet de sécurité du découpage en colonnes.

    Les numéros du manuel se suivent et ne se répètent pas : c'est le repère le
    plus sûr dont on dispose. Si une coupe tombe malgré tout dans une colonne
    (mise en page inattendue, réglage inadapté), l'exercice est lu deux fois,
    partiellement de chaque côté. On garde alors la lecture la plus complète —
    et l'énoncé le plus long est celui auquel il manque le moins de texte."""
    best: dict[str, dict] = {}
    order: list[str] = []
    for item in items:
        key = str(item.get("number") or "").strip()
        if not key:
            continue
        current = best.get(key)
        if current is None:
            order.append(key)
        if current is None or len(item.get("text") or "") > len(current.get("text") or ""):
            best[key] = item
    return [best[k] for k in order]
