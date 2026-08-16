from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
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
    "students": [
        ("next_plan_json", "JSON"),
        ("next_plan_updated_at", "TIMESTAMP"),
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
    "students": ["first_name", "last_name"],
    "copy_items": ["lesson_snippet_id"],
}

# Tables supprimées du produit. Elles sont retirées après leurs colonnes de
# référence éventuelles afin que la migration fonctionne aussi sous Postgres.
_DROPPED_TABLES = {"lesson_snippets"}


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
