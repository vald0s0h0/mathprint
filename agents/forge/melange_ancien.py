"""Mélange à la main des cartes des anciennes forges (sans jumelles), et création de la jumelle.

Lancer depuis `backend/` avec `.venv/bin/python ../agents/forge/melange_ancien.py ...`.

    dump [--types qcm_single,matching,...] [--skip N] [--n N]   cartes à traiter, vue compacte
    apply decisions.json [--dry]                                   applique, vérifie, republie

Source de vérité : les lignes `IndigoExercise` (puis `indigo.publish_rows`, qui réécrit le fichier
publié, le miroir du dépôt et la banque). Jamais `generated_exercises` seule : `seed_published` la
purge et la resème depuis le fichier à chaque démarrage.

decisions.json : liste de
  {"id": "...", "d1": ordre, "d2": ordre}          # deux dispositions écrites par toi
  {"id": "...", "fixe": "raison"}                   # rien ne peut bouger : la carte reste seule
`ordre` = ids des choix (QCM), ids des lignes (grille), ids de droite (relier), numéros de colonnes (tableau).
La carte garde son identifiant avec la disposition 1 ; la disposition 2 devient une nouvelle ligne.
Avant d'écrire, chaque disposition est comparée à la carte d'origine : mêmes bonnes réponses, mêmes
messages, même barème.
"""
from __future__ import annotations

import copy
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

TYPES = ("qcm_single", "qcm_multiple", "checkbox_grid", "matching", "table_fill")


# ------------------------------------------------------------------ vérité d'une carte
def truth(rt: str, g: dict, e: dict):
    """Ce qui rend une réponse juste, indépendamment de l'ordre d'affichage."""
    if rt in ("qcm_single", "qcm_multiple"):
        ids = g["option_ids"]
        return sorted((ids[k], g["choices"][k]) for k in e["correct"]), sorted(zip(ids, g["choices"]))
    if rt == "checkbox_grid":
        rows = {r["label"]: r["correct"] for r in g["rows"]}
        rows_e = {r["label"]: r["correct"] for r in e["rows"]}
        return sorted(rows.items()), sorted(rows_e.items()), g["cols"], sorted(zip(g["option_ids"], [r["label"] for r in g["rows"]]))
    if rt == "matching":
        ids, right = g["right_ids"], g["right"]
        return (sorted((a, ids[b], right[b]) for a, b in g["pairs"]),
                sorted((a, e["right"][b]) for a, b in e["pairs"]), g["left"], sorted(zip(ids, right)))
    if rt == "table_fill":
        cells = e["cells"]
        return [[json.dumps(c, sort_keys=True) for c in row] for row in cells], g.get("col_labels")
    return None


def cleaned(rt, g, e):
    """Forme normalisée : ne dépend plus de l'ordre. Pour un tableau, chaque case est repérée par
    l'identité de sa ligne et de sa colonne (leur libellé, sinon l'ensemble de leurs valeurs)."""
    if rt == "table_fill":
        cells = e["cells"]
        nrow, ncol = len(cells), len(cells[0])
        rl = g.get("row_labels") or [None] * nrow
        cl = g.get("col_labels") or [None] * ncol
        dump = lambda c: json.dumps(c, sort_keys=True)
        rid = [str(rl[r]) if rl[r] is not None else "|".join(sorted(dump(c) for c in cells[r])) for r in range(nrow)]
        cid = [str(cl[c]) if cl[c] is not None else "|".join(sorted(dump(cells[r][c]) for r in range(nrow))) for c in range(ncol)]
        body = sorted((rid[r], cid[c], dump(cells[r][c])) for r in range(nrow) for c in range(ncol))
        wrong = sorted(json.dumps({**{k: v for k, v in w.items() if k != "cell"},
                                   "row": rid[w["cell"][0]], "col": cid[w["cell"][1]]}, sort_keys=True)
                       for w in (g.get("feedback") or {}).get("wrong_answers") or [] if w.get("cell"))
        return body, wrong, sorted(cid), sorted(rid)
    return truth(rt, g, e)


# ------------------------------------------------------------------ permutations
def arrange(rt: str, g: dict, e: dict, order: list, axis: str = "colonnes") -> tuple[dict, dict]:
    g, e = copy.deepcopy(g), copy.deepcopy(e)
    if rt in ("qcm_single", "qcm_multiple"):
        ids = g["option_ids"]
        if sorted(map(str, order)) != sorted(ids):
            raise ValueError(f"ordre = exactement {ids}")
        good = {ids[k] for k in e["correct"]}
        g["choices"] = [g["choices"][ids.index(i)] for i in order]
        g["option_ids"] = list(order)
        e["correct"] = sorted(k for k, i in enumerate(order) if i in good)
        for key in ("choices", "options"):
            if key in e:
                e[key] = [e[key][ids.index(i)] for i in order]
    elif rt == "checkbox_grid":
        ids = g["option_ids"]
        if sorted(map(str, order)) != sorted(ids):
            raise ValueError(f"ordre = exactement {ids}")
        if [r["label"] for r in g["rows"]] != [r["label"] for r in e["rows"]]:
            raise ValueError("grading et expected n'ont pas les mêmes lignes")
        g["rows"] = [g["rows"][ids.index(i)] for i in order]
        e["rows"] = [e["rows"][ids.index(i)] for i in order]
        g["option_ids"] = list(order)
    elif rt == "matching":
        ids = g["right_ids"]
        if sorted(map(str, order)) != sorted(ids):
            raise ValueError(f"ordre = exactement {ids}")
        if list(e.get("right") or []) != list(g["right"]):
            raise ValueError("grading.right et expected.right diffèrent")
        pos = {old: order.index(i) for old, i in enumerate(ids)}
        for d in (g, e):
            d["right"] = [d["right"][ids.index(i)] for i in order]
            d["pairs"] = [[int(a), pos[int(b)]] for a, b in d["pairs"]]
        g["right_ids"] = list(order)
        if "option_ids" in g:
            g["option_ids"] = list(order)
    elif rt == "table_fill":
        rows = axis == "lignes"
        n = len(e["cells"]) if rows else len(e["cells"][0])
        order = [int(i) for i in order]
        if sorted(order) != list(range(n)):
            raise ValueError(f"ordre = permutation de 0..{n - 1}")
        pos = {old: new for new, old in enumerate(order)}
        labels = "row_labels" if rows else "col_labels"
        for d in (g, e):
            d["cells"] = ([d["cells"][i] for i in order] if rows else [[row[i] for i in order] for row in d["cells"]])
        if g.get(labels):
            g[labels] = [g[labels][i] for i in order]
        for w in (g.get("feedback") or {}).get("wrong_answers") or []:
            if w.get("cell"):
                cell = list(w["cell"])
                cell[0 if rows else 1] = pos[int(cell[0 if rows else 1])]
                w["cell"] = cell
    return g, e


def position_vector(rt: str, g: dict, e: dict):
    if rt == "qcm_single":
        return (e["correct"][0],)
    if rt == "qcm_multiple":
        return tuple(e["correct"])
    if rt == "checkbox_grid":
        return tuple(r["correct"] for r in g["rows"])
    if rt == "matching":
        return tuple(b for _a, b in sorted(g["pairs"]))
    return tuple(json.dumps(c, sort_keys=True) for row in e["cells"] for c in row)


def problems_of(rt: str, g: dict, e: dict) -> list[str]:
    v = position_vector(rt, g, e)
    out = []
    if rt == "matching":
        if len(v) >= 2 and all(x < y for x, y in zip(v, v[1:])):
            out.append("traits parallèles")
        if sum(1 for a, k in enumerate(v) if k == a) > 1:
            out.append("plusieurs éléments en face d'eux-mêmes")
    if rt == "checkbox_grid" and len(g["rows"]) >= 3 and len(set(v)) == 1:
        out.append("toutes les lignes ont la même réponse")
    return out


# ------------------------------------------------------------------ dump
LETTERS = re.compile(r"\b(réponse|proposition|case|choix)\s*\(?[A-Da-d1-4]\)?\b|\b(toutes|aucune|les deux|ci-dessus|ci-dessous|précédent|a et b)\b", re.I)


def show(row) -> str:
    g, e, rt = row.grading_json, row.expected_json, row.response_type
    st = re.split(r"\{\{aide\}\}", row.statement)[0].strip().replace("\n", " ⏎ ")
    head = f"## {row.id} {rt} [{(row.raw_ocr_json or {}).get('archetype') or ''}] fig={'oui' if row.has_figure else 'non'}\n{st[:210]}"
    lines = []
    if rt in ("qcm_single", "qcm_multiple"):
        for k, (i, c) in enumerate(zip(g["option_ids"], g["choices"])):
            lines.append(f"  {i}{'*' if k in e['correct'] else ' '} {c[:110]}")
    elif rt == "checkbox_grid":
        lines.append(f"  colonnes: {g['cols']}")
        for i, r in zip(g["option_ids"], g["rows"]):
            lines.append(f"  {i} → {g['cols'][r['correct']]} | {r['label'][:110]}")
    elif rt == "matching":
        lines.append("  gauche: " + " ¦ ".join(f"{k}:{x[:40]}" for k, x in enumerate(g["left"])))
        links = {int(b): int(a) for a, b in g["pairs"]}
        for i, r in zip(g["right_ids"], g["right"]):
            k = g["right_ids"].index(i)
            lines.append(f"  {i}{' ←'+str(links[k]) if k in links else ' (piège)'} {r[:70]}")
    elif rt == "table_fill":
        lines.append(f"  colonnes: {g.get('col_labels')} lignes: {g.get('row_labels')} unordered={g.get('unordered')}")
        for r in e["cells"]:
            lines.append("  " + " | ".join(json.dumps(c.get("value"), ensure_ascii=False)[:30] for c in r))
    flag = "  ⚠ renvoi possible entre réponses" if LETTERS.search(row.statement + " ".join(g.get("choices") or [])) else ""
    return head + "\n" + "\n".join(lines) + flag


def candidates(db, types):
    from app.models import IndigoExercise
    rows = db.query(IndigoExercise).filter(IndigoExercise.response_type.in_(types),
                                           IndigoExercise.status == "validated").order_by(IndigoExercise.source_number).all()
    out = []
    for r in rows:
        g = r.grading_json or {}
        if (r.raw_ocr_json or {}).get("pipeline") != "forge" or g.get("twin") or g.get("author_order"):
            continue
        if (r.raw_ocr_json or {}).get("ordre_impose"):
            continue
        if r.response_type == "table_fill" and len(r.expected_json.get("cells") or []) * len((r.expected_json.get("cells") or [[]])[0]) < 2:
            continue
        out.append(r)
    return out


# ------------------------------------------------------------------ apply
def clone(row, key: str):
    from app.models import IndigoExercise, uid
    new = IndigoExercise()
    for col in IndigoExercise.__table__.columns:
        setattr(new, col.name, copy.deepcopy(getattr(row, col.name)))
    new.id = uid()
    new.source_number = key
    new.created_at = new.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return new


def twin_meta(group: str, disposition: int) -> dict:
    return {"group": group, "jeu": 1, "disposition": disposition, "jeux": 1, "dispositions": 2,
            "principal": disposition == 1}


def auto_orders(rt: str, g: dict, e: dict, seed: str, last: list | None = None, axis: str = "colonnes") -> tuple[list, list]:
    """Deux ordres tirés (graine = identifiant de la carte, donc reproductibles) qui passent les
    mêmes contrôles qu'une disposition écrite à la main. `last` : ids qui restent à la fin, dans
    cet ordre (« aucune de ces réponses », « les deux »)."""
    import random
    rng = random.Random(seed)
    if rt == "table_fill":
        ids = list(range(len(e["cells"]) if axis == "lignes" else len(e["cells"][0])))
    else:
        ids = list(g["right_ids"] if rt == "matching" else g["option_ids"])
    last = [i for i in (last or []) if i in ids]
    free = [i for i in ids if i not in last]
    tries = []
    for _ in range(400):
        order = free[:]
        rng.shuffle(order)
        order += last
        try:
            g2, e2 = arrange(rt, g, e, order, axis)
        except ValueError:
            continue
        if problems_of(rt, g2, e2):
            continue
        tries.append((order, position_vector(rt, g2, e2)))
        if len(tries) >= 2 and any(a[1] != tries[0][1] for a in tries[1:]):
            break
    other = next((t[0] for t in tries[1:] if t[1] != tries[0][1]), None) if tries else None
    if other is None:
        raise ValueError("aucun couple de dispositions valable : écris-les à la main")
    return tries[0][0], other


def apply(db, decisions: list[dict], dry: bool = False) -> list[str]:
    from app.models import IndigoExercise
    from app.services import indigo
    touched, log, errors = [], [], []
    for d in decisions:
        row = db.get(IndigoExercise, d["id"])
        if row is None:
            errors.append(f"{d['id']} : introuvable"); continue
        rt = row.response_type
        if "fixe" in d:
            if not dry:
                row.raw_ocr_json = {**(row.raw_ocr_json or {}), "ordre_impose": d["fixe"]}
            log.append(f"{row.id[:8]} {rt} : ordre imposé ({d['fixe'][:60]})"); continue
        g0, e0 = row.grading_json, row.expected_json
        if (g0.get("twin") or g0.get("author_order")):
            errors.append(f"{row.id} : déjà traitée"); continue
        try:
            if d.get("auto"):
                d = {**d, "d1": None, "d2": None}
                d["d1"], d["d2"] = auto_orders(rt, g0, e0, row.id, d.get("last"), d.get("axe", "colonnes"))
            g1, e1 = arrange(rt, g0, e0, d["d1"], d.get("axe", "colonnes"))
            g2, e2 = arrange(rt, g0, e0, d["d2"], d.get("axe", "colonnes"))
        except (ValueError, KeyError) as exc:
            errors.append(f"{row.id} : {exc}"); continue
        base = cleaned(rt, g0, e0)
        for rank, (g, e) in enumerate(((g1, e1), (g2, e2)), 1):
            if cleaned(rt, g, e) != base:
                errors.append(f"{row.id} : disposition {rank} change la vérité de la carte")
            for p in problems_of(rt, g, e):
                errors.append(f"{row.id} : disposition {rank} : {p}")
        if position_vector(rt, g1, e1) == position_vector(rt, g2, e2):
            errors.append(f"{row.id} : les deux dispositions donnent la même place aux bonnes réponses")
        if any(x.startswith(row.id) for x in errors):
            continue
        group = row.source_number
        if db.query(IndigoExercise).filter(IndigoExercise.id != row.id,
                                           IndigoExercise.source_number == group + ".j1d2").count():
            errors.append(f"{row.id} : {group}.j1d2 existe déjà"); continue
        if dry:
            log.append(f"{row.id[:8]} {rt} : ok (essai)"); continue
        twin = clone(row, group + ".j1d2")
        for target, (g, e), rank in ((row, (g1, e1), 1), (twin, (g2, e2), 2)):
            g = {**g, "author_order": True, "twin": twin_meta(group, rank)}
            target.grading_json, target.expected_json = g, e
            target.payload_json = {**(target.payload_json or {}), "expected": e, "grading": g}
        db.add(twin)
        touched += [row, twin]
        log.append(f"{row.id[:8]} {rt} : d1 {position_vector(rt, g1, e1)} / d2 {position_vector(rt, g2, e2)} → jumelle {twin.id[:8]}")
    if errors:
        db.rollback()
        raise SystemExit("REFUSÉ, rien n'est écrit :\n" + "\n".join(errors))
    if not dry:
        db.commit()
        if touched:
            indigo.publish_rows(db, touched)
    return log


def main(argv: list[str]) -> None:
    from app.db import SessionLocal
    db = SessionLocal()
    cmd = argv[0] if argv else ""
    opt = lambda name, default=None: argv[argv.index(name) + 1] if name in argv else default
    if cmd == "dump":
        types = opt("--types", ",".join(TYPES)).split(",")
        rows = candidates(db, types)
        skip, n = int(opt("--skip", 0)), int(opt("--n", 25))
        print(f"# {len(rows)} cartes à traiter ({skip}..{skip + n})", file=sys.stderr)
        for r in rows[skip:skip + n]:
            print(show(r))
    elif cmd == "count":
        rows = candidates(db, TYPES)
        import collections
        print(collections.Counter(r.response_type for r in rows))
    elif cmd == "apply":
        for line in apply(db, json.load(open(argv[1])), dry="--dry" in argv):
            print(line)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
