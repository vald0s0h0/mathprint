"""Tests du worker avec le moteur `fake` et un faux serveur (aucun modèle, aucun réseau)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ocr_worker import config as config_mod
from ocr_worker.config import Config, Server
from ocr_worker.engines import FAMILIES, build_engine
from ocr_worker.engines.base import looks_suspect
from ocr_worker.engines.fake import FakeEngine
from ocr_worker.tests.stub_server import StubServer, png
from ocr_worker.waves import Worker


def make_worker(servers: dict[str, StubServer], engines: dict, batch=32):
    cfg = Config(servers=[Server(n, f"http://{n}", s.token) for n, s in servers.items()],
                 claim_batch=batch)

    class Router:                                    # un transport, plusieurs serveurs
        def request(self, method, url, **kw):
            host = url.split("//", 1)[1].split("/", 1)[0]
            return servers[host].request(method, url, **kw)

    return Worker(cfg, transport=Router(), engines=engines)


def test_config_reads_tokens_from_env_and_never_prints_them(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[[servers]]\nname="a"\nurl="http://x:1/"\ntoken_env="TK"\n[engines.trocr]\nmodel="m"\n')
    cfg = config_mod.load(p, env={"TK": "s3cret-token"})
    assert cfg.servers[0].token == "s3cret-token" and cfg.servers[0].url == "http://x:1"
    assert "s3cret" not in repr(cfg.servers[0])
    assert cfg.engines["trocr"]["impl"] == "trocr"


def test_shipped_config_is_valid_and_has_no_secret():
    cfg = config_mod.load(config_mod.DEFAULT_PATH, env={})
    assert {s.name for s in cfg.servers} == {"local"} and cfg.servers[0].token == ""
    assert set(cfg.engines) == {"apple_vision", "trocr", "unimumer", "lighton"}
    assert set(cfg.engines) == set(FAMILIES)
    assert "token =" not in config_mod.DEFAULT_PATH.read_text()


def test_apple_vision_is_disabled_off_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert build_engine("apple_vision", {"impl": "apple_vision"}) is None


def test_unknown_engine_impl_is_rejected():
    with pytest.raises(ValueError):
        build_engine("x", {"impl": "gpt"})


def test_wave_reads_everything_and_posts_results():
    s = StubServer()
    for i in range(70):
        s.add(f"j{i}", "unimumer", f"c{i}")
    eng = FakeEngine("unimumer", "qwen35", {"reads": {"*": {"raw_text": "1,79", "logprob_mean": -0.05}}})
    w = make_worker({"srv": s}, {"unimumer": eng})
    stats = w.run_once()
    assert len(s.results) == 70 and all(j["status"] == "done" for j in s.jobs)
    assert {r["raw_text"] for r in s.results} == {"1,79"}
    r = s.results[0]
    assert (r["engine"], r["family"], r["backend"], r["engine_version"]) == ("unimumer", "qwen35", "fake", "fake-1")
    assert stats["jobs"] == 70 and stats["waves"] == 1 and not eng.loaded     # déchargé après la vague
    claims = [x for x in s.requests if x[1].endswith("/claim")]
    assert len(claims) == 4                                                 # 32 + 32 + 6 + vide


def test_engine_with_most_work_goes_first_and_only_one_model_is_loaded():
    s = StubServer()
    for i in range(3):
        s.add(f"a{i}", "trocr", f"ca{i}")
    for i in range(5):
        s.add(f"b{i}", "unimumer", f"cb{i}")
    order, live = [], []

    class Spy(FakeEngine):
        def load(self):
            assert not live, f"{live} encore chargé"
            live.append(self.name); order.append(self.name); super().load()

        def unload(self):
            live.remove(self.name); super().unload()

    w = make_worker({"srv": s}, {"trocr": Spy("trocr", "encdec", {"reads": {"*": {"raw_text": "2"}}}),
                                 "unimumer": Spy("unimumer", "qwen35", {"reads": {"*": {"raw_text": "2"}}})})
    w.run_once()
    assert order == ["unimumer", "trocr"] and live == []


def test_claims_across_all_servers_until_exhausted():
    a, b = StubServer(), StubServer()
    for i in range(3):
        a.add(f"a{i}", "trocr", f"ca{i}")
    for i in range(40):
        b.add(f"b{i}", "trocr", f"cb{i}")
    eng = FakeEngine("trocr", "encdec", {"reads": {"*": {"raw_text": "5"}}})
    make_worker({"a": a, "b": b}, {"trocr": eng}, batch=10).run_once()
    assert len(a.results) == 3 and len(b.results) == 40
    assert eng.loaded is False


def test_results_are_posted_to_the_server_that_issued_the_job():
    a, b = StubServer(), StubServer()
    a.add("ja", "trocr", "ca"); b.add("jb", "trocr", "cb")
    make_worker({"a": a, "b": b}, {"trocr": FakeEngine("trocr", "encdec", {"reads": {"*": {"raw_text": "5"}}})}).run_once()
    assert [r["job_id"] for r in a.results] == ["ja"] and [r["job_id"] for r in b.results] == ["jb"]


def test_a_failing_crop_does_not_cost_the_whole_batch():
    s = StubServer()
    for i in range(3):
        s.add(f"j{i}", "unimumer", f"c{i}")
    eng = FakeEngine("unimumer", "qwen35", {"reads": {"*": {"raw_text": "7"}, "c1": {"error": "boom"}}})
    make_worker({"srv": s}, {"unimumer": eng}).run_once()
    by = {r["job_id"]: r for r in s.results}
    assert by["j0"]["raw_text"] == "7" and by["j2"]["raw_text"] == "7"
    assert "boom" in by["j1"]["error"] and "raw_text" not in by["j1"]


def test_sha_mismatch_is_reported_as_an_error():
    s = StubServer()
    s.add("j0", "unimumer", "c0", sha="0" * 64)
    make_worker({"srv": s}, {"unimumer": FakeEngine("unimumer", "qwen35", {"reads": {"*": {"raw_text": "7"}}})}).run_once()
    assert "sha256" in s.results[0]["error"]


def test_score_job_returns_candidate_scores_and_unsupported_is_an_error():
    s = StubServer()
    s.add("s1", "trocr", "c1", kind="score", candidates=["1,79", "1,8"])
    s.add("s2", "lighton", "c2", kind="score", candidates=["x"])
    eng = FakeEngine("trocr", "encdec", {"scores": {"*": {"1,79": -0.5, "1,8": -4.0}}})
    noscore = FakeEngine("lighton", "pixtral_qwen3", {}, supports_score=False)
    make_worker({"srv": s}, {"trocr": eng, "lighton": noscore}).run_once()
    by = {r["job_id"]: r for r in s.results}
    assert by["s1"]["scores"] == {"1,79": -0.5, "1,8": -4.0}
    assert "score_unsupported" in by["s2"]["error"]


def test_server_down_is_skipped_and_the_other_one_is_served():
    a, b = StubServer(), StubServer()
    a.down = True
    b.add("jb", "trocr", "cb")
    make_worker({"a": a, "b": b}, {"trocr": FakeEngine("trocr", "encdec", {"reads": {"*": {"raw_text": "5"}}})}).run_once()
    assert len(b.results) == 1


def test_wrong_token_does_nothing_and_does_not_crash():
    s = StubServer(token="right")
    s.add("j", "trocr", "c")
    cfg = Config(servers=[Server("srv", "http://srv", "wrong")])
    w = Worker(cfg, transport=type("T", (), {"request": lambda self, m, u, **kw: s.request(m, u, **kw)})(),
               engines={"trocr": FakeEngine("trocr", "encdec", {})})
    assert w.run_once()["jobs"] == 0 and s.jobs[0]["status"] == "pending"


def test_post_failure_is_retried_once():
    s = StubServer(fail_posts=1)
    s.add("j", "trocr", "c")
    make_worker({"srv": s}, {"trocr": FakeEngine("trocr", "encdec", {"reads": {"*": {"raw_text": "5"}}})}).run_once()
    assert len(s.results) == 1


def test_no_work_means_no_model_is_loaded():
    s = StubServer()
    eng = FakeEngine("trocr", "encdec", {})
    make_worker({"srv": s}, {"trocr": eng}).run_once()
    assert eng.calls == [] and not eng.loaded


def test_engine_without_a_plan_entry_is_left_alone():
    s = StubServer()
    s.add("j", "ghost", "c")
    w = make_worker({"srv": s}, {"trocr": FakeEngine("trocr", "encdec", {})})
    assert w.run_once()["jobs"] == 0


@pytest.mark.parametrize("raw,bad", [("1,79", False), ("œuf", False), ("2 \\times 3", False),
                                     ("<figure>\n### Figure", True), ("```latex\n2\n```", True),
                                     ("2 \\square 3", True), ("$$\n\\boxed{2}\n$$", True),
                                     ("[ILLISIBLE]", True), ("□12", True)])
def test_suspect_outputs_are_flagged_never_rewritten(raw, bad):
    assert looks_suspect(raw) is bad


def test_fake_engine_defaults_to_illegible_when_empty():
    e = FakeEngine("x", "f", {"reads": {"*": {"raw_text": ""}}}); e.load()
    from ocr_worker.engines.base import Crop
    assert e.read([Crop(image=None)])[0].illegible is True


# ---------------------------------------------------------------- profil CUDA et parité

def test_unavailable_device_is_refused_not_silently_replaced():
    import torch
    from ocr_worker.engines.base import ensure_device
    ensure_device("cpu")
    with pytest.raises(RuntimeError):
        ensure_device("quantum")
    if not torch.cuda.is_available():
        with pytest.raises(RuntimeError, match="indisponible"):
            ensure_device("cuda")


def test_cuda_example_config_is_valid_and_names_the_same_engines_as_the_mac_one():
    cfg = config_mod.load(Path(__file__).resolve().parents[1] / "config.cuda.example.toml", env={})
    assert cfg.device == "cuda" and {e["backend"] for e in cfg.engines.values()} == {"cuda"}
    assert set(cfg.engines) == {"trocr", "unimumer", "lighton"}          # Apple Vision n'existe pas hors macOS
    assert cfg.claim_batch == 16 and cfg.servers[0].token == ""


def test_parity_compare_flags_a_lost_comma_and_accepts_numeric_noise():
    import importlib.util
    spec = importlib.util.spec_from_file_location("parity", Path(__file__).resolve().parents[1] / "scripts" / "parity.py")
    parity = importlib.util.module_from_spec(spec); spec.loader.exec_module(parity)
    mac = {"meta": {"device": "mps"}, "engines": {"unimumer": {str(i): {"raw": "1,79", "lp": -0.050} for i in range(50)}}}
    cuda_ok = {"meta": {"device": "cuda"}, "engines": {"unimumer": {str(i): {"raw": "1,79", "lp": -0.052} for i in range(50)}}}
    ok = parity.compare(mac, cuda_ok)
    assert ok["ok"] and ok["engines"]["unimumer"]["agreement"] == 1.0
    assert ok["engines"]["unimumer"]["logprob_abs_diff_mean"] == pytest.approx(0.002, abs=1e-4)
    bad = {"meta": {"device": "cuda"}, "engines": {"unimumer": {**{str(i): {"raw": "1,79", "lp": -0.05} for i in range(49)},
                                                                  "49": {"raw": "179", "lp": -0.05}}}}
    r = parity.compare(mac, bad)
    assert not r["ok"] and r["engines"]["unimumer"]["serious_differences"] == ["49"]     # virgule perdue = grave
    spaced = {"meta": {"device": "cuda"}, "engines": {"unimumer": {str(i): {"raw": "1 , 79", "lp": -0.05} for i in range(50)}}}
    assert parity.compare(mac, spaced)["ok"]                      # les espaces seuls ne comptent pas


def test_lighton_applies_the_mistral_regex_fix():
    from ocr_worker.engines.lighton import LightonEngine
    assert LightonEngine.processor_kwargs == {"fix_mistral_regex": True}


def test_apple_vision_reading_order_left_to_right_then_top_to_bottom():
    from ocr_worker.engines.apple_vision import reading_order
    # boîtes (x, y, w, h), origine en bas à gauche : « 2 » à droite donné avant « 1 » à gauche
    one_line = [(0.6, 0.30, 0.2, 0.4), (0.1, 0.32, 0.2, 0.4)]
    assert reading_order(one_line) == [1, 0]
    # deux lignes : la plus haute d'abord, chacune de gauche à droite
    two_lines = [(0.5, 0.1, 0.2, 0.3), (0.1, 0.6, 0.2, 0.3), (0.1, 0.1, 0.2, 0.3), (0.5, 0.62, 0.2, 0.3)]
    assert reading_order(two_lines) == [1, 3, 2, 0]
    assert reading_order([]) == []
