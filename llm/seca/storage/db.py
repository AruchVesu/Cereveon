import sqlite3
from pathlib import Path

DB_PATH = Path("data/seca.db")


def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Initialise (or migrate) the raw-sqlite schema.

    1.  Run schema.sql — creates any missing tables.
    2.  Apply column-add migrations for tables that schema.sql created
        before the columns existed.  SQLite has no proper ALTER TABLE
        for IF-NOT-EXISTS semantics, so we detect via PRAGMA table_info
        and add columns whose names aren't there yet.  Idempotent;
        safe to call multiple times.
    """
    conn = get_conn()
    try:
        schema = Path(__file__).with_name("schema.sql").read_text()
        conn.executescript(schema)
        _apply_games_checkpoint_columns(conn)
        conn.commit()
    finally:
        conn.close()


def _apply_games_checkpoint_columns(conn: sqlite3.Connection) -> None:
    """Add the cross-device-resume checkpoint columns to existing
    `games` rows.  Defensive: only adds columns that aren't already
    there, so this is safe for both fresh databases (schema.sql
    already created the columns) and pre-migration databases (where
    schema.sql ran before the column existed)."""
    rows = conn.execute("PRAGMA table_info(games)").fetchall()
    existing = {r[1] for r in rows}  # row[1] is column name
    for col_name, col_type in (
        ("current_fen", "TEXT"),
        ("current_uci_history", "TEXT"),
        ("last_checkpoint_at", "TIMESTAMP"),
    ):
        if col_name not in existing:
            conn.execute(f"ALTER TABLE games ADD COLUMN {col_name} {col_type}")
