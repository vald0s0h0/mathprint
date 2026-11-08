"""Manifestes et preuves d'une reprise ciblée. Aucun modèle n'est appelé ici."""
from pathlib import Path
from app.services import exercise_review as review


def export(db, cards: list[dict]) -> dict:
    from app.models import IndigoExercise
    refs = {c["ref"] for c in cards}
    return {"version": 1, "cards": [
        {"ref": row.source_number, "id": row.id, "before": review.snapshot(row),
         "fingerprint": review.fingerprint(review.snapshot(row))}
        for row in db.query(IndigoExercise).all() if row.source_number in refs
        and (row.raw_ocr_json or {}).get("review_verdict") == "dislike"]}


def verify(rows, manifest: dict, targets: set[str]) -> None:
    cards = {c["ref"]: c for c in manifest.get("cards", [])}
    found = {r.source_number: r for r in rows}
    if not targets or targets - cards.keys() or targets - found.keys():
        raise SystemExit("Reprise hors sélection du manifeste ou exercice absent.")
    for ref in targets:
        row, card = found[ref], cards[ref]
        if (row.raw_ocr_json or {}).get("review_verdict") != "dislike":
            raise SystemExit(f"{ref} : la carte n’est plus dislikée.")
        if row.id != card["id"] or review.fingerprint(review.snapshot(row)) != card["fingerprint"]:
            raise SystemExit(f"{ref} : carte ou avis modifié depuis l’export, réexporte la sélection.")


def proof(run: Path, key: str, exercise: dict, contract: dict, rendered: dict,
          verdict: dict, *, required: bool) -> None:
    if "inputs" not in rendered and not required:
        return  # compatibilité des anciens runs, hors reprise avec manifeste
    if rendered.get("inputs", {}).get(key) != review.fingerprint({"exercise": exercise, "contract": contract}):
        raise SystemExit(f"{key} : contenu modifié après render, relance le rendu.")
    import hashlib
    path = run / "cards" / f"{key}.png"
    image_hash = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    if required and (not image_hash or verdict.get("image_sha256") != image_hash
                     or verdict.get("complete") is not True):
        raise SystemExit(f"{key} : Goldfish doit relire le dernier rendu complètement.")
    if verdict.get("image_sha256") and verdict["image_sha256"] != image_hash:
        raise SystemExit(f"{key} : verdict Goldfish périmé.")


def failed(db, row, reason: str, *, actor="agent") -> None:
    review.append(row, review.event("failed", "La reprise n’a pas abouti", actor=actor,
                                    reason=reason, previous=review.snapshot(row)))
    db.commit()
