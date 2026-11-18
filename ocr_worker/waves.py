"""Boucle de vagues du worker (16 Go : jamais deux modèles de vision en mémoire).

1. interroger chaque serveur (`pending`) ;
2. choisir le moteur : d'abord ceux qui ne chargent rien (Apple Vision), puis celui
   qui a le plus de travail ; le charger ;
3. réserver des paquets de 32 sur tous les serveurs jusqu'à les épuiser,
   télécharger, lire, poster. Une même vague traite les deux passages d'un moteur
   (libre puis avec contexte) et les scores forcés que ses propres résultats
   viennent de déclencher : le modèle n'est chargé qu'une fois pour tout cela ;
4. décharger le moteur et passer au suivant.

Pourquoi Apple Vision d'abord : il ne coûte aucun chargement, et ses lectures
débloquent côté serveur les décisions et les tâches suivantes (étape 2, scores)
avant que les gros modèles ne passent ; elles tombent alors dans leur vague au lieu
d'exiger un rechargement.
"""
import hashlib
import io
import logging

from .client import ServerClient, ServerError
from .config import Config
from .engines import Crop, Engine, build_engine
from .engines.base import device_gb, rss_gb

log = logging.getLogger("ocr_worker")
POST_CHUNK = 100


class Worker:
    def __init__(self, cfg: Config, transport=None, engines: dict[str, Engine] | None = None):
        self.cfg = cfg
        self.servers = [ServerClient(s, cfg.worker_name, transport) for s in cfg.servers]
        if engines is None:
            engines = {}
            for name, ecfg in cfg.engines.items():
                eng = build_engine(name, ecfg, cfg.device)
                if eng is None:
                    log.warning("moteur %s indisponible sur cette machine : ignoré", name)
                else:
                    engines[name] = eng
        self.engines = engines
        self._loaded: str | None = None
        self.stats = {"waves": 0, "jobs": 0, "errors": 0}

    # ----------------------------------------------------------- serveurs

    def pending_by_engine(self) -> dict[str, int]:
        total: dict[str, int] = {}
        for srv in self.servers:
            try:
                for eng, n in srv.pending().items():
                    total[eng] = total.get(eng, 0) + n
            except ServerError as e:
                log.warning("%s", e)
        return total

    def next_engine(self) -> str | None:
        counts = {e: n for e, n in self.pending_by_engine().items() if n > 0 and e in self.engines}
        if not counts:
            return None
        return min(sorted(counts), key=lambda e: (getattr(self.engines[e], "load_cost", 1), -counts[e]))

    # ------------------------------------------------------------- vagues

    def run_wave(self, name: str) -> int:
        eng = self.engines[name]
        assert self._loaded is None, f"{self._loaded} encore en mémoire"
        log.info("charge %s (RSS %.2f Go, appareil %.2f Go)", name, rss_gb(), device_gb())
        eng.load()
        self._loaded = name
        log.info("%s chargé (RSS %.2f Go, appareil %.2f Go)", name, rss_gb(), device_gb())
        done = 0
        try:
            while True:
                claimed = 0
                for srv in self.servers:
                    try:
                        jobs = srv.claim(name, self.cfg.claim_batch)
                    except ServerError as e:
                        log.warning("%s", e)
                        continue
                    if jobs:
                        claimed += len(jobs)
                        done += self.process(srv, eng, jobs)
                if not claimed:
                    break
        finally:
            eng.unload()
            self._loaded = None
            log.info("%s déchargé (RSS %.2f Go, appareil %.2f Go)", name, rss_gb(), device_gb())
        self.stats["waves"] += 1
        return done

    def run_once(self) -> dict:
        """Vagues jusqu'à ce qu'aucun moteur n'ait de travail."""
        while (name := self.next_engine()) is not None:
            if self.run_wave(name) == 0:
                break                              # rien de lisible : évite de tourner à vide
        return self.stats

    def serve(self, sleep=None) -> None:
        import time
        sleep = sleep or time.sleep
        while True:
            self.run_once()
            sleep(self.cfg.poll_seconds)

    # ------------------------------------------------------------ travail

    def process(self, srv: ServerClient, eng: Engine, jobs: list[dict]) -> int:
        from PIL import Image
        results: list[dict] = []
        reads: list[tuple[dict, Crop]] = []
        ctx_reads: list[tuple[dict, Crop]] = []
        scores: list[tuple[dict, Crop]] = []
        for job in jobs:
            try:
                data = srv.image(job)
                sha = hashlib.sha256(data).hexdigest()
                if job.get("image_sha256") and job["image_sha256"] != sha:
                    raise ValueError("sha256 de l'image différent de celui annoncé")
                img = Image.open(io.BytesIO(data)); img.load()
                kind = job.get("kind", "read")
                context = job.get("context") if kind == "read_ctx" else None
                if kind == "read_ctx" and not (eng.supports_context and context):
                    raise RuntimeError("context_unsupported" if not eng.supports_context else "context_missing")
                crop = Crop(image=img, zone_class=job["zone_class"], charset=job.get("charset") or "",
                            crop_id=job["crop_id"], sha256=sha, context=context)
            except Exception as e:                  # image illisible ou réseau : le serveur réessaiera
                results.append(self._error(job, eng, e))
                continue
            {"score": scores, "read_ctx": ctx_reads}.get(kind, reads).append((job, crop))

        if reads:
            results += self._read_batch(eng, reads)
        if ctx_reads:                               # même modèle, déjà en mémoire
            results += self._read_batch(eng, ctx_reads)
        for job, crop in scores:
            try:
                vals = eng.score(crop, job["candidates"])
                if vals is None:
                    raise RuntimeError("score_unsupported")
                results.append({"job_id": job["job_id"], "engine": eng.name, **eng.describe(),
                                "scores": dict(zip(job["candidates"], vals))})
            except Exception as e:
                results.append(self._error(job, eng, e))

        self.stats["jobs"] += len(results)
        self.stats["errors"] += sum(1 for r in results if r.get("error"))
        for i in range(0, len(results), POST_CHUNK):
            chunk = results[i:i + POST_CHUNK]
            for attempt in (1, 2):
                try:
                    srv.post_results(chunk)
                    break
                except ServerError as e:
                    log.warning("envoi des résultats (essai %d) : %s", attempt, e)
        return len(results)

    def _read_batch(self, eng: Engine, items: list[tuple[dict, Crop]]) -> list[dict]:
        out = []
        try:
            reads = eng.read([c for _j, c in items])
            assert len(reads) == len(items)
        except Exception:                           # un cliché fautif ne doit pas coûter le paquet
            reads = None
        if reads is None:
            reads = []
            for job, crop in items:
                try:
                    reads.append(eng.read([crop])[0])
                except Exception as e:
                    reads.append(e)
        for (job, _crop), r in zip(items, reads):
            if isinstance(r, Exception):
                out.append(self._error(job, eng, r))
                continue
            out.append({"job_id": job["job_id"], "engine": eng.name, **eng.describe(),
                        "raw_text": r.raw_text, "illegible": r.illegible,
                        "confidence_raw": r.confidence_raw, "logprob_mean": r.logprob_mean,
                        "logprob_min": r.logprob_min, "alternatives": r.alternatives,
                        "latency_ms": r.latency_ms})
        return out

    @staticmethod
    def _error(job: dict, eng: Engine, exc: Exception) -> dict:
        return {"job_id": job["job_id"], "engine": eng.name, **eng.describe(),
                "error": f"{type(exc).__name__}: {exc}"[:500]}
