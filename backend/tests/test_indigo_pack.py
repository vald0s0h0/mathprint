"""Indigo — PACK DE TRAVAIL : fabriquer des exercices sans les PDF des manuels.

Ce que ces tests verrouillent, c'est la promesse du pack : une instance qui n'a
AUCUN manuel doit produire exactement les mêmes exercices que celle qui les a.
D'où deux angles :

  • le pack se substitue au PDF partout où la pipeline lit des pixels
    (`indigo_manual.raster_page`), et les corrigés — du texte — se lisent depuis
    l'index sans le moindre PDF ;
  • la COULEUR survit au transport. La difficulté d'un exercice se lit dans un
    badge, et le fond « expert » se reconnaît à ±16 par canal : c'est la
    propriété la plus fragile du pack, donc celle qu'on mesure.
"""
import json
import sys
import zipfile
from pathlib import Path

import cv2
import fitz
import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db import Base
from app.services import indigo, indigo_cv, indigo_index, indigo_manual, indigo_pack


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Instance vierge : un data_dir à elle, et AUCUN manuel résolvable."""
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "indigo_manuals", {})
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield tmp_path, s
    finally:
        s.close()


def _make_pdf(path: Path, pages: int = 3) -> Path:
    """Petit manuel de test : une page par couleur franche, pour que la
    comparaison PDF/pack porte sur des pixels reconnaissables."""
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=300, height=400)
        page.draw_rect(fitz.Rect(20, 20 + 30 * i, 160, 120 + 30 * i),
                       color=None, fill=(0.157, 0.706, 0.726))
        page.insert_text((40, 200), f"page {i + 1}", fontsize=24)
    doc.save(path)
    doc.close()
    return path


def _install_manuals(monkeypatch, tmp_path, pages=3):
    pdf = _make_pdf(tmp_path / "manuel_test_eleve.pdf", pages)
    monkeypatch.setattr(settings, "indigo_manuals",
                        {"3e": {"eleve": str(pdf), "prof": str(tmp_path / "absent_prof.pdf")}})
    return pdf


def _write_index(grade, which, page_count, pages=None):
    data = {"version": indigo_index.INDEX_VERSION, "grade_level": grade, "which": which,
            "sha256": "", "page_count": page_count,
            "pages": pages if pages is not None
            else {str(i): {"source_page": i, "dims": {"width": 100, "height": 100},
                           "blocks": [], "numbers": []} for i in range(page_count)}}
    indigo_index.index_path(grade, which).write_text(json.dumps(data), encoding="utf-8")
    return data


# --------------------------------------------------------------- transport

def test_le_pack_remplace_le_manuel_pixel_pour_pixel(env, monkeypatch):
    """Exporté depuis la machine qui a le PDF, importé sur une instance qui ne
    l'a pas : `open_doc` rend une source de pages, et ses rasters sont ceux du
    PDF (aux quelques niveaux près que coûte la compression)."""
    tmp_path, db = env
    _install_manuals(monkeypatch, tmp_path)
    _write_index("3e", "eleve", 3)
    _write_index("3e", "prof", 2)
    from_pdf = [indigo_manual.raster_page(indigo_manual.open_pdf("3e", "eleve"), i)
                for i in range(3)]

    archive = tmp_path / "pack.zip"
    info = indigo_pack.export_zip("3e", archive)
    assert info["pages"] == 3

    # l'instance de destination : plus aucun manuel, et un data_dir tout neuf
    monkeypatch.setattr(settings, "indigo_manuals", {})
    monkeypatch.setattr(settings, "data_dir", tmp_path / "cible")
    assert indigo_manual.open_doc("3e", "eleve") is None

    indigo_pack.import_zip(archive)
    doc = indigo_manual.open_doc("3e", "eleve")
    assert doc is not None and doc.page_count == 3
    for i in range(3):
        got = indigo_manual.raster_page(doc, i)
        assert got.shape == from_pdf[i].shape
        assert float(np.abs(got.astype(int) - from_pdf[i].astype(int)).mean()) < 2.0
    # l'index voyage avec les pages : rien à réindexer, aucun OCR à repayer
    assert len((indigo_index.load("3e", "eleve") or {}).get("pages") or {}) == 3
    assert len((indigo_index.load("3e", "prof") or {}).get("pages") or {}) == 2


def test_le_pack_ne_change_pas_la_lecture_des_couleurs(env):
    """La couleur PORTE la difficulté : badge teal/orange/rouge, et surtout le
    fond « expert » reconnu à ±16 par canal. Le format du pack doit rendre la
    même classification que le raster d'origine — c'est ce qui interdit de
    baisser la qualité ou de laisser le sous-échantillonnage par défaut."""
    for name, badge_rgb, expert in (("exercice", (40, 180, 185), False),
                                    ("flash", (240, 134, 47), False),
                                    ("enigme", (240, 70, 43), False),
                                    ("expert", (40, 180, 185), True)):
        page = np.full((400, 300, 3), 255, np.uint8)
        if expert:                      # carte au fond vert d'eau très pâle
            page[:, :] = indigo_cv.EXPERT_BG_RGB[::-1]
        cv2.circle(page, (60, 60), 22, badge_rgb[::-1], -1)     # RVB -> BGR
        cv2.putText(page, "42", (48, 68), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        num_box = {"x0": 38, "y0": 38, "x1": 82, "y1": 82}

        packed = cv2.imdecode(np.frombuffer(indigo_pack.encode_page(page), np.uint8),
                              cv2.IMREAD_COLOR)
        avant = indigo_cv.analyze(page, False, number_box=num_box)
        apres = indigo_cv.analyze(packed, False, number_box=num_box)
        assert (apres["badge_type"], apres["difficulty"]) \
            == (avant["badge_type"], avant["difficulty"]), name
        assert apres["badge_type"] == name


def test_un_pack_tronque_est_refuse_sans_rien_installer(env, monkeypatch):
    """Un transfert coupé ne doit pas laisser l'instance à moitié équipée : on
    refuse AVANT d'écrire, et `open_doc` continue de rendre None (donc le
    message « importe un pack » reste affiché)."""
    tmp_path, db = env
    _install_manuals(monkeypatch, tmp_path, pages=3)
    _write_index("3e", "eleve", 3)
    archive = tmp_path / "pack.zip"
    indigo_pack.export_zip("3e", archive)

    tronque = tmp_path / "tronque.zip"
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(tronque, "w") as out:
        for name in src.namelist():
            if name == "pages/0002.jpg":
                continue
            out.writestr(name, src.read(name))

    monkeypatch.setattr(settings, "indigo_manuals", {})
    monkeypatch.setattr(settings, "data_dir", tmp_path / "cible")
    with pytest.raises(RuntimeError, match="incomplet"):
        indigo_pack.import_zip(tronque)
    assert indigo_pack.manifest("3e") is None
    assert indigo_manual.open_doc("3e", "eleve") is None


def test_un_pack_n_ecrit_jamais_hors_de_son_dossier(env, monkeypatch):
    """Une archive vient d'ailleurs : une entrée « ../../ » ne doit rien
    écrire en dehors du dossier du pack."""
    tmp_path, db = env
    monkeypatch.setattr(settings, "data_dir", tmp_path / "cible")
    piege = tmp_path / "piege.zip"
    page = indigo_pack.encode_page(np.full((40, 40, 3), 255, np.uint8))
    with zipfile.ZipFile(piege, "w") as z:
        z.writestr("pages/0000.jpg", page)
        z.writestr("../../evade.txt", "nope")
        z.writestr("index/../../evade2.txt", "nope")
        z.writestr(indigo_pack.MANIFEST, json.dumps(
            {"version": indigo_pack.PACK_VERSION, "grade_level": "3e",
             "page_count": 1, "dpi": indigo_manual.RASTER_DPI}))
    indigo_pack.import_zip(piege)
    assert not (tmp_path / "evade.txt").exists()
    assert not (tmp_path.parent / "evade.txt").exists()
    assert not list(tmp_path.rglob("evade*.txt"))
    assert indigo_pack.load("3e") is not None


def test_exporter_sans_index_complet_est_refuse_avec_la_marche_a_suivre(env, monkeypatch):
    """Un pack sans index ne porterait aucun énoncé : l'instance de destination
    devrait repayer tout l'OCR. On refuse, en disant quoi faire."""
    tmp_path, db = env
    _install_manuals(monkeypatch, tmp_path, pages=3)
    _write_index("3e", "eleve", 3, pages={"0": {"source_page": 0, "dims": {}, "blocks": []}})
    with pytest.raises(RuntimeError, match="Indexer le manuel"):
        indigo_pack.export_zip("3e", tmp_path / "pack.zip")


def test_seule_une_instance_qui_a_les_pdf_peut_exporter(env):
    tmp_path, db = env
    with pytest.raises(RuntimeError, match="introuvable"):
        indigo_pack.export_zip("3e", tmp_path / "pack.zip")


# ------------------------------------------------- lecture sans aucun PDF

def test_les_corriges_du_prof_se_lisent_sans_le_moindre_pdf(env):
    """Les corrigés sont du TEXTE : ils voyagent dans l'index. Une instance sans
    manuel prof doit les servir sans appeler l'OCR (donc sans dépense)."""
    tmp_path, db = env
    pages = {"7": {"source_page": 7, "dims": {"width": 100, "height": 100},
                   "blocks": [{"type": "text", "content": "12 PGCD(24, 36) = 12.",
                               "top_left_x": 0, "top_left_y": 0,
                               "bottom_right_x": 50, "bottom_right_y": 10}],
                   "numbers": [12]}}
    _write_index("3e", "prof", 8, pages=pages)
    got = indigo._ocr_pages(db, None, [7], "prof-A1.1", grade="3e", which="prof")
    assert got[0]["blocks"][0]["content"].startswith("12 PGCD")


def test_une_page_absente_de_l_index_sans_manuel_le_dit_clairement(env):
    """Sans PDF, une page hors index est définitivement illisible : mieux vaut
    une erreur qui nomme la solution qu'un plantage dans le découpeur de PDF."""
    tmp_path, db = env
    _write_index("3e", "eleve", 3)
    with pytest.raises(RuntimeError, match="pack de travail"):
        indigo._ocr_pages(db, None, [99], "eleve-A1.1", grade="3e", which="eleve")


def test_sans_pdf_ni_pack_l_extraction_dit_quoi_faire(env):
    """Le message d'échec est la seule chose que verra l'admin : il doit nommer
    les deux voies, pas seulement constater l'absence."""
    tmp_path, db = env
    from app.models import IndigoExtraction
    ext = IndigoExtraction(grade_level="3e", targets_json=[{"competency_id": "x"}],
                           status="running")
    db.add(ext); db.commit()
    with pytest.raises(RuntimeError) as e:
        indigo._run_extraction(db, ext)
    assert "pack de travail" in str(e.value) and "manuals/" in str(e.value)


def test_indexer_sans_pdf_renvoie_vers_le_pack(env):
    """Indexer LIT le PDF : impossible sur une instance qui n'en a pas. Le
    message doit dire que le pack rend justement l'indexation inutile."""
    tmp_path, db = env
    with pytest.raises(RuntimeError, match="PACK DE TRAVAIL"):
        indigo_index.build(db, "3e", lambda m, f=None: None)


def test_le_statut_dit_si_l_on_peut_exporter_ou_s_il_faut_importer(env, monkeypatch):
    tmp_path, db = env
    assert indigo_pack.status("3e")["source"] == "aucune"
    _install_manuals(monkeypatch, tmp_path)
    st = indigo_pack.status("3e")
    assert st["source"] == "manuel" and st["can_export"] is True

    _write_index("3e", "eleve", 3)
    archive = tmp_path / "pack.zip"
    indigo_pack.export_zip("3e", archive)
    monkeypatch.setattr(settings, "indigo_manuals", {})
    monkeypatch.setattr(settings, "data_dir", tmp_path / "cible")
    indigo_pack.import_zip(archive)
    st = indigo_pack.status("3e")
    assert st["source"] == "pack" and st["can_export"] is False
    assert st["pack"]["pages_present"] == 3


# ------------------------------------------------------------------ API

def test_l_aller_retour_passe_par_l_api_comme_pour_le_professeur(env, monkeypatch):
    """Le trajet réel : la machine qui a les manuels TÉLÉCHARGE le pack, celle
    qui fabrique l'ENVOIE. C'est le branchement HTTP (fichier temporaire servi
    puis effacé, réception en flux) qui se vérifie ici, pas le contenu."""
    from fastapi.testclient import TestClient

    from app.deps import current_user
    from app.db import get_db
    from app.main import app
    from app.models import User

    tmp_path, db = env
    _install_manuals(monkeypatch, tmp_path)
    _write_index("3e", "eleve", 3)
    _write_index("3e", "prof", 2)

    app.dependency_overrides[get_db] = lambda: db
    # require_role("admin") fabrique une fonction neuve à chaque appel : c'est
    # `current_user`, dont elle dépend, qu'il faut surcharger.
    app.dependency_overrides[current_user] = lambda: User(
        id="u1", email="admin@test.fr", role="admin")
    try:
        client = TestClient(app)
        assert client.get("/api/indigo/manuals?grade_level=3e").json()["pack"]["source"] == "manuel"

        r = client.get("/api/indigo/pack/export?grade_level=3e")
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
        blob = r.content
        assert not list((settings.data_dir / "tmp").glob("*.zip")), \
            "l'archive temporaire doit être effacée après l'envoi"

        # l'instance de destination : plus de manuel, un volume neuf
        monkeypatch.setattr(settings, "indigo_manuals", {})
        monkeypatch.setattr(settings, "data_dir", tmp_path / "cible")
        assert client.get("/api/indigo/pack?grade_level=3e").json()["source"] == "aucune"

        up = client.post("/api/indigo/pack/import?grade_level=3e",
                         files={"file": ("pack.zip", blob, "application/zip")})
        assert up.status_code == 200, up.text
        assert up.json()["pages"] == 3

        manuals = client.get("/api/indigo/manuals?grade_level=3e").json()
        assert manuals["pack"]["source"] == "pack"
        # `available` dit « on peut travailler », pas « le PDF est là » : c'est
        # ce booléen qui rouvre l'assistant d'extraction côté interface.
        assert manuals["manuals"]["eleve"]["available"] is True
        assert manuals["manuals"]["eleve"]["pdf"] is False
        assert manuals["manuals"]["eleve"]["pages"] == 3
    finally:
        app.dependency_overrides.clear()


def test_une_archive_qui_n_est_pas_un_pack_est_refusee_proprement(env, monkeypatch):
    from fastapi.testclient import TestClient

    from app.deps import current_user
    from app.db import get_db
    from app.main import app
    from app.models import User

    tmp_path, db = env
    app.dependency_overrides[get_db] = lambda: db
    # require_role("admin") fabrique une fonction neuve à chaque appel : c'est
    # `current_user`, dont elle dépend, qu'il faut surcharger.
    app.dependency_overrides[current_user] = lambda: User(
        id="u1", email="admin@test.fr", role="admin")
    try:
        client = TestClient(app)
        r = client.post("/api/indigo/pack/import?grade_level=3e",
                        files={"file": ("bidon.zip", b"pas un zip", "application/zip")})
        assert r.status_code == 422
        # sans fichier ET sans dépôt sur le volume : on dit où déposer l'archive
        r = client.post("/api/indigo/pack/import?grade_level=3e")
        assert r.status_code == 404 and indigo_pack.DROP_NAME in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()
