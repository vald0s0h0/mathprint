"""Diagnostic : le rapport doit retrouver des erreurs connues dans une base de décisions synthétiques."""
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from app.models import OcrCrop, OcrDecision, OcrEngineRead
from app.services import ocr_analysis as A
from tests.pgsupport import make_engine
from tests.test_ocr_worker_api import _seed


@pytest.fixture
def world(tmp_path):
    engine = make_engine()
    Session = sessionmaker(bind=engine)
    db = Session()
    base, = _seed(db, tmp_path, n=1, engines=())
    w = type("W", (), {})()
    w.db, w.engine, w.Session, w.tmp, w.batch, w.zone = db, engine, Session, tmp_path, base.batch_id, base.zone_id
    w.crops = {}
    yield w
    db.close()


def add_crop(w, key, decision, *, accepted="24", truth=None, source=None, auto_error=None, mode="cascade",
             reads=(), full=False, cost=0.0, reason="", scores=None):
    n = len(w.crops)
    (w.tmp / f"x{n}.png").write_bytes(b"x")
    crop = OcrCrop(batch_id=w.batch, zone_id=w.zone, cell_index=100 + n, image_path=f"x{n}.png",
                   image_sha256=f"s{n}", zone_class="num_line", ink_width_px=int(80 * 0.25 * 2), height_px=80,
                   mode=mode, status="decided", full_sample=full)
    w.db.add(crop); w.db.flush()
    w.db.add(OcrDecision(crop_id=crop.id, zone_id=w.zone, batch_id=w.batch, decision=decision, reason=reason,
                         accepted_surface=accepted if decision != "manual" else None, truth_surface=truth,
                         truth_source=source, auto_error=auto_error, mode=mode, mathpix_used=cost > 0, cost_eur=cost,
                         full_decision={"action": "accept", "surface": accepted} if full else None))
    fam = {"apple_vision": "apple", "unimumer": "qwen35", "lighton": "pixtral_qwen3", "trocr": "encdec"}
    for eng, raw, lat, lp in reads:
        w.db.add(OcrEngineRead(crop_id=crop.id, engine=eng, family=fam[eng], raw_text=raw, surface=raw,
                               illegible=(raw == ""), logprob_mean=lp, confidence_raw=0.9 if eng == "apple_vision" else None,
                               latency_ms=lat, device="mps"))
    w.crops[key] = crop
    return crop


def populate(w):
    # A : 10 décisions automatiques auditées et justes
    for i in range(10):
        add_crop(w, f"A{i}", "local_consensus", truth="24", source="audit", auto_error=False,
                 reads=[("apple_vision", "24", 20, None), ("unimumer", "2 4" if False else "24", 1000 + 10 * i, -0.05),
                        ("lighton", "24", 300, -0.1)])
    # B : 2 ERREURS AUTOMATIQUES connues : accepté 24 (deux moteurs d'accord), le professeur a écrit 29
    for i in range(2):
        add_crop(w, f"B{i}", "local_consensus", accepted="24", truth="29", source="audit", auto_error=True,
                 reads=[("apple_vision", "24", 20, None), ("unimumer", "24", 1100 + 10 * i, -0.05)])
    # C : 3 confirmations Mathpix justes, coût 0,004 chacune
    for i in range(3):
        add_crop(w, f"C{i}", "mathpix_confirmed", truth="24", source="teacher_ocr", auto_error=False, cost=0.004,
                 reason="no_majority>mathpix_confirmed",
                 reads=[("apple_vision", "21", 20, None), ("unimumer", "24", 1120 + 10 * i, -0.05), ("lighton", "26", 300, -0.1)])
    # D : 3 manuels sans vérité ; E : 2 manuels avec vérité (jamais une erreur automatique)
    for i in range(3):
        add_crop(w, f"D{i}", "manual", reason="illegible_consensus", reads=[("apple_vision", "", 20, None)])
    for i in range(2):
        add_crop(w, f"E{i}", "manual", truth="7", source="teacher_ocr", auto_error=False, reason="no_majority>mathpix_not_read_locally")
    w.db.commit()


def test_report_finds_the_known_automatic_errors(world):
    populate(world)
    rep = A.build_report(world.db)
    ae = rep["auto_errors"]
    assert ae["count"] == 2 and {e["crop_id"] for e in ae["items"]} == {world.crops["B0"].id, world.crops["B1"].id}
    assert all(e["accepted_surface"] == "24" and e["truth_surface"] == "29" and e["truth_source"] == "audit"
               for e in ae["items"])
    assert list(rep)[1] == "auto_errors" or list(rep)[0] == "filters"     # les erreurs viennent en tête du rapport
    g = rep["go_no_go"]["by_class"]["num_line"]
    assert g["decisions"] == 20 and g["automatic"] == 15 and g["manual"] == 5
    assert (g["audited"]["n"], g["audited"]["k"]) == (12, 2)
    assert (g["with_truth"]["n"], g["with_truth"]["k"]) == (15, 2)
    assert g["go"] is False and g["audits_missing"] == A.GO_MIN_AUDITS - 12
    assert 0.0 < g["audited"]["low"] < g["audited"]["rate"] < g["audited"]["high"] < 1.0


def test_report_volume_mix_reasons_and_mathpix_cost(world):
    populate(world)
    rep = A.build_report(world.db)
    assert rep["volume"]["total"] == 20
    assert rep["volume"]["mix"] == {"local_consensus": 12, "mathpix_confirmed": 3, "manual": 5}
    assert rep["volume"]["mix_share"]["manual"] == 0.25
    reasons = {(r["decision"], r["reason"]): r["count"] for r in rep["reasons"]}
    assert reasons[("manual", "illegible_consensus")] == 3 and reasons[("mathpix_confirmed", "no_majority>mathpix_confirmed")] == 3
    mp = rep["mathpix"]
    assert mp["cost_eur"] == pytest.approx(0.012) and mp["crops_read_by_mathpix"] == 3
    assert mp["copies"] == 1 and mp["cost_per_copy_eur"] == pytest.approx(0.012)


def test_report_engine_accuracy_latency_and_illegible_rate(world):
    populate(world)
    e = A.build_report(world.db)["engines"]["num_line"]
    u = e["unimumer"]
    assert (u["reads"], u["with_truth"], u["correct"]) == (15, 15, 13)         # faux sur les 2 erreurs automatiques
    assert u["accuracy"]["rate"] == pytest.approx(13 / 15, abs=1e-5) and u["accuracy"]["low"] < 13 / 15 < u["accuracy"]["high"]
    assert u["latency_p50_ms"] == 1070 and u["latency_p95_ms"] == 1140 and u["devices"] == ["mps"]
    assert e["apple_vision"]["illegible"] == 3 and e["apple_vision"]["illegible_rate"] == pytest.approx(3 / 18, abs=1e-3)
    assert e["lighton"]["correct"] == 10 and e["lighton"]["reads"] == 13       # A justes, C faux (26)
    cal = {c["bucket"]: c for c in u["calibration"]}
    assert cal["lp>=-0.05"]["n"] == 15 and cal["lp>=-0.05"]["k"] == 13        # confiance maximale et pourtant 2 erreurs
    assert e["apple_vision"]["calibration"][0]["metric"] == "confidence"


def test_report_pairs_expose_agreement_on_a_wrong_surface(world):
    populate(world)
    pairs = {(p["a"], p["b"]): p for p in A.build_report(world.db)["pairs"]}
    p = pairs[("apple_vision", "unimumer")]
    assert p["wrong_agree"] == 2 and p["agree"] == 12                          # d'accord sur 24 : juste 10 fois, faux 2 fois
    assert p["wrong_agree_rate"]["k"] == 2 and p["wrong_agree_rate"]["n"] == 12
    q = pairs[("lighton", "unimumer")]
    assert q["wrong_agree"] == 0 and q["agree"] == 10                          # les accords lighton x unimumer sont tous justes


def test_filters_narrow_the_report_and_an_empty_db_does_not_crash(world):
    populate(world)
    assert A.build_report(world.db, zone_class="text_line")["volume"]["total"] == 0
    assert A.build_report(world.db, mode="shadow")["volume"]["total"] == 0
    assert A.build_report(world.db, mode="cascade")["volume"]["total"] == 20
    from datetime import datetime, timedelta
    assert A.build_report(world.db, since=datetime.utcnow() + timedelta(days=1))["auto_errors"]["count"] == 0
    # base vide à part (SQLite mémoire) : sur PostgreSQL, `make_engine()` viderait les tables que la session
    # de la fixture tient encore ouvertes (verrou)
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from app.db import Base
    e2 = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(e2)
    empty = sessionmaker(bind=e2)()
    rep = A.build_report(empty)
    assert rep["auto_errors"]["count"] == 0 and rep["volume"]["total"] == 0 and rep["engines"] == {}
    assert rep["mathpix"]["cost_per_copy_eur"] is None and rep["marginal"]["crops"] == 0
    json.dumps(rep, default=str)
    empty.close()


def test_shadow_agreement_between_local_and_mathpix(world):
    populate(world)
    for fd in ({"local": ["5", "1,79"], "mathpix": ["5", "1,8"], "agree": [True, False]},
               {"local": ["24"], "mathpix": ["24"], "agree": [True]}):
        world.db.add(OcrDecision(crop_id=None, zone_id=world.zone, batch_id=world.batch, decision="local_consensus",
                                 mode="shadow", full_decision=fd))
    world.db.commit()
    sh = A.build_report(world.db)["shadow"]
    assert (sh["zones"], sh["compared_items"], sh["agree"]) == (2, 3, 2)


def test_marginal_contribution_on_the_full_sample(world):
    populate(world)
    # apple illisible, unimumer et lighton d'accord, trocr (observateur) contredit : le passage complet renvoie vers
    # Mathpix ; sans trocr, la paire corrélée n'est plus contredite et la lecture est acceptée.
    add_crop(world, "F", "local_consensus", accepted="55", truth="55", source="audit", auto_error=False, full=True,
             reads=[("apple_vision", "", 20, None), ("unimumer", "55", 900, -0.05), ("lighton", "55", 300, -0.05),
                    ("trocr", "xx", 400, -0.05)])
    world.db.commit()
    m = A.build_report(world.db)["marginal"]
    assert m["crops"] == 1
    assert m["engines"]["trocr"]["decisions_changed"] == 1 and m["engines"]["trocr"]["automatic_decisions_lost"] == 0
    assert m["engines"]["apple_vision"]["decisions_changed"] == 0


def test_replay_with_stricter_thresholds_removes_the_wrong_automatic_decisions(world):
    populate(world)
    same = A.replay_local(world.db)["by_class"]["num_line"]
    assert same["old_auto_errors"] == 2 and same["new_auto_errors"] == 2          # mêmes réglages : mêmes erreurs
    strict = A.replay_local(world.db, thresholds={"unimumer": {"min_logprob_mean": -0.001}})["by_class"]["num_line"]
    assert strict["old_auto_errors"] == 2 and strict["new_auto_errors"] == 0      # unimumer n'est plus assez sûr : plus d'erreur
    assert strict["auto_lost"] > 0 and strict["new_to_mathpix"] + strict["new_incomplete"] > 0


def test_replay_recomputes_surfaces_from_raw_text(world):
    c = add_crop(world, "G", "manual", reads=[("apple_vision", "24", 20, None), ("unimumer", "2 4", 900, -0.05)])
    world.db.execute(OcrEngineRead.__table__.update().where(OcrEngineRead.crop_id == c.id).values(surface="OBSOLETE"))
    world.db.commit()
    r = A.replay_local(world.db)["by_class"]["num_line"]
    assert r["new_auto"] == 1                      # "2 4" et "24" se rejoignent : la surface stockée est ignorée


def test_missing_jobs_lists_crops_never_read_by_an_engine(world):
    populate(world)
    assert len(A.missing_jobs(world.db, "trocr")) == 20
    from app.services import ocr_queue
    ocr_queue.enqueue_jobs(world.db, world.crops["A0"], ["trocr"]); world.db.commit()
    assert len(A.missing_jobs(world.db, "trocr")) == 19


def test_wilson_interval():
    assert A.wilson(0, 0)["rate"] is None
    z = A.wilson(0, 600)
    assert z["rate"] == 0 and z["high"] < 0.0065          # règle de trois : ~0,5 %
    assert A.wilson(5, 10)["low"] < 0.5 < A.wilson(5, 10)["high"]


def test_text_rendering_puts_automatic_errors_first(world):
    populate(world)
    import ocr_report
    text = ocr_report.render(A.build_report(world.db))
    assert text.startswith("ERREURS AUTOMATIQUES : 2")
    assert "num_line" in text and "NO-GO" in text and "MATHPIX" in text
    assert ocr_report.parse_since("7d") is not None and ocr_report.parse_since(None) is None


def test_export_dataset_uses_only_teacher_truth_and_hides_students(world, monkeypatch, tmp_path):
    populate(world)
    from app.config import settings
    import app.db as dbmod
    import ocr_export_dataset
    monkeypatch.setattr(settings, "data_dir", world.tmp)
    monkeypatch.setattr(dbmod, "SessionLocal", world.Session)
    out = tmp_path / "ds"
    monkeypatch.setattr(sys, "argv", ["x", "--out", str(out), "--eval-share", "0.5"])
    ocr_export_dataset.main()
    rows = [json.loads(l) for l in (out / "metadata.jsonl").read_text().splitlines()]
    assert len(rows) == 17                                     # 10 + 2 + 3 + 2 avec vérité ; les 3 manuels sans vérité exclus
    assert {r["truth_source"] for r in rows} <= {"teacher_ocr", "audit"}
    assert {r["text"] for r in rows} == {"24", "29", "7"}
    assert all((out / r["image"]).is_file() for r in rows)
    blob = (out / "metadata.jsonl").read_text()
    assert "student" not in blob.lower() and "A B" not in blob
    assert len({r["group"] for r in rows}) == 1 and len({r["split"] for r in rows}) == 1   # un élève : un seul jeu
    assert "mineurs" in (out / "README.txt").read_text()
