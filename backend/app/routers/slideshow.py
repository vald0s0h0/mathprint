"""Diaporama de correction au tableau d'un sujet (cf. services.slideshow)."""
import unicodedata
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import ScanBatch
from ..services import slideshow

router = APIRouter(prefix="/api/scans", tags=["scans"], dependencies=[Depends(current_user)])


@router.get("/batches/{batch_id}/slideshow.html", response_class=HTMLResponse)
def batch_slideshow(batch_id: str, download: bool = False, db: Session = Depends(get_db)):
    """Page HTML autonome (images intégrées). `download=1` : en pièce jointe, pour
    l'export ; sinon affichée par l'application dans un cadre plein écran."""
    b = db.get(ScanBatch, batch_id)
    if not b:
        raise HTTPException(404)
    try:
        data = slideshow.build(db, b)
    finally:
        db.rollback()      # lecture seule
    if not data["responses"]:
        raise HTTPException(409, "Aucune copie corrigée dans ce lot")
    headers = {"Cache-Control": "no-store"}
    if download:
        name = slideshow.filename(data)
        ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode() or "diaporama.html"
        headers["Content-Disposition"] = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"
    return HTMLResponse(slideshow.render_html(data), headers=headers)
