"""Moteur de test déterministe, piloté par un fichier JSON (aucun modèle).

Format :
{
  "reads":  {"<crop_id | sha256 | *>": {"raw_text": "1,79", "logprob_mean": -0.05,
                                        "illegible": false, "error": "optionnel"}},
  "scores": {"<crop_id | sha256 | *>": {"1,79": -0.4, "1,8": -3.0}},
  "delay_s": 0
}
La clé `*` sert de valeur par défaut. Une entrée `error` lève une exception :
c'est ainsi qu'on teste les reprises.
"""
import json
import time
from pathlib import Path

from .base import Crop, Engine, Read


class FakeEngine(Engine):
    def __init__(self, name: str, family: str, table: dict | str | Path | None = None,
                 supports_score: bool = True):
        self.name, self.family = name, family
        self.version, self.weights, self.backend, self.device = "fake-1", f"fake/{name}", "fake", "cpu"
        self.supports_score = supports_score
        if isinstance(table, (str, Path)):
            table = json.loads(Path(table).read_text(encoding="utf-8"))
        self.table = table or {}
        self.loaded = False
        self.calls: list[str] = []          # crop_id lus, pour les tests

    def load(self) -> None:
        self.loaded = True

    def unload(self) -> None:
        self.loaded = False

    def _lookup(self, section: str, crop: Crop):
        t = self.table.get(section, {})
        for key in (crop.crop_id, crop.sha256, "*"):
            if key and key in t:
                return t[key]
        return None

    def read(self, crops: list[Crop]) -> list[Read]:
        assert self.loaded, "moteur non chargé"
        out = []
        for crop in crops:
            self.calls.append(crop.crop_id)
            if self.table.get("delay_s"):
                time.sleep(float(self.table["delay_s"]))
            entry = self._lookup("reads", crop) or {}
            if entry.get("error"):
                raise RuntimeError(entry["error"])
            raw = entry.get("raw_text", "")
            out.append(Read(raw_text=raw, illegible=bool(entry.get("illegible", raw == "")),
                            confidence_raw=entry.get("confidence_raw"),
                            logprob_mean=entry.get("logprob_mean", -0.01),
                            logprob_min=entry.get("logprob_min", entry.get("logprob_mean", -0.01)),
                            alternatives=entry.get("alternatives", []), latency_ms=1.0))
        return out

    def score(self, crop: Crop, candidates: list[str]) -> list[float] | None:
        assert self.loaded, "moteur non chargé"
        if not self.supports_score:
            return None
        entry = self._lookup("scores", crop) or {}
        return [float(entry.get(c, -50.0)) for c in candidates]
