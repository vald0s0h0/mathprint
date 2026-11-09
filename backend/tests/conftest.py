import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _no_card_disk_cache(monkeypatch):
    """Le cache disque des cartes masquerait les rendus que certains tests
    espionnent, et écrirait dans le vrai dossier de données."""
    from app.services import card_preview
    monkeypatch.setattr(card_preview, "DISK_CACHE", False)
