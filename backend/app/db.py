from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

engine = create_engine(
    settings.database_url,
    # `timeout` (secondes) : SQLite attend un verrou libéré au lieu de renvoyer
    # aussitôt "database is locked" — deux workers de fond (génération de
    # sujet + extraction Indigo) écrivent en concurrence sur le même fichier,
    # et un échec de commit non protégé y laissait un job bloqué en
    # "running"/"pending" pour toujours (cf. job_worker._run_job, indigo._drain).
    connect_args={"check_same_thread": False, "timeout": 15}
    if settings.database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# colonnes ajoutées après la mise en service initiale : `create_all` ne modifie
# jamais les tables existantes, donc on complète ici (SQLite comme Postgres).
# Format : (nom, type) — ou (nom, type, défaut SQL) quand les lignes existantes
# doivent recevoir une valeur précise plutôt que le défaut générique de
# `_default_sql` (ex. assessments.note_base : les contrôles créés avant la
# notion de base de notation étaient tous notés sur 20).
_ADDED_COLUMNS: dict[str, list[tuple[str, ...]]] = {
    "users": [("subscription_plan", "TEXT")],
    "scan_batches": [("overlay_printed", "BOOLEAN"), ("overlay_distributed", "BOOLEAN")],
    "scanned_pages": [("manual_page_id", "TEXT"), ("dismissed", "BOOLEAN")],
    "copies": [("appreciation_json", "JSON"), ("variant_key", "TEXT")],
    "generated_exercises": [
        ("verifier_model", "TEXT"),
        ("verifier_verdict_json", "JSON"),
        ("figure_json", "JSON"),
        ("source", "TEXT"),
        ("kind", "TEXT"),
        ("quality_json", "JSON"),
        ("raw_extract_json", "JSON"),
    ],
    "jobs": [
        ("assessment_id", "TEXT"),
        ("progress_message", "TEXT"),
        ("updated_at", "TIMESTAMP"),
        ("log_text", "TEXT"),
    ],
    "assessments": [
        ("error_message", "TEXT"),
        ("pronote_entered", "BOOLEAN", "0" if engine.dialect.name == "sqlite" else "FALSE"),
        # sujets antérieurs au barème : la note était calculée sur 20 en dur
        # (cf. services.pipeline.build_overlays), ils gardent donc /20
        ("note_base", "INTEGER", "20"),
    ],
    # DEFAULT NULL explicite sur les colonnes dont le vide doit rester un vide :
    # le défaut générique de `_default_sql` est la chaîne VIDE, et une chaîne
    # vide se lit comme une valeur. `generated_exercise_id = ''` désignerait un
    # exercice inexistant au lieu de « exercice inconnu », et le backfill
    # ci-dessous ne reconnaîtrait plus les lignes à reprendre.
    "copy_items": [
        # exercice de banque réellement servi (cf. modèle CopyItem)
        ("generated_exercise_id", "TEXT", "NULL"),
    ],
    "copy_item_results": [
        # historique du suivi élève (cf. modèle CopyItemResult) — rempli pour
        # l'existant par _backfill_item_history
        ("student_id", "TEXT", "NULL"),
        ("generated_exercise_id", "TEXT", "NULL"),
        ("difficulty_level", "INTEGER", "0"),
        ("answer_text", "TEXT"),
        ("success_ratio",
         "REAL" if engine.dialect.name == "sqlite" else "DOUBLE PRECISION", "0"),
        ("occurred_at", "TIMESTAMP", "NULL"),
    ],
    "student_levels": [
        ("assessment_id", "TEXT"),
    ],
    "competency_frameworks": [
        ("cycle", "INTEGER"),
        ("program_year", "INTEGER"),
    ],
    "competencies": [
        ("short_id", "TEXT"),
    ],
    "student_responses": [
        ("selected_pairs", "JSON"),
    ],
    "indigo_exercises": [
        ("figure_required", "BOOLEAN"),
        # trio base / dérivé facile / dérivé difficile (§ services.indigo_qcm).
        # TEXT et non un type propre à un moteur : le piège « DATETIME »
        # (accepté par SQLite, refusé par Postgres) a déjà mis l'api en boucle
        # de crash en production.
        ("variant_kind", "TEXT", "'base'"),
        ("derived_from_id", "TEXT"),
    ],
    "printers": [
        ("duplex", "BOOLEAN"),
        ("reverse_order", "BOOLEAN"),
        ("pickup_reverse_order", "BOOLEAN"),
        ("app_default", "BOOLEAN"),
        ("adf_reverse_order", "BOOLEAN"),
    ],
}

# renommages de colonnes (hiérarchie H1/H2 documentée sur `Competency` :
# l'ancien "thème" du programme officiel devient "chapitre", cf. modèle) :
# {table: [(ancien_nom, nouveau_nom), ...]}
_RENAMED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "competencies": [
        ("theme_code", "chapter_code"),
        ("theme_name", "chapter_name"),
    ],
}

# colonnes SUPPRIMÉES du modèle : {table: [colonne, ...]}. Il ne suffit pas de
# les retirer du modèle — une colonne NOT NULL sans défaut SQL laissée en base
# fait échouer tous les INSERT suivants (Postgres refuse, SQLite laisse passer :
# le même piège que le type DATETIME, invisible en test local).
# `indigo_exercises.effort_points` doublonnait `grading_json["bareme_points"]`,
# qui est le SEUL barème (§ services.scoring) : la banque ne lisait déjà que
# celui-ci, la colonne ne servait qu'à l'affichage et pouvait diverger.
_DROPPED_COLUMNS: dict[str, list[str]] = {
    # `is_mock` a disparu avec la classe de démonstration. Sur les bases
    # PostgreSQL déjà créées, cette ancienne colonne NOT NULL restait pourtant
    # présente et faisait échouer tout nouvel INSERT dans `classes`, car le
    # modèle courant ne lui fournit plus de valeur.
    "classes": ["is_mock"],
    "indigo_exercises": ["effort_points"],
    # `next_plan_json`/`next_plan_updated_at` portaient la « trame » d'exercices
    # écrite par le LLM après chaque correction. Supprimée : elle pariait sur
    # une date de sujet suivant que personne ne connaissait (cf. modèle Student).
    "students": ["first_name", "last_name", "next_plan_json", "next_plan_updated_at"],
    "copy_items": ["lesson_snippet_id"],
}

# Tables supprimées du produit. Elles sont retirées après leurs colonnes de
# référence éventuelles afin que la migration fonctionne aussi sous Postgres.
_DROPPED_TABLES = {"lesson_snippets"}

# Index à poser sur des tables DÉJÀ existantes : {nom: (table, "colonnes")}.
# `create_all` ne crée les index que des tables qu'il crée lui-même — une base
# en service garderait donc la colonne sans son index, et la table de suivi est
# précisément celle qu'on interroge par élève × compétence à chaque génération
# de sujet. `IF NOT EXISTS` est compris par SQLite comme par Postgres.
_ADDED_INDEXES: dict[str, tuple[str, str]] = {
    "ix_copy_item_results_student_comp": ("copy_item_results", "student_id, competency_id"),
}


def _default_sql(col_type: str) -> str:
    """Défaut SQL d'une colonne ajoutée, quand l'appelant n'en impose pas.

    Le type de la valeur doit correspondre à celui de la COLONNE : Postgres
    refuse « ADD COLUMN cycle INTEGER DEFAULT '' » (default de type text sur
    une colonne integer) là où SQLite l'accepte sans broncher — la migration
    ne pète alors qu'en production, jamais en test local (même piège que le
    type DATETIME, cf. incident migration SQLite/Postgres)."""
    if col_type == "BOOLEAN":
        return "0" if engine.dialect.name == "sqlite" else "FALSE"
    if col_type in ("JSON", "TIMESTAMP", "INTEGER", "FLOAT", "REAL"):
        return "NULL"
    return "''"


def run_migrations():
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        # Migration d'identité des élèves. Elle doit précéder la suppression
        # des deux anciennes colonnes afin de conserver les noms existants.
        if "students" in tables:
            student_cols = {c["name"] for c in insp.get_columns("students")}
            additions = (
                ("name", "TEXT", "''"),
                ("order_index", "INTEGER", "0"),
                ("dyslexic", "BOOLEAN", _default_sql("BOOLEAN")),
            )
            for name, col_type, default in additions:
                if name not in student_cols:
                    conn.execute(text(
                        f"ALTER TABLE students ADD COLUMN {name} {col_type} "
                        f"DEFAULT {default}"))
                    student_cols.add(name)
            if {"first_name", "last_name"}.issubset(student_cols):
                conn.execute(text(
                    "UPDATE students SET name = TRIM(COALESCE(last_name, '') || ' ' || "
                    "COALESCE(first_name, '')) WHERE name IS NULL OR name = ''"))
                rows = conn.execute(text(
                    "SELECT id, class_id FROM students "
                    "ORDER BY class_id, LOWER(last_name), LOWER(first_name), id"
                )).fetchall()
                positions: dict[str | None, int] = {}
                for student_id, class_id in rows:
                    position = positions.get(class_id, 0)
                    conn.execute(text(
                        "UPDATE students SET order_index=:position WHERE id=:student_id"),
                        {"position": position, "student_id": student_id})
                    positions[class_id] = position + 1
        for table, renames in _RENAMED_COLUMNS.items():
            if table not in tables:
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for old_name, new_name in renames:
                if old_name in existing and new_name not in existing:
                    conn.execute(text(
                        f"ALTER TABLE {table} RENAME COLUMN {old_name} TO {new_name}"))
        for table, columns in _DROPPED_COLUMNS.items():
            if table not in tables:
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name in columns:
                if name in existing:
                    conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {name}"))
        for table in _DROPPED_TABLES:
            if table in tables:
                conn.execute(text(f"DROP TABLE {table}"))
        for table, columns in _ADDED_COLUMNS.items():
            if table not in tables:
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for spec in columns:
                name, col_type = spec[0], spec[1]
                if name in existing:
                    continue
                default = spec[2] if len(spec) > 2 else _default_sql(col_type)
                conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN {name} {col_type} "
                    f"DEFAULT {default}"))
        for index_name, (table, columns) in _ADDED_INDEXES.items():
            if table in tables:
                conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({columns})"))
        if "users" in tables:
            # `viewer` était le nom historique, jamais exposé dans l'UI, du
            # futur rôle correcteur. Les utilisateurs classiques déjà en base
            # commencent sur l'offre gratuite ; les comptes système restent
            # volontairement sans abonnement.
            conn.execute(text(
                "UPDATE users SET role='corrector' WHERE role='viewer'"))
            conn.execute(text(
                "UPDATE users SET subscription_plan='free' "
                "WHERE role='teacher' AND "
                "(subscription_plan IS NULL OR subscription_plan='')"))
            conn.execute(text(
                "UPDATE users SET subscription_plan=NULL "
                "WHERE role IN ('admin', 'corrector')"))
        if "assessments" in tables:
            conn.execute(text(
                "UPDATE assessments SET personalization_mode='common_variants' "
                "WHERE personalization_mode='equivalent_variants'"))
            conn.execute(text(
                "UPDATE assessments SET personalization_mode='individual' "
                "WHERE personalization_mode IN ('guided_individual','free_individual')"))
            conn.execute(text(
                "UPDATE assessments SET status='ready' WHERE status='generated'"))
        _migrate_difficulty_to_3_levels(conn, tables)
        _backfill_item_history(conn, tables)
        _clamp_stability_to_mastery(conn, tables)


# Passage de 5 à 3 niveaux de difficulté (§ exercise_gen.DIFFICULTY_LEVELS).
# NON IDEMPOTENTE : la conversion 1,2->1 / 3->2 / 4,5->3 rejouée une seconde
# fois écraserait les niveaux déjà convertis (un « 3 » migré deviendrait « 2 »).
# Elle est donc gardée par un marqueur en base, écrit dans la MÊME transaction
# que les UPDATE — un plantage entre les deux rejouerait proprement la migration
# plutôt que de la sauter.
_DIFFICULTY_3_MARKER = "migration_difficulty_1_3"
# CASE portable SQLite ET Postgres (pas de fonction propre à un moteur, cf.
# le piège du type "DATETIME" accepté par SQLite et refusé par Postgres).
_TO_LEVEL3_SQL = ("CASE WHEN {col} <= 2 THEN 1 WHEN {col} = 3 THEN 2 ELSE 3 END")


def _migrate_difficulty_to_3_levels(conn, tables: set[str]) -> None:
    if "system_settings" not in tables:
        return          # base neuve : create_all a déjà posé les niveaux 1-3
    done = conn.execute(text("SELECT 1 FROM system_settings WHERE key = :k"),
                        {"k": _DIFFICULTY_3_MARKER}).first()
    if done:
        return
    for table, col in (("generated_exercises", "difficulty_level"),
                       ("indigo_exercises", "difficulty")):
        if table in tables:
            conn.execute(text(f"UPDATE {table} SET {col} = "
                              + _TO_LEVEL3_SQL.format(col=col)))
    conn.execute(text(
        "INSERT INTO system_settings (key, value_json, version) "
        "VALUES (:k, :v, 1)"), {"k": _DIFFICULTY_3_MARKER, "v": '{"value": true}'})


def _mark_done(conn, marker: str) -> None:
    conn.execute(text(
        "INSERT INTO system_settings (key, value_json, version) "
        "VALUES (:k, :v, 1)"), {"k": marker, "v": '{"value": true}'})


def _already_done(conn, tables: set[str], marker: str) -> bool:
    if "system_settings" not in tables:
        return True         # base neuve : rien d'ancien à reprendre
    return bool(conn.execute(text("SELECT 1 FROM system_settings WHERE key = :k"),
                             {"k": marker}).first())


# Reprise de l'historique de suivi (§ services.student_history) sur les
# corrections déjà finalisées. Les colonnes ajoutées à copy_item_results sont
# toutes reconstructibles depuis les tables existantes — SAUF
# `generated_exercise_id`, qui n'a jamais été écrit nulle part : l'historique
# antérieur reste donc sans exercice identifiable (le moteur le traite comme
# « exercice inconnu » : pas d'anti-répétition dessus, mais compétence, niveau
# et score restent exploités).
_ITEM_HISTORY_MARKER = "migration_item_history_backfill"


def _backfill_item_history(conn, tables: set[str]) -> None:
    if _already_done(conn, tables, _ITEM_HISTORY_MARKER):
        return
    if not {"copy_item_results", "copy_results"}.issubset(tables):
        _mark_done(conn, _ITEM_HISTORY_MARKER)
        return
    # Sous-requêtes corrélées : le seul UPDATE ... FROM portable SQLite ET
    # Postgres (cf. le piège du type "DATETIME", accepté par l'un, refusé par
    # l'autre — on ne mise sur aucune syntaxe propre à un moteur).
    conn.execute(text("""
        UPDATE copy_item_results SET
          student_id = (SELECT cr.student_id FROM copy_results cr
                        WHERE cr.id = copy_item_results.copy_result_id),
          occurred_at = (SELECT COALESCE(a.scheduled_at, cp.generated_at, cr.finalized_at)
                         FROM copy_results cr
                         LEFT JOIN assessments a ON a.id = cr.assessment_id
                         LEFT JOIN copies cp ON cp.id = cr.copy_id
                         WHERE cr.id = copy_item_results.copy_result_id)
        WHERE student_id IS NULL OR student_id = ''
    """))
    # difficulty vaut 3/6/9 depuis le passage à 3 niveaux, mais l'historique
    # antérieur porte encore 12/15 (ancienne échelle 1-5) et le défaut 5 : on
    # ramène par tranches plutôt que par une division qui les enverrait hors bornes.
    conn.execute(text("""
        UPDATE copy_item_results
           SET difficulty_level = CASE WHEN difficulty <= 4 THEN 1
                                       WHEN difficulty <= 7 THEN 2 ELSE 3 END
         WHERE difficulty_level IS NULL OR difficulty_level = 0
    """))
    conn.execute(text(
        "UPDATE copy_item_results SET generated_exercise_id = NULL "
        "WHERE generated_exercise_id = ''"))
    if "copy_items" in tables:
        conn.execute(text(
            "UPDATE copy_items SET generated_exercise_id = NULL "
            "WHERE generated_exercise_id = ''"))
    conn.execute(text("""
        UPDATE copy_item_results
           SET success_ratio = CASE WHEN max_score > 0
                                    THEN CASE WHEN score / max_score > 1 THEN 1
                                              WHEN score < 0 THEN 0
                                              ELSE score / max_score END
                                    ELSE 0 END
         WHERE success_ratio IS NULL OR success_ratio = 0
    """))
    if "student_responses" in tables:
        # Reprise limitée au texte brut : aplatir un QCM ou un tableau demande
        # de lire du JSON, ce qu'aucune syntaxe commune aux deux moteurs ne
        # fait. Les réponses non textuelles de l'historique restent donc vides ;
        # les nouvelles passent par scoring.student_answer_text.
        conn.execute(text("""
            UPDATE copy_item_results
               SET answer_text = COALESCE((
                     SELECT sr.final_text FROM student_responses sr
                      WHERE sr.copy_item_id = copy_item_results.copy_item_id), '')
             WHERE answer_text IS NULL OR answer_text = ''
        """))
    _mark_done(conn, _ITEM_HISTORY_MARKER)


# La stabilité était plafonnée à 3650 jours pour tout le monde — un garde-fou
# contre l'OverflowError, pas une règle pédagogique. Elle est désormais bornée
# PAR LA MAÎTRISE (services.forgetting.stability_ceiling) : une compétence à
# moitié acquise ne peut pas obtenir une fenêtre de rappel d'un an. Les états
# déjà énormes en base garderaient sinon une priorité nulle à vie.
_STABILITY_CEILING_MARKER = "migration_stability_mastery_ceiling"


def _clamp_stability_to_mastery(conn, tables: set[str]) -> None:
    if _already_done(conn, tables, _STABILITY_CEILING_MARKER):
        return
    if "student_competency_state" not in tables:
        _mark_done(conn, _STABILITY_CEILING_MARKER)
        return
    # CASE plutôt que MIN/LEAST : SQLite ne connaît pas LEAST, Postgres ne
    # connaît pas MIN à deux arguments. La formule duplique
    # forgetting.stability_ceiling — elle est figée ici par le marqueur, donc
    # ne dérivera pas avec elle.
    conn.execute(text("""
        UPDATE student_competency_state
           SET stability = 1.0 + 364.0 * mastery * mastery * mastery,
               due_at = NULL
         WHERE stability > 1.0 + 364.0 * mastery * mastery * mastery
    """))
    _mark_done(conn, _STABILITY_CEILING_MARKER)
