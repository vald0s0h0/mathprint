"""Modèle de données MathPrint — cf. cahier des charges §10.

Conventions :
- clés primaires UUID (stockées en texte pour compatibilité SQLite/PostgreSQL) ;
- dates en UTC ;
- événements pédagogiques et décisions de correction en append-only ;
- les fichiers lourds restent sur le volume (file_objects ne stocke que les métadonnées).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def uid() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- identité

class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String, unique=True)
    password_hash: Mapped[str] = mapped_column(String)
    display_name: Mapped[str] = mapped_column(String, default="")
    role: Mapped[str] = mapped_column(String, default="teacher")  # admin | teacher | viewer
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SchoolYear(Base):
    __tablename__ = "school_years"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    label: Mapped[str] = mapped_column(String)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class SchoolClass(Base):
    __tablename__ = "classes"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    school_year_id: Mapped[str | None] = mapped_column(ForeignKey("school_years.id"), nullable=True)
    name: Mapped[str] = mapped_column(String)
    grade_level: Mapped[str] = mapped_column(String, default="5e")  # 6e/5e/4e/3e
    teacher_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    students: Mapped[list["Student"]] = relationship(back_populates="school_class")


class Student(Base):
    __tablename__ = "students"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    class_id: Mapped[str | None] = mapped_column(ForeignKey("classes.id"), nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    first_name: Mapped[str] = mapped_column(String)
    last_name: Mapped[str] = mapped_column(String)
    # Pseudonyme technique : seule identité transmise aux API externes (RM-010)
    llm_pseudonym: Mapped[str] = mapped_column(String, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    level_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    school_class: Mapped["SchoolClass | None"] = relationship(back_populates="students")


# ------------------------------------------------------- référentiel pédagogique

class CompetencyFramework(Base):
    __tablename__ = "competency_frameworks"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    grade_level: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    version: Mapped[str] = mapped_column(String, default="1.0")
    status: Mapped[str] = mapped_column(String, default="draft")  # draft | published | archived
    source: Mapped[str] = mapped_column(String, default="local")


class Competency(Base):
    __tablename__ = "competencies"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    framework_id: Mapped[str] = mapped_column(ForeignKey("competency_frameworks.id"))
    code: Mapped[str] = mapped_column(String)
    label: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text, default="")
    parent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    # hiérarchie du programme officiel : domaine > thème > objectif
    domain_code: Mapped[str] = mapped_column(String, default="")
    domain_name: Mapped[str] = mapped_column(String, default="")
    theme_code: Mapped[str] = mapped_column(String, default="")
    theme_name: Mapped[str] = mapped_column(String, default="")


class LessonSnippet(Base):
    __tablename__ = "lesson_snippets"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    competency_id: Mapped[str] = mapped_column(ForeignKey("competencies.id"))
    level_min: Mapped[int] = mapped_column(Integer, default=1)
    level_max: Mapped[int] = mapped_column(Integer, default=10)
    title: Mapped[str] = mapped_column(String)
    content_latex: Mapped[str] = mapped_column(Text, default="")
    example_latex: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[str] = mapped_column(String, default="1.0")
    validated: Mapped[bool] = mapped_column(Boolean, default=False)
    # Vérification croisée Claude
    verifier_model: Mapped[str] = mapped_column(String, default="")
    verifier_verdict_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # Figure illustrative optionnelle
    figure_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Status: active ou retired
    status: Mapped[str] = mapped_column(String, default="active")  # active | retired
    # Rappel structuré v3 : {essentiel, methode[], exemple{enonce,etapes[],resultat}, astuce}
    blocks_json: Mapped[dict] = mapped_column(JSON, default=dict)


class ExerciseCatalog(Base):
    __tablename__ = "exercise_catalog"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    provider: Mapped[str] = mapped_column(String, default="builtin")  # builtin | mathalea
    provider_ref: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    grade_level: Mapped[str] = mapped_column(String)
    difficulty: Mapped[int] = mapped_column(Integer, default=5)  # 1-10
    response_type: Mapped[str] = mapped_column(String)  # qcm_single | qcm_multiple | short_text | multiline_text
    expected_schema: Mapped[str] = mapped_column(String, default="integer")  # integer|rational|expression|text|steps
    automation_tier: Mapped[str] = mapped_column(String, default="auto")  # auto|auto_with_llm|review_required|manual
    params_json: Mapped[dict] = mapped_column(JSON, default=dict)


class GeneratedExercise(Base):
    """Banque d'exercices créés par DeepSeek : un exercice concret et validé
    par couple compétence × niveau de difficulté (1-5), stocké pour réutilisation."""
    __tablename__ = "generated_exercises"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    competency_id: Mapped[str] = mapped_column(ForeignKey("competencies.id"))
    difficulty_level: Mapped[int] = mapped_column(Integer)  # 1-5
    variant: Mapped[int] = mapped_column(Integer, default=0)
    statement: Mapped[str] = mapped_column(Text)
    correction: Mapped[str] = mapped_column(Text, default="")
    response_type: Mapped[str] = mapped_column(String, default="short_text")
    expected_json: Mapped[dict] = mapped_column(JSON, default=dict)
    grading_json: Mapped[dict] = mapped_column(JSON, default=dict)
    model: Mapped[str] = mapped_column(String, default="")
    prompt_version: Mapped[str] = mapped_column(String, default="1")
    status: Mapped[str] = mapped_column(String, default="active")  # active | retired
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    # Vérification croisée Claude
    verifier_model: Mapped[str] = mapped_column(String, default="")
    verifier_verdict_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # Figure illustrative optionnelle
    figure_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Provenance (deepseek | mathalea) et nature (application | probleme)
    source: Mapped[str] = mapped_column(String, default="deepseek")
    kind: Mapped[str] = mapped_column(String, default="application")
    # Scores qualité du vérificateur (justesse, adéquation compétence/niveau, clarté)
    quality_json: Mapped[dict] = mapped_column(JSON, default=dict)


class ExerciseCompetency(Base):
    __tablename__ = "exercise_competencies"
    exercise_id: Mapped[str] = mapped_column(ForeignKey("exercise_catalog.id"), primary_key=True)
    competency_id: Mapped[str] = mapped_column(ForeignKey("competencies.id"), primary_key=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    evidence_strength: Mapped[float] = mapped_column(Float, default=1.0)


# -------------------------------------------------- évaluations, copies, documents

class Assessment(Base):
    __tablename__ = "assessments"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    class_id: Mapped[str] = mapped_column(ForeignKey("classes.id"))
    type: Mapped[str] = mapped_column(String, default="training")  # control | training
    title: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="draft")  # draft|generated|printed|scanning|finalized
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_min: Mapped[int] = mapped_column(Integer, default=55)  # conservé, informatif
    pages_target: Mapped[int] = mapped_column(Integer, default=1)   # 1=recto, 2=recto/verso…
    duplex: Mapped[bool] = mapped_column(Boolean, default=False)
    personalization_mode: Mapped[str] = mapped_column(String, default="common")
    # common | equivalent_variants | guided_individual | free_individual
    blueprint_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Copy(Base):
    __tablename__ = "copies"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"))
    seed: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="generated")  # generated|printed|scanned|graded|finalized|absent
    total_pages: Mapped[int] = mapped_column(Integer, default=1)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    # cache {progress, synthesis} de la zone Appréciation (§ appréciation) —
    # calculé une fois à la finalisation, réutilisé pour une réimpression sans
    # re-facturer l'appel Claude Haiku.
    appreciation_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class CopyItem(Base):
    __tablename__ = "copy_items"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    copy_id: Mapped[str] = mapped_column(ForeignKey("copies.id"))
    catalog_id: Mapped[str] = mapped_column(ForeignKey("exercise_catalog.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    difficulty: Mapped[int] = mapped_column(Integer, default=5)
    response_type: Mapped[str] = mapped_column(String)
    statement: Mapped[str] = mapped_column(Text)        # instantané énoncé (RM-014)
    correction: Mapped[str] = mapped_column(Text)       # instantané correction
    expected_json: Mapped[dict] = mapped_column(JSON, default=dict)   # réponse(s) attendue(s)
    grading_json: Mapped[dict] = mapped_column(JSON, default=dict)    # barème, tolérances
    lesson_snippet_id: Mapped[str | None] = mapped_column(String, nullable=True)


class DocumentPage(Base):
    __tablename__ = "document_pages"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    copy_id: Mapped[str] = mapped_column(ForeignKey("copies.id"))
    page_no: Mapped[int] = mapped_column(Integer)
    side: Mapped[str] = mapped_column(String, default="recto")  # recto | verso
    template_version: Mapped[str] = mapped_column(String, default="1")
    qr_payload: Mapped[str] = mapped_column(String, default="")   # payload signé HMAC
    hmac_version: Mapped[str] = mapped_column(String, default="1")


class ResponseZone(Base):
    __tablename__ = "response_zones"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    page_id: Mapped[str] = mapped_column(ForeignKey("document_pages.id"))
    item_id: Mapped[str] = mapped_column(ForeignKey("copy_items.id"))
    type: Mapped[str] = mapped_column(String)
    # coordonnées canoniques A4 en points PDF (§5.5)
    x_pt: Mapped[float] = mapped_column(Float)
    y_pt: Mapped[float] = mapped_column(Float)
    w_pt: Mapped[float] = mapped_column(Float)
    h_pt: Mapped[float] = mapped_column(Float)
    padding_pt: Mapped[float] = mapped_column(Float, default=4.0)
    meta_json: Mapped[dict] = mapped_column(JSON, default=dict)  # ex: positions des cases QCM


class FileObject(Base):
    __tablename__ = "file_objects"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    owner_type: Mapped[str] = mapped_column(String)
    owner_id: Mapped[str] = mapped_column(String)
    storage_path: Mapped[str] = mapped_column(String)
    sha256: Mapped[str] = mapped_column(String, default="")
    mime: Mapped[str] = mapped_column(String, default="application/pdf")
    size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


# ------------------------------------------------------------- scans & correction

class ScanBatch(Base):
    __tablename__ = "scan_batches"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"))
    source_file_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Machine d'états §6.1
    status: Mapped[str] = mapped_column(String, default="uploaded")
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[str | None] = mapped_column(String, nullable=True)
    progress_json: Mapped[dict] = mapped_column(JSON, default=dict)  # paliers verts/orange pour l'UI
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # suivi manuel post-overlay (cases à cocher, §9.5) : le lot grise sa ligne
    # une fois l'overlay imprimé ET distribué aux élèves.
    overlay_printed: Mapped[bool] = mapped_column(Boolean, default=False)
    overlay_distributed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ScannedPage(Base):
    __tablename__ = "scanned_pages"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    batch_id: Mapped[str] = mapped_column(ForeignKey("scan_batches.id"))
    source_index: Mapped[int] = mapped_column(Integer)
    page_id: Mapped[str | None] = mapped_column(String, nullable=True)  # nul tant que non identifiée (RM-001)
    original_file_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending|identified|registered|blocked
    quality_json: Mapped[dict] = mapped_column(JSON, default=dict)


class SandboxUpload(Base):
    """Fichier brut déposé au bac à sable (§5c) : PDFs et images en vrac,
    traités page par page, dédupliqués par sha256 du fichier puis par
    page_id déjà enregistrée (cf. services/sandbox.py)."""
    __tablename__ = "sandbox_uploads"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    uploaded_by: Mapped[str | None] = mapped_column(String, nullable=True)
    original_filename: Mapped[str] = mapped_column(String)
    sha256: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="processing")
    # processing | processed | duplicate_rejected | error
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class OcrAttempt(Base):
    __tablename__ = "ocr_attempts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    zone_id: Mapped[str] = mapped_column(ForeignKey("response_zones.id"))
    scanned_page_id: Mapped[str | None] = mapped_column(String, nullable=True)
    provider: Mapped[str] = mapped_column(String, default="mathpix")  # mathpix | cv_local | mock
    variant: Mapped[int] = mapped_column(Integer, default=1)
    raw_json: Mapped[dict] = mapped_column(JSON, default=dict)
    latex: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class StudentResponse(Base):
    __tablename__ = "student_responses"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    copy_item_id: Mapped[str] = mapped_column(ForeignKey("copy_items.id"))
    zone_id: Mapped[str | None] = mapped_column(String, nullable=True)
    normalized_json: Mapped[dict] = mapped_column(JSON, default=dict)
    selected_choices: Mapped[list] = mapped_column(JSON, default=list)
    final_text: Mapped[str] = mapped_column(Text, default="")


class GradingDecision(Base):
    __tablename__ = "grading_decisions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    response_id: Mapped[str] = mapped_column(ForeignKey("student_responses.id"))
    source: Mapped[str] = mapped_column(String)  # deterministic | deepseek | teacher
    score: Mapped[float] = mapped_column(Float, default=0.0)
    max_score: Mapped[float] = mapped_column(Float, default=1.0)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    reason_code: Mapped[str] = mapped_column(String, default="")
    tier: Mapped[str] = mapped_column(String, default="A")  # échelle de décision §6.4
    evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String, default="auto")  # auto|review_pending|validated|revised
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ManualReview(Base):
    __tablename__ = "manual_reviews"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    decision_id: Mapped[str] = mapped_column(ForeignKey("grading_decisions.id"))
    category: Mapped[str] = mapped_column(String)  # rature|double_coche|ocr_ambigu|scan_faible|bareme
    priority: Mapped[int] = mapped_column(Integer, default=5)
    resolution: Mapped[str | None] = mapped_column(String, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Annotation(Base):
    __tablename__ = "annotations"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    copy_id: Mapped[str] = mapped_column(ForeignKey("copies.id"))
    page_id: Mapped[str | None] = mapped_column(String, nullable=True)
    zone_id: Mapped[str | None] = mapped_column(String, nullable=True)
    type: Mapped[str] = mapped_column(String, default="correction")
    content: Mapped[str] = mapped_column(Text, default="")
    color: Mapped[str] = mapped_column(String, default="#C62828")
    geometry_json: Mapped[dict] = mapped_column(JSON, default=dict)


# ------------------------------------------------------ progression & mémorisation

class CompetencyEvidence(Base):
    __tablename__ = "competency_evidence"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"))
    competency_id: Mapped[str] = mapped_column(ForeignKey("competencies.id"))
    item_id: Mapped[str | None] = mapped_column(String, nullable=True)
    mode: Mapped[str] = mapped_column(String, default="training")  # control | training
    score_ratio: Mapped[float] = mapped_column(Float)
    difficulty: Mapped[int] = mapped_column(Integer, default=5)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class StudentCompetencyState(Base):
    __tablename__ = "student_competency_state"
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"), primary_key=True)
    competency_id: Mapped[str] = mapped_column(ForeignKey("competencies.id"), primary_key=True)
    mastery: Mapped[float] = mapped_column(Float, default=0.0)      # 0-1
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    stability: Mapped[float] = mapped_column(Float, default=1.0)    # jours (modèle type FSRS simplifié)
    memory_difficulty: Mapped[float] = mapped_column(Float, default=5.0)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CompetencyStateHistory(Base):
    __tablename__ = "competency_state_history"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    student_id: Mapped[str] = mapped_column(String)
    competency_id: Mapped[str] = mapped_column(String)
    before_json: Mapped[dict] = mapped_column(JSON, default=dict)
    after_json: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class StudentLevel(Base):
    __tablename__ = "student_levels"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"))
    level: Mapped[int] = mapped_column(Integer)          # 1-10, privé professeur (RM-007)
    proposed_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String, default="deterministic")  # deterministic|deepseek|teacher
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    valid_from: Mapped[datetime] = mapped_column(DateTime, default=now)


class StudentReport(Base):
    __tablename__ = "student_reports"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"))
    period: Mapped[str] = mapped_column(String)
    prompt_version: Mapped[str] = mapped_column(String, default="1")
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String, default="draft")  # draft|approved|exported
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


# -------------------------------------------------------- paramètres, coûts, audit

class ProviderConfig(Base):
    __tablename__ = "provider_configs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    provider: Mapped[str] = mapped_column(String, unique=True)  # mathpix | deepseek | anthropic
    model: Mapped[str] = mapped_column(String, default="")
    encrypted_secret: Mapped[str] = mapped_column(String, default="")  # jamais renvoyé intégralement
    limits_json: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=False)


class ApiUsageEvent(Base):
    __tablename__ = "api_usage_events"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    provider: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String, default="")
    operation: Mapped[str] = mapped_column(String)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    units: Mapped[int] = mapped_column(Integer, default=0)  # requêtes Mathpix
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)
    correlation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Printer(Base):
    __tablename__ = "printers"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String, unique=True)
    uri: Mapped[str] = mapped_column(String, default="")
    protocol: Mapped[str] = mapped_column(String, default="ipp")
    capabilities_json: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class CalibrationProfile(Base):
    __tablename__ = "calibration_profiles"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    printer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    printer_name: Mapped[str] = mapped_column(String, default="")
    scanner_name: Mapped[str] = mapped_column(String, default="")
    paper: Mapped[str] = mapped_column(String, default="A4")
    side: Mapped[str] = mapped_column(String, default="recto")
    offset_x_mm: Mapped[float] = mapped_column(Float, default=0.0)
    offset_y_mm: Mapped[float] = mapped_column(Float, default=0.0)
    scale_x: Mapped[float] = mapped_column(Float, default=1.0)
    scale_y: Mapped[float] = mapped_column(Float, default=1.0)
    rotation_deg: Mapped[float] = mapped_column(Float, default=0.0)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    type: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending|running|done|failed
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    actor_id: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String)
    entity_type: Mapped[str] = mapped_column(String, default="")
    entity_id: Mapped[str] = mapped_column(String, default="")
    before_json: Mapped[dict] = mapped_column(JSON, default=dict)
    after_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value_json: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str | None] = mapped_column(String, nullable=True)
