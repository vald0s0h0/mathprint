"""Configuration du worker : `ocr_worker/config.toml` (aucun secret dedans).

Les jetons viennent de variables d'environnement (`token_env`) ; une valeur
`token` en clair est acceptée pour un essai à la main mais déconseillée.
"""
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("config.toml")


@dataclass
class Server:
    name: str
    url: str
    token: str = ""

    def __repr__(self) -> str:                    # jamais le jeton dans un journal
        return f"Server(name={self.name!r}, url={self.url!r})"


@dataclass
class Config:
    worker_name: str = "mac"
    device: str = "mps"
    poll_seconds: int = 30
    claim_batch: int = 32
    servers: list[Server] = field(default_factory=list)
    engines: dict[str, dict] = field(default_factory=dict)


def load(path: str | Path | None = None, env: dict | None = None) -> Config:
    env = os.environ if env is None else env
    raw = tomllib.loads(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
    servers = []
    for s in raw.get("servers", []):
        token = s.get("token") or env.get(s.get("token_env", ""), "")
        servers.append(Server(name=s["name"], url=s["url"].rstrip("/"), token=token))
    cfg = Config(
        worker_name=raw.get("name", "mac"), device=raw.get("device", "mps"),
        poll_seconds=int(raw.get("poll_seconds", 30)),
        claim_batch=max(1, min(int(raw.get("claim_batch", 32)), 64)),
        servers=servers, engines=raw.get("engines", {}))
    for name, e in cfg.engines.items():
        e.setdefault("impl", name)
    return cfg
