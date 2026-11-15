"""`vague --nas` (relever la file du NAS) : export / import séparés, reprise, essai,
validation, et le transport SSH, remplacé par un faux qui exécute le script reçu
sur une base de test. Jamais de vrai SSH."""
import contextlib
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings as cfg
from app.db import Base
from app.models import Copy
from app.services import cli_llm, vague, vague_distante
from test_vague import _decisions, _engine, _isolated, _seed, db  # noqa: F401


class _Prete:
    """La session du « NAS » : close() ne ferme pas la base du test."""

    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        return getattr(self._db, name)

    def close(self):
        pass


@pytest.fixture
def nas(db, monkeypatch):
    """Faux SSH : exécute le script envoyé sur l'entrée standard, sur `db`."""
    appels = []
    monkeypatch.setattr(vague_distante, "_session", lambda: _Prete(db))

    def run(cmd, input=None, **kw):
        appels.append((cmd, input))
        assert cmd[0] == "ssh" and cmd[-1] == vague_distante.COMMANDE and cmd[-2] == "x@nas"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(input, {})
        return SimpleNamespace(returncode=0, stdout=out.getvalue(), stderr="")
    monkeypatch.setattr(vague_distante.subprocess, "run", run)
    return appels


def _etat(db):
    return sorted((d.status, d.reason_code, d.score) for d in _decisions(db))


def test_export_has_no_student_name_and_is_json(db):
    _seed(db)
    paquets = vague.exporter(db, ["tout"])
    texte = json.dumps(paquets, ensure_ascii=False)
    assert paquets and "Awa" not in texte and "Koné" not in texte
    assert all(p["system"] and p["text"] and p["id"] for p in paquets)
    assert len({p["id"] for p in paquets}) == len(paquets)
    assert vague.exporter(db, ["tout"]) == paquets          # identifiants stables


def test_export_then_import_equals_the_local_wave(db):
    _seed(db)
    ask, _ = _engine()
    other = sessionmaker(bind=create_engine("sqlite:///:memory:"))()
    Base.metadata.create_all(other.get_bind())
    _seed(other)
    vague.corriger(other, ask=ask, log=lambda *_: None)
    attendu = _etat(other)

    bilan = vague.Bilan()
    for p in vague.exporter_corriger(db):
        raw = vague.demander(p, ask)
        vague.importer_paquet(db, p, raw, bilan=bilan, log=lambda *_: None)
    assert _etat(db) == attendu and bilan.paquets == 1 and bilan.corrigees == 2


def test_a_packet_already_written_is_not_redone(db):
    _seed(db)
    ask, _ = _engine()
    p = vague.exporter_corriger(db)[0]
    raw = vague.demander(p, ask)
    bilan = vague.importer_paquet(db, p, raw, log=lambda *_: None)
    etat = _etat(db)
    again = vague.importer_paquet(db, p, raw, log=lambda *_: None)
    assert bilan.paquets == 1 and again.paquets == 0 and _etat(db) == etat
    assert vague.exporter_corriger(db) == []


@pytest.mark.parametrize("mutation", [
    lambda p, raw: [c.update(decision="inconnue-id") for c in p["champs"]],   # tout inconnu : sans objet
    lambda p, raw: p["champs"][0].update(sig="0000000000"),           # base changée
    lambda p, raw: p["champs"][0].update(cle="r1; rm -rf"),
    lambda p, raw: raw["corrections"][0].update(points=99),           # hors barème
    lambda p, raw: raw["corrections"][0].update(id="r42"),            # identifiant inconnu
    lambda p, raw: raw.update(corrections="n'importe quoi"),
])
def test_invalid_import_writes_nothing(db, mutation):
    _seed(db)
    ask, _ = _engine()
    p = vague.exporter_corriger(db)[0]
    raw = vague.demander(p, ask)
    mutation(p, raw)
    avant = _etat(db)
    bilan = vague.importer_paquet(db, p, raw, log=lambda *_: None)
    assert _etat(db) == avant and bilan.paquets == 0


def test_a_decision_graded_meanwhile_is_not_overwritten(db):
    _seed(db)
    ask, _ = _engine()
    p = vague.exporter_corriger(db)[0]
    raw = vague.demander(p, ask)
    d = _decisions(db)[0]
    d.status = "manual"
    db.commit()
    vague.importer_paquet(db, p, raw, log=lambda *_: None)
    db.expire_all()
    assert db.get(type(d), d.id).status == "manual"


def test_nas_run_through_the_fake_ssh(db, nas):
    _seed(db)
    ask, calls = _engine()
    lignes = []
    bilan = vague_distante.run(["corriger"], hote="x@nas", ask=ask, log=lignes.append)
    assert bilan.paquets == 1 and bilan.corrigees == 2 and not bilan.arret and calls
    assert all(d.reason_code != "llm_queued" for d in _decisions(db))
    # deux appels SSH (export, import), script par l'entrée standard, commande constante
    assert [c[0][-1] for c in nas] == [vague_distante.COMMANDE] * 2
    assert "Awa" not in nas[0][1]


def test_nas_import_by_small_lots_and_resume(db, nas, monkeypatch):
    _seed(db)
    monkeypatch.setattr(cfg, "correction_batch_size", 1)     # deux paquets
    ask, calls = _engine()

    def coupe(system, text):
        if calls:
            raise cli_llm.CliError("usage limit reached", stop=True)
        return ask(system, text)
    bilan = vague_distante.run(["corriger"], hote="x@nas", ask=coupe, lot=1, log=lambda *_: None)
    assert bilan.paquets == 1 and "usage limit" in bilan.arret
    calls.clear()
    bilan = vague_distante.run(["corriger"], hote="x@nas", ask=ask, lot=1, log=lambda *_: None)
    assert bilan.paquets == 1 and len(calls) == 1


def test_nas_essai_calls_nothing_and_writes_nothing(db, nas):
    _seed(db)
    ask, calls = _engine()
    lignes = []
    bilan = vague_distante.run(["tout"], hote="x@nas", essai=True, ask=ask, log=lignes.append)
    assert calls == [] and bilan.paquets == 0 and len(nas) == 1       # l'export seul
    assert any("1 paquet" in x or "paquet(s)" in x for x in lignes)
    assert all(d.reason_code == "llm_queued" for d in _decisions(db))


def test_unreachable_nas_is_a_clear_error(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("ssh", 15)
    monkeypatch.setattr(vague_distante.subprocess, "run", boom)
    bilan = vague_distante.run(["tout"], hote="x@nas", log=lambda *_: None)
    assert "injoignable" in bilan.arret

    monkeypatch.setattr(vague_distante.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=255, stdout="", stderr="ssh: connect to host: Operation timed out"))
    assert "timed out" in vague_distante.run(["tout"], log=lambda *_: None).arret


def test_nas_without_the_code_is_reported(monkeypatch):
    monkeypatch.setattr(vague_distante.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="", stderr="ImportError: cannot import name 'vague_distante'"))
    assert "vague_distante" in vague_distante.run(["tout"], log=lambda *_: None).arret


def test_script_and_args_are_literals():
    s = vague_distante._script(["importer"], 'x"\'; rm -rf / #\n')
    ns = {}
    compile(s, "s", "exec")
    assert "rm -rf" in s and s.count("\n") == 2          # donnée citée, sur une ligne
    assert ns == {}
