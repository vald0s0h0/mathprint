"""Cache disque des cartes + préchauffage (pause pendant une correction)."""
from app.services import card_preview, card_warm

EX = {"statement": "Calcule $2+3$.", "response_type": "short_text",
      "expected": {"type": "integer", "value": 5}, "level": 2.0}


def test_cle_identique_pour_1_et_1_0():
    a = dict(EX, level=2.0)
    b = dict(EX, level=2)
    assert card_preview.canonical_key(a) == card_preview.canonical_key(b)


def test_rendu_ecrit_puis_relu_sans_rerendre(tmp_path, monkeypatch):
    monkeypatch.setattr(card_preview, "DISK_CACHE", True)
    monkeypatch.setattr(card_preview.settings, "data_dir", tmp_path)
    png = card_preview.render(EX, True, True)
    f = card_preview.cached_file(EX, True, True)
    assert f.exists() and f.read_bytes() == png
    # 2e appel : lit le disque, ne rend pas
    monkeypatch.setattr(card_preview, "_render", lambda *a: (_ for _ in ()).throw(AssertionError("rendu inutile")))
    assert card_preview.render(dict(EX, level=2), True, True) == png
    # un autre réglage (sans corrigé) a son propre fichier
    assert card_preview.cached_file(EX, True, False) != f


def test_prune_garde_la_version_courante(tmp_path, monkeypatch):
    monkeypatch.setattr(card_preview.settings, "data_dir", tmp_path)
    old = tmp_path / "cardcache" / "ancienne-version"
    cur = tmp_path / "cardcache" / card_preview._renderer_version()
    old.mkdir(parents=True); cur.mkdir(parents=True)
    card_preview.prune_old_versions()
    assert not old.exists() and cur.exists()


class _FakeDb:
    def __init__(self, rows): self.rows = rows
    def query(self, *a): return self
    def filter(self, *a): return self
    def order_by(self, *a): return self
    def __iter__(self): return iter([(r.id,) for r in self.rows])
    def get(self, model, i): return next((r for r in self.rows if r.id == i), None)
    def rollback(self): pass
    def close(self): pass


def test_prechauffage_attend_la_fin_d_une_correction(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(card_preview, "DISK_CACHE", True)
    monkeypatch.setattr(card_preview.settings, "data_dir", tmp_path)
    row = SimpleNamespace(id="e1")
    monkeypatch.setattr(card_warm, "SessionLocal", lambda: _FakeDb([row]))
    monkeypatch.setattr(card_warm, "card_for_catalog", lambda r: dict(EX))
    monkeypatch.setattr(card_preview, "resolve_card", lambda ex, db: ex)
    from app.services import generation, runtime_settings
    tpl = {"exercise": card_preview.pdfgen.DEFAULT_TEMPLATES['exercise']}
    monkeypatch.setattr(runtime_settings, "doc_templates", lambda db: tpl)
    monkeypatch.setattr(generation, "render_shape", lambda r, mode: card_preview.shape(EX))
    states = iter([True, True, False, False, False])   # correction en cours 2 fois, puis libre
    sleeps = []
    stats = card_warm.warm_pass(sleep=sleeps.append, busy=lambda db: next(states))
    assert stats["paused"] == 2 and stats["rendered"] == 1 and stats["measured"] >= 1
    assert sleeps[:2] == [card_warm.BUSY_RECHECK_S] * 2
    # 2e passe : tout est déjà en cache, rien n'est calculé ni rendu, même « occupé »
    stats = card_warm.warm_pass(sleep=sleeps.append, busy=lambda db: True)
    assert stats == {"rendered": 0, "already": 1, "failed": 0, "paused": 0, "measured": 0}


def test_hauteur_mise_en_cache_et_relue(tmp_path, monkeypatch):
    monkeypatch.setattr(card_preview, "DISK_CACHE", True)
    monkeypatch.setattr(card_preview.settings, "data_dir", tmp_path)
    shape = card_preview.shape(EX)
    tpl = card_preview.pdfgen.DEFAULT_TEMPLATES['exercise']
    assert not card_preview.has_cached_height(shape, 9, 9, tpl)
    h1 = card_preview.cached_height(shape, 9, 9, tpl)
    assert h1 > 0 and card_preview.has_cached_height(shape, 9, 9, tpl)
    monkeypatch.setattr(card_preview.pdfgen, "estimate_item_height",
                        lambda *a: (_ for _ in ()).throw(AssertionError("mesure inutile")))
    assert card_preview.cached_height(shape, 9, 9, tpl) == h1
    # une autre police = une autre mesure
    assert not card_preview.has_cached_height(shape, 10, 9, tpl)


def test_version_suit_le_contenu_des_fichiers_de_mise_en_page(tmp_path, monkeypatch):
    from app.services import layout_version as lv
    v1 = lv.layout_version()
    assert v1 == lv.layout_version()
    fake = tmp_path / "pdfgen.py"
    fake.write_text("x = 1")
    monkeypatch.setattr(lv, "_SERVICES", tmp_path)
    lv.layout_version.cache_clear()
    v2 = lv.layout_version()
    fake.write_text("x = 2")
    lv.layout_version.cache_clear()
    assert v2 != lv.layout_version() and v1 != v2
    lv.layout_version.cache_clear()
