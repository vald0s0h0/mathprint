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
_ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "scan_batches": [("overlay_printed", "BOOLEAN"), ("overlay_distributed", "BOOLEAN")],
    "copies": [("appreciation_json", "JSON")],
    "generated_exercises": [
        ("verifier_model", "TEXT"),
        ("verifier_verdict_json", "JSON"),
        ("figure_json", "JSON"),
        ("source", "TEXT"),
        ("kind", "TEXT"),
        ("quality_json", "JSON"),
    ],
    "lesson_snippets": [
        ("verifier_model", "TEXT"),
        ("verifier_verdict_json", "JSON"),
        ("figure_json", "JSON"),
        ("status", "TEXT"),
        ("blocks_json", "JSON"),
    ],
    "jobs": [
        ("assessment_id", "TEXT"),
        ("progress_message", "TEXT"),
        ("updated_at", "TIMESTAMP"),
        ("log_text", "TEXT"),
    ],
    "assessments": [
        ("error_message", "TEXT"),
    ],
    "students": [
        ("next_plan_json", "JSON"),
        ("next_plan_updated_at", "TIMESTAMP"),
    ],
    "copy_items": [
        ("lesson_snippet_id", "TEXT"),
    ],
}


def run_migrations():
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if table not in tables:
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, col_type in columns:
                if name not in existing:
                    if col_type == "BOOLEAN":
                        default = "0" if engine.dialect.name == "sqlite" else "FALSE"
                    elif col_type in ("JSON", "TIMESTAMP"):
                        default = "NULL"
                    else:
                        default = "''"
                    conn.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN {name} {col_type} "
                        f"DEFAULT {default}"))
        if "assessments" in tables:
            conn.execute(text(
                "UPDATE assessments SET personalization_mode='common_variants' "
                "WHERE personalization_mode='equivalent_variants'"))
            conn.execute(text(
                "UPDATE assessments SET personalization_mode='individual' "
                "WHERE personalization_mode IN ('guided_individual','free_individual')"))
            conn.execute(text(
                "UPDATE assessments SET status='ready' WHERE status='generated'"))
