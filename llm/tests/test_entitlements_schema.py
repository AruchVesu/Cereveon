"""Schema tests for the entitlements layer (freemium tiers, Phase 1).

Subtask 1 of the freemium plan ships ONLY dormant schema: the
``Player.plan`` column and the ``usage_counters`` table
(``llm/seca/entitlements/models.py``).  No runtime path reads either
yet — these tests pin the persistence shapes the (next-subtask)
entitlements service will build on, so its behaviour can't be silently
undermined by schema drift:

1.  Shared-Base registration — ``usage_counters`` must be in
    ``Base.metadata`` via the load-bearing wildcard-import block in
    ``llm/seca/auth/router.py`` (the ``test_schema_boundary`` invariant:
    one ``create_all`` covers the full schema on both dialects).
2.  ``Player.plan`` persists ``"free"`` for BOTH an omitted value and an
    explicit ``None`` — SQLAlchemy fires column defaults on ``None``
    (the PR #327 gotcha), and the billing endpoint depends on "unset
    means free" holding in both forms.
3.  ``init_schema()`` adds ``plan`` to a pre-existing ``players`` table
    and backfills ``'free'`` (``create_all`` alone does NOT alter
    existing tables — the PR #135 production failure mode), and a
    second run is a no-op.
4.  ``uq_usage_counter_scope`` uniqueness actually holds for pure
    counters because ``subject`` is a NOT NULL ``""`` sentinel — with a
    nullable column, unique constraints treat NULLs as DISTINCT on both
    SQLite and Postgres and duplicate counter rows could stack.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from llm.seca.auth.models import Base, Player
from llm.seca.entitlements.models import UsageCounter

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# In-memory DB fixture (same shape as test_chat_persistence.py)
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _make_player(db, email: str, **kwargs) -> Player:
    """Insert a Player row directly, bypassing the register/login flow."""
    p = Player(email=email, password_hash="not-used-here", **kwargs)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


# ---------------------------------------------------------------------------
# 1. Shared-Base registration
# ---------------------------------------------------------------------------


class TestSharedBaseRegistration:
    def test_usage_counters_table_in_base_metadata(self):
        """usage_counters must be created by the single create_all.

        ``llm/conftest.py`` runs ``init_schema()`` (which wildcard-imports
        every model module) before any test, so metadata is fully
        populated here.  If this fails, the entitlements wildcard import
        was dropped from ``llm/seca/auth/router.py`` and fresh deploys
        would miss the table entirely.
        """
        assert "usage_counters" in Base.metadata.tables

    def test_router_wildcard_imports_entitlements_models(self):
        """Source pin on the load-bearing import (coarse on purpose,
        same trade-off as test_schema_boundary's repo.py grep)."""
        router_src = (_REPO_ROOT / "llm" / "seca" / "auth" / "router.py").read_text(
            encoding="utf-8"
        )
        assert "from llm.seca.entitlements.models import *" in router_src


# ---------------------------------------------------------------------------
# 2. Player.plan default shape
# ---------------------------------------------------------------------------


class TestPlayerPlanDefault:
    def test_omitted_plan_persists_free(self, db):
        player = _make_player(db, "omitted@test.com")
        assert player.plan == "free"

    def test_explicit_none_plan_persists_free(self, db):
        """Player(plan=None) must ALSO land as "free".

        SQLAlchemy fires the column default when the attribute is None
        at flush time, so an upstream caller passing None to mean
        "unset" persists the default, not NULL.  The entitlements
        service assumes no NULL plans can exist; this pins that.
        """
        player = _make_player(db, "explicit-none@test.com", plan=None)
        assert player.plan == "free"

    def test_explicit_pro_persists(self, db):
        player = _make_player(db, "pro@test.com", plan="pro")
        assert player.plan == "pro"


# ---------------------------------------------------------------------------
# 3. init_schema in-place migration
# ---------------------------------------------------------------------------


class TestInitSchemaMigration:
    def test_adds_plan_to_preexisting_players_table_and_is_idempotent(
        self, tmp_path, monkeypatch
    ):
        """A players table that pre-dates the column must gain it.

        create_all skips existing tables, so without the _ensure_column
        step in init_schema every live deploy (Postgres) and legacy dev
        file (SQLite) would 500 on first read of Player.plan — the same
        failure mode PR #135 hit with sessions.previous_token_hash.
        """
        import llm.seca.auth.router as auth_router

        db_file = tmp_path / "legacy.db"
        legacy_engine = create_engine(f"sqlite:///{db_file}")
        with legacy_engine.connect() as conn:
            conn.execute(
                text("CREATE TABLE players (id TEXT PRIMARY KEY, email TEXT, password_hash TEXT)")
            )
            conn.execute(
                text("INSERT INTO players (id, email, password_hash) VALUES ('p1', 'l@t.co', 'x')")
            )
            conn.commit()

        monkeypatch.setattr(auth_router, "engine", legacy_engine)
        monkeypatch.setattr(auth_router, "_is_sqlite", True)

        auth_router.init_schema()

        cols = {c["name"] for c in inspect(legacy_engine).get_columns("players")}
        assert "plan" in cols, "init_schema did not add players.plan to a pre-existing table"

        with legacy_engine.connect() as conn:
            backfilled = conn.execute(text("SELECT plan FROM players WHERE id = 'p1'")).scalar()
        assert backfilled == "free", f"legacy row backfilled to {backfilled!r}, expected 'free'"

        # Second run must be a clean no-op (lifespan + conftest + manual
        # maintenance all call init_schema; it must stay re-entrant).
        auth_router.init_schema()

        legacy_engine.dispose()


# ---------------------------------------------------------------------------
# 4. usage_counters uniqueness + default shapes
# ---------------------------------------------------------------------------


class TestUsageCounterShape:
    def test_none_subject_and_none_count_fire_column_defaults(self, db):
        """subject=None / count=None persist as "" / 0, never NULL.

        Same default-fires-on-None semantics pinned for Player.plan
        above.  The service layer's IntegrityError-based race handling
        only works if a subject-less row lands on the "" sentinel."""
        player = _make_player(db, "defaults@test.com")
        row = UsageCounter(
            player_id=player.id, metric="chat_turn", period_key="2026-07-03",
            subject=None, count=None,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        assert row.subject == ""
        assert row.count == 0

    def test_duplicate_pure_counter_scope_is_rejected(self, db):
        """Two subject-less rows for the same (player, metric, period)
        must violate uq_usage_counter_scope.

        This is THE reason subject is a NOT NULL "" sentinel: were it
        nullable, both dialects would treat the NULLs as distinct and
        happily stack duplicate counter rows, and the service's
        one-row-per-scope increment logic would silently miscount."""
        player = _make_player(db, "dup@test.com")
        db.add(UsageCounter(player_id=player.id, metric="chat_turn", period_key="2026-07-03"))
        db.commit()

        db.add(UsageCounter(player_id=player.id, metric="chat_turn", period_key="2026-07-03"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    def test_distinct_scopes_coexist(self, db):
        """Different subjects (admission markers for different games),
        different periods, and different metrics are all separate rows."""
        player = _make_player(db, "scopes@test.com")
        db.add_all(
            [
                UsageCounter(
                    player_id=player.id,
                    metric="coached_game",
                    period_key="2026-07-03",
                    subject="game-a",
                ),
                UsageCounter(
                    player_id=player.id,
                    metric="coached_game",
                    period_key="2026-07-03",
                    subject="game-b",
                ),
                UsageCounter(player_id=player.id, metric="chat_turn", period_key="2026-07-03"),
                UsageCounter(player_id=player.id, metric="chat_turn", period_key="2026-07-04"),
                UsageCounter(player_id=player.id, metric="import_analysis", period_key="2026-07"),
            ]
        )
        db.commit()

        rows = db.query(UsageCounter).filter(UsageCounter.player_id == player.id).all()
        assert len(rows) == 5
