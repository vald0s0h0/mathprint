"""Lots de scans, machine d'états, file de validation professeur (§6, §9.5)."""
import hashlib
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal, get_db
from ..deps import current_user
from ..models import (
    Assessment, Copy, CopyItem, GradingDecision, ManualReview, OcrAttempt,
    ResponseZone, ScanBatch, SchoolClass, Student, StudentResponse, User,
)
from ..services import grading
from ..services import providers
from ..services import sandbox as sandbox_service
from ..services import scan_intake, scoring
from ..services.pipeline import build_overlays, finalize_batch, process_batch

router = APIRouter(prefix="/api/scans", tags=["scans"], dependencies=[Depends(current_user)])


def _sniff_file(content: bytes) -> tuple[str, str] | None:
    """(extension, mime) reconnus par signature d'octets (magic bytes) — jamais
    le Content-Type client, purement déclaratif (§5b : PDF, JPEG, PNG, HEIC)."""
    if content.startswith(b"%PDF-"):
        return ".pdf", "application/pdf"
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if len(content) >= 12 and content[4:8] == b"ftyp" and content[8:12] in (
            b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"):
        return ".heic", "image/heic"
    return None


# Message unique du blocage « pas de clé Mathpix » — la correction lit
# l'écriture manuscrite des élèves via Mathpix ; sans clé, le repli déterministe
# ne fait que RECOPIER la réponse attendue (tout paraît juste), ce qui trompe le
# professeur. On refuse donc le dépôt tant qu'aucune clé n'est configurée.
MATHPIX_REQUIRED = ("La clé Mathpix est indispensable pour corriger ces copies. "
                    "Configurez-la dans Paramètres → API avant de déposer un scan.")


@router.get("/config")
def scan_config(db: Session = Depends(get_db)):
    """Capacités de correction visibles par le professeur (bannière/boutons)."""
    return {"mathpix_configured": not providers.offline(db, "mathpix")}


def _run_pipeline(batch_id: str):
    db = SessionLocal()
    try:
        batch = db.get(ScanBatch, batch_id)
        process_batch(db, batch)
    except Exception as e:
        batch = db.get(ScanBatch, batch_id)
        batch.error = str(e)
        db.commit()
    finally:
        db.close()


def _run_build_overlays(batch_id: str):
    """Régénère UNIQUEMENT les copies corrigées d'un lot déjà finalisé (les
    résultats/notes sont acquis) — bouton de déblocage quand seule la génération
    des overlays a échoué. Ne repasse pas par l'OCR."""
    db = SessionLocal()
    try:
        batch = db.get(ScanBatch, batch_id)
        build_overlays(db, batch)
        batch.error = None
        db.commit()
    except Exception as e:
        batch = db.get(ScanBatch, batch_id)
        batch.error = f"Copies corrigées non générées : {e}"
        db.commit()
    finally:
        db.close()


def _raster_bytes(ext: str, content: bytes):
    """Rastérise un fichier déposé (PDF page par page, ou image isolée) en une
    liste d'images BGR, via un fichier temporaire (worker_cv lit un chemin)."""
    from ..services import worker_cv

    tmp_dir = settings.data_dir / "scans" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"in-{uuid.uuid4().hex}{ext}"
    tmp.write_bytes(content)
    try:
        return worker_cv.raster_any(str(tmp))
    finally:
        tmp.unlink(missing_ok=True)


@router.post("/batches")
async def create_batch(tasks: BackgroundTasks, assessment_id: str | None = None,
                       file: UploadFile | None = None,
                       db: Session = Depends(get_db),
                       user: User = Depends(current_user)):
    """Dépôt d'un scan. Le scan s'ACCUMULE dans l'unique ScanBatch du sujet
    (règle « un sujet = une correction = une ligne », cf. services.scan_intake)
    — jamais un second batch."""
    if providers.offline(db, "mathpix"):
        raise HTTPException(400, MATHPIX_REQUIRED)
    content = await file.read() if file is not None else None
    if content is None:
        raise HTTPException(422, "Déposer un scan (PDF, JPEG, PNG ou HEIC)")

    sniffed = _sniff_file(content)
    if sniffed is None:
        raise HTTPException(400, "Format non reconnu — PDF, JPEG, PNG ou HEIC uniquement")
    ext, _mime = sniffed
    # rastérisation + identification = travail CV lourd et SYNCHRONE : on le sort
    # de la boucle d'événements (threadpool) pour ne pas geler l'API (la liste des
    # corrections doit rester réactive pendant le traitement du scan).
    images = await run_in_threadpool(_raster_bytes, ext, content)
    if not images:
        raise HTTPException(422, "Scan illisible")

    # sujet identifié par le QR signé d'une page (sauf dépôt ciblé « en attente
    # de scan » où le sujet est déjà connu)
    if not assessment_id:
        assessment_id = await run_in_threadpool(scan_intake.detect_assessment, db, images)
        if not assessment_id:
            raise HTTPException(422, "Aucun QR MathPrint reconnu dans ce scan — "
                                     "vérifier qu'il s'agit bien de copies générées ici")
    if not db.get(Assessment, assessment_id):
        raise HTTPException(404, "Évaluation inconnue")

    r = await run_in_threadpool(scan_intake.attach_scan, db, assessment_id, images, user.id)
    batch = db.get(ScanBatch, r["batch_id"])
    if not batch.source_file_id:
        # aucune page reconnue et batch vierge : ne pas laisser un lot fantôme
        # (qui basculerait à tort sur le chemin sans-scan)
        db.delete(batch)
        db.commit()
        raise HTTPException(422, "Aucune page MathPrint reconnue dans ce scan")
    db.commit()
    tasks.add_task(_run_pipeline, batch.id)
    return {"id": batch.id, "assessment_id": assessment_id,
            "pages_added": r["pages_added"],
            "duplicates_rejected": r["duplicates_rejected"],
            "blocked_pages": r["blocked_pages"]}


@router.post("/sandbox")
async def sandbox_upload(tasks: BackgroundTasks, files: list[UploadFile],
                         db: Session = Depends(get_db),
                         user: User = Depends(current_user)):
    """Bac à sable (§5c) : dépôt en vrac de PDFs/images mélangés. Traité page
    par page, mais REGROUPÉ GLOBALEMENT par sujet sur tout le dépôt (plusieurs
    fichiers d'un même sujet → une seule correction) ; doublons rejetés
    silencieusement. Réinjecté dans le pipeline existant sans modification."""
    if providers.offline(db, "mathpix"):
        raise HTTPException(400, MATHPIX_REQUIRED)
    recognized: list[tuple[str, str, bytes]] = []
    results = []
    for f in files:
        content = await f.read()
        sniffed = _sniff_file(content)
        if sniffed is None:
            results.append({"filename": f.filename, "status": "unrecognized", "pages_added": 0,
                            "duplicates_rejected": 0, "blocked_pages": 0, "batches_created": []})
            continue
        ext, _mime = sniffed
        recognized.append((f.filename or "scan", ext, content))
    # ingestion = raster + identification de chaque page (CV lourd, synchrone) :
    # threadpool pour ne pas bloquer la boucle d'événements pendant le dépôt en
    # vrac (sinon l'UI n'affiche plus rien le temps du traitement).
    out = await run_in_threadpool(sandbox_service.ingest_files, db, recognized, user.id)
    results.extend(out["results"])
    for batch_id in out["batch_ids"]:
        tasks.add_task(_run_pipeline, batch_id)
    return {"results": results}


@router.get("/batches")
def list_batches(assessment_id: str | None = None, db: Session = Depends(get_db)):
    q = (db.query(ScanBatch)
         .join(Assessment, ScanBatch.assessment_id == Assessment.id)
         .join(SchoolClass, Assessment.class_id == SchoolClass.id)
         .filter(SchoolClass.archived_at.is_(None))
         .order_by(ScanBatch.created_at.desc()))
    if assessment_id:
        q = q.filter(ScanBatch.assessment_id == assessment_id)
    rows = [_batch_view(db, b) for b in q.all()]
    if not assessment_id:
        rows += _awaiting_scan_rows(db)
    return rows


def _awaiting_scan_rows(db: Session) -> list[dict]:
    """Sujets générés/imprimés sans aucun lot de scan encore déposé (§5a) —
    invisibles jusqu'ici puisque Corrections ne listait que des ScanBatch."""
    scanned_ids = {a for (a,) in db.query(ScanBatch.assessment_id).distinct()}
    q = (db.query(Assessment)
         .join(SchoolClass, Assessment.class_id == SchoolClass.id)
         .filter(Assessment.status.in_(("ready", "printed")),
                 SchoolClass.archived_at.is_(None))
         .order_by(Assessment.created_at.desc()))
    out = []
    for a in q.all():
        if a.id in scanned_ids:
            continue
        cls = db.get(SchoolClass, a.class_id)
        out.append({
            "id": f"awaiting-{a.id}", "assessment_id": a.id, "status": "awaiting_scan",
            "assessment_title": a.title, "assessment_type": a.type,
            "note_base": scoring.assessment_note_base(a) or None,
            "class_name": cls.name if cls else "?", "class_id": cls.id if cls else None,
            "grade_level": cls.grade_level if cls else "", "page_count": 0,
            "error": None, "pending_reviews": 0, "segments": [],
            "overlay_printed": False, "overlay_distributed": False,
            "created_at": str(a.created_at),
        })
    return out


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, db: Session = Depends(get_db)):
    b = db.get(ScanBatch, batch_id)
    if not b:
        raise HTTPException(404)
    return _batch_view(db, b)


def _business_steps(status: str, progress: dict | None, pending: int,
                    error: str | None) -> list[dict]:
    """Étapes MÉTIER de la correction, pour un visualiseur lisible par le
    professeur — pas les 10 phases techniques internes. La règle de couleur est
    simple et cohérente avec les actions proposées :

    - vert  = fait ;
    - bleu  = en cours (traitement automatique) ;
    - orange = À VOUS : correction manuelle requise (la SEULE vraie halte) ;
    - gris  = à venir ;
    - rouge = bloqué (une erreur), sur l'étape où ça coince.

    Le flux « coule » de gauche à droite : une fois la correction faite, la
    validation puis les copies corrigées passent au vert automatiquement — il
    n'y a jamais d'orange coincé au milieu avec du vert après."""
    done = progress or {}
    scanned = "split" in done or "uploaded" in done
    read = "ocr_complete" in done
    graded = "graded" in done
    finalized = "finalized" in done
    overlay = "overlay_ready" in done

    if graded and not pending:
        correct = "green"
    elif pending:
        correct = "orange"
    elif read and not graded:
        correct = "blue"      # notation automatique en cours
    else:
        correct = "gray"

    steps = [
        {"phase": "scan", "label": "Scan déposé",
         "state": "green" if scanned else "blue"},
        {"phase": "read", "label": "Lecture des copies",
         "state": "green" if read else ("blue" if scanned else "gray")},
        {"phase": "correct", "label": "Correction", "state": correct},
        {"phase": "validate", "label": "Validation",
         "state": "green" if finalized else ("blue" if (graded and not pending) else "gray")},
        {"phase": "done", "label": "Copies corrigées",
         "state": "green" if overlay else ("blue" if finalized else "gray")},
    ]
    if error:
        for s in steps:
            if s["state"] != "green":
                s["state"] = "red"   # le blocage est sur la 1re étape non finie
                break
    return steps


def _batch_view(db: Session, b: ScanBatch) -> dict:
    pending = _pending_reviews_query(db, b.assessment_id).count()
    segments = _business_steps(b.status, b.progress_json, pending, b.error)
    assessment = db.get(Assessment, b.assessment_id)
    cls = db.get(SchoolClass, assessment.class_id) if assessment else None
    return {"id": b.id, "assessment_id": b.assessment_id, "status": b.status,
            "assessment_title": assessment.title if assessment else "?",
            "assessment_type": assessment.type if assessment else "training",
            # base de notation du contrôle (§ barème) — None si non noté
            "note_base": (scoring.assessment_note_base(assessment) or None)
                         if assessment else None,
            "class_name": cls.name if cls else "?",
            "class_id": cls.id if cls else None,
            "grade_level": cls.grade_level if cls else "",
            "page_count": b.page_count, "error": b.error,
            "pending_reviews": pending, "segments": segments,
            "overlay_printed": b.overlay_printed, "overlay_distributed": b.overlay_distributed,
            "created_at": str(b.created_at)}


def _pending_reviews_query(db: Session, assessment_id: str):
    return (db.query(ManualReview)
            .join(GradingDecision, ManualReview.decision_id == GradingDecision.id)
            .join(StudentResponse, GradingDecision.response_id == StudentResponse.id)
            .join(CopyItem, StudentResponse.copy_item_id == CopyItem.id)
            .join(Copy, CopyItem.copy_id == Copy.id)
            .filter(Copy.assessment_id == assessment_id,
                    ManualReview.resolved_at.is_(None)))


def _latest_decision_for_response(db: Session, response_id: str) -> GradingDecision | None:
    return (db.query(GradingDecision).filter_by(response_id=response_id)
            .order_by(GradingDecision.created_at.desc()).first())


def _open_review_for_response(db: Session, response_id: str) -> ManualReview | None:
    """Revue professeur encore ouverte rattachée à une décision de cette réponse
    (peu importe laquelle : les décisions sont append-only, la revue est posée
    sur la décision automatique)."""
    return (db.query(ManualReview)
            .join(GradingDecision, ManualReview.decision_id == GradingDecision.id)
            .filter(GradingDecision.response_id == response_id,
                    ManualReview.resolved_at.is_(None))
            .first())


# Mode de correction manuelle d'un exercice — pilote l'UI de la modale :
#   cells   : tableau / cases à trous → validation CASE PAR CASE (Juste/Faux),
#             le script recalcule le barème depuis les verdicts (RM correction) ;
#   binary  : QCM → une seule décision Juste/Faux sur toute la réponse ;
#   partial : réponse rédigée (raisonnement, formulation, réponse unique) →
#             les 4 boutons de crédit partiel (tous / 2⁄3 / 1⁄3 / 0).
def _grade_mode(response_type: str) -> str:
    if response_type in ("table_fill", "multi_blank"):
        return "cells"
    if response_type.startswith("qcm"):
        return "binary"
    return "partial"


def _fmt_value_latex(ctype: str, value) -> str:
    """Réponse attendue en fragment LISIBLE (LaTeX $...$ rendu par KaTeX dans la
    modale), pas un bout de JSON : le professeur valide d'un coup d'œil. Aligné
    sur la normalisation FR du moteur (virgule décimale, \\dfrac)."""
    if ctype == "rational":
        try:
            num, den = value
        except (TypeError, ValueError):
            return str(value)
        return f"$\\dfrac{{{num}}}{{{den}}}$"
    if ctype == "decimal":
        v = int(value) if isinstance(value, float) and value.is_integer() else value
        return f"${str(v).replace('.', '{,}')}$"
    if ctype == "integer":
        return f"${value}$"
    if ctype == "expression":
        v = str(value).strip()
        return v if v.startswith("$") else f"${v}$"
    return str(value)  # text : pas de maths


def _expected_display(item: CopyItem) -> str:
    """Réponse ATTENDUE d'une réponse « en un bloc » (QCM, réponse unique,
    formulation, raisonnement), rendue lisible pour la validation manuelle —
    uniquement la réponse attendue, pas tout le corrigé."""
    exp = item.expected_json or {}
    g = item.grading_json or {}
    if item.response_type.startswith("qcm"):
        choices = g.get("choices") or []
        labels = [choices[i] for i in (exp.get("correct") or []) if 0 <= i < len(choices)]
        return "  ·  ".join(labels)
    t = exp.get("type")
    if t in ("integer", "decimal", "rational", "expression", "text"):
        return _fmt_value_latex(t, exp.get("value"))
    # matching / tracé : pas de « valeur » ponctuelle → le corrigé fait foi
    return item.correction or ""


def _cell_units(item: CopyItem, ocr: OcrAttempt | None,
                decision: GradingDecision | None) -> list[dict]:
    """Une entrée PAR CASE à corriger d'un tableau / de cases à trous (cellules
    « given » exclues, déjà imprimées et non notées). Chaque case porte sa
    réponse attendue lisible, ce que l'OCR a cru lire, le verdict automatique du
    moteur (`auto_ok`) et, s'il existe, le verdict déjà posé par le professeur
    (`teacher_ok`). La modale ne met en validation QUE les cases non tranchées."""
    exp_cells = (item.expected_json or {}).get("cells") or []
    ocr_cells = ((ocr.raw_json or {}).get("cells") if ocr else None) or []
    g = item.grading_json or {}
    row_labels = g.get("row_labels") or []
    col_labels = g.get("col_labels") or []
    # verdicts professeur déjà enregistrés (set_cells) — pour rouvrir la modale
    # sur SES choix, pas re-déduire d'un texte de cellule réécrit "" côté faux.
    teacher = None
    if decision and decision.source == "teacher":
        teacher = (decision.evidence_json or {}).get("cell_verdicts")
    out, k = [], 0
    for ri, row in enumerate(exp_cells):
        for ci, cell in enumerate(row):
            if cell.get("given"):
                continue
            raw = ocr_cells[k] if k < len(ocr_cells) else ""
            rl = row_labels[ri] if ri < len(row_labels) else None
            cl = col_labels[ci] if ci < len(col_labels) else None
            if rl and len(row) == 1:
                label = str(rl)
            elif rl and cl:
                label = f"{rl} · {cl}"
            elif cl or rl:
                label = str(cl or rl)
            else:
                label = f"Case {k + 1}"
            out.append({
                "index": k, "label": label,
                "expected_display": _fmt_value_latex(cell["type"], cell["value"]),
                "ocr_text": raw or "",
                "auto_ok": grading._cell_ok(cell, raw),
                "teacher_ok": (teacher[k] if teacher and k < len(teacher) else None),
            })
            k += 1
    return out


def _review_unit(db: Session, resp: StudentResponse, item: CopyItem, copy: Copy,
                 student: Student, *, review: ManualReview | None = None,
                 decision: GradingDecision | None = None) -> dict:
    """Vue unifiée d'une réponse d'élève à corriger, que la correction soit
    SIGNALÉE (une ManualReview ouverte) ou simplement RELUE par le professeur
    (aucune revue, mais il veut vérifier/ajuster). Clé de correction = la réponse
    (`response_id`), pas la revue : le professeur peut corriger n'importe quelle
    réponse, pas seulement celles que le moteur a marquées.

    Exercices identiques = même entrée catalogue ET même énoncé figé (RM-014) :
    on regroupe dessus (`group_key`) pour enchaîner tout un lot d'un coup, en
    montrant le crop scanné de chaque élève et le barème réel de l'exercice."""
    if decision is None:
        decision = _latest_decision_for_response(db, resp.id)
    ocr = (db.query(OcrAttempt).filter_by(zone_id=resp.zone_id)
           .order_by(OcrAttempt.created_at.desc()).first()) if resp.zone_id else None
    sig = hashlib.sha1(item.statement.encode()).hexdigest()[:8]
    bareme = scoring.item_bareme(item.grading_json, item.response_type)
    earned = (scoring.earned_points(decision.score, decision.max_score, bareme)
              if decision else 0.0)
    full = bool(decision and decision.max_score and decision.score >= decision.max_score)
    src = decision.source if decision else "auto"  # deterministic|deepseek|teacher
    cancelled = bool(decision and src == "teacher" and not decision.max_score)
    return {
        "response_id": resp.id,
        "review_id": review.id if review else None,
        "flagged": review is not None,
        "category": review.category if review else None,
        "student": f"{student.last_name} {student.first_name}",
        "statement": item.statement, "expected": item.expected_json,
        "correction": item.correction,
        "ocr_text": resp.final_text, "selected_choices": resp.selected_choices,
        "ocr_confidence": ocr.confidence if ocr else None,
        "reason_code": decision.reason_code if decision else "",
        # source de la note actuelle, pour distinguer auto vs correction prof
        "decision_source": src,
        "proposed_score": decision.score if decision else 0.0,
        "max_score": decision.max_score if decision else 0.0,
        # points de barème actuellement attribués (ratio × barème)
        "current_points": round(earned, 2),
        "full_credit": full, "cancelled": cancelled,
        # barème réel (points professeur) de l'exercice, pour l'affichage
        "bareme_points": bareme,
        "zone_id": resp.zone_id,
        # a un crop scanné exploitable ? (un lot sans scan n'en a pas)
        "has_scan": _zone_crop_path(copy.assessment_id, resp.zone_id).exists()
                    if resp.zone_id else False,
        "group_key": f"{item.catalog_id}|{sig}",
        "group_label": f"Ex. {item.sequence}",
        "response_type": item.response_type,
        "sequence": item.sequence,
        # correction manuelle : mode d'UI + réponse attendue LISIBLE (plus de
        # JSON brut), et le détail CASE PAR CASE pour les tableaux/cases à trous
        "grade_mode": _grade_mode(item.response_type),
        "expected_display": _expected_display(item),
        "cells": (_cell_units(item, ocr, decision)
                  if item.response_type in ("table_fill", "multi_blank") else []),
    }


@router.get("/batches/{batch_id}/reviews")
def list_reviews(batch_id: str, category: str | None = None, db: Session = Depends(get_db)):
    """Réponses SIGNALÉES (revue automatique ouverte) — le sous-ensemble que le
    moteur n'a pas su trancher. Voir `list_items` pour toutes les réponses."""
    b = db.get(ScanBatch, batch_id)
    if not b:
        raise HTTPException(404)
    q = _pending_reviews_query(db, b.assessment_id)
    if category:
        q = q.filter(ManualReview.category == category)
    out = []
    for r in q.all():
        decision = db.get(GradingDecision, r.decision_id)
        resp = db.get(StudentResponse, decision.response_id)
        item = db.get(CopyItem, resp.copy_item_id)
        copy = db.get(Copy, item.copy_id)
        student = db.get(Student, copy.student_id)
        out.append(_review_unit(db, resp, item, copy, student, review=r, decision=decision))
    # exercices identiques consécutifs (group_key), puis par élève : le prof
    # enchaîne tout un lot d'exercices identiques avant de passer au suivant
    out.sort(key=lambda x: (x["sequence"], x["group_key"], x["student"]))
    return out


@router.get("/batches/{batch_id}/items")
def list_items(batch_id: str, scope: str = "flagged", db: Session = Depends(get_db)):
    """File de correction du professeur.

    - `scope=flagged` (défaut) : uniquement les réponses signalées par le moteur
      (identique à `/reviews`) ;
    - `scope=all` : TOUTES les réponses scannées du sujet, pour relire et ajuster
      n'importe quelle note même si le moteur était sûr de lui (le professeur
      reste maître de la correction, cf. demande « corriger manuellement »).

    Dans les deux cas la clé de résolution est `response_id`."""
    b = db.get(ScanBatch, batch_id)
    if not b:
        raise HTTPException(404)
    if scope != "all":
        return list_reviews(batch_id, None, db)

    open_by_resp: dict[str, ManualReview] = {}
    for r in _pending_reviews_query(db, b.assessment_id).all():
        d = db.get(GradingDecision, r.decision_id)
        if d:
            open_by_resp[d.response_id] = r

    out = []
    copies = db.query(Copy).filter_by(assessment_id=b.assessment_id).all()
    for copy in copies:
        student = db.get(Student, copy.student_id)
        items = (db.query(CopyItem).filter_by(copy_id=copy.id)
                 .order_by(CopyItem.sequence).all())
        for item in items:
            resp = db.query(StudentResponse).filter_by(copy_item_id=item.id).first()
            if not resp:
                continue  # exercice non scanné pour cet élève : rien à corriger
            out.append(_review_unit(db, resp, item, copy, student,
                                    review=open_by_resp.get(resp.id)))
    # signalés d'abord, puis exercice par exercice, puis par élève
    out.sort(key=lambda x: (0 if x["flagged"] else 1, x["sequence"],
                            x["group_key"], x["student"]))
    return out


@router.get("/batches/{batch_id}/summary")
def batch_summary(batch_id: str, db: Session = Depends(get_db)):
    """Récapitulatif AVANT validation, pour la modale « Valider la correction » :
    par copie scannée, points de barème obtenus/total et note PRÉVISIONNELLE
    (calculés sans rien persister), plus le nombre de réponses encore à corriger.
    Le professeur vérifie tout — notes de chaque élève, restes à corriger — avant
    de verrouiller la correction."""
    b = db.get(ScanBatch, batch_id)
    if not b:
        raise HTTPException(404)
    assessment = db.get(Assessment, b.assessment_id)
    base = scoring.assessment_note_base(assessment) if assessment else 0

    open_resp_ids: set[str] = set()
    for r in _pending_reviews_query(db, b.assessment_id).all():
        d = db.get(GradingDecision, r.decision_id)
        if d:
            open_resp_ids.add(d.response_id)

    copies_out = []
    total_pending = 0
    copies = db.query(Copy).filter_by(assessment_id=b.assessment_id).all()
    for copy in copies:
        student = db.get(Student, copy.student_id)
        items = (db.query(CopyItem).filter_by(copy_id=copy.id)
                 .order_by(CopyItem.sequence).all())
        earned = total = 0.0
        graded = flagged = 0
        has_resp = False
        for item in items:
            resp = db.query(StudentResponse).filter_by(copy_item_id=item.id).first()
            if not resp:
                continue
            has_resp = True
            if resp.id in open_resp_ids:
                flagged += 1
            dec = _latest_decision_for_response(db, resp.id)
            if not dec or dec.status == "review_pending" or not dec.max_score:
                continue  # non tranché ou question annulée : hors barème
            bareme = scoring.item_bareme(item.grading_json, item.response_type)
            earned += scoring.earned_points(dec.score, dec.max_score, bareme)
            total += bareme
            graded += 1
        if not has_resp:
            continue  # copie non scannée : rien à valider
        total_pending += flagged
        note = None
        if base and total:
            _, note = scoring.note_from_points(earned, total, base)
        copies_out.append({
            "student": f"{student.last_name} {student.first_name}",
            "points_earned": round(earned, 2), "points_total": round(total, 2),
            "note": note, "graded_items": graded, "flagged": flagged,
        })
    copies_out.sort(key=lambda c: c["student"])
    return {"assessment_title": assessment.title if assessment else "?",
            "note_base": base or None, "pending_reviews": total_pending,
            "scanned_copies": len(copies_out), "copies": copies_out}


def _zone_crop_path(assessment_id: str, zone_id: str):
    return (settings.data_dir / "assessments" / assessment_id / "scans" /
            "derived" / f"{zone_id}.png")


@router.get("/reviews/{review_id}/scan")
def review_scan(review_id: str, db: Session = Depends(get_db)):
    """Crop scanné (recalé, dropout appliqué) de la zone de réponse de l'élève —
    pour voir précisément ce que le moteur n'a pas su identifier. 404 si absent
    (lot sans scan, ou zone sans encre)."""
    r = db.get(ManualReview, review_id)
    if not r:
        raise HTTPException(404)
    decision = db.get(GradingDecision, r.decision_id)
    resp = db.get(StudentResponse, decision.response_id) if decision else None
    if not resp or not resp.zone_id:
        raise HTTPException(404, "Aucune zone scannée")
    item = db.get(CopyItem, resp.copy_item_id)
    copy = db.get(Copy, item.copy_id) if item else None
    if not copy:
        raise HTTPException(404)
    path = _zone_crop_path(copy.assessment_id, resp.zone_id)
    if not path.exists():
        raise HTTPException(404, "Crop indisponible")
    return FileResponse(path, media_type="image/png")


@router.get("/responses/{response_id}/scan")
def response_scan(response_id: str, db: Session = Depends(get_db)):
    """Crop scanné de la zone de réponse — même image que `review_scan`, mais
    adressé par réponse (utilisé par la relecture « toutes les réponses », où il
    n'y a pas forcément de revue ouverte)."""
    resp = db.get(StudentResponse, response_id)
    if not resp or not resp.zone_id:
        raise HTTPException(404, "Aucune zone scannée")
    item = db.get(CopyItem, resp.copy_item_id)
    copy = db.get(Copy, item.copy_id) if item else None
    if not copy:
        raise HTTPException(404)
    path = _zone_crop_path(copy.assessment_id, resp.zone_id)
    if not path.exists():
        raise HTTPException(404, "Crop indisponible")
    return FileResponse(path, media_type="image/png")


class ResolveIn(BaseModel):
    action: str          # accept | set_score | set_ratio | cancel_item | set_cells | correct_ocr | rescan
    score: float | None = None
    # fraction du barème attribuée par un raccourci (1 = tous les points,
    # 2/3, 1/3, 0) — appliquée au max_score interne, si bien que
    # earned_points = ratio × barème exactement (cf. services.scoring)
    ratio: float | None = None
    # correction CASE PAR CASE d'un tableau / de cases à trous : un booléen par
    # case NON-"given" (ordre ligne par ligne). Le script en déduit le barème
    # (points = nombre de cases justes), le professeur ne saisit que Juste/Faux.
    cell_verdicts: list[bool] | None = None
    corrected_text: str | None = None
    note: str = ""


def _apply_resolution(db: Session, resp: StudentResponse, body: ResolveIn) -> dict:
    """Enregistre une décision professeur (append-only, RM-006) sur une réponse,
    et clôt la revue automatique éventuellement ouverte dessus. Partagé par la
    correction des réponses signalées ET la relecture de toutes les réponses."""
    old = _latest_decision_for_response(db, resp.id)
    if old is None:
        raise HTTPException(404, "Aucune décision à corriger pour cette réponse")

    score = old.score
    max_score = old.max_score
    evidence = {"previous_decision": old.id, "note": body.note}
    if body.action == "set_score":
        if body.score is None:
            raise HTTPException(422, "score requis")
        score = min(max(0.0, body.score), old.max_score)
    elif body.action == "set_ratio":
        if body.ratio is None:
            raise HTTPException(422, "ratio requis")
        score = min(max(0.0, body.ratio), 1.0) * old.max_score
    elif body.action == "cancel_item":
        # question annulée : hors barème (numérateur ET dénominateur, § barème)
        score = 0.0
        max_score = 0.0
    elif body.action == "set_cells":
        # validation case par case : le barème se recalcule tout seul depuis les
        # verdicts Juste/Faux du professeur (points = nombre de cases justes).
        if body.cell_verdicts is None:
            raise HTTPException(422, "cell_verdicts requis")
        item = db.get(CopyItem, resp.copy_item_id)
        flat = [c for row in ((item.expected_json or {}).get("cells") or [])
                for c in row if not c.get("given")] if item else []
        verdicts = [bool(v) for v in body.cell_verdicts]
        if len(verdicts) != len(flat):
            raise HTTPException(422, "nombre de cases incohérent avec l'exercice")
        score = float(sum(verdicts))
        max_score = float(len(verdicts))
        evidence["cell_verdicts"] = verdicts
        # réécrit le texte de CHAQUE case selon le verdict (juste → valeur
        # canonique, faux → vide) pour que la marque ✓/✗ imprimée par l'overlay
        # (dérivée du texte de cellule via grading.cell_marks) reste cohérente
        # avec la note. Attempt « teacher » : devient le plus récent, fait foi.
        if resp.zone_id:
            corrected = [grading.cell_reference_text(c) if ok else ""
                         for c, ok in zip(flat, verdicts)]
            db.add(OcrAttempt(zone_id=resp.zone_id, provider="teacher",
                              raw_json={"cells": corrected}, confidence=1.0))
    if body.corrected_text is not None:
        resp.final_text = body.corrected_text

    new = GradingDecision(response_id=resp.id, source="teacher",
                          score=score, max_score=max_score,
                          confidence=1.0, tier="D",
                          reason_code=f"teacher_{body.action}", status="validated",
                          evidence_json=evidence)
    old.status = "revised"
    db.add(new)
    review = _open_review_for_response(db, resp.id)
    if review is not None:
        review.resolution = body.action
        review.note = body.note
        review.resolved_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


@router.post("/reviews/{review_id}/resolve")
def resolve_review(review_id: str, body: ResolveIn, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    r = db.get(ManualReview, review_id)
    if not r or r.resolved_at:
        raise HTTPException(404, "Revue introuvable ou déjà résolue")
    old = db.get(GradingDecision, r.decision_id)
    resp = db.get(StudentResponse, old.response_id)
    return _apply_resolution(db, resp, body)


@router.post("/responses/{response_id}/resolve")
def resolve_response(response_id: str, body: ResolveIn, db: Session = Depends(get_db),
                     user: User = Depends(current_user)):
    """Corrige/ajuste une réponse par son id — voie de la relecture « toutes les
    réponses » (le professeur reste maître de la note, même sans revue ouverte)."""
    resp = db.get(StudentResponse, response_id)
    if not resp:
        raise HTTPException(404, "Réponse introuvable")
    return _apply_resolution(db, resp, body)


@router.post("/batches/{batch_id}/retry")
def retry_batch(batch_id: str, tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Relance un lot bloqué — bouton de déblocage côté professeur (§ « proposer
    une action quand la pipeline est bloquée »). Choisit AUTOMATIQUEMENT quoi
    reprendre selon l'endroit du blocage :

    - blocage après la validation (résultats acquis, seuls les overlays ont
      échoué) → régénère uniquement les copies corrigées, sans refaire l'OCR ;
    - blocage plus tôt (lecture/correction interrompue) → relance `process_batch`,
      idempotent : il ne re-corrige pas une réponse déjà notée et reprend où il
      s'était arrêté.
    """
    b = db.get(ScanBatch, batch_id)
    if not b:
        raise HTTPException(404)
    b.error = None
    db.commit()
    progress = b.progress_json or {}
    if "finalized" in progress and b.status != "overlay_ready":
        tasks.add_task(_run_build_overlays, b.id)
    else:
        tasks.add_task(_run_pipeline, b.id)
    return {"ok": True, "status": b.status}


@router.delete("/batches/{batch_id}")
def reset_batch(batch_id: str, db: Session = Depends(get_db)):
    """« Effacer la correction » / « Recommencer » : supprime définitivement ce
    lot de scans et TOUT ce qui en dérive (réponses, décisions, revues, notes,
    images recadrées, scan original, overlays) et remet les copies à « generated ».
    Le sujet réapparaît « en attente de scan », prêt pour un nouveau dépôt propre.
    Réservé à la correction du professeur ; identique à la suppression de l'onglet
    Paramètres → Données (services.data_admin.delete_scan_batch)."""
    from ..services import data_admin

    b = db.get(ScanBatch, batch_id)
    if not b:
        raise HTTPException(404)
    result = data_admin.delete_scan_batch(db, b)
    db.commit()
    return {"ok": True, **result}


@router.post("/batches/{batch_id}/finalize")
def finalize(batch_id: str, db: Session = Depends(get_db)):
    b = db.get(ScanBatch, batch_id)
    if not b:
        raise HTTPException(404)
    try:
        result = finalize_batch(db, b)
    except ValueError as e:
        raise HTTPException(409, str(e))
    db.commit()
    return result


@router.post("/batches/{batch_id}/overlays")
def overlays(batch_id: str, db: Session = Depends(get_db)):
    b = db.get(ScanBatch, batch_id)
    if not b:
        raise HTTPException(404)
    if b.status != "finalized" and "finalized" not in (b.progress_json or {}):
        raise HTTPException(409, "Finaliser le lot avant de générer les overlays")
    path = build_overlays(db, b)
    db.commit()
    return {"path": path, "download": f"/api/assessments/{b.assessment_id}/files/correction_overlay.pdf"}


class BatchFlagsIn(BaseModel):
    overlay_printed: bool | None = None
    overlay_distributed: bool | None = None


@router.patch("/batches/{batch_id}")
def update_batch_flags(batch_id: str, body: BatchFlagsIn, db: Session = Depends(get_db)):
    """Cases à cocher Imprimé / Distribué (§9.5) : suivi manuel post-overlay."""
    b = db.get(ScanBatch, batch_id)
    if not b:
        raise HTTPException(404)
    if body.overlay_printed is not None:
        b.overlay_printed = body.overlay_printed
    if body.overlay_distributed is not None:
        b.overlay_distributed = body.overlay_distributed
    db.commit()
    return _batch_view(db, b)
