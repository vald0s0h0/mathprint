"""Indigo — orchestration de l'onglet Exercices (admin).

Pipeline classique d'un run (`IndigoExtraction`) : pour chaque cible {compétence,
pages élève, pages prof} choisie dans l'assistant :
  1. OCR Mistral des pages élève et prof (mini-PDF réduit aux pages voulues) ;
  2. segmentation en exercices par les blocs-titres numérotés (repère commun :
     raster de la page à indigo_manual.RASTER_DPI) ;
  3. crop PNG de chaque exercice (+ crop figure si présente) ;
  4. CV (indigo_cv) : couleur du badge/titre → type + difficulté ;
  5. appariement de la correction du manuel prof PAR NUMÉRO ;
  6. DeepSeek pro (indigo_gemini) : mise au propre → 1 exercice app corrigeable,
     puis vérification finale (indigo_verify) contre la source ;
  7. écriture des lignes `IndigoExercise` en statut brouillon.

Le mode QCM multipass prend une voie dédiée : deux bornes de pages élève,
extraction structurée et crops par DeepSeek Vision, rattachement de compétence
en passe 1, puis génération, résolution indépendante, mise en page et retouche.
Aucun exercice n'y est renvoyé en génération : ce qui cloche se répare sur place
et ce qui résiste part en brouillon avec son badge. Il n'utilise ni OCR Mistral,
ni prédécoupage Gemini, ni manuel professeur.

Le run tourne dans une file de fond dédiée (thread), l'UI interroge le statut.
Les exercices VALIDÉS seront ensuite PUBLIÉS (bake) vers des fichiers
versionnés — non couvert par cette tranche (cf. `publish`).
"""
from __future__ import annotations

import io
import json
import logging
import re
import shutil
import threading
import unicodedata
import zipfile
from datetime import datetime, timezone

import numpy as np

from ..config import _APP_DIR, settings
from ..db import SessionLocal
from ..models import (Competency, CompetencyFramework, GeneratedExercise,
                      IndigoExercise, IndigoExtraction, uid)
from . import (exercise_gen, figures, indigo_check, indigo_cv, indigo_fields,
               indigo_gemini, indigo_llm, indigo_manual, indigo_multipass,
               indigo_offpeak, indigo_qcm, indigo_segment, indigo_verify,
               indigo_vision, providers, scoring)
from . import statement as statement_mod

logger = logging.getLogger("app.indigo")

CROP_PAD_PX = 10                     # marge autour du crop (badge/cadre non rognés)
_NUMBER_RE = re.compile(r"^\s*(\d{1,3})\b")
# plage saisie dans l'assistant : « 34-67 » (bornes INCLUSES), « 34 » (unique).
# Tirets ascii/typographiques et « à » acceptés (« 34 à 67 »).
_RANGE_RE = re.compile(r"^\s*(\d+)\s*(?:[-–—]|à|a)?\s*(\d+)?\s*$")


def parse_int_range(spec: str | None) -> list[int]:
    """« 34-67 » → [34, 35, …, 67] (bornes incluses) ; « 34 » → [34] ; vide → []."""
    if spec is None:
        return []
    m = _RANGE_RE.match(str(spec))
    if not m:
        return []
    a = int(m.group(1))
    b = int(m.group(2)) if m.group(2) else a
    if b < a:
        a, b = b, a
    return list(range(a, b + 1))


def normalize_target(t: dict) -> dict:
    """Développe les plages saisies (pages + numéros d'exercices) en listes
    concrètes. Les pages sont saisies en numéro PDF 1-based (comme l'ancien
    sélecteur « p.N ») → indices 0-based. « numbers » = plage des numéros
    d'exercices (source de vérité du découpage). Les listes déjà fournies
    (`eleve_pages`/`prof_pages`) sont conservées si aucune plage n'est donnée
    (rétrocompatibilité)."""
    out = dict(t)
    er = str(t.get("eleve_page_range") or "").strip()
    pr = str(t.get("prof_page_range") or "").strip()
    # Les listes CONCRÈTES l'emportent sur les plages. Une cible déduite de
    # l'index (services.indigo_index) porte les deux : ses listes sont exactes
    # (pages et numéros réellement repérés), ses plages ne sont là que pour être
    # lisibles dans le journal. Ré-étaler la plage écraserait une liste de
    # numéros trouée par les exercices d'une autre compétence.
    if er and not t.get("eleve_pages"):
        out["eleve_pages"] = [p - 1 for p in parse_int_range(er) if p >= 1]
    if pr and not t.get("prof_pages"):
        out["prof_pages"] = [p - 1 for p in parse_int_range(pr) if p >= 1]
    if not t.get("numbers"):
        out["numbers"] = parse_int_range(t.get("number_range"))
    return out
# Compétences mathématiques officielles (folded -> affichage). Dans le manuel
# Indigo, un PROBLÈME affiche, JUSTE APRÈS son titre, une petite ligne listant
# les compétences travaillées (« Raisonner, Calculer »). Cette LIGNE DE
# MARQUEURS est le SEUL indice d'un problème — et surtout PAS la simple présence
# du verbe « Calculer » au fil d'un énoncé ordinaire (cause des faux problèmes).
_COMPETENCES = {"chercher": "Chercher", "modeliser": "Modéliser",
                "representer": "Représenter", "raisonner": "Raisonner",
                "calculer": "Calculer", "communiquer": "Communiquer"}
# Parmi elles, celles qui DÉCLENCHENT le statut « problème » (liste explicite de
# l'utilisateur : Représenter, Raisonner, Calculer, Modéliser). Chercher et
# Communiquer n'apparaissent jamais SEULES dans le manuel (toujours accolées à
# l'une des quatre) : on les garde comme tags mais elles ne suffisent pas.
_PROBLEM_MARKERS = {"representer", "raisonner", "calculer", "modeliser"}
_FIGURE_TYPES = {"image", "picture", "figure"}
# séparateurs possibles entre marqueurs sur la ligne (virgule, « et », puces,
# espaces) ; l'OCR perd parfois la virgule → on tolère aussi l'espace simple.
_MARKER_SPLIT = re.compile(r"[,/;•·]|\s+|\bet\b")

# --------------------------------------------------------- besoin de figure
#
# COUCHE SUPPLÉMENTAIRE (demande utilisateur, 31/07) : détecter qu'un exercice a
# BESOIN d'un schéma/image pour être compréhensible, indépendamment du succès de
# l'étape géométrique (crop/CV) qui, elle, ne fait que constater ce que l'OCR a
# effectivement isolé comme bloc "image". Deux signaux, combinés dans
# `_persist_exercise` :
#   1. un indice TEXTUEL déterministe (ci-dessous) sur l'énoncé BRUT du manuel
#      (avant réécriture Claude) — un exercice qui dit « ci-contre »/« la
#      figure »/« le schéma » dépend visiblement d'un visuel ;
#   2. le jugement de Claude après réécriture (champ "needs_figure" du contrat,
#      cf. services.indigo_gemini) — lui seul peut juger qu'un énoncé, une fois
#      remis au propre, reste incompréhensible sans voir l'original.
# Quand le besoin est confirmé mais qu'aucune figure n'a été isolée par l'OCR,
# `_fallback_figure_from_crop` rattache l'extrait COMPLET du manuel (déjà cropé
# pour l'aperçu admin) plutôt que de publier un énoncé borgne — l'admin garde la
# main pour le recadrer ou le supprimer (cf. nudge_figure/remove_figure).
# La règle elle-même vit dans services.indigo_check, qui l'applique aussi aux
# énoncés produits (« tu parles d'une figure, il en faut une ») : une seule
# définition des deux côtés, sinon elles divergent.
_mentions_figure = indigo_check.mentions_figure


def _detach_figure(row: "IndigoExercise") -> None:
    """Détache la figure d'une ligne : l'exercice ne s'appuie pas dessus.

    Le fichier reste sur le disque (l'admin peut vouloir le revoir) ; seule la
    ligne cesse d'y renvoyer. `figure_path` est vidé avec une CHAÎNE VIDE, pas
    None : la colonne est NOT NULL, et un None y lève à l'écriture."""
    row.has_figure = False
    row.figure_path = ""
    row.figure_box_json = None


def _fallback_figure_from_crop(row: "IndigoExercise") -> None:
    """Filet de sécurité : le besoin de figure est confirmé mais l'OCR n'en a
    isolé aucune (segmentation ratée, ou exercice reconnu par le seul
    pré-découpage Gemini, sans géométrie). Si un extrait du manuel existe
    (`crop_path`), on le COPIE (jamais le même fichier que `crop_path` : une
    suppression de figure ne doit pas effacer l'extrait de référence admin) et
    on l'attache comme figure — imparfait (montre tout l'exercice, pas la seule
    figure) mais toujours préférable à un énoncé muet. Sans crop (placeholder
    sans géométrie), on ne peut rien attacher : `figure_required` reste seul à
    signaler le manque à l'admin."""
    if not row.crop_path:
        return
    src = crop_abs_path(row.crop_path)
    if not src.exists():
        return
    fig_rel = f"indigo/drafts/{row.id}_fig_fallback.png"
    crop_abs_path(fig_rel).write_bytes(src.read_bytes())
    box = row.crop_box_json or {}
    row.has_figure = True
    row.figure_path = fig_rel
    row.figure_box_json = {"page_index": box.get("page_index", row.source_page),
                           "x0": box.get("x0", 0), "y0": box.get("y0", 0),
                           "x1": box.get("x1", 0), "y1": box.get("y1", 0)}


# ------------------------------------------------------------------ dossiers

def _draft_dir():
    d = settings.data_dir / "indigo" / "drafts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def crop_abs_path(rel: str):
    """Chemin absolu d'un crop stocké (rel = chemin relatif à data_dir)."""
    return settings.data_dir / rel


# --------------------------------------------------------------- texte / tags

def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s or "")
                   if not unicodedata.combining(c)).lower()


def _competence_line(line: str) -> list[str]:
    """Si `line` est une LIGNE DE MARQUEURS (uniquement des compétences séparées
    par virgules / « et » / espaces), renvoie les compétences reconnues (pour
    l'affichage) ; sinon []. Ainsi « Raisonner, Calculer » est reconnu, mais
    « Calculer le PGCD de 24 et 36 » ne l'est pas (mots en trop)."""
    folded = _fold(line).strip(" .:·•\t")
    if not folded:
        return []
    tokens = [t for t in _MARKER_SPLIT.split(folded) if t]
    if not (1 <= len(tokens) <= 4) or not all(t in _COMPETENCES for t in tokens):
        return []
    seen, out = set(), []
    for t in tokens:                      # dé-doublonne en gardant l'ordre du manuel
        if t not in seen:
            seen.add(t)
            out.append(_COMPETENCES[t])
    return out


def _find_markers(text: str) -> tuple[list[str], int]:
    """Cherche la ligne de marqueurs de compétences dans les premières lignes
    (juste après le titre). Retourne (marqueurs, index de ligne) ou ([], -1)."""
    for i, ln in enumerate((text or "").split("\n")[:4]):
        markers = _competence_line(ln)
        if markers:
            return markers, i
    return [], -1


# --------------------------------------------------------- OCR & segmentation

def _ocr_pages(db, doc, page_indices: list[int], tag: str, *,
               grade: str = "", which: str = "", use_index: bool = True) -> list[dict]:
    """Blocs des pages `page_indices` : {source_page, dims, blocks} dans l'ordre.

    L'INDEX D'ABORD (services.indigo_index) : une page déjà lue n'est jamais
    relue, ce qui rend une extraction rejouable à l'identique et gratuite. Seules
    les pages absentes de l'index partent à l'OCR Mistral. `use_index=False` sert
    à la CONSTRUCTION de l'index elle-même, qui ne peut évidemment pas se lire.
    """
    if not page_indices:
        return []
    if use_index and grade and which:
        from . import indigo_index
        cached = {i: indigo_index.page_entry(grade, which, i) for i in page_indices}
        missing = [i for i, entry in cached.items() if entry is None]
        if not missing:
            return [cached[i] for i in page_indices]
        if len(missing) < len(page_indices):
            fresh = {e["source_page"]: e for e in
                     _ocr_pages(db, doc, missing, tag, use_index=False)}
            return [cached[i] or fresh[i] for i in page_indices if cached[i] or i in fresh]
    # Repli OCR : il exige le PDF. Sur une instance qui travaille au PACK (pages
    # rendues d'avance, cf. indigo_pack), il n'y a pas de PDF à découper — une
    # page absente de l'index est donc définitivement absente, et le dire vaut
    # mieux que planter dans build_mini_pdf sur un objet qui n'est pas un PDF.
    if doc is None or getattr(doc, "raster_page", None) is not None:
        raise RuntimeError(
            f"Pages {[i + 1 for i in page_indices]} absentes de l'index et manuel "
            f"indisponible sur cette instance : impossible de les lire. Réimporte "
            f"un pack de travail complet, ou dépose les PDF des manuels.")
    pdf_bytes = indigo_manual.build_mini_pdf(doc, page_indices)
    data = providers.mistral_ocr(db, f"indigo_ocr_{tag}", pdf_bytes,
                                 len(page_indices), correlation_id=f"indigo-{tag}")
    pages = sorted(data.get("pages") or [], key=lambda p: p.get("index", 0))
    out = []
    for i, page in enumerate(pages):
        if i >= len(page_indices):
            break
        out.append({"source_page": page_indices[i],
                    "dims": page.get("dimensions") or {},
                    "blocks": page.get("blocks") or []})
    return out


# blocs de mise en page sans valeur d'exercice
_SKIP_BLOCKS = {"header", "footer", "aside_text"}
# écart max toléré entre deux numéros d'exercice consécutifs (couvre un numéro
# raté par l'OCR). Distingue un vrai début (13→14) d'une sous-question (« 1. »
# après l'exercice 38) ou d'un nombre parasite (« 153 est divisible… »).
_SEQ_GAP = 6
_LEAD_NUM = re.compile(r"\s*(\d{1,3})[\s.)]")


def _leading_num(content: str) -> int | None:
    m = _LEAD_NUM.match(content or "")
    return int(m.group(1)) if m else None


def _x0(b: dict) -> float:
    return float(b.get("top_left_x", 0))


def _y0(b: dict) -> float:
    return float(b.get("top_left_y", 0))


def _column_centers(xs: list[float], width: float) -> list[float]:
    """Centres des colonnes du manuel, déduits des abscisses des blocs NUMÉROTÉS
    (les vrais bords gauches de colonne). Amas par proximité, puis fusion des
    amas trop proches (< 150 px) pour absorber un outlier isolé (ex. un nombre
    parasite au milieu d'une colonne)."""
    xs = sorted(xs)
    if not xs:
        return [width * 0.25, width * 0.75]
    clusters: list[list[float]] = [[xs[0]]]
    for x in xs[1:]:
        if x - clusters[-1][-1] > 60:
            clusters.append([x])
        else:
            clusters[-1].append(x)
    centers = [sum(c) / len(c) for c in clusters]
    sizes = [len(c) for c in clusters]
    changed = True
    while changed and len(centers) > 1:
        changed = False
        for i in range(len(centers) - 1):
            if centers[i + 1] - centers[i] < 150:
                tot = sizes[i] + sizes[i + 1]
                centers[i] = (centers[i] * sizes[i] + centers[i + 1] * sizes[i + 1]) / tot
                sizes[i] = tot
                del centers[i + 1], sizes[i + 1]
                changed = True
                break
    return centers


def _order_blocks(blocks: list[dict], width: float):
    """Blocs en ordre de LECTURE colonne par colonne (manuel multi-colonnes)."""
    numbered_x = [_x0(b) for b in blocks if _leading_num(b.get("content"))]
    centers = _column_centers(numbered_x, width)

    def col_of(b: dict) -> int:
        cx = _x0(b)
        return min(range(len(centers)), key=lambda i: abs(cx - centers[i]))

    return sorted(blocks, key=lambda b: (col_of(b), _y0(b)))


def _match_competency(title: str, comps: list):
    """La compétence dont le libellé correspond à ce bloc-titre, ou None (un
    titre « QUESTIONS FLASH » / « ÂGE EXPERT » ne matche aucune compétence)."""
    t = _fold(title.lstrip("#"))
    if len(t) < 6:
        return None
    for c in comps:
        lab = _fold(c.label)
        if lab and (lab == t or (len(t) > 10 and (lab in t or t in lab))):
            return c
    return None


def _flatten_text(blocks: list[dict]) -> str:
    lines = []
    for b in blocks:
        if b.get("type") in _FIGURE_TYPES:
            continue
        c = str(b.get("content") or "").strip()
        if c:
            lines.append(c)
    return "\n".join(lines)


def _union_box(blocks: list[dict]) -> tuple[float, float, float, float]:
    xs0 = [float(b.get("top_left_x", 0)) for b in blocks]
    ys0 = [float(b.get("top_left_y", 0)) for b in blocks]
    xs1 = [float(b.get("bottom_right_x", 0)) for b in blocks]
    ys1 = [float(b.get("bottom_right_y", 0)) for b in blocks]
    return min(xs0), min(ys0), max(xs1), max(ys1)


def _enrich(ex: dict) -> dict:
    """Complète un exercice segmenté : texte, figure, problème/tags/titre."""
    # le 1er nombre de l'OCR est le NUMÉRO de l'exercice dans le manuel : on le
    # retire du texte donné à Gemini pour que l'énoncé ne commence pas par lui.
    text = statement_mod.strip_leading_number(_flatten_text(ex["blocks"]), ex["number"])
    # problème SSI une LIGNE DE MARQUEURS de compétences suit le titre (et
    # contient l'une des 4 compétences déclencheuses) — pas la simple présence
    # d'un verbe « Calculer » dans l'énoncé.
    markers, mi = _find_markers(text)
    is_probleme = any(_fold(m) in _PROBLEM_MARKERS for m in markers)
    if mi >= 0:                            # retire la ligne de marqueurs de l'énoncé
        parts = text.split("\n")
        del parts[mi]
        text = "\n".join(parts).strip()
    ex["text"] = text
    figs = [b for b in ex["blocks"] if b.get("type") in _FIGURE_TYPES]
    ex["has_figure"] = bool(figs)
    ex["figure_blocks"] = figs
    ex["tags"] = markers if is_probleme else []
    ex["is_probleme"] = is_probleme
    # titre = contenu du bloc-titre sans le numéro de tête (n'a de sens que pour
    # un problème ; un exercice ordinaire n'a pas de titre)
    title = _NUMBER_RE.sub("", str(ex["start_block"].get("content") or ""), count=1).strip()
    ex["title"] = title if is_probleme else ""
    return ex


def _segment_target(page: dict, target, comps: list) -> list[dict]:
    """Tous les exercices d'une page appartenant à la compétence CIBLE. Un
    exercice débute au bloc dont le numéro POURSUIT la séquence croissante
    (13, 14, 15…) ; ses blocs suivants (suite d'énoncé, figure) lui sont
    rattachés. La compétence courante change dès qu'un bloc-titre correspond au
    libellé d'une AUTRE compétence — les exercices d'une autre compétence sur la
    même page sont ainsi écartés (cf. demande utilisateur)."""
    blocks = [b for b in page["blocks"] if b.get("type") not in _SKIP_BLOCKS]
    ordered = _order_blocks(blocks, float(page["dims"].get("width", 0)))
    current, last, cur = target, 0, None
    exercises: list[dict] = []
    for b in ordered:
        n = _leading_num(b.get("content"))
        # un bloc-titre SANS numéro est un en-tête de section : il change la
        # compétence courante s'il correspond au libellé d'une compétence
        # (« Reconnaître un nombre premier »), sinon c'est une sous-section
        # neutre (« QUESTIONS FLASH », « ÂGE EXPERT ») qu'on ignore.
        if b.get("type") == "title" and n is None:
            m = _match_competency(str(b.get("content") or ""), comps)
            if m is not None:
                current, cur = m, None
            continue
        if n is not None and (last == 0 or last < n <= last + _SEQ_GAP):
            last = n
            cur = {"number": str(n), "source_page": page["source_page"],
                   "dims": page["dims"], "blocks": [b], "start_block": b,
                   "competency": current}
            exercises.append(cur)
        elif cur is not None:
            cur["blocks"].append(b)
    return [_enrich(e) for e in exercises if e["competency"].id == target.id]


def _segment_corrections(page: dict) -> dict[str, str]:
    """num d'exercice -> texte du corrigé (manuel prof) : même détection par
    séquence croissante, SANS filtre de compétence."""
    blocks = [b for b in page["blocks"] if b.get("type") not in _SKIP_BLOCKS]
    ordered = _order_blocks(blocks, float(page["dims"].get("width", 0)))
    out: dict[str, str] = {}
    last, cur = 0, None
    for b in ordered:
        n = _leading_num(b.get("content"))
        if b.get("type") == "title" and n is None:
            continue
        if n is not None and (last == 0 or last < n <= last + _SEQ_GAP):
            last, cur = n, str(n)
            out[cur] = _flatten_text([b])
        elif cur is not None:
            out[cur] = (out[cur] + "\n" + _flatten_text([b])).strip()
    return out


def _ordered_text(page: dict) -> str:
    """Texte OCR d'une page en ordre de LECTURE (colonne par colonne), figures
    et mobilier de page retirés — envoyé au pré-découpage Gemini."""
    blocks = [b for b in page["blocks"] if b.get("type") not in _SKIP_BLOCKS]
    ordered = _order_blocks(blocks, float(page["dims"].get("width", 0)))
    return _flatten_text(ordered)


def _segment_by_numbers(page: dict, comp, expected: set[int]) -> list[dict]:
    """Découpe une page en s'appuyant sur la PLAGE de numéros attendue (source de
    vérité). Un bloc DÉBUTE un exercice ssi son numéro de tête est dans
    `expected` et STRICTEMENT supérieur au dernier commencé (croissance stricte) :
    plus besoin de deviner par écart (`_SEQ_GAP`) ni de filtrer par titre de
    compétence — la plage définit l'appartenance. Un nombre parasite hors plage
    (« 153 est divisible… ») est ignoré."""
    blocks = [b for b in page["blocks"] if b.get("type") not in _SKIP_BLOCKS]
    ordered = _order_blocks(blocks, float(page["dims"].get("width", 0)))
    exercises: list[dict] = []
    cur, last = None, 0
    for b in ordered:
        n = _leading_num(b.get("content"))
        if n is not None and n in expected and n > last:
            last = n
            cur = {"number": str(n), "source_page": page["source_page"],
                   "dims": page["dims"], "blocks": [b], "start_block": b,
                   "competency": comp}
            exercises.append(cur)
        elif cur is not None:
            cur["blocks"].append(b)
    return [_enrich(e) for e in exercises]


def _segment_corrections_by_numbers(page: dict, expected: set[int]) -> dict[str, str]:
    """Corrigés du manuel prof découpés par la même plage de numéros (bien plus
    robuste que la détection par séquence : on connaît les numéros exacts)."""
    blocks = [b for b in page["blocks"] if b.get("type") not in _SKIP_BLOCKS]
    ordered = _order_blocks(blocks, float(page["dims"].get("width", 0)))
    out: dict[str, str] = {}
    cur, last = None, 0
    for b in ordered:
        n = _leading_num(b.get("content"))
        if n is not None and n in expected and n > last:
            last, cur = n, str(n)
            out[cur] = _flatten_text([b])
        elif cur is not None:
            out[cur] = (out[cur] + "\n" + _flatten_text([b])).strip()
    return out


def _placeholder_exercise(number: str, text: str, eleve_pages: list[int]) -> dict:
    """Exercice reconnu par le pré-découpage Gemini mais SANS géométrie (l'OCR a
    fusionné son bloc avec le voisin) : on le garde quand même — le crop du
    manuel n'est qu'une référence, un exercice ne doit jamais manquer. On y
    applique la MÊME détection problème/marqueurs qu'un exercice géométrique."""
    body = statement_mod.strip_leading_number(text or "", number)
    markers, mi = _find_markers(body)
    is_probleme = any(_fold(m) in _PROBLEM_MARKERS for m in markers)
    if mi >= 0:
        parts = body.split("\n")
        del parts[mi]
        body = "\n".join(parts).strip()
    return {"number": str(number), "source_page": eleve_pages[0] if eleve_pages else 0,
            "dims": {}, "blocks": [], "start_block": None, "text": body,
            "has_figure": False, "figure_blocks": [], "tags": markers if is_probleme else [],
            "is_probleme": is_probleme, "title": ""}


def _framework_competencies(db, comp) -> list:
    return db.query(Competency).filter_by(framework_id=comp.framework_id).all()


# ------------------------------------------------------------------- crops

def _scale_box(box, dims, rw: int, rh: int):
    """(x0,y0,x1,y1) en px Mistral → px du raster (rw×rh)."""
    dw = float(dims.get("width") or rw) or rw
    dh = float(dims.get("height") or rh) or rh
    sx, sy = rw / dw, rh / dh
    x0, y0, x1, y1 = box
    return x0 * sx, y0 * sy, x1 * sx, y1 * sy


def _clamp_box(x0, y0, x1, y1, w, h, pad=0):
    return (max(0, int(x0 - pad)), max(0, int(y0 - pad)),
            min(w, int(x1 + pad)), min(h, int(y1 + pad)))


def _save_crop(raster: np.ndarray, box, dest_rel: str):
    x0, y0, x1, y1 = box
    crop = raster[y0:y1, x0:x1]
    if crop.size == 0:
        return False
    dest = crop_abs_path(dest_rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(indigo_manual.encode_png(crop))
    return True


# ------------------------------------------------------------------ pipeline

def _process_target(db, doc_eleve, doc_prof, grade: str, target: dict,
                    extraction_id: str, progress_cb, states: list | None = None) -> int:
    comp = db.get(Competency, target.get("competency_id"))
    if comp is None:
        logger.warning("Indigo : compétence %s introuvable, cible ignorée",
                       target.get("competency_id"))
        return 0
    mode = indigo_llm.mode(db)
    if mode == indigo_llm.MODE_MULTIPASS:
        # HEURES CREUSES — le portillon est franchi ICI, avant le premier appel
        # payant de la cible (OCR compris) : la cible entière attend l'ouverture
        # plutôt que de dépenser la moitié de son budget hors plage. Les appels
        # suivants repassent par le même portillon, exercice par exercice
        # (§ _run_multipass), ce qui suffit à « ne pas commencer un nouvel appel »
        # après la fermeture sans jamais couper celui qui tourne.
        indigo_offpeak.wait_until_open(db, progress_cb=progress_cb)
    comps = _framework_competencies(db, comp)   # pour reconnaître les titres de compétence
    eleve_pages = [int(p) for p in target.get("eleve_pages") or []]
    prof_pages = [int(p) for p in target.get("prof_pages") or []]
    expected = [int(n) for n in target.get("numbers") or []]   # plage 34→67 (source de vérité)
    exp_set = set(expected)

    progress_cb(f"OCR élève ({comp.short_id or comp.code})…")
    eleve = _ocr_pages(db, doc_eleve, eleve_pages, f"eleve-{comp.code}",
                       grade=grade, which="eleve")
    progress_cb(f"Corrigés prof ({comp.short_id or comp.code})…")
    # Les corrigés sont du TEXTE : ils vivent dans l'index, et se lisent donc
    # même sans le PDF du prof (instance équipée d'un pack). Leur absence
    # dégrade l'exercice, elle ne doit jamais faire échouer l'extraction.
    try:
        prof = _ocr_pages(db, doc_prof, prof_pages, f"prof-{comp.code}",
                          grade=grade, which="prof") if prof_pages else []
    except RuntimeError as e:
        progress_cb(f"⚠ Corrigés du prof indisponibles ({e}) — les exercices "
                    f"seront créés sans corrigé de référence.")
        prof = []

    corrections = _collect_corrections(db, comp, grade, prof, expected, exp_set, progress_cb)

    exercises = _collect_exercises(db, comp, comps, grade, eleve, eleve_pages,
                                   expected, exp_set, progress_cb)
    logger.info("Indigo : %s — %s exercice(s) détecté(s), %s corrigé(s) prof",
                comp.code, len(exercises), len(corrections))
    if not exercises:
        return 0

    # 1) découpe (crop) + couleur (CV) en LOCAL, sans LLM
    prepared: list[tuple] = []
    for order, ex in enumerate(exercises):
        try:
            pr = _prepare_exercise(doc_eleve, grade, comp, ex, corrections,
                                   extraction_id, order)
            if pr is not None:
                prepared.append(pr)
        except Exception:
            logger.exception("Indigo : découpe de l'exercice n°%s échouée",
                             ex.get("number"))
    progress_cb(f"{comp.short_id or comp.code} : {len(prepared)} exercice(s) "
                "découpé(s), adaptation…")

    # 2-4) génération + persistance, selon le MODE choisi dans l'onglet :
    #   • classique : adaptation libre (indigo_gemini) puis relecture (indigo_verify) ;
    #   • QCM only  : conversion en QCM vérifiés (indigo_qcm), un TRIO de lignes
    #     par exercice source (base + dérivé facile + dérivé difficile) ;
    #   • multipass : cinq passes DeepSeek Flash par exercice (indigo_multipass),
    #     trio publié DÈS qu'il est prêt, source douteuse écartée.
    # Les deux rendent le même quadruplet, pour que le compte rendu de fin de
    # cible (ci-dessous) reste écrit UNE seule fois.
    if mode == indigo_llm.MODE_MULTIPASS:
        made, adapted_ok, stopped, errors = _run_multipass(
            db, comp, grade, prepared, progress_cb, states)
    elif mode == indigo_llm.MODE_QCM:
        made, adapted_ok, stopped, errors = _run_qcm(
            db, comp, grade, prepared, progress_cb)
    else:
        made, adapted_ok, stopped, errors = _run_classic(
            db, comp, grade, prepared, progress_cb)

    # RENDRE VISIBLE l'échec d'adaptation, ET SA CAUSE : un exercice non adapté
    # est un repli OCR brut (short_text, guide/corrigé « à compléter »), pas une
    # « mauvaise génération ». Le message doit nommer la cause dès qu'il en
    # manque UN (pas seulement quand il n'y en a aucun) : c'est l'absence de
    # cause sur un « 1/21 adapté(s) » qui a rendu l'incident A1.3 indéchiffrable.
    fallback = made - adapted_ok
    if mode == indigo_llm.MODE_MULTIPASS:
        # Le mode multipass ne fait PAS de repli OCR brut : une source douteuse
        # est écartée (REJECTED_SOURCE), une génération qui n'aboutit pas est
        # abandonnée (REJECTED_GENERATION). Son compte rendu est écrit par
        # `_run_multipass`, qui seul connaît la répartition des états — le
        # message générique ci-dessous parlerait de replis qui n'existent pas.
        pass
    elif fallback and made:
        prov = indigo_llm.label(db)
        if indigo_llm.offline(db):
            cause = f"clé {prov} absente/inactive (adaptation en mode hors-ligne)"
        elif stopped:
            cause = f"{stopped} — plafond de dépense quotidien"
        elif errors:
            cause = f"erreurs {prov} : " + " ; ".join(dict.fromkeys(errors))[:300]
        else:
            cause = ("sorties refusées par le validateur (format non conforme) — "
                     "voir les journaux serveur pour le détail par exercice")
        level = "⚠" if adapted_ok else "⛔"
        progress_cb(f"{level} {comp.short_id or comp.code} : {adapted_ok}/{made} "
                    f"exercice(s) adapté(s), {fallback} en repli OCR brut — {cause}. "
                    f"Vérifie Paramètres → Fournisseurs ({prov}) et la page Coûts.")
    else:
        progress_cb(f"{comp.short_id or comp.code} : {adapted_ok}/{made} exercice(s) "
                    f"adapté(s)")
    return made


def _run_classic(db, comp, grade, prepared, progress_cb) -> tuple[int, int, str, list[str]]:
    """Mode CLASSIQUE : adaptation libre par lots, relecture finale, persistance.

    Retourne (exercices créés, exercices adaptés, cause d'arrêt, erreurs)."""
    # 2) adaptation DeepSeek pro PAR LOTS de 5 à 7 (moins d'appels, réponses plus
    #    courtes donc plus fiables — cf. demande utilisateur « lots de 5 à 7
    #    selon le nombre »). On COLLECTE les (row, manual, valid) sans persister :
    #    la vérification finale (étape 3) travaille sur tout le lot d'un coup.
    triples: list[tuple] = []
    errors: list[str] = []
    stopped = ""            # cause d'un ARRÊT net (plafond de dépense atteint)
    batch_size = indigo_gemini.choose_batch_size(len(prepared))
    for i in range(0, len(prepared), batch_size):
        chunk = prepared[i:i + batch_size]
        if stopped:         # plafond atteint : les appels suivants échoueraient tous
            triples.extend((row, manual, None) for row, manual in chunk)
            continue
        try:
            adapted = indigo_gemini.adapt_batch(db, comp, grade,
                                                [m for _r, m in chunk], errors)
        except providers.BudgetExceeded as e:
            stopped = str(e)
            adapted = {}
        for row, manual in chunk:
            valid = adapted.get(str(manual["number"]))
            if valid is None and not stopped:
                # manquant/refusé dans le lot : 2e chance en SOLO avant l'OCR brut
                try:
                    valid = indigo_gemini.adapt_one(db, comp, grade, manual)
                except providers.BudgetExceeded as e:
                    stopped = str(e)
            triples.append((row, manual, valid))
        done = min(i + batch_size, len(prepared))
        n_ok = sum(1 for _r, _m, v in triples if v is not None)
        progress_cb(f"{comp.short_id or comp.code} : {done}/{len(prepared)} traité(s), "
                    f"{n_ok} adapté(s)…")
        if stopped:
            spent, cap = providers.budget_state(db, indigo_llm.config_provider_key(db))
            progress_cb(f"⛔ {comp.short_id or comp.code} : adaptation ARRÊTÉE — "
                        f"{stopped} ({spent:.2f} € dépensés sur 24 h, plafond "
                        f"{cap:.2f} €). Les exercices restants sont en repli OCR "
                        f"brut : relance l'extraction quand le plafond est "
                        f"reconduit, ou augmente "
                        f"MATHPRINT_LLM_DAILY_COST_LIMIT_EUR.")

    # 3) VÉRIFICATION FINALE DeepSeek pro, par lots courts (cf. indigo_verify) :
    #    RÉSOUT chaque exercice contre la SOURCE (OCR + corrigé prof) et corrige une
    #    réponse fausse / lecture OCR infidèle / formulation floue, sans réécrire.
    #    Ne dégrade jamais : un exercice non vérifié garde sa version adaptée.
    #    Plafond de dépense déjà atteint = on ne la tente même pas (elle
    #    échouerait lot après lot, en pure perte de temps).
    if stopped:
        reviewed = {str(m.get("number", "")).strip(): {k: x for k, x in v.items()
                                                       if k != "_raw"}
                    for (_r, m, v) in triples if v is not None}
    else:
        reviewed = indigo_verify.review(db, comp, grade, triples, progress_cb)

    # 4) persistance (la relecture a pu remplacer la version adaptée)
    made = 0
    adapted_ok = 0
    for row, manual, valid in triples:
        final = reviewed.get(str(manual["number"]).strip()) if valid is not None else None
        _persist_exercise(db, row, manual, final)
        made += 1
        if final is not None:
            adapted_ok += 1
        if made % 10 == 0:
            db.commit()
    db.commit()
    return made, adapted_ok, stopped, errors


def _run_qcm(db, comp, grade, prepared, progress_cb) -> tuple[int, int, str, list[str]]:
    """Mode « QCM only » : chaque exercice du manuel donne un TRIO de QCM
    vérifiés (base + dérivé facile + dérivé difficile), corrigeables par CV.

    Même contrat de retour que `_run_classic`. Un exercice dont AUCUNE variante
    n'a survécu à la vérification déterministe est persisté en repli OCR brut,
    exactement comme une adaptation ratée en mode classique : jamais un exercice
    manquant en silence, jamais un QCM non vérifié sur une copie.

    Le dé-doublonnage est partagé par toute la CIBLE (`existing_norms`) : deux
    dérivés identiques, ou un « facile » recopié de la base, sont écartés au
    profit de la base — c'est elle qui est validée en premier (§ indigo_qcm)."""
    made = adapted_ok = 0
    errors: list[str] = []
    stopped = ""
    existing_norms: set[str] = set()
    batch_size = indigo_qcm.choose_batch_size(len(prepared))
    for i in range(0, len(prepared), batch_size):
        chunk = prepared[i:i + batch_size]
        converted: dict[str, dict] = {}
        if not stopped:
            try:
                converted = indigo_qcm.generate_batch(
                    db, comp, grade, [m for _r, m in chunk], existing_norms, errors)
            except providers.BudgetExceeded as e:
                stopped = str(e)
        for row, manual in chunk:
            entry = converted.get(str(manual["number"]).strip())
            made += _persist_qcm_trio(db, row, manual, entry)
            if entry:
                adapted_ok += 1
        db.commit()
        done = min(i + batch_size, len(prepared))
        progress_cb(f"{comp.short_id or comp.code} : {done}/{len(prepared)} traité(s), "
                    f"{adapted_ok} converti(s) en QCM, {made} exercice(s) créé(s)…")
        if stopped:
            spent, cap = providers.budget_state(db, indigo_llm.config_provider_key(db))
            progress_cb(f"⛔ {comp.short_id or comp.code} : conversion QCM ARRÊTÉE — "
                        f"{stopped} ({spent:.2f} € dépensés sur 24 h, plafond "
                        f"{cap:.2f} €). Les exercices restants sont en repli OCR "
                        f"brut : relance l'extraction quand le plafond est "
                        f"reconduit, ou augmente "
                        f"MATHPRINT_LLM_DAILY_COST_LIMIT_EUR.")
    return made, adapted_ok, stopped, errors


def _run_multipass(db, comp, grade, prepared, progress_cb,
                   states: list | None = None) -> tuple[int, int, str, list[str]]:
    """Mode « QCM multipass » : cinq passes DeepSeek Flash PAR exercice source.

    Même contrat de retour que `_run_classic`, à une différence près qui compte :
    `made == adapted_ok`. Il n'y a pas de repli OCR brut ici — mais il n'y a plus
    non plus de source jetée pour imperfection : ce qui subsiste après les
    tentatives part en BROUILLON avec ses réserves (§ indigo_multipass, état
    NEEDS_REVIEW). Seule une famille dont pas une variante n'est exploitable ne
    laisse rien derrière elle.

    Rien n'est publié ici. La publication est un geste du professeur, après
    relecture — cf. `_persist_multipass_family`.
    """
    made = 0
    errors: list[str] = []
    stopped = ""
    existing_norms: set[str] = set()
    tally: dict[str, int] = {}
    short = comp.short_id or comp.code

    def gate() -> None:
        indigo_offpeak.wait_until_open(db, progress_cb=progress_cb)

    for i, (row, manual) in enumerate(prepared, 1):
        if stopped:
            break
        try:
            family = indigo_multipass.run_family(
                db, comp, grade, manual, existing_norms, gate=gate)
        except providers.BudgetExceeded as e:
            stopped = str(e)
            errors.append(str(e))
            break
        tally[family.state] = tally.get(family.state, 0) + 1
        if states is not None:
            states.append({"competency": short, **family.as_dict()})
        if family.kept:
            rows = _persist_multipass_family(db, row, manual, family)
            db.commit()
            made += len(rows)
            detail = (f", {family.attempts} tentative(s)" if family.attempts > 1 else "")
            reserve = (" · à relire : " + family.notes[0][:110]) if family.notes else ""
            progress_cb(f"{short} : {i}/{len(prepared)} — n°{family.number} → "
                        f"{len(rows)} brouillon(s){detail}{reserve}")
        else:
            progress_cb(f"{short} : {i}/{len(prepared)} — n°{family.number} sans "
                        f"variante exploitable ({family.state}) : {family.reason[:180]}")

    kept = sum(tally.get(state, 0) for state in indigo_multipass.KEPT_STATES)
    ecartes = [f"{n} {state}" for state, n in sorted(tally.items())
               if state not in indigo_multipass.KEPT_STATES]
    progress_cb(
        f"{short} : {kept}/{len(prepared)} source(s) retenue(s) → {made} exercice(s) "
        f"en brouillon"
        + (" · écartées : " + " · ".join(ecartes) if ecartes else "")
        + (f" · ⛔ ARRÊTÉ : {stopped}" if stopped else ""))
    if stopped:
        spent, cap = providers.budget_state(db, indigo_llm.config_provider_key(db))
        progress_cb(f"⛔ {short} : génération multipass ARRÊTÉE — {stopped} "
                    f"({spent:.2f} € dépensés sur 24 h, plafond {cap:.2f} €). Les "
                    f"exercices restants n'ont PAS été créés : relance l'extraction "
                    f"quand le plafond est reconduit, ou augmente "
                    f"MATHPRINT_LLM_DAILY_COST_LIMIT_EUR.")
    return made, made, stopped, errors


def _run_multipass_vision(db, doc_eleve, grade: str, page_indices: list[int],
                          extraction_id: str, progress_cb,
                          states: list[dict]) -> int:
    """Nouveau point d'entrée : pages élève → Vision → cinq passes.

    Il n'existe plus de cible compétence/numéros à deviner. Vision découvre tous
    les badges de la plage et lit le titre rose actif ; la passe 1 choisit ensuite
    la compétence officielle. Chaque famille prête conserve la publication
    immédiate de la pipeline historique.
    """
    fw = db.query(CompetencyFramework).filter_by(grade_level=grade).first()
    if fw is None:
        raise RuntimeError(f"Référentiel de compétences {grade} introuvable")
    comps = (db.query(Competency).filter_by(framework_id=fw.id)
             .order_by(Competency.order_index).all())
    if not comps:
        raise RuntimeError(f"Aucune compétence disponible pour la classe {grade}")

    indigo_offpeak.wait_until_open(db, progress_cb=progress_cb)
    extracted = indigo_vision.extract_pages(
        db, doc_eleve, grade, page_indices, progress_cb=progress_cb)
    progress_cb(f"Vision DeepSeek : {len(extracted)} exercice(s) complet(s) lu(s), "
                "rattachement et génération multipasse…")

    made = 0
    # Les pages peuvent traverser plusieurs sections : dédoublonnage isolé par
    # compétence, après le rattachement de la passe 1.
    existing_norms: dict[str, set[str]] = {}

    def gate() -> None:
        indigo_offpeak.wait_until_open(db, progress_cb=progress_cb)

    # Traités par LOT (§ settings.indigo_multipass_batch_size) : la passe 2 se
    # partage entre les sources d'un même lot qui se rattachent à la même
    # compétence (indigo_multipass.run_family_pair), les passes 1, contexte, 3,
    # 4, 5 restant chacune une source à la fois. Un lot d'une seule source (le
    # reliquat final, ou le réglage à 1) retombe sur exactement le même
    # comportement qu'avant le 04/09.
    #
    # Le corrigé du manuel prof n'est PLUS cherché ici, sur un chapitre deviné
    # avant tout rattachement réel : `indigo_multipass._resolve_family`
    # (passe contexte, § 04/09 soir) le cherche lui-même, sur la compétence
    # RÉELLEMENT résolue par la passe 1, et reçoit TOUS les candidats quand le
    # chapitre en porte plusieurs — jamais un seul choisi à l'aveugle ici.
    numbered = list(enumerate(extracted))
    batch_size = max(1, int(settings.indigo_multipass_batch_size))
    for start in range(0, len(numbered), batch_size):
        chunk = numbered[start:start + batch_size]
        manuals = [{
            "number": str(ex.get("number") or ""),
            "statement": str(ex.get("text") or "").strip(),
            "has_figure": bool(ex.get("has_figure")),
            "figure_description": str(ex.get("figure_description") or "").strip(),
            "competency_title": str(ex.get("competency_title") or "").strip(),
            "vision_extracted": True,
        } for _order, ex in chunk]
        families = indigo_multipass.run_family_pair(
            db, comps, grade, manuals, existing_norms, gate=gate)

        for (order, ex), family in zip(chunk, families):
            def traced_state(ex=ex, family=family) -> dict:
                """Diagnostic durable, y compris quand aucune ligne n'est persistée."""
                return {**family.as_dict(), "source": {
                    "page": int(ex.get("source_page") or 0) + 1,
                    "number": str(ex.get("number") or ""),
                    "competency_title": str(ex.get("competency_title") or ""),
                    "statement": str(ex.get("text") or ""),
                    "has_figure": bool(ex.get("has_figure")),
                    "figure_description": str(ex.get("figure_description") or ""),
                    "exercise_bbox": ex.get("exercise_bbox"),
                    "figure_bbox": ex.get("figure_bbox"),
                }}

            page = int(ex.get("source_page") or 0) + 1
            comp = db.get(Competency, family.competency_id) if family.competency_id else None
            if comp is None:
                # Rattachement impossible : on prend la première compétence de la
                # classe et on marque l'exercice « à confirmer ». Il atterrit dans la
                # liste du professeur, qui le range ; il ne disparaît pas.
                comp = comps[0]
                family.competency_id, family.competency_code = comp.id, comp.code
                family.competency_confirmed = False
                family.notes = list(family.notes) + [
                    "compétence non déterminée : range cet exercice avant de le valider"]
            try:
                row, prepared_manual = _prepare_vision_exercise(
                    doc_eleve, grade, comp, ex, extraction_id, order)
                if family.kept:
                    rows = _persist_multipass_family(db, row, prepared_manual, family)
                else:
                    # AUCUNE variante exploitable. La lecture Vision, elle, est
                    # bonne : on écrit le brouillon de repli (énoncé source, réponse
                    # à rédiger) plutôt que de perdre l'exercice. Le professeur voit
                    # la source et écrit lui-même le QCM s'il le veut — c'est ce que
                    # les tentatives n'ont pas su faire, pas une raison de tout jeter.
                    rows = _persist_multipass_fallback(db, row, prepared_manual, family)
                db.commit()
                made += len(rows)
            except Exception as exc:
                db.rollback()
                family.state = indigo_multipass.REJECTED_GENERATION
                family.reason = f"préparation du crop Vision impossible : {exc}"
                logger.exception("Indigo/Vision : n°%s non persisté", family.number)
                states.append(traced_state())
                continue
            states.append(traced_state())
            # AUCUNE PUBLICATION AUTOMATIQUE. Les lignes sont des brouillons : le
            # professeur les relit, les valide, puis publie depuis l'onglet. Publier
            # ici mettait des exercices jamais relus dans la banque de toutes les
            # classes, et la seule façon de les en retirer était de les y retrouver.
            detail = (" · à relire : " + family.notes[0][:110]
                      if family.notes else "")
            kind = "brouillon(s)" if family.kept else "brouillon de repli (source brute)"
            progress_cb(f"Page {page}, n°{family.number} → {family.competency_code} : "
                        f"{len(rows)} {kind}{detail}")
    return made


def _persist_multipass_fallback(db, row: IndigoExercise, manual: dict,
                                family) -> list[IndigoExercise]:
    """Brouillon de REPLI : l'exercice source, tel que Vision l'a lu.

    Le mode refusait tout repli, au motif qu'un exercice moyen coûte plus cher
    qu'un exercice absent. C'est vrai d'un exercice PUBLIÉ ; ça ne l'est pas d'un
    brouillon. Un exercice du manuel correctement lu et que les cinq passes n'ont
    pas su mettre en cases (« recopie et complète ce tableau », « vérifie à la
    calculatrice ») reste un exercice que le professeur peut reprendre en deux
    minutes — s'il le voit. Le supprimer ne lui fait pas gagner ces deux minutes,
    ça lui fait perdre l'exercice."""
    _persist_exercise(db, row, {**manual, "correction": ""}, None,
                      allow_figure_fallback=False)
    row.correction_solution = ""
    row.prompt_version = indigo_multipass.PROMPT_VERSION
    row.status = "draft"
    row.validated_by = None
    row.validated_at = None
    row.raw_ocr_json = {
        **(row.raw_ocr_json or {}),
        "pipeline": "deepseek-v4-flash-vision-exp",
        "competency_title": manual.get("competency_title") or "",
        "figure_description": manual.get("figure_description") or "",
        "review_state": family.state,
        "competency_confirmed": bool(getattr(family, "competency_confirmed", True)),
        "review_blocking": ["aucune variante QCM exploitable : l'énoncé source "
                            "est là, le QCM reste à écrire à la main"],
        "review_notes": list(family.notes or []),
    }
    row.updated_at = datetime.now(timezone.utc)
    return [row]


# Une source « expert » (badge CV, indigo_cv.DIFFICULTY_BY_TYPE) est déjà
# l'exercice le plus dur du manuel : sa variante Base — MÊME niveau que la
# source — tient donc lieu de dérivé DIFFICILE de la plateforme, et son
# dérivé Facile généré (plus simple qu'un expert, mais pas plus qu'un
# exercice ordinaire) devient une variante BASE. Les prompts de la passe 2
# (services.indigo_multipass) ne savent rien de ce reclassement : ils
# continuent de rendre exactement le même duo Base/Facile qu'un exercice
# normal, seule l'ÉTIQUETTE change au moment d'écrire les lignes.
_EXPERT_VARIANT_REMAP = {"base": "difficile", "facile": "base"}
_VARIANT_LEVEL = {"facile": 1, "base": 2, "difficile": 3}


def _multipass_variant_tag(kind: str, row: IndigoExercise) -> str:
    """Le `variant_kind` réellement écrit pour une variante GÉNÉRÉE `kind`,
    selon que la source est un exercice « expert » ou non (§ ci-dessus)."""
    if row.badge_type == "expert":
        return _EXPERT_VARIANT_REMAP[kind]
    return kind


def _persist_multipass_family(db, row: IndigoExercise, manual: dict,
                              family) -> list[IndigoExercise]:
    """Écrit les lignes d'une famille conservée, en BROUILLON. Les retourne.

    Deux différences avec `_persist_qcm_trio`, toutes deux voulues :
      • chaque variante porte SON guide (30 mots) — il n'est plus commun aux trois ;
      • `correction_solution` reste VIDE : ce mode ne lit pas le manuel du
        professeur et n'écrit aucun corrigé. Un placeholder « à compléter »
        annoncerait du travail humain là où il n'y en a pas.

    LE STATUT EST `draft`, comme partout ailleurs dans Indigo. Le mode se
    validait lui-même, au motif qu'aucun de ses exercices ne devait avoir besoin
    d'une correction humaine ; c'était se donner raison d'avance. Aucune
    pipeline ne publie sous le nom du professeur sans qu'il ait regardé : la
    validation puis la publication sont des gestes qu'il pose, et les réserves
    de la famille (§ Family.notes) sont là pour l'aider à les poser vite.

    LA FIGURE est tranchée AVANT la génération (§ indigo_multipass.run_family) :
    `family.figure` dit si les trois exercices s'appuient dessus. Sinon on la
    DÉTACHE — une figure isolée mais inutilisée s'imprimerait en décor à côté
    d'un exercice réécrit, et le repli « à défaut de figure, l'extrait complet du
    manuel » collerait carrément l'énoncé d'origine sur la carte."""
    made: list[IndigoExercise] = []
    now = datetime.now(timezone.utc)
    if not family.figure:
        _detach_figure(row)
    # `row` est la ligne déjà préparée (crop, figure, badge) : elle doit être
    # occupée par une variante, sinon elle resterait en base à demi remplie et
    # les dérivés pointeraient un parent vide. C'est normalement la BASE ; quand
    # le sauvetage n'a pas pu la garder, le PREMIER niveau conservé prend sa
    # place — sans changer de nom ni de difficulté, il reste ce qu'il est.
    principal = next((kind for kind, _ in family.variants if kind == "base"),
                     family.variants[0][0] if family.variants else "base")
    for kind, valid in family.variants:
        target = row if kind == principal else _clone_for_variant(row, kind)
        tag = _multipass_variant_tag(kind, row)
        target.variant_kind = tag
        target.difficulty = _VARIANT_LEVEL[tag]
        target.derived_from_id = None if kind == principal else row.id
        # `manual` sans corrigé : le mode ignore le manuel du professeur
        _persist_exercise(db, target, {**manual, "correction": ""}, valid,
                          allow_figure_fallback=False)
        target.correction_solution = ""
        target.prompt_version = indigo_multipass.PROMPT_VERSION
        if manual.get("vision_extracted"):
            target.raw_ocr_json = {
                **(target.raw_ocr_json or {}),
                "pipeline": "deepseek-v4-flash-vision-exp",
                "competency_title": manual.get("competency_title") or "",
                "figure_description": manual.get("figure_description") or "",
            }
        # Les réserves suivent CHAQUE ligne : c'est sur la carte de l'exercice
        # que le professeur les lit, pas dans le journal d'extraction.
        notes = list(getattr(family, "notes", []) or [])
        # Un badge ROUGE doit désigner LA carte qu'il concerne. Les réserves de
        # la famille nomment leur niveau (« Difficile : … ») : coller le défaut
        # du niveau difficile sur la carte facile ferait douter d'un exercice
        # sain, et un badge qu'on voit partout ne signale plus rien. Ce qui ne
        # nomme aucun niveau vaut, lui, pour les trois.
        blocking = [b for b in (getattr(family, "blocking", []) or [])
                    if not (other := next(
                        (lab for lab in indigo_multipass.VARIANT_LABEL.values()
                         if b.startswith(f"{lab} :")), ""))
                    or other == indigo_multipass.VARIANT_LABEL[kind]]
        target.raw_ocr_json = {**(target.raw_ocr_json or {}),
                               "review_notes": notes,
                               # Ce que la passe 5 n'a PAS su réparer et juge
                               # grave : l'onglet en fait un badge rouge, pour
                               # que « à regarder » et « ne l'imprime pas tel
                               # quel » ne se confondent pas dans le même jaune.
                               "review_blocking": blocking,
                               "review_state": family.state,
                               "competency_confirmed": bool(
                                   getattr(family, "competency_confirmed", True))}
        # Un visuel réclamé mais absent est le travail de relecture le plus
        # courant : on le remonte dans la colonne prévue pour ça, que l'onglet
        # Exercices trie déjà (« figure requise, aucune image »).
        if not row.has_figure:
            target.figure_required = indigo_check.mentions_figure(target.statement)
        target.status = "draft"
        target.validated_by = None
        target.validated_at = None
        target.updated_at = now
        made.append(target)
    return made


def _collect_exercises(db, comp, comps, grade, eleve, eleve_pages,
                       expected, exp_set, progress_cb) -> list[dict]:
    """Liste finale des exercices d'une cible, un par NUMÉRO. Deux sources se
    complètent :
      • GÉOMÉTRIE (crop/CV/figure) : localise chaque numéro dans l'OCR ;
      • GEMINI (pré-découpage texte) : recoupe proprement les énoncés quand
        l'OCR a fusionné deux exercices (le numéro fait autorité).
    Sans plage de numéros (rétrocompat / hors ligne), on garde le découpage
    géométrique historique par séquence + titre de compétence."""
    if not exp_set:
        return [ex for page in eleve for ex in _segment_target(page, comp, comps)]

    # géométrie : {numéro -> exercice enrichi} (premier gagne s'il se répète)
    geom: dict[str, dict] = {}
    for page in eleve:
        for ex in _segment_by_numbers(page, comp, exp_set):
            geom.setdefault(ex["number"], ex)

    # pré-découpage Gemini (texte propre par numéro) — autorité sur les frontières
    progress_cb(f"{comp.short_id or comp.code} : découpage des exercices…")
    page_texts = [(p["source_page"], _ordered_text(p)) for p in eleve]
    seg_text = indigo_segment.segment_statements(db, comp, grade, page_texts, expected)

    out: list[dict] = []
    for n in expected:                         # ordre croissant = ordre du manuel
        num = str(n)
        g = geom.get(num)
        clean = (seg_text.get(num) or "").strip()
        if g is not None:
            if clean:                          # texte Gemini préféré au flatten géométrique
                g["text"] = statement_mod.strip_leading_number(clean, num)
            out.append(g)
        elif clean:                            # reconnu par Gemini seul (OCR fusionné) : gardé sans crop
            out.append(_placeholder_exercise(num, clean, eleve_pages))
        else:
            logger.info("Indigo : n°%s absent des pages fournies (%s) — ignoré", num, comp.code)
    return out


def _corrections_for(grade: str, chapter_name: str, number: str) -> list[str]:
    """TOUS les corrigés CANDIDATS du manuel PROF pour cet exercice, si
    l'INDEX (déjà construit, § indigo_index.build) en porte — jamais un appel
    OCR déclenché ici : lire l'index est gratuit (couche texte du manuel
    prof), un OCR de secours ne l'est pas, et ce repérage tourne pour chaque
    exercice de la pipeline Vision. `chapter_name` n'est qu'une aide :
    `_prof_pages_for` retombe sur les seuls numéros s'il ne correspond à rien.

    Rend une LISTE (jamais un seul candidat choisi ici, § révision du 04/09
    soir) : un même chapitre du manuel prof peut contenir plusieurs lots
    d'exercices qui repartent chacun à 1, et un même numéro peut donc y
    apparaître plusieurs fois pour des exercices DIFFÉRENTS (mesuré : le n°36
    de « Fonctions affines » existait en page 107 ET 118). Choisir lequel — ou
    aucun — parle vraiment de CET exercice revient à `indigo_multipass.
    _pass_context`, la seule passe outillée pour comparer le contenu au
    corrigé ; en décider ici, à l'aveugle sur le premier trouvé, avait fait
    rejeter à tort des sources qui s'en passaient très bien avant. Une absence
    (index non construit, numéro hors plage) rend simplement une liste vide."""
    from . import indigo_index
    try:
        num = int(str(number).strip())
    except (TypeError, ValueError):
        return []
    out: list[str] = []
    for idx in indigo_index._prof_pages_for(grade, chapter_name, {num}):
        entry = indigo_index.page_entry(grade, "prof", idx)
        if entry is None:
            continue
        found = _segment_corrections_by_numbers(entry, {num}).get(str(num))
        if found:
            out.append(found)
    return out


def _collect_corrections(db, comp, grade, prof, expected, exp_set, progress_cb) -> dict[str, str]:
    """Corrigés du manuel PROF, un par NUMÉRO — MÊME politique que
    `_collect_exercises` côté élève (le numéro du badge/exercice est la source
    de vérité, pour l'énoncé comme pour le corrigé) :
      • GÉOMÉTRIE (regroupement de blocs par page) localise chaque numéro ;
      • GEMINI (pré-découpage texte) recoupe proprement les corrigés quand
        l'OCR a fusionné ou coupé un bloc (le numéro fait autorité).
    Sans plage de numéros (rétrocompat / hors ligne), on garde le découpage
    géométrique historique par séquence."""
    geom: dict[str, str] = {}
    for page in prof:
        seg = _segment_corrections_by_numbers(page, exp_set) if exp_set else _segment_corrections(page)
        for num, txt in seg.items():
            geom.setdefault(num, txt)

    if not exp_set or not prof:
        return geom

    progress_cb(f"{comp.short_id or comp.code} : découpage des corrigés…")
    page_texts = [(p["source_page"], _ordered_text(p)) for p in prof]
    seg_text = indigo_segment.segment_corrections(db, comp, grade, page_texts, expected)

    out = dict(geom)
    for num, txt in seg_text.items():
        out[num] = txt          # texte Gemini préféré au flatten géométrique
    return out


def _prepare_exercise(doc_eleve, grade, comp, ex, corrections, extraction_id, order):
    """Découpe le crop + lit la couleur (CV) d'un exercice. Retourne (row,
    manual) — la ligne n'est PAS encore en base : l'adaptation Gemini (par lots)
    remplit le reste ensuite.

    On NE saute JAMAIS un exercice : les exercices sont numérotés et séquentiels,
    aucun ne doit manquer. Si le crop du manuel échoue (boîte dégénérée) ou si
    l'exercice n'a AUCUNE géométrie (reconnu par le seul pré-découpage Gemini,
    OCR fusionné), on garde quand même la ligne SANS image (le crop n'est qu'une
    référence) et on retombe sur les défauts CV (badge exercice)."""
    # id explicite : default=uid n'est appliqué qu'au flush, or on nomme les crops tout de suite
    ex_id = uid()
    row = IndigoExercise(id=ex_id, extraction_id=extraction_id, competency_id=comp.id,
                         grade_level=grade, source_page=ex["source_page"],
                         source_number=ex["number"], order_index=order,
                         badge_type="exercice", difficulty=3, calculator="autorisee",
                         title=ex["title"], tags_json=ex["tags"])
    # sans blocs (exercice « placeholder » du pré-découpage Gemini), on ne crope
    # pas : ligne conservée sans image, l'admin ajustera l'énoncé si besoin.
    if not ex["blocks"] or ex.get("start_block") is None:
        logger.info("Indigo : n°%s sans géométrie (OCR fusionné) — conservé sans image",
                    ex["number"])
        manual = {"number": ex["number"], "statement": ex["text"],
                  "correction": corrections.get(ex["number"], ""), "has_figure": False}
        return row, manual

    raster = indigo_manual.raster_page(doc_eleve, ex["source_page"])
    rh, rw = raster.shape[:2]
    crop_box = _clamp_box(*_scale_box(_union_box(ex["blocks"]), ex["dims"], rw, rh),
                          rw, rh, pad=CROP_PAD_PX)
    num_box = _clamp_box(*_scale_box(_union_box([ex["start_block"]]), ex["dims"], rw, rh),
                         rw, rh)
    crop_rel = f"indigo/drafts/{ex_id}.png"
    if _save_crop(raster, crop_box, crop_rel):
        row.crop_path = crop_rel
        row.crop_box_json = {"page_index": ex["source_page"], "x0": crop_box[0],
                             "y0": crop_box[1], "x1": crop_box[2], "y1": crop_box[3],
                             "raster_dpi": indigo_manual.RASTER_DPI, "img_w": rw, "img_h": rh,
                             "number_box": list(num_box)}
        crop_img = raster[crop_box[1]:crop_box[3], crop_box[0]:crop_box[2]]
        local_num = {"x0": num_box[0] - crop_box[0], "y0": num_box[1] - crop_box[1],
                     "x1": num_box[2] - crop_box[0], "y1": num_box[3] - crop_box[1]}
        cv = indigo_cv.analyze(crop_img, ex["is_probleme"], number_box=local_num)
        row.badge_type = cv["badge_type"]
        row.difficulty = cv["difficulty"]
        row.calculator = cv["calculator"]
        row.badge_color_json = cv["color"] or {}
    else:
        logger.warning("Indigo : crop de l'exercice n°%s absent (boîte dégénérée) "
                       "— exercice conservé sans image", ex["number"])

    if ex["has_figure"]:
        fbox = _clamp_box(*_scale_box(_union_box(ex["figure_blocks"]), ex["dims"], rw, rh),
                          rw, rh, pad=CROP_PAD_PX)
        fig_rel = f"indigo/drafts/{ex_id}_fig.png"
        if _save_crop(raster, fbox, fig_rel):
            # moteur déterministe (aucun LLM) : une photographie est toujours
            # illustrative, elle n'est jamais insérée dans l'énoncé imprimé —
            # cf. figures.is_photograph. Un schéma/diagramme/capture d'écran/
            # dessin, potentiellement nécessaire à la résolution, l'est.
            fig_abs = crop_abs_path(fig_rel)
            if figures.is_photograph(fig_abs.read_bytes()):
                logger.info("Indigo : n°%s — image exclue (photographie détectée)",
                           ex["number"])
                fig_abs.unlink(missing_ok=True)
            else:
                row.has_figure = True
                row.figure_path = fig_rel
                row.figure_box_json = {"page_index": ex["source_page"], "x0": fbox[0],
                                       "y0": fbox[1], "x1": fbox[2], "y1": fbox[3]}

    manual = {"number": ex["number"], "statement": ex["text"],
              "correction": corrections.get(ex["number"], ""),
              "has_figure": row.has_figure}
    return row, manual


def _prepare_vision_exercise(doc_eleve, grade, comp, ex, extraction_id, order):
    """Construit la ligne et les crops depuis les coordonnées DeepSeek Vision.

    Contrairement au chemin OCR historique, aucune bbox de bloc n'est reconstruite
    et aucune photographie n'est supprimée : Vision a explicitement déclaré que
    l'image appartient à l'exercice. Le crop de figure est celui demandé au modèle,
    serré et exprimé dans le même repère raster que l'éditeur manuel.
    """
    ex_id = uid()
    page_index = int(ex["source_page"])
    row = IndigoExercise(
        id=ex_id, extraction_id=extraction_id, competency_id=comp.id,
        grade_level=grade, source_page=page_index,
        source_number=str(ex.get("number") or ""), order_index=order,
        badge_type="exercice", difficulty=2, calculator="autorisee",
        title="", tags_json=[])

    raster = indigo_manual.raster_page(doc_eleve, page_index)
    rh, rw = raster.shape[:2]
    crop_box = indigo_vision.pixel_box(ex.get("exercise_bbox"), rw, rh,
                                       pad=CROP_PAD_PX)
    if crop_box is None:
        raise ValueError(f"crop d'exercice Vision invalide (n°{ex.get('number')})")
    crop_rel = f"indigo/drafts/{ex_id}.png"
    if not _save_crop(raster, crop_box, crop_rel):
        raise ValueError(f"crop d'exercice Vision vide (n°{ex.get('number')})")
    row.crop_path = crop_rel

    number_box = indigo_vision.pixel_box(ex.get("number_bbox"), rw, rh)
    row.crop_box_json = {
        "page_index": page_index, "x0": crop_box[0], "y0": crop_box[1],
        "x1": crop_box[2], "y1": crop_box[3],
        "raster_dpi": indigo_manual.RASTER_DPI, "img_w": rw, "img_h": rh,
        "number_box": list(number_box) if number_box else [],
        "pipeline": "deepseek-vision",
    }
    local_num = None
    if number_box:
        local_num = {"x0": number_box[0] - crop_box[0],
                     "y0": number_box[1] - crop_box[1],
                     "x1": number_box[2] - crop_box[0],
                     "y1": number_box[3] - crop_box[1]}
    crop_img = raster[crop_box[1]:crop_box[3], crop_box[0]:crop_box[2]]
    cv = indigo_cv.analyze(crop_img, False, number_box=local_num)
    row.badge_type = cv["badge_type"]
    row.difficulty = cv["difficulty"]
    row.calculator = cv["calculator"]
    row.badge_color_json = cv["color"] or {}

    if ex.get("has_figure"):
        figure_page = int(ex.get("figure_page", page_index))
        figure_raster = indigo_manual.raster_page(doc_eleve, figure_page)
        fh, fw = figure_raster.shape[:2]
        figure_box = indigo_vision.pixel_box(ex.get("figure_bbox"), fw, fh,
                                             pad=CROP_PAD_PX)
        if figure_box is None:
            raise ValueError(f"crop de figure Vision invalide (n°{ex.get('number')})")
        fig_rel = f"indigo/drafts/{ex_id}_fig.png"
        if not _save_crop(figure_raster, figure_box, fig_rel):
            raise ValueError(f"crop de figure Vision vide (n°{ex.get('number')})")
        row.has_figure = True
        row.figure_path = fig_rel
        row.figure_box_json = {
            "page_index": figure_page, "x0": figure_box[0], "y0": figure_box[1],
            "x1": figure_box[2], "y1": figure_box[3],
        }

    manual = {
        "number": str(ex.get("number") or ""),
        "statement": str(ex.get("text") or "").strip(),
        "correction": "",
        "has_figure": row.has_figure,
        "figure_description": str(ex.get("figure_description") or "").strip(),
        "competency_title": str(ex.get("competency_title") or "").strip(),
        "vision_extracted": True,
    }
    return row, manual


_GUIDE_TODO = "À compléter : guide d'auto-correction (règle utile + piège), à saisir."
_SOLUTION_TODO = "À compléter : corrigé de référence à saisir."


def _persist_exercise(db, row: IndigoExercise, manual: dict, valid: dict | None, *,
                      allow_figure_fallback: bool = True) -> None:
    """Remplit la ligne avec l'exercice adapté par Gemini (ou un repli si
    l'adaptation a échoué : on garde le crop + les métadonnées CV, l'admin
    saisira l'énoncé) puis l'ajoute en base.

    DEUX champs DISTINCTS, jamais confondus :
      • correction_solution = le VRAI corrigé (à défaut de Gemini, le corrigé du
        manuel prof — c'est bien la solution) ;
      • correction_guide = un COURT guide d'auto-correction élève. Il ne doit
        JAMAIS être le corrigé complet : si Gemini ne l'a pas produit (ou l'a
        recopié depuis la solution), on met un placeholder à compléter, PAS la
        solution (cause du bug « guide copié depuis le corrigé »).

    `allow_figure_fallback` : rattacher l'extrait COMPLET du manuel faute de
    figure isolée (§ _fallback_figure_from_crop). Le mode « QCM multipass » le
    coupe — il a déjà tranché AVANT de générer (figure isolée ou source écartée),
    et coller l'image de l'exercice d'origine à côté d'un exercice RÉÉCRIT
    imprimerait deux énoncés différents sur la même carte."""
    prof = (manual.get("correction") or "").strip()
    # couche « besoin de figure » — cf. commentaire au-dessus de _mentions_figure :
    # indice textuel sur l'énoncé BRUT, combiné (ci-dessous) au jugement Claude
    # quand l'adaptation a réussi. Calculé AVANT tout retour anticipé pour couvrir
    # aussi le repli OCR brut (valid is None).
    row.figure_required = _mentions_figure(manual.get("statement", ""))
    if valid is None:
        row.statement = (manual["statement"] or "")[:1000]
        row.response_type = "short_text"
        row.correction_solution = prof or _SOLUTION_TODO
        row.correction_guide = _GUIDE_TODO      # jamais la solution
        row.raw_ocr_json = {"statement": manual["statement"],
                            "correction": manual["correction"], "adapted": False}
        if row.figure_required and not row.has_figure and allow_figure_fallback:
            _fallback_figure_from_crop(row)
        db.add(row)
        return
    # moteur de champs de réponse : présence du champ (une réponse courte sans
    # case en reçoit une, une case orpheline est retirée), mini-case / case
    # pleine largeur selon la réponse attendue, une case par sous-question,
    # lignes de raisonnement dimensionnées sur le corrigé (services.indigo_fields).
    anomalies = indigo_fields.audit(valid["statement"], valid["response_type"],
                                    valid.get("expected"))
    if anomalies:
        # jamais bloquant (on ne dégrade pas un exercice pour un champ rattrapé) :
        # c'est un signal de qualité du prompt d'adaptation, relu dans les journaux.
        logger.warning("Indigo : champs de réponse n°%s (%s) — %s",
                       manual.get("number"), valid["response_type"], " ; ".join(anomalies))
    statement, expected, grading = indigo_fields.adapt_fields(
        valid["statement"], valid["response_type"],
        valid.get("expected"), valid.get("grading"))
    row.statement = statement
    row.response_type = valid["response_type"]
    row.expected_json = expected
    row.grading_json = grading
    solution = (valid.get("correction_solution") or "").strip() or prof or _SOLUTION_TODO
    guide = (valid.get("correction") or "").strip()
    # garde-fou : un guide vide OU recopié depuis la solution (même texte) est
    # remplacé par un placeholder — le guide n'est pas le corrigé.
    if not guide or _fold(guide) == _fold(solution):
        guide = _GUIDE_TODO
    row.correction_solution = solution
    row.correction_guide = guide
    # provenance = le modèle qui a réellement mis au propre l'exercice, selon le
    # fournisseur choisi dans l'onglet (Sonnet côté Anthropic, DeepSeek pro sinon).
    row.model = indigo_llm.model_for(db, "adapt")
    row.prompt_version = indigo_gemini.PROMPT_VERSION
    # jugement Claude (après réécriture) OU indice textuel (sur l'énoncé brut,
    # déjà posé ci-dessus) : le besoin est confirmé par L'UN OU L'AUTRE.
    row.figure_required = row.figure_required or bool(valid.get("needs_figure"))
    if row.figure_required and not row.has_figure and allow_figure_fallback:
        _fallback_figure_from_crop(row)
    # garde-fou de PLACEMENT de l'image : {{figure}} au début ou avant la 1re
    # question, jamais après (règle Indigo) — cf. statement.place_figure_marker.
    # Sur un composite, les questions ne sont PAS dans ce texte : la figure va
    # à la fin du contexte, c'est-à-dire juste avant la première sous-question.
    statement = statement_mod.place_figure_marker(
        statement, row.has_figure, at_end=row.response_type == "composite")
    row.statement = statement
    # contrat archivé : on y reporte l'énoncé et le barème APRÈS le moteur de
    # champs (ce sont eux qui seront rendus/publiés), pas la sortie brute de Gemini.
    row.payload_json = {**{k: valid.get(k) for k in
                           ("response_type", "correction",
                            "correction_solution", "kind", "figure_json")},
                        "statement": statement, "expected": expected,
                        "grading": grading}
    row.raw_ocr_json = {"statement": manual["statement"],
                        "correction": manual["correction"], "adapted": True}
    db.add(row)


def _persist_qcm_trio(db, row: IndigoExercise, manual: dict,
                      entry: dict | None) -> int:
    """Persiste le TRIO d'un exercice source. Retourne le nombre de lignes créées.

    La ligne `row` déjà préparée (crop, figure, badge CV) porte la variante de
    BASE ; chaque dérivé est une NOUVELLE ligne qui la référence
    (`derived_from_id`) et reçoit sa PROPRE copie du fichier figure — partager le
    chemin ferait qu'une suppression d'image sur l'un viderait les trois.

    Les trois lignes partagent le MÊME guide d'auto-correction (il est déjà porté
    par chaque contrat validé) : même notion, même règle, même piège — seuls les
    nombres changent d'une variante à l'autre.

    `entry` vide (aucune variante n'a passé la vérification déterministe) : on
    retombe sur le repli OCR brut, comme une adaptation ratée en mode classique.
    Un exercice ne disparaît jamais en silence."""
    if not entry or not entry.get("variants"):
        _persist_exercise(db, row, manual, None)
        return 1
    # `manual` est passé TEL QUEL : son champ "correction" est le corrigé du
    # manuel du PROFESSEUR, lu gratuitement, et c'est lui qui alimente
    # `correction_solution` (le modèle ne le réécrit plus — cf. indigo_qcm).
    made = 0
    for kind, valid in entry["variants"]:
        target = row if kind == "base" else _clone_for_variant(row, kind)
        target.variant_kind = kind
        target.difficulty = indigo_qcm.VARIANT_LEVEL[kind]
        target.derived_from_id = None if kind == "base" else row.id
        _persist_exercise(db, target, manual, valid)
        target.prompt_version = indigo_qcm.PROMPT_VERSION
        made += 1
    return made


def _clone_for_variant(row: IndigoExercise, kind: str) -> IndigoExercise:
    """Nouvelle ligne pour un DÉRIVÉ : mêmes provenance et images que la base.

    Le crop de référence et la figure sont RECOPIÉS sur disque sous le nouvel
    id — `delete_exercise` supprime les fichiers de la ligne qu'il efface, et
    trois lignes qui pointeraient le même PNG se videraient mutuellement."""
    new_id = uid()
    clone = IndigoExercise(
        id=new_id, extraction_id=row.extraction_id, competency_id=row.competency_id,
        grade_level=row.grade_level, source_page=row.source_page,
        source_number=row.source_number, order_index=row.order_index,
        badge_type=row.badge_type, calculator=row.calculator, title=row.title,
        tags_json=list(row.tags_json or []),
        badge_color_json=dict(row.badge_color_json or {}),
        crop_box_json=dict(row.crop_box_json or {}))
    for attr, suffix in (("crop_path", ""), ("figure_path", "_fig")):
        rel = getattr(row, attr, "")
        if not rel:
            continue
        src = crop_abs_path(rel)
        if not src.exists():
            continue
        dest_rel = f"indigo/drafts/{new_id}{suffix}.png"
        dest = crop_abs_path(dest_rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        setattr(clone, attr, dest_rel)
    if clone.figure_path:
        clone.has_figure = True
        clone.figure_box_json = dict(row.figure_box_json or {})
    return clone


# Une INDEXATION est un run comme un autre : même table, même worker, même
# bannière de progression dans l'UI. Elle se reconnaît à sa cible unique, ce qui
# évite d'ajouter une colonne (et donc une migration) pour un simple aiguillage.
INDEX_TARGET = {"kind": "index"}
VISION_TARGET_KIND = "multipass_vision"


def is_index_run(extraction: IndigoExtraction) -> bool:
    targets = extraction.targets_json or []
    return len(targets) == 1 and (targets[0] or {}).get("kind") == "index"


def is_vision_run(extraction: IndigoExtraction) -> bool:
    targets = extraction.targets_json or []
    return (len(targets) == 1
            and (targets[0] or {}).get("kind") == VISION_TARGET_KIND)


def _run_index(db, extraction: IndigoExtraction) -> None:
    grade = extraction.grade_level
    log: list[str] = []

    def progress(msg: str, frac: float | None = None):
        # relu à chaque appel (pas seulement `extraction.status` en mémoire) :
        # le bouton Stop de l'onglet Exercices écrit "cancelling" depuis une
        # AUTRE requête/session, invisible sans requalifier la ligne.
        current = db.query(IndigoExtraction.status).filter_by(id=extraction.id).scalar()
        if current == "cancelling":
            raise _ExtractionCancelled()
        extraction.progress_message = msg
        if frac is not None:
            extraction.progress = int(max(0, min(100, frac * 100)))
        extraction.updated_at = datetime.now(timezone.utc)
        log.append(msg)
        extraction.log_text = "\n".join(log[-200:])
        db.commit()

    from . import indigo_index
    stats = indigo_index.build(db, grade, progress)
    extraction.stats_json = {"index": stats}
    extraction.status = "done"
    extraction.progress = 100
    extraction.progress_message = (f"Index : {stats.get('eleve', 0)} page(s) élève, "
                                   f"{stats.get('prof', 0)} page(s) prof")
    extraction.updated_at = datetime.now(timezone.utc)
    db.commit()


class _ExtractionCancelled(Exception):
    """Levée par `progress()` quand l'utilisateur a demandé l'arrêt (bouton
    Stop de l'onglet Exercices) — interrompt _run_extraction proprement entre
    deux cibles/pages plutôt que de tuer le thread (impossible en Python)."""


def _run_extraction(db, extraction: IndigoExtraction) -> None:
    if is_index_run(extraction):
        _run_index(db, extraction)
        return
    grade = extraction.grade_level
    doc_eleve = indigo_manual.open_doc(grade, "eleve")
    doc_prof = indigo_manual.open_doc(grade, "prof")
    if doc_eleve is None:
        raise RuntimeError(
            f"Aucune source de pages pour le manuel élève {grade} sur cette "
            f"instance : ni le PDF, ni un pack de travail. Importe le pack exporté "
            f"depuis l'instance qui porte les manuels (onglet Exercices → Pack de "
            f"travail), ou dépose les PDF dans <data>/manuals/.")

    targets = extraction.targets_json or []
    total = 0
    log: list[str] = []

    def progress(msg: str, frac: float | None = None):
        # relu à chaque appel (pas seulement `extraction.status` en mémoire) :
        # le bouton Stop de l'onglet Exercices écrit "cancelling" depuis une
        # AUTRE requête/session, invisible sans requalifier la ligne.
        current = db.query(IndigoExtraction.status).filter_by(id=extraction.id).scalar()
        if current == "cancelling":
            raise _ExtractionCancelled()
        extraction.progress_message = msg
        if frac is not None:
            extraction.progress = int(max(0, min(100, frac * 100)))
        extraction.updated_at = datetime.now(timezone.utc)
        log.append(msg)
        extraction.log_text = "\n".join(log[-200:])
        db.commit()

    vision_run = is_vision_run(extraction)
    if vision_run:
        # Le run reste DeepSeek Flash même si le sélecteur global a changé
        # pendant son attente en file. Aucun repli OCR ne serait acceptable :
        # sans Vision il n'existe tout simplement aucune source exploitable.
        vision_provider = providers.provider_for_model(
            settings.indigo_multipass_vision_model)
        if providers.offline(db, vision_provider):
            raise RuntimeError(
                "Clé DeepSeek Flash absente : extraction Vision impossible")
        spent, cap = providers.budget_state(db, vision_provider)
        if spent >= cap:
            raise providers.BudgetExceeded(
                f"Budget {vision_provider} quotidien atteint "
                f"({spent:.2f} € / {cap:.2f} €)")
        if spent >= 0.75 * cap:
            progress(f"⚠ Plafond DeepSeek bientôt atteint ({spent:.2f} € sur "
                     f"24 h pour {cap:.2f} €) : l'extraction peut s'arrêter en cours.")

    # garde-fou visible : sans la clé du fournisseur CHOISI, les TROIS étapes LLM
    # (découpage, adaptation, vérification) tournent en repli hors-ligne et NE
    # PRODUISENT RIEN — tous les exercices seraient des replis OCR bruts (short_text,
    # guide/corrigé « à compléter »). On l'annonce dès le départ plutôt que de
    # livrer silencieusement des exercices dégradés.
    if not vision_run and indigo_llm.offline(db):
        prov = indigo_llm.label(db)
        progress(f"⚠ Clé {prov} absente : l'adaptation des exercices "
                 "est HORS-LIGNE — les exercices seront en repli OCR brut, non "
                 "adaptés (ni QCM, ni cases par sous-question, guides/corrigés "
                 f"« à compléter »). Configure Paramètres → Fournisseurs → {prov}.")
    elif not vision_run:
        # même logique pour le PLAFOND DE DÉPENSE : le dire AVANT de lancer une
        # extraction qui finirait en replis OCR bruts (incident A1.3 du 02/08 —
        # 20 exercices « non adaptés », plafond atteint, aucun message).
        spent, cap = providers.budget_state(db, indigo_llm.config_provider_key(db))
        if spent >= cap:
            progress(f"⛔ Plafond de dépense atteint ({spent:.2f} € sur 24 h pour "
                     f"{cap:.2f} €) : l'adaptation ÉCHOUERA et les exercices seront "
                     f"en repli OCR brut. Attends la fin des 24 h glissantes ou "
                     f"augmente MATHPRINT_LLM_DAILY_COST_LIMIT_EUR.")
        elif spent >= 0.75 * cap:
            progress(f"⚠ Plafond de dépense bientôt atteint ({spent:.2f} € sur 24 h "
                     f"pour {cap:.2f} €) : l'adaptation peut s'arrêter en cours "
                     f"d'extraction.")

    if vision_run:
        states: list[dict] = []
        page_indices = [int(p) for p in (targets[0].get("eleve_pages") or [])]
        total = _run_multipass_vision(
            db, doc_eleve, grade, page_indices, extraction.id,
            lambda message: progress(message), states)
        tally: dict[str, int] = {}
        for state in states:
            name = str(state.get("state") or "")
            tally[name] = tally.get(name, 0) + 1
        extraction.stats_json = {
            "exercises": total, "pages": len(page_indices),
            "vision": {"model": settings.indigo_multipass_vision_model,
                       "families": states, "states": tally},
        }
        extraction.status = "done"
        extraction.progress = 100
        extraction.progress_message = (
            f"{total} exercice(s) publié(s) depuis {len(page_indices)} page(s)")
        extraction.updated_at = datetime.now(timezone.utc)
        db.commit()
        return

    # États par famille du mode multipass (QUEUED → READY / REJECTED_*). Ils vont
    # dans stats_json plutôt que dans le journal : une ligne par exercice y
    # noierait le compte rendu, alors qu'ici l'onglet peut les relire en entier.
    states: list[dict] = []
    for i, target in enumerate(targets):
        progress(f"Cible {i + 1}/{len(targets)}…", i / max(1, len(targets)))
        total += _process_target(db, doc_eleve, doc_prof, grade, target,
                                 extraction.id, lambda m: progress(m), states)

    extraction.stats_json = {"exercises": total, "targets": len(targets)}
    if states:
        tally: dict[str, int] = {}
        for st in states:
            tally[st["state"]] = tally.get(st["state"], 0) + 1
        extraction.stats_json["multipass"] = {"states": tally, "families": states}
    extraction.status = "done"
    extraction.progress = 100
    extraction.progress_message = f"{total} exercice(s) extrait(s)"
    extraction.updated_at = datetime.now(timezone.utc)
    db.commit()


# --------------------------------------------------------------- worker de fond

_wake = threading.Event()
_started = False


def _claim(db, extraction: IndigoExtraction) -> bool:
    n = (db.query(IndigoExtraction)
         .filter_by(id=extraction.id, status="pending")
         .update({"status": "running"}))
    db.commit()
    return n == 1


def _drain() -> None:
    db = SessionLocal()
    try:
        while True:
            ext = (db.query(IndigoExtraction).filter_by(status="pending")
                   .order_by(IndigoExtraction.created_at).first())
            if not ext:
                break
            try:
                if not _claim(db, ext):
                    break
            except Exception:
                # verrou DB en concurrence avec l'autre worker de fond
                # (génération de sujet) : on relâche cette session et on
                # laisse `_loop` retenter au prochain réveil plutôt que de
                # planter `_drain` en silence (l'extraction restait "pending"
                # pour toujours, rejouée en boucle sans jamais échouer proprement).
                logger.exception("Indigo : impossible de réserver l'extraction %s", ext.id)
                db.rollback()
                break
            try:
                _run_extraction(db, ext)
            except _ExtractionCancelled:
                db.rollback()
                ext.status = "cancelled"
                ext.error_message = "Extraction arrêtée par l'utilisateur"
                ext.progress_message = "Arrêtée"
                ext.updated_at = datetime.now(timezone.utc)
                db.commit()
            except Exception as e:
                logger.exception("Indigo : extraction %s échouée", ext.id)
                db.rollback()
                ext.status = "failed"
                ext.error_message = f"{type(e).__name__}: {e}"
                ext.updated_at = datetime.now(timezone.utc)
                db.commit()
            db.expunge_all()
    finally:
        db.close()


def _loop() -> None:
    while True:
        _wake.wait(timeout=5)
        _wake.clear()
        try:
            _drain()
        except Exception:
            logger.exception("Indigo : boucle du worker interrompue")


def start_worker() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="mathprint-indigo-worker").start()


def resume_stuck(db) -> int:
    n = 0
    for ext in db.query(IndigoExtraction).filter_by(status="running").all():
        ext.status = "pending"
        n += 1
    if n:
        db.commit()
    return n


# ------------------------------------------------------------------- API publique

def dismiss_extraction(db, ext: IndigoExtraction) -> IndigoExtraction:
    """Masque le bandeau d'une extraction TERMINÉE, EN ÉCHEC ou ARRÊTÉE (ex.
    échec réseau « ConnectError » quand les API sont coupées, ou Stop manuel) :
    on la passe en statut « dismissed », filtré à l'affichage. Aucune colonne
    ajoutée (le statut est déjà une string) → pas de migration (cf. piège
    SQLite/Postgres). Les exercices déjà créés par ce run ne sont pas touchés."""
    if ext.status in ("failed", "done", "cancelled"):
        ext.status = "dismissed"
        ext.updated_at = datetime.now(timezone.utc)
        db.commit()
    return ext


def create_index_run(db, grade_level: str, created_by: str | None = None) -> IndigoExtraction:
    """Met en file une INDEXATION du manuel. Idempotente côté file : une
    indexation déjà en attente ou en cours est renvoyée telle quelle plutôt que
    d'en empiler une seconde qui lirait les mêmes pages."""
    for ext in (db.query(IndigoExtraction)
                .filter(IndigoExtraction.status.in_(("pending", "running")))
                .order_by(IndigoExtraction.created_at.desc()).all()):
        if is_index_run(ext):
            return ext
    ext = IndigoExtraction(grade_level=grade_level, targets_json=[dict(INDEX_TARGET)],
                           status="pending", created_by=created_by)
    db.add(ext)
    db.commit()
    _wake.set()
    return ext


def create_extraction(db, grade_level: str, targets: list[dict],
                      created_by: str | None = None) -> IndigoExtraction:
    # développe les plages saisies (« 34-40 » de pages, « 34-67 » de numéros) en
    # listes concrètes une bonne fois pour toutes → targets_json auto-suffisant
    norm = [normalize_target(t) for t in targets]
    ext = IndigoExtraction(grade_level=grade_level, targets_json=norm,
                           status="pending", created_by=created_by)
    db.add(ext)
    db.commit()
    _wake.set()
    return ext


def create_vision_extraction(db, grade_level: str, page_start: int, page_end: int,
                             created_by: str | None = None) -> IndigoExtraction:
    """Met en file une extraction Vision sur une plage élève 1-based inclusive."""
    doc = indigo_manual.open_doc(grade_level, "eleve")
    if doc is None:
        raise ValueError(f"Manuel élève {grade_level} indisponible")
    start, end = sorted((int(page_start), int(page_end)))
    if start < 1 or end > doc.page_count:
        raise ValueError(
            f"Pages hors manuel : indique une plage entre 1 et {doc.page_count}")
    pages = list(range(start - 1, end))
    target = {"kind": VISION_TARGET_KIND, "eleve_page_start": start,
              "eleve_page_end": end, "eleve_pages": pages}
    ext = IndigoExtraction(grade_level=grade_level, targets_json=[target],
                           status="pending", created_by=created_by)
    db.add(ext)
    db.commit()
    _wake.set()
    return ext


def extraction_out(e: IndigoExtraction) -> dict:
    return {"id": e.id, "grade_level": e.grade_level, "targets": e.targets_json,
            "status": e.status, "progress": e.progress,
            "progress_message": e.progress_message, "error_message": e.error_message,
            "stats": e.stats_json, "log_text": e.log_text,
            "created_at": e.created_at.isoformat() if e.created_at else None}


def exercise_out(db, ex: IndigoExercise) -> dict:
    comp = db.get(Competency, ex.competency_id)
    grading = ex.grading_json or {}
    short = (comp.short_id or comp.code) if comp else ""
    # ID facile à retrouver dans le manuel : compétence + numéro de badge (ex. « A1.1-29 »)
    ref = f"{short}-{ex.source_number}" if short and ex.source_number else (short or ex.source_number)
    return {
        "id": ex.id, "ref": ref, "extraction_id": ex.extraction_id,
        "competency_id": ex.competency_id,
        "competency_short_id": short,
        "competency_label": comp.label if comp else "",
        "competency_code": comp.code if comp else "",
        "source_page": ex.source_page, "source_number": ex.source_number,
        "order_index": ex.order_index,
        "badge_type": ex.badge_type, "difficulty": ex.difficulty,
        # trio base / dérivé facile / dérivé difficile (§ services.indigo_qcm)
        "variant_kind": ex.variant_kind or "base",
        "derived_from_id": ex.derived_from_id,
        "badge_color": ex.badge_color_json, "calculator": ex.calculator,
        "title": ex.title, "tags": ex.tags_json,
        "has_figure": ex.has_figure,
        # exercice jugé DÉPENDANT d'un schéma/image (indice textuel + Claude,
        # cf. _mentions_figure) : si vrai ET has_figure faux, aucune image n'a pu
        # être rattachée (ni OCR ni repli) — à traiter en priorité par l'admin.
        "figure_required": ex.figure_required,
        # Réserves de relecture posées par le mode « QCM multipass » : ce que les
        # portes reprochent encore à l'exercice conservé. Une liste vide veut
        # dire « rien à signaler », pas « pas encore contrôlé ».
        "review_notes": list((ex.raw_ocr_json or {}).get("review_notes") or []),
        # Sous-ensemble GRAVE des réserves : ce que la passe de retouche n'a pas
        # su réparer (réponse fausse, consigne incompréhensible, figure
        # indispensable et muette). À traiter avant de valider l'exercice.
        "review_blocking": list((ex.raw_ocr_json or {}).get("review_blocking") or []),
        # Rattachement à la compétence non confirmé : exercice à ranger.
        "competency_confirmed": bool(
            (ex.raw_ocr_json or {}).get("competency_confirmed", True)),
        # normalisé à l'affichage : rattrape les exercices créés avant les fixes
        # de rédaction (blank dé-wrappé, sauts de ligne a./b./c.)
        "statement": statement_mod.normalize(ex.statement),
        "response_type": ex.response_type,
        # False = l'ADAPTATION LLM (DeepSeek) n'a rien produit et l'exercice est un
        # REPLI OCR BRUT (short_text, guide/corrigé « à compléter », ni QCM ni
        # placement d'image) : ce n'est pas une « mauvaise génération » mais un
        # ÉCHEC silencieux de l'étape d'adaptation (clé DeepSeek pro absente, budget
        # atteint, erreur API). Surfacé pour ne plus le confondre avec la qualité.
        "adapted": bool((ex.raw_ocr_json or {}).get("adapted")),
        "expected": ex.expected_json, "choices": grading.get("choices") or [],
        # libellés de tableau (table_fill) pour l'aperçu — vivent dans grading
        "row_labels": grading.get("row_labels"), "col_labels": grading.get("col_labels"),
        # nombre EXACT de lignes du champ « raisonnement rédigé » (multiline_text),
        # tel qu'il sera imprimé — pour un aperçu fidèle dans l'onglet Exercices
        "lines": grading.get("lines"),
        # barème : LU dans grading_json, jamais dupliqué en colonne (§ barème).
        # `item_bareme` résout le repli des exercices d'avant le champ.
        "bareme_points": scoring.item_bareme(grading, ex.response_type),
        # normalisés à l'affichage comme l'énoncé : puces « • » (jamais « - »),
        # pastilles a./b./1., espaces — l'aperçu du guide/corrigé est cohérent
        "correction_solution": statement_mod.normalize(ex.correction_solution or ""),
        "correction_guide": statement_mod.normalize(ex.correction_guide or ""),
        "raw_ocr": ex.raw_ocr_json,
        "status": ex.status,
        # extrait du manuel = image de RÉFÉRENCE (non éditable) ; la FIGURE
        # (schéma/dessin de l'énoncé) est le crop éditable, cf. nudge_figure
        "crop_url": f"/api/indigo/exercises/{ex.id}/crop.png" if ex.crop_path else None,
        # Version dans l'URL : après édition le chemin physique reste identique,
        # mais toutes les cartes React doivent re-fetcher les nouveaux octets.
        "figure_url": (f"/api/indigo/exercises/{ex.id}/figure.png"
                       f"?v={int(ex.updated_at.timestamp() * 1_000_000)}")
                      if ex.figure_path else None,
        "figure_box": ex.figure_box_json,
    }


def _exercise_number_key(ex: IndigoExercise) -> tuple:
    """Clé de tri par NUMÉRO d'exercice du manuel (source_number), numérique et
    croissant — l'ordre de lecture que l'utilisateur voit sur la référence
    (A1.1-2, A1.1-3, A1.1-10…). Les numéros non numériques (rares) passent
    après, triés sur leur texte ; page + order_index départagent des ex-æquo."""
    m = re.match(r"\s*0*(\d+)", ex.source_number or "")
    num = int(m.group(1)) if m else 10 ** 9
    return (num, ex.source_number or "", ex.source_page, ex.order_index)


def list_exercises(db, competency_id: str | None = None,
                   extraction_id: str | None = None,
                   status: str | None = None) -> list[IndigoExercise]:
    q = db.query(IndigoExercise)
    if competency_id:
        q = q.filter(IndigoExercise.competency_id == competency_id)
    if extraction_id:
        q = q.filter(IndigoExercise.extraction_id == extraction_id)
    if status:
        q = q.filter(IndigoExercise.status == status)
    # tri par numéro d'exercice (source_number) — SQL trierait « 10 » avant « 2 »
    # (lexicographique), on trie donc numériquement en Python.
    return sorted(q.all(), key=_exercise_number_key)


_EDITABLE = {"statement", "response_type", "expected_json", "grading_json",
             "correction_solution", "correction_guide", "badge_type", "difficulty",
             "calculator", "title", "tags_json",
             # rattachement manuel : c'est la sortie de secours des exercices
             # dont la compétence n'a pas pu être confirmée (§ indigo_multipass
             # ._resolve_competency). Sans elle, un exercice mal rangé ne
             # pouvait qu'être supprimé puis réextrait.
             "competency_id"}


def update_exercise(db, ex: IndigoExercise, patch: dict) -> IndigoExercise:
    for k, v in patch.items():
        if k == "expected":
            ex.expected_json = v or {}
        elif k == "bareme_points":
            # le barème s'édite LÀ OÙ il vit (grading_json), calé sur la même
            # grille de 0,125 que celle imposée au modèle — pas de colonne
            # parallèle qui divergerait de la note réellement calculée.
            ex.grading_json = {**(ex.grading_json or {}),
                               "bareme_points": scoring.snap_bareme(v)
                               or scoring.BAREME_MIN}
        elif k == "tags":
            ex.tags_json = v or []
        elif k == "statement":
            ex.statement = statement_mod.normalize(str(v or ""))
        elif k == "competency_id":
            # Rattachement manuel : il fait AUTORITÉ. Une compétence choisie par
            # le professeur est confirmée par définition, la réserve tombe.
            target = db.get(Competency, str(v or ""))
            if target is None:
                raise ValueError(f"Compétence inconnue : {v!r}")
            ex.competency_id = target.id
            ex.raw_ocr_json = {**(ex.raw_ocr_json or {}),
                               "competency_confirmed": True}
        elif k in _EDITABLE:
            setattr(ex, k, v)
    # même garantie de champ de réponse qu'à la génération (cf. indigo_fields) :
    # une édition manuelle ne doit pas plus qu'un LLM laisser une réponse courte
    # sans case, ni une case orpheline dans un format à zone dessinée.
    if {"statement", "response_type", "expected"} & set(patch):
        ex.statement, ex.expected_json = indigo_fields.ensure_answer_field(
            ex.statement, ex.response_type, ex.expected_json)
    # toute édition sort du statut « validé » (à revalider)
    ex.status = "draft"
    ex.validated_by = None
    ex.validated_at = None
    ex.updated_at = datetime.now(timezone.utc)
    db.commit()
    return ex


# --------------------------------------------------- figure d'une FAMILLE
# Un exercice source donne un TRIO (facile / base / difficile) qui partage la
# même situation, donc la même figure : c'est la même image du manuel, recadrée
# une seule fois par l'admin. Corriger le cadrage sur l'un et laisser les deux
# autres avec l'ancien n'a aucun sens pédagogique — et c'est un piège classique,
# puisque rien à l'écran ne dit que les trois cartes montrent trois fichiers
# différents. Toute modification d'image se propage donc à la famille.
#
# Chaque ligne garde son PROPRE fichier (jamais un chemin partagé) : c'est ce
# qui permet à `delete_exercise` de supprimer les fichiers de la ligne qu'il
# efface sans vider les images de ses sœurs.


def family_ids(db, ex: IndigoExercise) -> list[str]:
    """Ids du trio auquel `ex` appartient, lui compris (la base et ses dérivés)."""
    root = ex.derived_from_id or ex.id
    rows = db.query(IndigoExercise.id).filter(
        (IndigoExercise.id == root) | (IndigoExercise.derived_from_id == root)).all()
    return [r[0] for r in rows]


def _siblings(db, ex: IndigoExercise) -> list[IndigoExercise]:
    """Les AUTRES exercices de la famille (le trio moins `ex`)."""
    ids = [i for i in family_ids(db, ex) if i != ex.id]
    if not ids:
        return []
    return db.query(IndigoExercise).filter(IndigoExercise.id.in_(ids)).all()


def _mirror_figure(db, ex: IndigoExercise) -> None:
    """Recopie l'état d'image de `ex` sur le reste de sa famille : même cadrage,
    mêmes caches, même placement du marqueur dans l'énoncé — ou même absence."""
    source = crop_abs_path(ex.figure_path) if ex.figure_path else None
    payload = source.read_bytes() if source and source.exists() else None
    for sib in _siblings(db, ex):
        if payload is not None:
            dest_rel = sib.figure_path or f"indigo/drafts/{sib.id}_fig.png"
            dest = crop_abs_path(dest_rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(payload)
            sib.has_figure = True
            sib.figure_path = dest_rel
            sib.figure_box_json = dict(ex.figure_box_json or {})
            sib.statement = statement_mod.place_figure_marker(sib.statement, True)
        else:
            if sib.figure_path:
                old = crop_abs_path(sib.figure_path)
                if old.exists():
                    old.unlink()
            sib.has_figure = False
            sib.figure_path = ""      # colonne NOT NULL (cf. _detach_figure)
            sib.figure_box_json = None
            sib.statement = statement_mod.strip_figure_marker(sib.statement)
        sib.updated_at = datetime.now(timezone.utc)


def nudge_figure(db, ex: IndigoExercise, deltas: dict) -> IndigoExercise:
    """Ajuste les 4 bords du crop de la FIGURE (schéma/dessin de l'énoncé),
    en px (+ agrandit / - rétrécit), et re-découpe son PNG. C'est le SEUL crop
    éditable : Mistral se trompe parfois sur le cadrage d'une figure, alors que
    l'extrait complet de l'exercice n'est qu'une image de référence."""
    box = dict(ex.figure_box_json or {})
    if not box or not ex.figure_path:
        return ex
    doc = indigo_manual.open_doc(ex.grade_level, "eleve")
    if doc is None:
        raise RuntimeError("Manuel élève introuvable")
    raster = indigo_manual.raster_page(doc, int(box["page_index"]))
    rh, rw = raster.shape[:2]
    x0 = max(0, min(rw, int(box["x0"]) - int(deltas.get("left", 0))))
    y0 = max(0, min(rh, int(box["y0"]) - int(deltas.get("top", 0))))
    x1 = max(0, min(rw, int(box["x1"]) + int(deltas.get("right", 0))))
    y1 = max(0, min(rh, int(box["y1"]) + int(deltas.get("bottom", 0))))
    if x1 - x0 < 20 or y1 - y0 < 20:
        return ex
    _save_edited_figure(raster, (x0, y0, x1, y1),
                        box.get("masks") or [], ex.figure_path)
    box.update({"x0": x0, "y0": y0, "x1": x1, "y1": y1})
    ex.figure_box_json = box
    ex.updated_at = datetime.now(timezone.utc)
    _mirror_figure(db, ex)      # même image pour tout le trio
    db.commit()
    return ex


def _normalized_box(raw: dict, width: int, height: int,
                    *, min_size: int = 2) -> tuple[int, int, int, int]:
    """Trie et borne un rectangle fourni par l'UI dans le raster source."""
    try:
        ax, bx = sorted((int(raw["x0"]), int(raw["x1"])))
        ay, by = sorted((int(raw["y0"]), int(raw["y1"])))
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("Rectangle d'image invalide")
    x0, x1 = max(0, min(width, ax)), max(0, min(width, bx))
    y0, y1 = max(0, min(height, ay)), max(0, min(height, by))
    if x1 - x0 < min_size or y1 - y0 < min_size:
        raise RuntimeError("Le rectangle sélectionné est trop petit")
    return x0, y0, x1, y1


def _save_edited_figure(raster: np.ndarray, crop_box, masks: list[dict],
                        dest_rel: str) -> None:
    """Produit le PNG publié à partir de la page originale.

    Les caches vivent en coordonnées page, puis sont peints sur une copie du
    crop. La page PDF n'est jamais altérée et le crop de référence reste intact.
    """
    x0, y0, x1, y1 = crop_box
    crop = raster[y0:y1, x0:x1].copy()
    for mask in masks:
        mx0, my0, mx1, my1 = _normalized_box(mask, raster.shape[1], raster.shape[0])
        # Intersection avec le crop, convertie dans son repère local.
        ix0, iy0, ix1, iy1 = max(x0, mx0), max(y0, my0), min(x1, mx1), min(y1, my1)
        if ix1 > ix0 and iy1 > iy0:
            crop[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0] = 255
    dest = crop_abs_path(dest_rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(indigo_manual.encode_png(crop))


def edit_figure(db, ex: IndigoExercise, crop: dict, masks_input: list[dict]) -> IndigoExercise:
    """Enregistre le cadrage libre et la liste complète des caches blancs."""
    box = dict(ex.figure_box_json or ex.crop_box_json or {})
    page_index = int(box.get("page_index", ex.source_page))
    doc = indigo_manual.open_doc(ex.grade_level, "eleve")
    if doc is None:
        raise RuntimeError("Manuel élève introuvable")
    if not (0 <= page_index < doc.page_count):
        raise RuntimeError("Page source hors limites")
    raster = indigo_manual.raster_page(doc, page_index)
    rh, rw = raster.shape[:2]
    crop_box = _normalized_box(crop, rw, rh, min_size=20)
    if len(masks_input) > 30:
        raise RuntimeError("Trop de caches sur une même image (maximum 30)")
    masks = [dict(zip(("x0", "y0", "x1", "y1"),
                      _normalized_box(m, rw, rh))) for m in masks_input]
    figure_path = ex.figure_path or f"indigo/drafts/{ex.id}_fig.png"
    _save_edited_figure(raster, crop_box, masks, figure_path)
    x0, y0, x1, y1 = crop_box
    ex.has_figure = True
    ex.figure_path = figure_path
    ex.figure_box_json = {"page_index": page_index, "x0": x0, "y0": y0,
                          "x1": x1, "y1": y1, "raster_dpi": indigo_manual.RASTER_DPI,
                          "img_w": rw, "img_h": rh, "masks": masks}
    ex.statement = statement_mod.place_figure_marker(ex.statement, True)
    ex.updated_at = datetime.now(timezone.utc)
    _mirror_figure(db, ex)          # même image pour tout le trio
    db.commit()
    return ex


def remove_figure(db, ex: IndigoExercise) -> IndigoExercise:
    """Supprime l'image (figure) de l'énoncé : fichier + métadonnées. L'énoncé
    renvoie souvent à « la figure ci-contre » ; à l'admin d'ajuster le texte
    ensuite si besoin (via Modifier)."""
    if ex.figure_path:
        p = crop_abs_path(ex.figure_path)
        if p.exists():
            p.unlink()
    ex.has_figure = False
    # `figure_path` est vidé avec une CHAÎNE VIDE, jamais None : la colonne est
    # NOT NULL, et un None y levait une IntegrityError au commit — « Supprimer
    # l'image » répondait donc 500 au lieu de retirer l'image (cf. _detach_figure,
    # qui portait déjà la règle).
    ex.figure_path = ""
    ex.figure_box_json = None
    ex.statement = statement_mod.strip_figure_marker(ex.statement)
    ex.updated_at = datetime.now(timezone.utc)
    _mirror_figure(db, ex)          # le trio perd l'image ensemble
    db.commit()
    return ex


def add_figure(db, ex: IndigoExercise) -> IndigoExercise:
    """Ajoute une image depuis la page PDF, même sans détection OCR de figure.

    Le crop complet de l'exercice sert de cadrage initial quand sa géométrie est
    disponible. Le fichier crop lui-même n'est pas requis : l'éditeur repart de
    la page originale, qui permet ensuite d'agrandir librement la sélection.
    """
    if ex.has_figure:
        return ex
    source = dict(ex.crop_box_json or {})
    page_index = int(source.get("page_index", ex.source_page))
    doc = indigo_manual.open_doc(ex.grade_level, "eleve")
    if doc is None:
        raise RuntimeError("Manuel élève introuvable")
    if not (0 <= page_index < doc.page_count):
        raise RuntimeError("Page source hors limites")
    raster = indigo_manual.raster_page(doc, page_index)
    rh, rw = raster.shape[:2]
    try:
        initial = _normalized_box(source, rw, rh, min_size=20)
    except RuntimeError:
        initial = (0, 0, rw, rh)
    figure_path = f"indigo/drafts/{ex.id}_fig.png"
    _save_edited_figure(raster, initial, [], figure_path)
    x0, y0, x1, y1 = initial
    ex.has_figure = True
    ex.figure_path = figure_path
    ex.figure_box_json = {"page_index": page_index, "x0": x0, "y0": y0,
                          "x1": x1, "y1": y1, "raster_dpi": indigo_manual.RASTER_DPI,
                          "img_w": rw, "img_h": rh, "masks": []}
    ex.statement = statement_mod.place_figure_marker(ex.statement, True)
    ex.updated_at = datetime.now(timezone.utc)
    _mirror_figure(db, ex)          # même image pour tout le trio
    db.commit()
    return ex


def _regen_classic(db, comp, ex: IndigoExercise, manual: dict) -> dict | None:
    """Régénération en mode classique : adaptation solo puis relecture."""
    valid = indigo_gemini.adapt_one(db, comp, ex.grade_level, manual)
    if valid is None:
        return None
    reviewed = indigo_verify.review(db, comp, ex.grade_level, [(ex, manual, valid)])
    return (reviewed.get(str(ex.source_number).strip())
            or indigo_verify._strip_raw(valid))


def _regen_qcm(db, comp, ex: IndigoExercise, manual: dict) -> dict | None:
    """Régénération en mode QCM : on rejoue le trio et on garde la variante de
    CETTE ligne. Un dérivé reste un dérivé — le régénérer ne le promeut pas en
    base, sinon les trois lignes d'un même exercice finiraient au même niveau.

    Rend None si la variante attendue n'a pas survécu à la vérification
    déterministe : l'appelant laisse alors la ligne INCHANGÉE, jamais dégradée."""
    kind = ex.variant_kind or "base"
    out = indigo_qcm.generate_batch(db, comp, ex.grade_level, [manual], set())
    entry = out.get(str(ex.source_number).strip())
    if not entry:
        return None
    for got_kind, valid in entry["variants"]:
        if got_kind == kind:
            return valid
    return None


def _regen_multipass(db, comp, ex: IndigoExercise, manual: dict) -> dict | None:
    """Régénération en mode multipass : on rejoue les cinq passes et on garde la
    variante de CETTE ligne.

    Une famille reste indivisible : si le trio ne repasse pas les cinq passes,
    on rend None et la ligne existante est laissée INCHANGÉE — jamais dégradée,
    jamais remplacée par un morceau de trio. Un dérivé reste un dérivé (le
    régénérer ne le promeut pas en base).

    La FIGURE suit la nouvelle décision de la passe 1 : un exercice régénéré qui
    ne s'appuie plus sur le dessin ne doit pas continuer de l'imprimer à côté."""
    kind = ex.variant_kind or "base"
    family = indigo_multipass.run_family(db, comp, ex.grade_level, manual, set())
    if not family.kept:
        logger.info("Indigo/multipass : régénération de %s abandonnée (%s) — %s",
                    ex.id, family.state, family.reason[:200])
        return None
    if not family.figure:
        _detach_figure(ex)
    for got_kind, valid in family.variants:
        if got_kind == kind:
            return valid
    return None


def regenerate_exercises(db, ids: list[str]) -> dict:
    """Régénère des exercices DEPUIS L'OCR déjà stocké (`raw_ocr_json` =
    {statement, correction}), avec le PROMPT et le fournisseur LLM ACTUELS —
    utile quand le prompt de génération a changé. Rejoue adaptation (solo) +
    vérification, MET À JOUR la ligne en place (garde crop/figure/badge/
    source_number), repasse en brouillon. Un exercice dont la régénération ÉCHOUE
    (LLM hors-ligne, refus) est LAISSÉ INCHANGÉ (jamais dégradé en repli OCR).

    S'ARRÊTE NET au plafond de dépense quotidien, avec la cause dans `stopped` :
    poursuivre ne produirait que des échecs (cf. incident A1.3 du 02/08)."""
    n_ok = n_fail = 0
    stopped = ""
    for ex in db.query(IndigoExercise).filter(IndigoExercise.id.in_(list(ids))).all():
        if stopped:
            n_fail += 1
            continue
        comp = db.get(Competency, ex.competency_id)
        raw = ex.raw_ocr_json or {}
        manual = {"number": ex.source_number,
                  "statement": (raw.get("statement") or ex.statement or ""),
                  "correction": (raw.get("correction") or ex.correction_solution or ""),
                  "has_figure": ex.has_figure}
        final = None
        mode = indigo_llm.mode(db)
        if comp is not None:
            try:
                regen = {indigo_llm.MODE_MULTIPASS: _regen_multipass,
                         indigo_llm.MODE_QCM: _regen_qcm}.get(mode, _regen_classic)
                final = regen(db, comp, ex, manual)
            except providers.BudgetExceeded as e:
                stopped = str(e)
                final = None
            except Exception:
                logger.exception("Indigo : régénération de l'exercice %s échouée", ex.id)
                final = None
        if final is None:                 # jamais de dégradation : on garde l'existant
            n_fail += 1
            continue
        # met à jour la ligne existante ; le repli « figure = extrait complet du
        # manuel » reste coupé en multipass (§ _persist_multipass_family)
        _persist_exercise(db, ex, manual, final,
                          allow_figure_fallback=mode != indigo_llm.MODE_MULTIPASS)
        ex.status = "draft"
        ex.validated_by = None
        ex.validated_at = None
        ex.updated_at = datetime.now(timezone.utc)
        n_ok += 1
    db.commit()
    out = {"regenerated": n_ok, "failed": n_fail}
    if stopped:
        spent, cap = providers.budget_state(db, indigo_llm.config_provider_key(db))
        out["stopped"] = (f"{stopped} ({spent:.2f} € dépensés sur 24 h, plafond "
                          f"{cap:.2f} €) — exercices inchangés.")
    return out


def validate_exercise(db, ex: IndigoExercise, user_id: str | None) -> IndigoExercise:
    ex.status = "validated"
    ex.validated_by = user_id
    ex.validated_at = datetime.now(timezone.utc)
    ex.updated_at = ex.validated_at
    db.commit()
    return ex


def delete_exercise(db, ex: IndigoExercise, *, cascade: bool = True) -> None:
    """Supprime le brouillon ET le désenregistre de la banque publiée s'il y
    était déjà (GeneratedExercise + fichier versionné) : sans ce câblage, un
    exercice supprimé depuis l'onglet Exercices restait servi aux élèves et
    ressurgissait au prochain démarrage (seed_published resème le fichier tel
    quel — cf. publish() / _unpublish()).

    Supprimer une BASE emporte ses DÉRIVÉS (§ services.indigo_qcm) : c'est le
    même exercice du manuel à deux autres niveaux, et laisser derrière soi des
    dérivés pointant une base disparue rendrait le trio incohérent dans l'onglet
    comme dans la banque. `cascade=False` sert à la récursion elle-même."""
    if cascade:
        for child in db.query(IndigoExercise).filter_by(derived_from_id=ex.id).all():
            delete_exercise(db, child, cascade=False)
    for rel in (ex.crop_path, ex.figure_path):
        if rel:
            p = crop_abs_path(rel)
            if p.exists():
                p.unlink()
    ex_id = ex.id
    db.delete(ex)
    db.query(GeneratedExercise).filter_by(id=ex_id, source="indigo").delete()
    _unpublish(ex_id)
    db.commit()


def delete_exercises_for_competency(db, competency_id: str) -> int:
    """Supprime TOUS les exercices d'une compétence (brouillons ET validés),
    avec le même nettoyage complet que `delete_exercise` par exercice (crops,
    GeneratedExercise, dé-publication). Retourne le nombre supprimé. Action de
    l'onglet Exercices « Tout supprimer » (après confirmation côté UI)."""
    rows = db.query(IndigoExercise).filter_by(competency_id=competency_id).all()
    for ex in rows:
        delete_exercise(db, ex)
    return len(rows)


# ------------------------------------------------------------- publication (bake)
#
# Les exercices VALIDÉS sont figés dans des fichiers VERSIONNÉS du repo, sous
# le dossier du package `app` (livré dans l'image Docker, cf. _APP_DIR) — donc
# présents à l'identique dans TOUS les déploiements, contrairement aux
# brouillons qui vivent dans la DB de l'instance admin. Au démarrage, chaque
# déploiement charge ce JSON et sème des lignes GeneratedExercise (source
# "indigo") : les exercices Indigo transitent alors par la banque et les sujets
# comme n'importe quelle autre source, SANS que l'onglet Exercices soit présent.

# DEUX COUCHES, et c'est la clé du déploiement NAS.
#
# `_IMAGE_PUB_DIR` est livré DANS l'image (versionné dans le repo) : c'est le
# socle commun de tous les déploiements, en lecture seule. Le conteneur est
# recréé à chaque `docker compose pull && up -d`, donc tout ce qu'on y écrit
# disparaît en silence — et `seed_published`, appelé au démarrage, remettrait
# la banque à l'état du repo sans le moindre message.
#
# `_VOLUME_PUB_DIR` vit sur le VOLUME persistant (/data). C'est là que
# `publish` écrit, pour que le professeur puisse extraire, corriger et publier
# depuis le NAS sans perdre son travail à la mise à jour suivante.
#
# La lecture prend le volume s'il existe, l'image sinon : une instance qui n'a
# jamais publié (tous les autres déploiements) lit le contenu livré, et
# l'instance qui publie lit le sien. Pour diffuser à tout le monde, on exporte
# le volume en archive (`export_bundle`) que l'on commite dans le repo — d'où
# elle repart dans l'image au build suivant.
_IMAGE_PUB_DIR = _APP_DIR / "data" / "indigo"


def _volume_pub_dir():
    return settings.data_dir / "indigo" / "published"


def _read_dir():
    """Couche qui fait autorité en LECTURE : le volume dès qu'il a été publié."""
    vol = _volume_pub_dir()
    return vol if (vol / "exercises.json").exists() else _IMAGE_PUB_DIR


def _write_dir():
    """Couche d'ÉCRITURE : toujours le volume — jamais l'image, qui est
    reconstruite à chaque mise à jour."""
    d = _volume_pub_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _layout(base):
    return base, base / "crops", base / "figures", base / "exercises.json"


def _pub_paths():
    """Chemins de LECTURE du contenu publié (cf. _read_dir)."""
    return _layout(_read_dir())


def _promote_to_volume() -> None:
    """Recopie le contenu livré dans l'image vers le volume, une seule fois.

    Nécessaire avant toute écriture PARTIELLE (retrait d'un exercice) : sans
    ça on modifierait l'image — c'est-à-dire rien du tout, la modification
    partant avec le conteneur. `publish`, lui, réécrit tout et n'en a pas
    besoin."""
    vol = _volume_pub_dir()
    if (vol / "exercises.json").exists():
        return
    src_base, src_crops, src_figs, src_json = _layout(_IMAGE_PUB_DIR)
    if not src_json.exists():
        return
    _, crops, figs, jf = _layout(_write_dir())
    crops.mkdir(parents=True, exist_ok=True)
    figs.mkdir(parents=True, exist_ok=True)
    for folder, dest in ((src_crops, crops), (src_figs, figs)):
        if folder.is_dir():
            for f in folder.glob("*.png"):
                shutil.copyfile(f, dest / f.name)
    shutil.copyfile(src_json, jf)
    logger.info("Indigo : contenu publié promu de l'image vers le volume (%s)", jf)


def _resolve_competency(db, code: str, grade: str):
    """Résout une compétence par son CODE (stable entre déploiements) et non par
    id (UUID régénéré à chaque seed de competencies_fr.json)."""
    fw = db.query(CompetencyFramework).filter_by(grade_level=grade).first()
    if fw is None:
        return None
    return db.query(Competency).filter_by(framework_id=fw.id, code=code).first()


class PublishRefused(RuntimeError):
    """Publication refusée pour ne pas détruire du contenu déjà en place."""


def publish(db, force: bool = False) -> dict:
    """Bake les exercices VALIDÉS sur le VOLUME persistant + rafraîchit la
    banque en base. À lancer sur l'instance qui porte les manuels (le NAS du
    professeur) ; `export_bundle` produit ensuite l'archive à commiter dans le
    repo pour livrer ces exercices à tous les déploiements.

    Refuse de publier un ensemble VIDE tant que du contenu est déjà publié :
    `publish` réécrit tout à partir des brouillons validés de CETTE instance,
    et une base de brouillons vide (instance neuve, purge, restauration
    partielle) effacerait donc silencieusement les exercices livrés — que
    `seed_published` retirerait de la banque dans la foulée. `force` lève le
    garde-fou quand la mise à zéro est réellement voulue."""
    _, crops, figs, jf = _layout(_write_dir())
    crops.mkdir(parents=True, exist_ok=True)
    figs.mkdir(parents=True, exist_ok=True)
    rows = (db.query(IndigoExercise).filter_by(status="validated")
            .order_by(IndigoExercise.competency_id, IndigoExercise.source_page,
                      IndigoExercise.order_index).all())
    records = [_published_record(db, ex, crops, figs) for ex in rows]
    already = len(load_published().get("exercises", []))
    if not records and already and not force:
        raise PublishRefused(
            f"Aucun exercice validé sur cette instance, alors que {already} "
            f"exercice(s) sont publiés : publier maintenant les effacerait. "
            f"Validez des exercices, ou forcez explicitement la remise à zéro.")
    payload = {"version": settings.indigo_schema_version, "grade_level": "3e",
               "generated_at": datetime.now(timezone.utc).isoformat(),
               "exercises": records}
    jf.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    seeded = seed_published(db)
    logger.info("Indigo : %s exercice(s) publié(s), %s semé(s) en banque",
                len(records), seeded)
    return {"published": len(records), "seeded": seeded}


def _published_record(db, ex: IndigoExercise, crops, figs) -> dict:
    """Un exercice validé figé en enregistrement publiable, ses images copiées.

    Extrait de `publish` pour être partagé avec `publish_rows` (publication
    IMMÉDIATE d'une famille) : les deux voies doivent écrire exactement le même
    enregistrement, sinon un exercice publié à chaud différerait du même exercice
    republié en bloc — et la différence ne se verrait qu'à l'usage."""
    comp = db.get(Competency, ex.competency_id)
    crop_file = fig_file = ""
    if ex.crop_path and crop_abs_path(ex.crop_path).exists():
        crop_file = f"{ex.id}.png"
        shutil.copyfile(crop_abs_path(ex.crop_path), crops / crop_file)
    if ex.has_figure and ex.figure_path and crop_abs_path(ex.figure_path).exists():
        fig_file = f"{ex.id}.png"
        shutil.copyfile(crop_abs_path(ex.figure_path), figs / fig_file)
    return {
        "id": ex.id, "competency_code": comp.code if comp else "",
        "grade_level": ex.grade_level, "source_number": ex.source_number,
        "badge_type": ex.badge_type, "difficulty": ex.difficulty,
        "variant_kind": ex.variant_kind or "base",
        "derived_from_id": ex.derived_from_id,
        "calculator": ex.calculator, "title": ex.title, "tags": ex.tags_json,
        "statement": ex.statement, "response_type": ex.response_type,
        # `grading` porte le barème (bareme_points) : rien à publier à côté
        "expected": ex.expected_json, "grading": ex.grading_json,
        "correction_guide": ex.correction_guide,
        "correction_solution": ex.correction_solution,
        "has_figure": ex.has_figure, "crop_file": crop_file, "figure_file": fig_file,
        "model": ex.model, "prompt_version": ex.prompt_version,
    }


def publish_rows(db, rows: list[IndigoExercise]) -> int:
    """Publie IMMÉDIATEMENT quelques exercices, sans réécrire tout le fichier.

    C'est la voie du mode « QCM multipass » : dès qu'une famille atteint READY,
    ses trois exercices partent en banque, sans attendre la fin de l'extraction
    ni un clic sur « Publier ». Une extraction interrompue à la moitié laisse
    donc derrière elle des exercices RÉELLEMENT utilisables, pas un tas de
    brouillons.

    Écriture PARTIELLE, donc `_promote_to_volume` d'abord — sinon on modifierait
    l'image, c'est-à-dire rien (le conteneur est recréé à chaque mise à jour).
    Republier un exercice déjà publié le REMPLACE (même id) : la fonction est
    idempotente, une famille régénérée écrase proprement la précédente.

    Retourne le nombre d'exercices publiés. Ne touche PAS aux autres : à la
    différence de `publish`, elle n'a rien à effacer, donc pas de garde-fou de
    remise à zéro à prévoir."""
    rows = [ex for ex in rows if ex is not None]
    if not rows:
        return 0
    _promote_to_volume()
    _, crops, figs, jf = _layout(_write_dir())
    crops.mkdir(parents=True, exist_ok=True)
    figs.mkdir(parents=True, exist_ok=True)
    data = load_published() if jf.exists() else {}
    records = [r for r in data.get("exercises", [])]
    new_records = [_published_record(db, ex, crops, figs) for ex in rows]
    new_ids = {r["id"] for r in new_records}
    payload = {"version": settings.indigo_schema_version,
               "grade_level": data.get("grade_level", "3e"),
               "generated_at": datetime.now(timezone.utc).isoformat(),
               "exercises": [r for r in records if r.get("id") not in new_ids]
                            + new_records}
    jf.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    # la banque suit dans la foulée : sans ce semis, les exercices n'existeraient
    # que dans le fichier et n'apparaîtraient qu'au prochain redémarrage.
    db.query(GeneratedExercise).filter(GeneratedExercise.id.in_(list(new_ids))).delete(
        synchronize_session=False)
    for rec in new_records:
        _seed_record(db, payload, rec, figs)
    db.commit()
    logger.info("Indigo : %s exercice(s) publié(s) à chaud (%s au total)",
                len(new_records), len(payload["exercises"]))
    return len(new_records)


def _unpublish(ex_id: str) -> bool:
    """Retire un exercice du fichier versionné (exercises.json) s'il y
    figurait déjà. Sans ça, seed_published — appelé à CHAQUE démarrage — le
    resème depuis le fichier tel quel, et un exercice supprimé depuis l'onglet
    Exercices réapparaîtrait dans la banque après le prochain redémarrage.
    No-op silencieux si l'exercice n'a jamais été publié."""
    if not (_read_dir() / "exercises.json").exists():
        return False
    # écriture partielle : on bascule d'abord sur le volume, sinon on
    # modifierait l'image (donc rien, cf. _promote_to_volume)
    _promote_to_volume()
    _, crops, figs, jf = _layout(_write_dir())
    if not jf.exists():
        return False
    try:
        data = json.loads(jf.read_text(encoding="utf-8"))
    except Exception:
        return False
    records = data.get("exercises", [])
    kept = [r for r in records if r.get("id") != ex_id]
    if len(kept) == len(records):
        return False
    for r in records:
        if r.get("id") != ex_id:
            continue
        for key, folder in (("crop_file", crops), ("figure_file", figs)):
            fname = r.get(key)
            if fname and (folder / fname).exists():
                (folder / fname).unlink()
    data["exercises"] = kept
    data["generated_at"] = datetime.now(timezone.utc).isoformat()
    jf.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return True


def export_bundle() -> tuple[bytes, str]:
    """Archive ZIP du contenu publié, arrangée EXACTEMENT comme
    `backend/app/data/indigo/` — (octets, nom de fichier).

    C'est le pont vers les autres déploiements. Le NAS écrit sur son volume,
    qui ne sort jamais tout seul de la machine ; cette archive se décompresse
    par-dessus `backend/app/data/indigo/` dans le dépôt, se commite, et repart
    dans l'image au build suivant — où `seed_published` la sème en banque pour
    tout le monde. Aucun secret ne vit sur le NAS : c'est vous qui décidez ce
    qui entre dans le dépôt à partir duquel vos images sont construites."""
    base, crops, figs, jf = _pub_paths()
    data = load_published()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if jf.exists():
            z.write(jf, "exercises.json")
        # Seuls les fichiers RÉFÉRENCÉS par le JSON : une image orpheline
        # (exercice retiré depuis) n'a rien à faire dans le dépôt.
        for rec in data.get("exercises", []):
            for key, folder, prefix in (("crop_file", crops, "crops"),
                                        ("figure_file", figs, "figures")):
                name = rec.get(key)
                if name and (folder / name).exists():
                    z.write(folder / name, f"{prefix}/{name}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return buf.getvalue(), f"indigo-publication-{stamp}.zip"


def published_status() -> dict:
    """D'où vient le contenu publié que lit CETTE instance, et combien il pèse.
    Sert à ce que le professeur voie noir sur blanc si sa publication est sur
    le volume (persistante) ou s'il lit encore le contenu livré dans l'image."""
    read_dir = _read_dir()
    on_volume = read_dir != _IMAGE_PUB_DIR
    data = load_published()
    return {"count": len(data.get("exercises", [])),
            "on_volume": on_volume,
            "source": "volume" if on_volume else "image",
            "path": str(read_dir),
            "generated_at": data.get("generated_at", "")}


def load_published() -> dict:
    _, _, _, jf = _pub_paths()
    if not jf.exists():
        return {"exercises": []}
    try:
        return json.loads(jf.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Indigo : exercises.json illisible")
        return {"exercises": []}


# Version du fichier publié à partir de laquelle `difficulty` est déjà sur
# l'échelle à TROIS niveaux. En deçà (fichier écrit avant la migration), il
# porte encore un 1-5 qu'il faut replier — et on ne peut pas trancher sur la
# seule valeur : « 3 » valait « moyen » avant et « difficile » après.
_LEVEL3_SCHEMA_VERSION = 2


def _published_level(data: dict, rec: dict) -> int:
    """Niveau 1-3 d'un exercice publié, quelle que soit la version du fichier."""
    try:
        version = int(data.get("version") or 1)
    except (TypeError, ValueError):
        version = 1
    raw = rec.get("difficulty", 2)
    if version >= _LEVEL3_SCHEMA_VERSION:
        try:
            return min(3, max(1, int(raw)))
        except (TypeError, ValueError):
            return 2
    return exercise_gen.to_level3(raw)


def seed_published(db) -> int:
    """Charge les exercices publiés en lignes GeneratedExercise (source=indigo).
    Idempotent : purge puis réinsère (le fichier versionné fait autorité).
    Appelé au démarrage de TOUS les déploiements."""
    _, _crops, figs, _ = _pub_paths()
    data = load_published()
    db.query(GeneratedExercise).filter_by(source="indigo").delete()
    n = sum(1 for rec in data.get("exercises", []) if _seed_record(db, data, rec, figs))
    db.commit()
    return n


def _seed_record(db, data: dict, rec: dict, figs) -> bool:
    """Sème UN exercice publié en ligne GeneratedExercise. False = sauté.

    Partagé par `seed_published` (démarrage, tout le fichier) et `publish_rows`
    (publication à chaud d'une famille) : une seule écriture de la ligne de
    banque, donc aucune divergence possible entre les deux chemins."""
    comp = _resolve_competency(db, rec.get("competency_code", ""),
                               rec.get("grade_level", "3e"))
    if comp is None:
        return False  # compétence absente de ce déploiement : on saute proprement
    fig_json = None
    if rec.get("figure_file"):
        fig_json = {"type": "image", "params": {"path": str(figs / rec["figure_file"])}}
    db.add(GeneratedExercise(
        id=rec["id"], competency_id=comp.id,
        difficulty_level=_published_level(data, rec), variant=0,
        statement=rec.get("statement", ""), correction=rec.get("correction_guide", ""),
        response_type=rec.get("response_type", "short_text"),
        expected_json=rec.get("expected") or {}, grading_json=rec.get("grading") or {},
        source="indigo",
        kind="probleme" if rec.get("badge_type") in ("probleme", "enigme") else "application",
        status="active", figure_json=fig_json,
        raw_extract_json={"indigo": {k: rec.get(k) for k in (
            "badge_type", "difficulty", "calculator", "title", "tags",
            "correction_solution", "crop_file", "source_number",
            "variant_kind", "derived_from_id")}},
        model=rec.get("model", ""), prompt_version=rec.get("prompt_version", "")))
    return True


def published_rows(db, competency, level: int) -> list:
    """Pool Indigo (fini) pour (compétence, niveau) — appelé par
    exercise_gen.ensure_bank via la source "indigo"."""
    return (db.query(GeneratedExercise)
            .filter_by(competency_id=competency.id, difficulty_level=level,
                       status="active", source="indigo").all())
