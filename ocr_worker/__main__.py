"""python -m ocr_worker [--once] [--config chemin] [--engine nom]"""
import argparse
import logging
import sys

from . import config as config_mod
from .waves import Worker


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ocr_worker")
    ap.add_argument("--once", action="store_true", help="traiter le travail en attente puis quitter")
    ap.add_argument("--config", default=None)
    ap.add_argument("--engine", default=None, help="ne lancer que ce moteur")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = config_mod.load(args.config)
    if not cfg.servers:
        print("aucun serveur dans la configuration", file=sys.stderr)
        return 2
    for s in cfg.servers:
        if not s.token:
            logging.warning("serveur %s : jeton absent (variable d'environnement non définie)", s.name)
    worker = Worker(cfg)
    if args.engine:
        worker.engines = {k: v for k, v in worker.engines.items() if k == args.engine}
    if args.once:
        stats = worker.run_once()
        logging.info("terminé : %s", stats)
        return 0
    worker.serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
