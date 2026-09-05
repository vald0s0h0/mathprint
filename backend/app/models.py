"""Modèle de données MathPrint — cf. cahier des charges §10.

Conventions :
- clés primaires UUID (stockées en texte pour compatibilité SQLite/PostgreSQL) ;
- dates en UTC ;
- événements pédagogiques et décisions de correction en append-only ;
- les fichiers lourds restent sur le volume (file_objects ne stocke que les métadonnées).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
)
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
    # teacher = utilisateur classique (nom interne historique conservé pour
    # compatibilité) ; les correcteurs auront leur interface dans une version
    # ultérieure.
    role: Mapped[str] = mapped_column(String, default="teacher")  # admin | teacher | corrector
    # Un abonnement ne concerne que les utilisateurs classiques :
    # free (100 copies/mois) | pro | max. Admins et correcteurs : NULL.
    subscription_plan: Mapped[str | None] = mapped_column(String, nullable=True)
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
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    students: Mapped[list["Student"]] = relationship(back_populates="school_class")


class Student(Base):
    __tablename__ = "students"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    class_id: Mapped[str | None] = mapped_column(ForeignKey("classes.id"), nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    # Identité telle qu'elle doit être affichée et imprimée. Elle est
    # volontairement indivisible : « Camille », « Camille B. », « Durand »…
    name: Mapped[str] = mapped_column(String)
    # Ordre de la liste de classe (saisie initiale / export Pronote futur).
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    # Adaptation d'impression individuelle.
    dyslexic: Mapped[bool] = mapped_column(Boolean, default=False)
    # Pseudonyme technique : seule identité transmise aux API externes (RM-010)
    llm_pseudonym: Mapped[str] = mapped_column(String, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    level_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    # Il y avait ici une « trame » d'exercices écrite par le LLM à la fin de
    # chaque correction. Elle a été supprimée : au moment où elle était écrite,
    # rien ne disait QUAND le sujet suivant serait lancé, alors que c'est
    # exactement le délai écoulé qui décide de ce qu'il faut retravailler. Le
    # suivi est désormais factuel (copy_item_results) et la trame est calculée
    # au moment de composer le sujet, avec la vraie date (services
    # .student_history).
    school_class: Mapped["SchoolClass | None"] = relationship(back_populates="students")


# ------------------------------------------------------- référentiel pédagogique

class CompetencyFramework(Base):
    """Un référentiel = un niveau (`grade_level`, ex. "5e") pour un programme
    donné. `cycle` est le cycle du programme (3 ou 4), `program_year` l'année
    de programme officielle (l'Éducation nationale peut en changer tous les
    ~10 ans, ex. 2026) — à distinguer de `SchoolYear` qui est l'année
    scolaire d'une classe."""
    __tablename__ = "competency_frameworks"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    grade_level: Mapped[str] = mapped_column(String)
    cycle: Mapped[int | None] = mapped_column(Integer, nullable=True)
    program_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String)
    version: Mapped[str] = mapped_column(String, default="1.0")
    status: Mapped[str] = mapped_column(String, default="draft")  # draft | published | archived
    source: Mapped[str] = mapped_column(String, default="local")


class Competency(Base):
    """Une compétence est une feuille de la hiérarchie à 3 niveaux du
    référentiel :
      - H1 = domaine (`domain_code`/`domain_name`, ex. "A" / "Nombres et calculs")
      - H2 = chapitre (`chapter_code`/`chapter_name`, ex. "A1" / "Opérations")
      - H3 = la compétence elle-même (`label`, ex. "Automatismes")
    `short_id` reprend la numérotation du sommaire (ex. "A1.1"), affiché
    partout dans la plateforme accompagné d'au moins le chapitre (H2) : un
    libellé de compétence isolé (ex. "Automatismes") ne suffit pas à savoir
    de quoi il s'agit. `code` reste l'identifiant technique legacy (verbeux,
    non affiché) pour les niveaux pas encore migrés vers ce modèle."""
    __tablename__ = "competencies"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    framework_id: Mapped[str] = mapped_column(ForeignKey("competency_frameworks.id"))
    code: Mapped[str] = mapped_column(String)
    short_id: Mapped[str] = mapped_column(String, default="")
    label: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text, default="")
    parent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    domain_code: Mapped[str] = mapped_column(String, default="")
    domain_name: Mapped[str] = mapped_column(String, default="")
    chapter_code: Mapped[str] = mapped_column(String, default="")
    chapter_name: Mapped[str] = mapped_column(String, default="")


class ExerciseCatalog(Base):
    __tablename__ = "exercise_catalog"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    provider: Mapped[str] = mapped_column(String, default="builtin")  # builtin
    provider_ref: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    grade_level: Mapped[str] = mapped_column(String)
    difficulty: Mapped[int] = mapped_column(Integer, default=5)  # 1-10
    response_type: Mapped[str] = mapped_column(String)  # qcm_single | qcm_multiple | short_text | multiline_text | table_fill | matching | manual_drawing
    expected_schema: Mapped[str] = mapped_column(String, default="integer")  # integer|rational|expression|text|steps
    automation_tier: Mapped[str] = mapped_column(String, default="auto")  # auto|auto_with_llm|review_required|manual
    params_json: Mapped[dict] = mapped_column(JSON, default=dict)


class GeneratedExercise(Base):
    """Banque d'exercices créés par DeepSeek : un exercice concret et validé
    par couple compétence × niveau de difficulté (1-3 : facile/moyen/difficile),
    stocké pour réutilisation."""
    __tablename__ = "generated_exercises"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    competency_id: Mapped[str] = mapped_column(ForeignKey("competencies.id"))
    difficulty_level: Mapped[int] = mapped_column(Integer)  # 1-3 (cf. exercise_gen.DIFFICULTY_LEVELS)
    variant: Mapped[int] = mapped_column(Integer, default=0)
    statement: Mapped[str] = mapped_column(Text)
    correction: Mapped[str] = mapped_column(Text, default="")
    response_type: Mapped[str] = mapped_column(String, default="short_text")
    # qcm_single | qcm_multiple | short_text | multiline_text | table_fill | matching | manual_drawing
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
    # Provenance (deepseek) et nature (application | probleme)
    source: Mapped[str] = mapped_column(String, default="deepseek")
    kind: Mapped[str] = mapped_column(String, default="application")
    # Scores qualité du vérificateur (justesse, adéquation compétence/niveau, clarté)
    quality_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # Blocs OCR Mistral bruts ({"blocks": [...]}) dont provient cette ligne
    # (source="sesamaths" uniquement), avant adaptation — affichage
    # "avant/après" en banque (cf. services.sesamaths._to_candidate)
    raw_extract_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


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
    status: Mapped[str] = mapped_column(String, default="draft")
    # draft|queued|generating|ready|error|printed|scanning|finalized
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_min: Mapped[int] = mapped_column(Integer, default=55)  # conservé, informatif
    pages_target: Mapped[int] = mapped_column(Integer, default=1)   # 1=recto, 2=recto/verso…
    duplex: Mapped[bool] = mapped_column(Boolean, default=False)
    personalization_mode: Mapped[str] = mapped_column(String, default="common")
    # common | common_variants | individual
    blueprint_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # {"competency_ids": [...]} choisi à l'étape Exercices de l'assistant
    # Base de scoring choisie à l'étape Contexte : 5, 10 ou 20 points pour le
    # sujet entier (§ barème). Les entraînements sont scorés mais leur note
    # n'est pas imprimée sur la copie.
    note_base: Mapped[int] = mapped_column(Integer, default=20)
    # Mémo professeur : les notes de cette colonne ont été saisies dans
    # Pronote. Purement organisationnel, sans effet sur le scoring.
    pronote_entered: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Copy(Base):
    __tablename__ = "copies"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"))
    seed: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="generated")  # generated|printed|scanned|graded|finalized|absent
    total_pages: Mapped[int] = mapped_column(Integer, default=1)
    # variante servie à cet élève sur un sujet composé à la main (assistant
    # « Créer mon sujet ») : "A"/"B"/… en anti-triche, "facile"/"moyen"/
    # "difficile" en variantes par niveau. Vide sur un sujet automatique.
    variant_key: Mapped[str] = mapped_column(String, default="")
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
    # Ligne de banque RÉELLEMENT servie (generated_exercises.id). Sans elle,
    # `catalog_id` ne désigne qu'une COMPÉTENCE (une seule entrée catalogue par
    # compétence, cf. exercise_gen.ensure_catalog_ref) : impossible de savoir
    # ensuite quel exercice l'élève a déjà vu, donc impossible de ne pas le lui
    # resservir. Volontairement SANS ForeignKey, comme CopyItemResult
    # .competency_id : la purge de la banque Indigo supprime des
    # generated_exercises, et une FK ferait soit échouer la purge, soit
    # détruire l'historique en cascade.
    generated_exercise_id: Mapped[str | None] = mapped_column(String, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer)
    difficulty: Mapped[int] = mapped_column(Integer, default=5)
    response_type: Mapped[str] = mapped_column(String)
    # qcm_single | qcm_multiple | short_text | multiline_text | table_fill | matching | manual_drawing
    statement: Mapped[str] = mapped_column(Text)        # instantané énoncé (RM-014)
    correction: Mapped[str] = mapped_column(Text)       # instantané correction
    expected_json: Mapped[dict] = mapped_column(JSON, default=dict)   # réponse(s) attendue(s)
    grading_json: Mapped[dict] = mapped_column(JSON, default=dict)    # barème, tolérances
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


# --------------------------------------------------- Sésamaths (extraction manuels PDF)

class SesamathsManual(Base):
    """Un manuel scolaire enregistré (un par `grade_level`, ex. "5e"). La
    table des matières est parsée une fois puis mise en cache dans `toc_json`
    (chapitre -> nom + page imprimée de départ, cf. services.sesamaths_pdf)."""
    __tablename__ = "sesamaths_manuals"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    grade_level: Mapped[str] = mapped_column(String, unique=True)
    file_object_id: Mapped[str | None] = mapped_column(ForeignKey("file_objects.id"), nullable=True)
    sha256: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="missing")  # missing | ready | error
    toc_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error_message: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class SesamathsChapterExtraction(Base):
    """État d'extraction d'une Série d'un manuel — une ligne par (manual_id,
    chapter_code=code compétence, cf. services.sesamaths._extraction_key).
    `step` porte la machine à états à 2 phases : pending (rien fait) ->
    extracted (JSON brut Mistral OCR en cache dans `raw_json`, pas encore
    adapté) -> done (`validated_json` = pool prêt). Une Série tient en 1-3
    pages en général, la reprise sur erreur se fait donc au niveau de la
    Série entière, pas page par page (§ Sésamaths)."""
    __tablename__ = "sesamaths_chapter_extractions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    manual_id: Mapped[str] = mapped_column(ForeignKey("sesamaths_manuals.id"))
    chapter_code: Mapped[str] = mapped_column(String)  # ex. "A1"
    step: Mapped[str] = mapped_column(String, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(Text, default="")
    # {start_index, end_index, series_number, series_name, versions:{extract,adapt,schema}}
    page_range_json: Mapped[dict] = mapped_column(JSON, default=dict)
    raw_json: Mapped[dict] = mapped_column(JSON, default=dict)          # réponse OCR Mistral brute (blocs typés)
    validated_json: Mapped[list] = mapped_column(JSON, default=list)    # candidats validés (pool de la Série)
    failed_series_json: Mapped[list] = mapped_column(JSON, default=list)  # non utilisé (granularité Série)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class SesamathsLlmCache(Base):
    """Cache des appels LLM Sésamaths, clé = sha256(pdf|chapitre|modèle|
    prompt_version|schéma|payload) — évite de repayer un appel identique lors
    d'une reprise sur erreur (§ Sésamaths)."""
    __tablename__ = "sesamaths_llm_cache"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    cache_key: Mapped[str] = mapped_column(String, unique=True)
    response_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


# --------------------------------------------------- Indigo (onglet Exercices, admin)

class IndigoExtraction(Base):
    """Un RUN du pipeline Exercices : l'admin choisit une ou plusieurs
    compétences 3e, des pages du manuel ÉLÈVE (énoncés) et des pages du manuel
    PROF (corrigés) ; le pipeline (file de fond dédiée, cf. services.indigo)
    passe l'OCR Mistral, segmente en exercices, découpe les crops, lit la
    couleur des badges (CV) puis met au propre via Gemini → lignes
    `IndigoExercise` en statut brouillon. Machine à états portée par `status`."""
    __tablename__ = "indigo_extractions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    grade_level: Mapped[str] = mapped_column(String, default="3e")
    # une cible = {competency_id, eleve_pages:[int], prof_pages:[int]} (index de
    # page 0-based). Plusieurs compétences peuvent être traitées dans un même run,
    # chacune avec son propre jeu de pages élève/prof.
    targets_json: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String, default="pending")    # pending|running|done|failed
    progress: Mapped[int] = mapped_column(Integer, default=0)
    progress_message: Mapped[str] = mapped_column(String, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    stats_json: Mapped[dict] = mapped_column(JSON, default=dict)      # {exercises, per_competency, ...}
    log_text: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class IndigoExercise(Base):
    """Un exercice « Indigo » repris d'un manuel : 1 exercice du manuel → 1
    ligne. Brouillon (status=draft) tant que l'admin ne l'a pas validé
    (status=validated). Les exercices VALIDÉS sont ensuite PUBLIÉS (bake) vers
    des fichiers versionnés du repo, livrés en lecture seule à tous les
    déploiements — la DB ne sert qu'à la construction sur l'instance admin."""
    __tablename__ = "indigo_exercises"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    extraction_id: Mapped[str | None] = mapped_column(
        ForeignKey("indigo_extractions.id"), nullable=True)
    competency_id: Mapped[str] = mapped_column(ForeignKey("competencies.id"))
    grade_level: Mapped[str] = mapped_column(String, default="3e")
    # --- provenance dans le manuel ---
    source_page: Mapped[int] = mapped_column(Integer, default=0)      # index de page PDF élève
    source_number: Mapped[str] = mapped_column(String, default="")    # n° imprimé de l'exercice
    order_index: Mapped[int] = mapped_column(Integer, default=0)       # ordre dans la page
    # crop (rectangle éditable, en pixels du raster à `raster_dpi`) + PNG sur disque
    crop_box_json: Mapped[dict] = mapped_column(JSON, default=dict)    # {page_index,x0,y0,x1,y1,raster_dpi,img_w,img_h}
    crop_path: Mapped[str] = mapped_column(String, default="")         # relatif à data_dir
    has_figure: Mapped[bool] = mapped_column(Boolean, default=False)
    # {page_index,x0,y0,x1,y1,raster_dpi,img_w,img_h,masks:[{x0,y0,x1,y1}]}
    # Les caches sont en coordonnées de la page originale afin de survivre à
    # un futur dé-recadrage de la figure.
    figure_box_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    figure_path: Mapped[str] = mapped_column(String, default="")
    # l'exercice a-t-il BESOIN d'un schéma/image pour être compréhensible, que
    # l'OCR en ait effectivement trouvé un ou non ? Signal de la « couche
    # supplémentaire » (indices textuels + jugement Claude) — cf. services.indigo.
    # Sert à repérer un exercice incomplet quand figure_required=True mais
    # has_figure=False (aucun crop n'a pu être rattaché, même en repli).
    figure_required: Mapped[bool] = mapped_column(Boolean, default=False)
    # --- métadonnées lues par CV (badge/titre) ---
    badge_type: Mapped[str] = mapped_column(String, default="exercice")
    # exercice | flash | expert | enigme | probleme
    difficulty: Mapped[int] = mapped_column(Integer, default=2)        # 1-3 (facile/moyen/difficile)
    badge_color_json: Mapped[dict] = mapped_column(JSON, default=dict) # {rgb, category, confidence} recalibrable
    title: Mapped[str] = mapped_column(String, default="")             # titre (problème/énigme)
    tags_json: Mapped[list] = mapped_column(JSON, default=list)        # ["Raisonner","Calculer"] (problèmes)
    calculator: Mapped[str] = mapped_column(String, default="autorisee")  # necessaire|interdite|autorisee
    # --- contenu mis au propre par Gemini ---
    statement: Mapped[str] = mapped_column(Text, default="")
    response_type: Mapped[str] = mapped_column(String, default="short_text")
    expected_json: Mapped[dict] = mapped_column(JSON, default=dict)    # answer/choices/cells (contrat app)
    # le barème vit dans grading_json["bareme_points"], nulle part ailleurs
    # (§ services.scoring) : une colonne de plus finirait par diverger, et
    # c'est déjà arrivé (`effort_points`, supprimée — cf. db._DROPPED_COLUMNS).
    grading_json: Mapped[dict] = mapped_column(JSON, default=dict)
    correction_solution: Mapped[str] = mapped_column(Text, default="")  # VRAIE solution recopiée du manuel prof
    correction_guide: Mapped[str] = mapped_column(Text, default="")     # guide d'auto-correction dérivé (overlay élève)
    # contrat app complet (statement/response_type/answer/choices/…), prêt pour rendu/publication
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    raw_ocr_json: Mapped[dict] = mapped_column(JSON, default=dict)     # blocs OCR bruts (affichage avant/après)
    # --- trio de variantes (mode « QCM only », cf. services.indigo_qcm) ---
    # Un exercice du manuel donne TROIS lignes : la version de base et deux
    # DÉRIVÉS (un plus facile, un plus difficile), pour que le même exercice
    # existe aux trois niveaux de la plateforme. « Dérivé » et pas « variante » :
    # les VARIANTES d'un sujet sont autre chose (anti-copie entre voisins).
    # `derived_from_id` pointe la ligne de BASE (NULL sur la base elle-même).
    variant_kind: Mapped[str] = mapped_column(String, default="base")   # base|facile|difficile
    derived_from_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # --- cycle de vie ---
    status: Mapped[str] = mapped_column(String, default="draft")       # draft | validated
    validated_by: Mapped[str | None] = mapped_column(String, nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    model: Mapped[str] = mapped_column(String, default="")
    prompt_version: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


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
    # suivi post-overlay (§9.5) : `overlay_printed` est posé automatiquement par
    # un envoi CUPS réussi ; la distribution reste cochée par le professeur et
    # grise alors la carte dans Sujets comme dans Corrections.
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
    # identité posée à la main par le professeur (QR/fiduciels illisibles mais
    # élève reconnu à l'œil sur l'aperçu, cf. résolution des scans bloqués) :
    # id de DocumentPage, jamais un page_id QR — process_batch la force au
    # prochain passage au lieu de retenter la lecture QR (services.worker_cv).
    manual_page_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # confirmé par le professeur comme n'étant PAS une copie réelle (mauvais
    # feuillet, page blanche happée par l'ADF...) : exclue du flux d'overlay,
    # ne bloque plus l'invariant de position (cf. services.pipeline.build_overlays)
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False)


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
    # paires [gauche,droite] détectées par CV pour un exercice "matching" (les
    # traits que l'élève a tracés à la règle) — persistées pour que la modale
    # de correction reconstruise un diagramme, pas seulement le scan photo.
    selected_pairs: Mapped[list] = mapped_column(JSON, default=list)
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
    category: Mapped[str] = mapped_column(String)  # rature|double_coche|ocr_ambigu|scan_faible|bareme|trace_dessin|points_a_relier
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


# ------------------------------------------------- résultats consolidés (suivi élève)

class CopyResult(Base):
    """Résultat d'un élève à un sujet, consolidé une fois pour toutes à la
    finalisation du lot (services.scoring.compute_copy_result) : points de
    barème obtenus, note sur la base choisie par le professeur, appréciation.

    Cette table est le SUIVI PERSONNALISÉ : sans elle, retrouver ce qu'un élève
    a obtenu à un sujet impose de rejoindre copy_items → student_responses →
    grading_decisions (append-only, il faut la dernière) et de reconstituer le
    barème de chaque exercice à chaque lecture. Elle est dérivée : on peut la
    reconstruire, jamais la corriger à la main.

    `note_raw` (exacte, avec décimales) et `note` (multiple de 0,5, arrondi au
    supérieur) coexistent délibérément : la seconde est celle qu'on imprime sur
    la copie, la première celle qu'il faut moyenner — arrondir puis moyenner
    accumule le biais d'arrondi. `note_base` = 0 désigne uniquement un ancien
    entraînement antérieur à leur scoring."""
    __tablename__ = "copy_results"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    copy_id: Mapped[str] = mapped_column(ForeignKey("copies.id"), unique=True)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"))
    points_earned: Mapped[float] = mapped_column(Float, default=0.0)
    points_total: Mapped[float] = mapped_column(Float, default=0.0)
    note_base: Mapped[int] = mapped_column(Integer, default=0)   # 5|10|20 ; 0 = historique
    note_raw: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[float | None] = mapped_column(Float, nullable=True)
    # instantané de la zone Appréciation imprimée (cf. services.appreciation),
    # recopié depuis Copy.appreciation_json à la création de l'overlay
    appreciation: Mapped[str] = mapped_column(Text, default="")
    progress_json: Mapped[dict] = mapped_column(JSON, default=dict)
    finalized_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class CopyItemResult(Base):
    """Résultat d'un élève à UN exercice d'un sujet (une ligne par exercice
    réellement corrigé, cf. CopyResult).

    `score`/`max_score` sont à l'échelle INTERNE du moteur de correction (1 par
    cellule de tableau, etc.), `bareme_points`/`points_earned` à l'échelle
    professeur — les deux sont conservées : la première dit ce qui était juste,
    la seconde ce que ça valait (cf. en-tête de services.scoring).

    C'est aussi l'HISTORIQUE que lit le moteur de sujets individuels
    (services.student_history) : quel exercice, quel dérivé, quelle réponse,
    quel jour. D'où `student_id` et `occurred_at` dénormalisés — la sélection
    interroge cette table pour chaque élève × compétence à chaque génération de
    sujet, et remonter au sujet à chaque ligne coûterait deux jointures."""
    __tablename__ = "copy_item_results"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    copy_result_id: Mapped[str] = mapped_column(ForeignKey("copy_results.id"))
    copy_item_id: Mapped[str] = mapped_column(ForeignKey("copy_items.id"))
    competency_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # dénormalisé depuis CopyResult : cf. docstring (index ci-dessous)
    student_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # ligne de banque servie, recopiée de CopyItem — NULL sur l'historique
    # antérieur à cette colonne (l'information n'a jamais été écrite : elle
    # n'est pas reconstructible, cf. migration)
    generated_exercise_id: Mapped[str | None] = mapped_column(String, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    response_type: Mapped[str] = mapped_column(String, default="")
    difficulty: Mapped[int] = mapped_column(Integer, default=5)
    # dérivé 1-3 (facile/base/difficile). `difficulty` garde l'échelle 3/6/9
    # attendue par le reste de la chaîne ; celui-ci porte le niveau tel que la
    # banque et le professeur le connaissent.
    difficulty_level: Mapped[int] = mapped_column(Integer, default=0)
    # réponse de l'élève, aplatie en texte lisible (cf. scoring.student_answer_text).
    # La ligne student_responses reste la source de vérité ; ceci est l'index.
    answer_text: Mapped[str] = mapped_column(Text, default="")
    # réussite 0-1. On stocke le RATIO et pas un booléen « juste » : le seuil à
    # partir duquel un exercice compte comme réussi appartient au moteur qui
    # lit, pas à la donnée écrite.
    success_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    # JOUR DU DEVOIR (et non de la correction) : c'est cette date que la courbe
    # de l'oubli mesure. Un lot corrigé dix jours plus tard ne doit pas offrir
    # dix jours de fraîcheur en cadeau.
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    max_score: Mapped[float] = mapped_column(Float, default=0.0)
    bareme_points: Mapped[float] = mapped_column(Float, default=0.0)
    points_earned: Mapped[float] = mapped_column(Float, default=0.0)
    # Index de l'historique : le moteur de sujets interroge cette table pour
    # chaque élève × compétence à chaque génération. Colonne de gauche =
    # student_id, si bien qu'il sert aussi les lectures « tout l'élève »
    # (onglet Historique). Déclaré ici ET recréé par la migration : `create_all`
    # ne pose les index que sur une base NEUVE, jamais sur une table existante.
    __table_args__ = (
        Index("ix_copy_item_results_student_comp", "student_id", "competency_id"),
    )


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
    # Correction ayant provoqué ce palier. Null pour les réglages manuels et
    # les niveaux historiques antérieurs au carnet de notes.
    assessment_id: Mapped[str | None] = mapped_column(String, nullable=True)
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
    provider: Mapped[str] = mapped_column(String, unique=True)  # mathpix | deepseek-flash | deepseek-pro | anthropic
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
    # Profil matériel MathPrint. Une ligne peut représenter une file CUPS locale
    # (protocol="cups", uri vide) ou une imprimante IPP enregistrée. Ces
    # réglages sont volontairement explicites : ils pilotent le chemin physique
    # des feuilles et ne doivent pas se perdre dans des options de pilote CUPS.
    duplex: Mapped[bool] = mapped_column(Boolean, default=False)
    # Deux inversions IMPRIMANTE distinctes : choix de la première copie du lot,
    # puis façon dont chaque feuille est déposée sur le bac de réception.
    pickup_reverse_order: Mapped[bool] = mapped_column(Boolean, default=False)
    output_reverse_order: Mapped[bool] = mapped_column("reverse_order", Boolean, default=False)
    app_default: Mapped[bool] = mapped_column(Boolean, default=False)
    adf_reverse_order: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class PrintConnector(Base):
    """Poste professeur autorisé à récupérer des travaux d'impression.

    Le secret remis au connecteur n'est jamais persisté en clair : seul son
    SHA-256 est conservé. ``installation_id`` est généré localement une fois et
    permet de reconnecter la même installation sans multiplier les appareils.
    """
    __tablename__ = "print_connectors"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    installation_id: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String, default="MathPrint Connector")
    token_hash: Mapped[str] = mapped_column(String, unique=True)
    platform: Mapped[str] = mapped_column(String, default="")
    arch: Mapped[str] = mapped_column(String, default="")
    app_version: Mapped[str] = mapped_column(String, default="")
    printers_json: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ConnectorPrintJob(Base):
    """PDF prêt à imprimer, réclamé exclusivement par un connecteur.

    Le PDF stocké a déjà subi les transformations de pages (passe recto/verso
    et ordre inverse). Le client n'a plus aucune décision métier à prendre et
    n'accepte jamais une commande arbitraire envoyée par le serveur.
    """
    __tablename__ = "connector_print_jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    connector_id: Mapped[str] = mapped_column(ForeignKey("print_connectors.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    printer_id: Mapped[str] = mapped_column(ForeignKey("printers.id"))
    assessment_id: Mapped[str | None] = mapped_column(
        ForeignKey("assessments.id"), nullable=True)
    title: Mapped[str] = mapped_column(String, default="Impression MathPrint")
    file_name: Mapped[str] = mapped_column(String, default="")
    pass_side: Mapped[str] = mapped_column(String, default="all")
    native_printer_name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="queued")
    # queued|claimed|submitted|failed|uncertain|cancelled
    options_json: Mapped[dict] = mapped_column(JSON, default=dict)
    document_relpath: Mapped[str] = mapped_column(String)
    document_sha256: Mapped[str] = mapped_column(String)
    document_size: Mapped[int] = mapped_column(Integer, default=0)
    spool_job_id: Mapped[str] = mapped_column(String, default="")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


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
    assessment_id: Mapped[str | None] = mapped_column(ForeignKey("assessments.id"), nullable=True)
    progress_message: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # journal lisible de la génération (bouton « Voir log » de l'écran Sujets)
    log_text: Mapped[str | None] = mapped_column(Text, nullable=True)


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


class MailIntakeConfig(Base):
    """Réception automatique des scans par mail (ADF réseau de
    l'établissement qui envoie les copies scannées par mail) : ligne unique
    ("default"), relevée périodiquement en IMAP par services.mail_intake.
    Le mot de passe n'est jamais renvoyé en clair par l'API (même convention
    que ProviderConfig.encrypted_secret : stocké tel quel, masqué à la
    lecture — cf. routers.misc)."""
    __tablename__ = "mail_intake_config"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: "default")
    host: Mapped[str] = mapped_column(String, default="")
    port: Mapped[int] = mapped_column(Integer, default=993)
    username: Mapped[str] = mapped_column(String, default="")
    encrypted_password: Mapped[str] = mapped_column(String, default="")
    folder: Mapped[str] = mapped_column(String, default="INBOX")
    poll_interval_s: Mapped[int] = mapped_column(Integer, default=120)
    # vide = tout expéditeur accepté ; sinon adresses autorisées (comparaison
    # insensible à la casse sur l'en-tête From)
    sender_allowlist_json: Mapped[list] = mapped_column(JSON, default=list)
    # dernier UID IMAP traité (watermark) — pas le flag \Seen, pour rester
    # robuste à un autre client qui lirait la même boîte
    last_uid: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    # supprime (IMAP \Deleted + EXPUNGE) chaque mail dès que son import est
    # commité en base, pour que la boîte dédiée ne s'accumule pas ; sur
    # Gmail ceci déplace vers la Corbeille (purge définitive à 30 j), jamais
    # une suppression irréversible immédiate
    delete_after_import: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
