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
}


def run_migrations():
    insp = inspect(engine)
    bool_default = "0" if engine.dialect.name == "sqlite" else "FALSE"
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if table not in tables:
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, col_type in columns:
                if name not in existing:
                    conn.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN {name} {col_type} "
                        f"NOT NULL DEFAULT {bool_default}"))
