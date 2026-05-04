"""Schema-init boundary regression tests.

Background
----------
``data/seca.db`` is initialised by two paths during FastAPI lifespan:

  1.  ``llm/seca/storage/db.py:init_db()`` executes
      ``llm/seca/storage/schema.sql`` via raw sqlite3.
  2.  ``llm/seca/auth/router.py:init_schema()`` runs
      ``Base.metadata.create_all()`` for every SQLAlchemy model that has
      been imported into the auth Base (auth, events, brain,
      brain.training, analytics).

Both paths target the same physical file.  Until commit 19d71cfd they
overlapped on three tables (``players``, ``training_decisions``,
``training_outcomes``) with intentionally-incomplete column lists in
``schema.sql``: that file ran first under lifespan, the partial tables
were created, then ``create_all`` saw the tables already existed and
skipped the missing columns entirely.  Fresh-SQLite deployments shipped
without ``email`` / ``password_hash`` on ``players`` and auth/register
would have crashed.

The fix split ownership cleanly: SQLAlchemy owns auth/analytics/training
state, schema.sql owns the per-game raw-sqlite tables (``games``,
``moves``, ``explanations``).  See the header comment in schema.sql for
the canonical ownership map.

This test pins the boundary so any regression — re-adding a duplicate
``CREATE TABLE`` to schema.sql, or adding a SQLAlchemy model for a
table that schema.sql already owns — fails loud at CI time rather than
being discovered at fresh-SQLite deployment time.
"""

from __future__ import annotations

import pathlib
import re
import unittest


# Tables that schema.sql legitimately owns and that no SQLAlchemy model
# may shadow.  Update this list together with the schema.sql header
# comment if the boundary is ever moved.
EXPECTED_RAW_TABLES = frozenset({
    "games", "moves", "explanations", "repertoire", "bandit_weights",
})


def _read_schema_sql() -> str:
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    schema = repo_root / "llm" / "seca" / "storage" / "schema.sql"
    return schema.read_text(encoding="utf-8")


def _raw_table_names(schema_text: str) -> set[str]:
    """Extract table names from ``CREATE TABLE [IF NOT EXISTS] <name>``."""
    pattern = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
        re.IGNORECASE,
    )
    return {m.group(1) for m in pattern.finditer(schema_text)}


class SchemaBoundaryTest(unittest.TestCase):

    def test_no_overlap_between_schema_sql_and_sqlalchemy(self):
        """schema.sql and Base.metadata must not redefine the same table.

        This is the regression-prevention guarantee.  If the assertion
        fails, fresh-SQLite deployments will silently ship with the
        first-runner's incomplete schema (whichever path of init_db /
        init_auth_schema runs first wins).

        Implementation note: ``llm/conftest.py:_backend_schema_init`` is
        a session-scoped autouse fixture that calls
        ``auth/router.init_schema()`` before any test runs, which
        cascades into the side-effect imports (``from
        llm.seca.brain.models import *`` etc.) that populate Base.
        Trust ``Base.metadata`` as-is rather than re-importing the model
        modules here — re-importing through ``llm.seca.X.models`` after
        the codebase has already loaded ``seca.X.models`` via the
        try/except fallback in ``seca/db.py`` creates a second module
        object and trips SQLAlchemy's "Table already defined" guard
        when the duplicate ``class TrainingDecision(Base)`` runs.
        """
        from llm.seca.auth.models import Base

        sqlalchemy_tables = set(Base.metadata.tables.keys())
        raw_tables = _raw_table_names(_read_schema_sql())

        overlap = sqlalchemy_tables & raw_tables
        self.assertFalse(
            overlap,
            f"schema.sql redefines SQLAlchemy-owned tables: {sorted(overlap)}. "
            f"See the header of llm/seca/storage/schema.sql for the canonical "
            f"ownership map.  SQLAlchemy currently owns "
            f"{sorted(sqlalchemy_tables)}.",
        )

    def test_schema_sql_owns_only_expected_raw_tables(self):
        """schema.sql must define exactly the documented raw-owned tables.

        Adding a new raw-sqlite table here is allowed but must update
        EXPECTED_RAW_TABLES in this test file (and the schema.sql
        header) in the same commit so the boundary stays explicit.
        """
        raw_tables = _raw_table_names(_read_schema_sql())
        unexpected = raw_tables - EXPECTED_RAW_TABLES
        missing = EXPECTED_RAW_TABLES - raw_tables
        self.assertFalse(
            unexpected,
            f"schema.sql defines tables not on the expected-raw allowlist: "
            f"{sorted(unexpected)}.  Either model the table in SQLAlchemy or "
            f"update EXPECTED_RAW_TABLES + the schema.sql header.",
        )
        self.assertFalse(
            missing,
            f"schema.sql is missing tables on the expected-raw allowlist: "
            f"{sorted(missing)}.  If you intended to remove one, update "
            f"EXPECTED_RAW_TABLES too.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
