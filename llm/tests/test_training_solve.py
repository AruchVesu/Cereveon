"""
Backend tests for POST /training/solve — verified-solve credit endpoint.

Endpoint owns the persistence + counter update for the training-XP
feature that replaced the user-visible Elo display on Android Home.
A "solve" is one verified mistake-replay / weekly micro-task /
standard puzzle completion; the running total is the
``Player.training_xp`` counter exposed via /auth/me.

Pinned invariants
-----------------
 1. SOLVE_SOURCE_TYPE_REJECTS_UNKNOWN     unknown source_type → ValidationError.
 2. SOLVE_SOURCE_REF_LENGTH_BOUNDED       >200-char source_ref → ValidationError.
 3. SOLVE_SOURCE_REF_BLANK_NORMALISED     ""/whitespace source_ref → stored as NULL.
 4. SOLVE_HAPPY_PATH_CREDITS_XP           new solve → +10 XP on player + row inserted.
 5. SOLVE_THREE_SOLVES_SUMS               three distinct solves → training_xp = 30.
 6. SOLVE_DEDUPS_BY_TRIPLE                same (source_type, source_ref) twice → xp_awarded=0
                                          on the second call, counter unchanged.
 7. SOLVE_DEDUPS_PER_PLAYER               same triple, different player → both credited.
 8. SOLVE_DIFFERENT_REF_DOES_NOT_DEDUP    same source_type different source_ref → both credited.
 9. SOLVE_DIFFERENT_TYPE_DOES_NOT_DEDUP   different source_type same source_ref → both credited.
10. SOLVE_NULL_REF_DOES_NOT_DEDUP         two solves with source_ref=None → both credited.
11. SOLVE_RESPONSE_SHAPE                  response includes xp_awarded + training_xp +
                                          completed_at (ISO-8601).
12. SOLVE_PERSISTS_TO_DB                  TrainingCompletion row visible to subsequent queries.
"""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request as StarletteRequest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

# Import all model modules so create_all sees every table.
import llm.seca.auth.models  # noqa: F401
import llm.seca.events.models  # noqa: F401
import llm.seca.brain.models  # noqa: F401
import llm.seca.analytics.models  # noqa: F401
import llm.seca.training.models  # noqa: F401

from llm.seca.auth.models import Base, Player
from llm.seca.shared_limiter import limiter
from llm.seca.training.models import (
    SOURCE_TYPE_MISTAKE_REPLAY,
    SOURCE_TYPE_STANDARD_PUZZLE,
    SOURCE_TYPE_WEEKLY_MICROTASK,
    TrainingCompletion,
    XP_PER_SOLVE,
)
from llm.seca.training.router import SolveRequest, solve


def _fake_request() -> StarletteRequest:
    """Minimal Request that satisfies slowapi's isinstance check.  The
    limiter is disabled inside each test so the request itself is
    never inspected for an IP / route key."""
    return StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": "/training/solve",
            "headers": [],
            "client": ("127.0.0.1", 0),
        }
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    """In-memory SQLite session with the full schema; torn down after each test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def player(db_session):
    p = Player(
        email="solver@test.com",
        password_hash="dummy-hash",
        rating=1500.0,
        confidence=0.5,
        skill_vector_json="{}",
        player_embedding="[]",
        training_xp=0,
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture()
def other_player(db_session):
    p = Player(
        email="other@test.com",
        password_hash="dummy-hash",
        rating=1500.0,
        confidence=0.5,
        skill_vector_json="{}",
        player_embedding="[]",
        training_xp=0,
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _solve(player, db, **kwargs):
    """Call the POST /training/solve handler directly, bypassing FastAPI
    dependency injection.  Disables the global limiter for the duration
    of the call so unit tests don't depend on slowapi state across
    tests."""
    req = SolveRequest(**kwargs)
    prev_enabled = limiter.enabled
    limiter.enabled = False
    try:
        return solve(req=req, request=_fake_request(), player=player, db=db)
    finally:
        limiter.enabled = prev_enabled


# ---------------------------------------------------------------------------
# 1.  Schema validation (Pydantic only — no DB)
# ---------------------------------------------------------------------------


class TestSolveRequestValidation:
    """SolveRequest schema enforces the source-type whitelist + ref bounds."""

    def test_known_source_types_accepted(self):
        for source_type in (
            SOURCE_TYPE_MISTAKE_REPLAY,
            SOURCE_TYPE_WEEKLY_MICROTASK,
            SOURCE_TYPE_STANDARD_PUZZLE,
        ):
            req = SolveRequest(source_type=source_type, source_ref="r1")
            assert req.source_type == source_type

    def test_unknown_source_type_rejected(self):
        """SOLVE_SOURCE_TYPE_REJECTS_UNKNOWN."""
        with pytest.raises(ValidationError, match="source_type must be one of"):
            SolveRequest(source_type="mystery_box", source_ref="r1")

    def test_source_ref_too_long_rejected(self):
        """SOLVE_SOURCE_REF_LENGTH_BOUNDED."""
        with pytest.raises(ValidationError, match="source_ref must be at most"):
            SolveRequest(
                source_type=SOURCE_TYPE_MISTAKE_REPLAY,
                source_ref="x" * 250,
            )

    def test_source_ref_at_limit_accepted(self):
        # Exactly 200 chars must pass — boundary test.
        req = SolveRequest(
            source_type=SOURCE_TYPE_MISTAKE_REPLAY,
            source_ref="x" * 200,
        )
        assert req.source_ref is not None
        assert len(req.source_ref) == 200

    def test_blank_source_ref_normalised_to_none(self):
        """SOLVE_SOURCE_REF_BLANK_NORMALISED — empty / whitespace-only
        strings are indistinguishable from "no ref" at the storage
        layer; normalise so the unique-index's NULL-distinct semantics
        kick in."""
        for blank in ("", "   ", "\t\n"):
            req = SolveRequest(
                source_type=SOURCE_TYPE_MISTAKE_REPLAY,
                source_ref=blank,
            )
            assert req.source_ref is None

    def test_source_ref_omitted_defaults_to_none(self):
        req = SolveRequest(source_type=SOURCE_TYPE_MISTAKE_REPLAY)
        assert req.source_ref is None

    def test_source_ref_strips_surrounding_whitespace(self):
        req = SolveRequest(
            source_type=SOURCE_TYPE_MISTAKE_REPLAY,
            source_ref="  game_42:move_18  ",
        )
        assert req.source_ref == "game_42:move_18"


# ---------------------------------------------------------------------------
# 2.  Endpoint behaviour (against an in-memory DB)
# ---------------------------------------------------------------------------


class TestSolveEndpoint:
    """POST /training/solve behaviour with a real SQLAlchemy session."""

    def test_happy_path_credits_xp_and_inserts_row(self, player, db_session):
        """SOLVE_HAPPY_PATH_CREDITS_XP."""
        result = _solve(
            player,
            db_session,
            source_type=SOURCE_TYPE_MISTAKE_REPLAY,
            source_ref="game_42:move_18",
        )
        assert result.xp_awarded == XP_PER_SOLVE
        assert result.training_xp == XP_PER_SOLVE
        # ISO-8601: parseable, ends with ``T<time>`` (no timezone since
        # the ORM stores naive UTC).
        assert "T" in result.completed_at

        rows = db_session.query(TrainingCompletion).all()
        assert len(rows) == 1
        assert rows[0].source_type == SOURCE_TYPE_MISTAKE_REPLAY
        assert rows[0].source_ref == "game_42:move_18"
        assert rows[0].xp_awarded == XP_PER_SOLVE
        assert rows[0].player_id == player.id

    def test_three_solves_sums_to_thirty(self, player, db_session):
        """SOLVE_THREE_SOLVES_SUMS — three distinct refs → training_xp = 3 × XP_PER_SOLVE."""
        for ref in ("g1:m1", "g2:m2", "g3:m3"):
            _solve(
                player,
                db_session,
                source_type=SOURCE_TYPE_MISTAKE_REPLAY,
                source_ref=ref,
            )
        assert player.training_xp == 3 * XP_PER_SOLVE

        # Re-fetch from DB to confirm the counter persisted, not just
        # the in-memory ORM instance.
        db_session.expire_all()
        refetched = db_session.query(Player).filter_by(id=player.id).first()
        assert refetched.training_xp == 3 * XP_PER_SOLVE

    def test_same_triple_dedups_second_call(self, player, db_session):
        """SOLVE_DEDUPS_BY_TRIPLE — second call with the same
        (source_type, source_ref) returns xp_awarded=0 and does NOT
        bump the counter."""
        first = _solve(
            player,
            db_session,
            source_type=SOURCE_TYPE_MISTAKE_REPLAY,
            source_ref="game_42:move_18",
        )
        assert first.xp_awarded == XP_PER_SOLVE

        second = _solve(
            player,
            db_session,
            source_type=SOURCE_TYPE_MISTAKE_REPLAY,
            source_ref="game_42:move_18",
        )
        assert second.xp_awarded == 0
        assert second.training_xp == XP_PER_SOLVE  # unchanged
        # completed_at should match the FIRST completion's timestamp
        # so the client can honestly render "solved on <date>" on a
        # retry.
        assert second.completed_at == first.completed_at

        # Still exactly one row in the DB.
        rows = db_session.query(TrainingCompletion).all()
        assert len(rows) == 1

    def test_dedup_is_scoped_per_player(self, player, other_player, db_session):
        """SOLVE_DEDUPS_PER_PLAYER — the dedup triple includes player_id;
        a different player solving the same source_ref must be credited."""
        _solve(
            player,
            db_session,
            source_type=SOURCE_TYPE_MISTAKE_REPLAY,
            source_ref="shared_ref",
        )
        result = _solve(
            other_player,
            db_session,
            source_type=SOURCE_TYPE_MISTAKE_REPLAY,
            source_ref="shared_ref",
        )
        assert result.xp_awarded == XP_PER_SOLVE
        assert other_player.training_xp == XP_PER_SOLVE
        assert db_session.query(TrainingCompletion).count() == 2

    def test_different_ref_does_not_dedup(self, player, db_session):
        """SOLVE_DIFFERENT_REF_DOES_NOT_DEDUP."""
        _solve(
            player,
            db_session,
            source_type=SOURCE_TYPE_MISTAKE_REPLAY,
            source_ref="ref_a",
        )
        result = _solve(
            player,
            db_session,
            source_type=SOURCE_TYPE_MISTAKE_REPLAY,
            source_ref="ref_b",
        )
        assert result.xp_awarded == XP_PER_SOLVE
        assert player.training_xp == 2 * XP_PER_SOLVE

    def test_different_source_type_does_not_dedup(self, player, db_session):
        """SOLVE_DIFFERENT_TYPE_DOES_NOT_DEDUP."""
        _solve(
            player,
            db_session,
            source_type=SOURCE_TYPE_MISTAKE_REPLAY,
            source_ref="shared",
        )
        result = _solve(
            player,
            db_session,
            source_type=SOURCE_TYPE_WEEKLY_MICROTASK,
            source_ref="shared",
        )
        assert result.xp_awarded == XP_PER_SOLVE
        assert player.training_xp == 2 * XP_PER_SOLVE

    def test_null_ref_does_not_dedup(self, player, db_session):
        """SOLVE_NULL_REF_DOES_NOT_DEDUP — rows with source_ref=NULL
        are explicitly NOT deduped (Postgres NULL-distinct semantics in
        the unique index; documented intent for catch-all completions
        where the caller doesn't yet have a stable identifier).  Two
        solves with no ref both credit XP."""
        first = _solve(
            player,
            db_session,
            source_type=SOURCE_TYPE_MISTAKE_REPLAY,
            source_ref=None,
        )
        second = _solve(
            player,
            db_session,
            source_type=SOURCE_TYPE_MISTAKE_REPLAY,
            source_ref=None,
        )
        assert first.xp_awarded == XP_PER_SOLVE
        assert second.xp_awarded == XP_PER_SOLVE
        assert player.training_xp == 2 * XP_PER_SOLVE
        assert db_session.query(TrainingCompletion).count() == 2

    def test_blank_ref_treated_as_null_for_dedup(self, player, db_session):
        """A blank source_ref is normalised to NULL at the schema layer,
        so two blank-ref solves are NOT deduped — same behaviour as
        explicit None.  Pins the schema validator's normalisation
        contract end-to-end."""
        _solve(
            player,
            db_session,
            source_type=SOURCE_TYPE_MISTAKE_REPLAY,
            source_ref="",
        )
        result = _solve(
            player,
            db_session,
            source_type=SOURCE_TYPE_MISTAKE_REPLAY,
            source_ref="   ",
        )
        assert result.xp_awarded == XP_PER_SOLVE
        assert player.training_xp == 2 * XP_PER_SOLVE

    def test_response_shape(self, player, db_session):
        """SOLVE_RESPONSE_SHAPE — response carries xp_awarded,
        training_xp, completed_at."""
        result = _solve(
            player,
            db_session,
            source_type=SOURCE_TYPE_STANDARD_PUZZLE,
            source_ref="lichess_abc123",
        )
        # Pydantic model_dump exposes the three documented fields.
        payload = result.model_dump()
        assert set(payload.keys()) == {"xp_awarded", "training_xp", "completed_at"}
        assert isinstance(payload["xp_awarded"], int)
        assert isinstance(payload["training_xp"], int)
        assert isinstance(payload["completed_at"], str)

    # ``test_player_training_xp_handles_null_legacy_value`` was deleted:
    # the column is ``nullable=False`` with ``DEFAULT 0`` on the
    # migration, so no DB row can carry NULL.  The defensive ``or 0``
    # in the router's increment line guards the in-memory mock case
    # (e.g. a ``SimpleNamespace`` without the attribute), which is
    # already pinned by ``test_api_contract_validation.py::
    # TestAuthMeContractSchema::test_training_xp_null_coerced_to_zero``.
