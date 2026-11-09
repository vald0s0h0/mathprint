"""Lecture du QR de page : fiable pour TOUS les identifiants, pas seulement la
plupart. Le détecteur par défaut d'OpenCV rate ~2 % des motifs (identifiants
tirés au hasard) : sans repli, un test à identifiant aléatoire échouait ~1 fois
sur 20 en CI. Ici les identifiants sont FIXES, dont des cas connus difficiles."""
import hashlib
import hmac
import random
import uuid

import cv2
import pytest

from app.config import settings
from app.services import worker_cv
from app.services.security import sign_page, verify_page_payload
from test_scan_intake import _marked_pdf

# identifiants que cv2.QRCodeDetector() ne lisait pas (OpenCV 5.0, 150 tirages)
HARD_IDS = ["e876e287-efed-4a84-9dc9-573f6b60835a",
            "24941cdf-7bc0-4aa2-b1cf-2f3f1fbf55d4",
            "ccacc167-6a2f-4d7a-b743-a72d5851d208"]
IDS = HARD_IDS + [str(uuid.UUID(int=random.Random(n).getrandbits(128), version=4)) for n in range(12)]


def _legacy(pid: str) -> str:
    sig = hmac.new(settings.hmac_key.encode(), pid.encode(), hashlib.sha256).hexdigest()[:16]
    return f"MP1|{pid}|{sig}"


def _image(payload: str, tmp_path):
    f = tmp_path / "p.pdf"
    f.write_bytes(_marked_pdf(payload))
    return worker_cv.raster_pdf(str(f))[0]


def _read(payload: str, tmp_path):
    img = _image(payload, tmp_path)
    return {verify_page_payload(t) for t, _ in worker_cv._detect_qrcodes(img)} - {None}


@pytest.mark.parametrize("make", [_legacy, sign_page], ids=["legacy_mp1", "compact_m2"])
def test_chaque_identifiant_est_lu(make, tmp_path):
    unread = [pid for pid in IDS if pid not in _read(make(pid), tmp_path)]
    assert not unread, f"QR non lus : {unread}"


class _Blind:                         # détecteur par défaut qui ne voit rien
    def detectAndDecodeMulti(self, _img):
        return False, [], None, None


def test_repli_aruco_quand_le_detecteur_par_defaut_ne_lit_rien(tmp_path, monkeypatch):
    img = _image(sign_page(HARD_IDS[0]), tmp_path)
    monkeypatch.setattr(cv2, "QRCodeDetector", _Blind)
    assert {verify_page_payload(t) for t, _ in worker_cv._detect_qrcodes(img)} == {HARD_IDS[0]}


def test_sans_aruco_on_retombe_sur_le_resultat_par_defaut(tmp_path, monkeypatch):
    img = _image(sign_page(HARD_IDS[0]), tmp_path)
    monkeypatch.setattr(cv2, "QRCodeDetector", _Blind)
    monkeypatch.delattr(cv2, "QRCodeDetectorAruco", raising=False)
    assert worker_cv._detect_qrcodes(img) == []
