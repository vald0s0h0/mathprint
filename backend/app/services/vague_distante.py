"""Relever la file du NAS depuis le Mac : `vague.py --nas` (docs/vague.md).

Aucun port, aucun secret, aucune route : le Mac joint déjà le NAS par SSH (clé)
et exécute du Python dans le conteneur par `docker exec -i … python -`. Le script
passe par l'entrée standard, données comprises (littéral Python produit par
repr) : aucune commande shell n'est construite avec du texte de la base ou du
modèle.

Côté conteneur (`main`) :
    exporter [--devoir ID] [--classe ID] [corriger|apprecier|tout]
    importer   (paquets et réponses du moteur en JSON)
Côté Mac (`run`) : exporter, demander au moteur localement, importer par lots.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, fields

from . import cli_llm, vague

HOTE = "Etienne@fabre.tail428d1a.ts.net"
COMMANDE = "sudo -n /usr/local/bin/docker exec -i mathprint-api-1 python -"
MARQUE = "VAGUE-JSON:"
LOT = 5                      # paquets par import : une coupure ne perd que le lot en cours


class NasError(RuntimeError):
    pass


def _session():
    from ..db import SessionLocal
    return SessionLocal()


def main(argv: list[str], entree: str | None = None) -> None:
    """Point d'entrée dans le conteneur : une ligne `VAGUE-JSON:{…}` sur la sortie."""
    commande, args = argv[0], argv[1:]
    devoir = classe = None
    taches: list[str] = []
    i = 0
    while i < len(args):
        if args[i] in ("--devoir", "--classe") and i + 1 < len(args):
            if args[i] == "--devoir":
                devoir = args[i + 1]
            else:
                classe = args[i + 1]
            i += 2
        elif args[i] in ("corriger", "apprecier", "tout"):
            taches.append(args[i])
            i += 1
        else:
            raise SystemExit(f"argument inconnu : {args[i]}")
    db = _session()
    try:
        if commande == "exporter":
            sortie = vague.exporter(db, taches, devoir=devoir, classe=classe)
        elif commande == "importer":
            sortie = _importer(db, json.loads(entree if entree is not None else sys.stdin.read()))
        else:
            raise SystemExit(f"commande inconnue : {commande}")
    finally:
        db.rollback()
        db.close()
    print(MARQUE + json.dumps(sortie, ensure_ascii=False))


def _importer(db, donnees: dict) -> dict:
    """`donnees` : {"moteur": str, "paquets": [{…paquet exporté, "reponse": JSON}]}.
    Chaque paquet est revalidé contre la base puis écrit seul (vague.importer_paquet)."""
    bilan = vague.Bilan()
    moteur = str(donnees.get("moteur") or "haiku")
    for p in donnees.get("paquets") or []:
        if not isinstance(p, dict):
            bilan.echecs += 1
            continue
        try:
            vague.importer_paquet(db, p, p.get("reponse"), moteur=moteur,
                                  log=bilan.lignes.append, bilan=bilan)
        except Exception as e:   # un paquet défaillant ne bloque pas les autres
            db.rollback()
            bilan.echecs += 1
            bilan.lignes.append(f"{p.get('id')} : erreur ({type(e).__name__})")
    return asdict(bilan)


# ------------------------------------------------------------------- côté Mac

def _script(argv: list[str], entree: str | None) -> str:
    return ("from app.services import vague_distante as v\n"
            f"v.main({argv!r}, {entree!r})\n")


def ssh_python(hote: str, argv: list[str], entree: str | None = None, timeout: int = 600):
    # un hôte qui commence par « - » serait lu par ssh comme une option
    if not hote or hote.startswith("-") or any(c.isspace() for c in hote):
        raise NasError(f"hôte SSH invalide : {hote!r}")
    """Exécute `main(argv, entree)` dans le conteneur du NAS ; rend le JSON."""
    try:
        proc = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", hote, COMMANDE],
                              input=_script(argv, entree), capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        raise NasError(f"NAS injoignable ({hote}) : {e}") from e
    if proc.returncode != 0:
        raise NasError(f"NAS injoignable ou refus ({hote}, code {proc.returncode}) : "
                       f"{(proc.stderr or proc.stdout).strip()[-300:]}")
    for line in reversed((proc.stdout or "").splitlines()):
        if line.startswith(MARQUE):
            try:
                return json.loads(line[len(MARQUE):])
            except json.JSONDecodeError as e:
                raise NasError(f"réponse du NAS illisible : {e}") from e
    raise NasError("le NAS n'a rien rendu : version sans vague_distante ? "
                   f"{(proc.stderr or proc.stdout).strip()[-300:]}")


def _ajouter(bilan: vague.Bilan, distant: dict) -> None:
    for f in fields(bilan):
        if f.name in ("arret", "lignes"):
            continue
        setattr(bilan, f.name, getattr(bilan, f.name) + int(distant.get(f.name) or 0))
    bilan.lignes += [str(x) for x in distant.get("lignes") or []]


def run(taches: list[str], *, hote: str = HOTE, moteur: str = "haiku", devoir: str | None = None,
        classe: str | None = None, essai: bool = False, ask: vague.Ask | None = None,
        log: Callable[[str], None] = print, lot: int = LOT, transport=None) -> vague.Bilan:
    bilan = vague.Bilan()
    transport = transport or ssh_python
    args = ["exporter", *(taches or ["tout"])]
    if devoir:
        args += ["--devoir", devoir]
    if classe:
        args += ["--classe", classe]
    try:
        paquets = transport(hote, args)
        if not isinstance(paquets, list):
            raise NasError("export du NAS inattendu")
        paquets = [p for p in paquets if isinstance(p, dict) and p.get("system") and p.get("text")]
        log(f"NAS : {len(paquets)} paquet(s) en attente.")
        if essai or not paquets:
            for n, p in enumerate(paquets, 1):
                log(f"[{n}/{len(paquets)}] {p.get('titre')}")
            return bilan
        if ask is None and not vague._verifier_moteur(moteur, bilan):
            return bilan
        call = vague._asker(moteur, ask)
        envoi: list[dict] = []

        def vider():
            if envoi:
                donnees = json.dumps({"moteur": moteur, "paquets": envoi}, ensure_ascii=False)
                _ajouter(bilan, transport(hote, ["importer"], donnees))
                envoi.clear()

        for n, p in enumerate(paquets, 1):
            head = f"[{n}/{len(paquets)}] {p.get('titre')}"
            try:
                raw = vague.demander(p, call)
            except cli_llm.CliError as e:
                if e.stop:
                    bilan.arret = str(e)
                    log(f"{head} : arrêt ({e})")
                    break
                bilan.echecs += 1
                log(f"{head} : échec ({e})")
                continue
            meta = {k: v for k, v in p.items() if k not in ("system", "text")}
            envoi.append({**meta, "titre": head, "reponse": raw})
            if len(envoi) >= lot:
                vider()
        vider()
    except NasError as e:
        bilan.arret = str(e)
    for ligne in bilan.lignes:
        log(ligne)
    return bilan


if __name__ == "__main__":
    main(sys.argv[1:])
