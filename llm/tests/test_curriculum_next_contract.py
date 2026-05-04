"""
Contract tests for POST /curriculum/next.

Verifies that the response schema produced by CurriculumGenerator matches the
contract documented in docs/API_CONTRACTS.md §2 and consumed by the Android
CurriculumRecommendation data class.

Tests call next_training() directly with a real in-memory SQLite database and
a real Player row, so CurriculumGenerator.generate() runs against actual DB
state rather than mocks.  No HTTP layer is required.

**Known schema note — difficulty field type**

The backend CurriculumPolicy.choose_difficulty() returns a string level
("easy", "medium", "hard"), stored as VARCHAR in TrainingPlan.difficulty.
The Android CurriculumRecommendation.difficulty is Float; the HTTP client
uses json.optDouble("difficulty", 0.5) which degrades to the default 0.5
when the field is a string.  This mismatch is pinned as-is by CURR_DIFFICULTY_IS_STR
so that a future schema fix (returning 0.0–1.0 float) will cause a deliberate
test failure prompting both sides to be updated together.

Pinned invariants
-----------------
 1. CURR_HAS_TOPIC              response contains 'topic' field.
 2. CURR_TOPIC_IS_STR           topic is a non-empty string.
 3. CURR_HAS_DIFFICULTY         response contains 'difficulty' field.
 4. CURR_DIFFICULTY_IS_STR      difficulty is a string (backend schema note above).
 5. CURR_DIFFICULTY_VALID_LEVEL difficulty is one of "easy", "medium", "hard".
 6. CURR_HAS_EXERCISE_TYPE      response contains 'exercise_type' field.
 7. CURR_EXERCISE_TYPE_STR      exercise_type is a non-empty string.
 8. CURR_HAS_PAYLOAD            response contains 'payload' field.
 9. CURR_PAYLOAD_IS_DICT        payload is a dict.
10. CURR_NO_FORMAT_FIELD        'format' must NOT appear (/next-training conflict guard).
11. CURR_NO_EXPECTED_GAIN       'expected_gain' must NOT appear (/next-training conflict guard).
12. CURR_DETERMINISTIC_TOPIC    two calls for the same player produce the same topic.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from llm.seca.auth.models import Base
import llm.seca.auth.models  # noqa: F401
import llm.seca.events.models  # noqa: F401
import llm.seca.brain.models  # noqa: F401
import llm.seca.analytics.models  # noqa: F401

from llm.seca.auth.models import Player
from llm.seca.curriculum.router import next_training


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    """In-memory SQLite session — torn down after each test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture()
def player(db_session):
    """Persisted Player row with default rating / empty skill vector."""
    p = Player(
        email="curriculum@test.com",
        password_hash="hashed",
        rating=1350.0,
        confidence=0.6,
        skill_vector_json="{}",
        player_embedding="[]",
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _call_next(player, db):
    """Call next_training() with the given player and db session."""
    return next_training(player=player, db=db)


# ---------------------------------------------------------------------------
# Contract test class
# ---------------------------------------------------------------------------


class TestCurriculumNextContractSchema:
    """POST /curriculum/next response must match CurriculumRecommendation schema."""

    def test_curr_has_topic(self, player, db_session):
        """CURR_HAS_TOPIC: response must contain 'topic'."""
        result = _call_next(player, db_session)
        assert "topic" in result, "POST /curriculum/next response missing 'topic'"

    def test_curr_topic_is_nonempty_str(self, player, db_session):
        """CURR_TOPIC_IS_STR: topic is a non-empty string."""
        result = _call_next(player, db_session)
        assert isinstance(result["topic"], str) and result["topic"], (
            f"topic must be a non-empty string, got {result['topic']!r}"
        )

    def test_curr_has_difficulty(self, player, db_session):
        """CURR_HAS_DIFFICULTY: response must contain 'difficulty'."""
        result = _call_next(player, db_session)
        assert "difficulty" in result, "POST /curriculum/next response missing 'difficulty'"

    def test_curr_difficulty_is_str(self, player, db_session):
        """CURR_DIFFICULTY_IS_STR: difficulty is a string level (backend schema — see module docstring).

        CurriculumPolicy.choose_difficulty() returns "easy" | "medium" | "hard".
        If this test starts failing because difficulty became a float, update both
        this test AND the Android CurriculumRecommendation parsing together.
        """
        result = _call_next(player, db_session)
        assert isinstance(result["difficulty"], str), (
            f"difficulty must be a string level ('easy'/'medium'/'hard'), "
            f"got {type(result['difficulty'])}: {result['difficulty']!r}. "
            f"See module docstring for the known Android/backend type mismatch."
        )

    def test_curr_difficulty_valid_level(self, player, db_session):
        """CURR_DIFFICULTY_VALID_LEVEL: difficulty is one of the documented string levels."""
        _VALID_LEVELS = {"easy", "medium", "hard"}
        result = _call_next(player, db_session)
        assert result["difficulty"] in _VALID_LEVELS, (
            f"difficulty={result['difficulty']!r} not in {_VALID_LEVELS}"
        )

    def test_curr_has_exercise_type(self, player, db_session):
        """CURR_HAS_EXERCISE_TYPE: response must contain 'exercise_type'."""
        result = _call_next(player, db_session)
        assert "exercise_type" in result, (
            "POST /curriculum/next response missing 'exercise_type'"
        )

    def test_curr_exercise_type_is_nonempty_str(self, player, db_session):
        """CURR_EXERCISE_TYPE_STR: exercise_type is a non-empty string."""
        result = _call_next(player, db_session)
        assert isinstance(result["exercise_type"], str) and result["exercise_type"], (
            f"exercise_type must be a non-empty string, got {result['exercise_type']!r}"
        )

    def test_curr_has_payload(self, player, db_session):
        """CURR_HAS_PAYLOAD: response must contain 'payload'."""
        result = _call_next(player, db_session)
        assert "payload" in result, "POST /curriculum/next response missing 'payload'"

    def test_curr_payload_is_dict(self, player, db_session):
        """CURR_PAYLOAD_IS_DICT: payload is a dict."""
        result = _call_next(player, db_session)
        assert isinstance(result["payload"], dict), (
            f"payload must be a dict, got {type(result['payload'])}"
        )

    def test_curr_no_format_field(self, player, db_session):
        """CURR_NO_FORMAT_FIELD: 'format' must NOT appear — belongs to /next-training."""
        result = _call_next(player, db_session)
        assert "format" not in result, (
            "'format' must not appear in /curriculum/next — it belongs to /next-training. "
            "Android must not conflate CurriculumRecommendation with TrainingRecommendation."
        )

    def test_curr_no_expected_gain_field(self, player, db_session):
        """CURR_NO_EXPECTED_GAIN: 'expected_gain' must NOT appear — belongs to /next-training."""
        result = _call_next(player, db_session)
        assert "expected_gain" not in result, (
            "'expected_gain' must not appear in /curriculum/next — it belongs to /next-training."
        )

    def test_curr_deterministic_topic(self, player, db_session):
        """CURR_DETERMINISTIC_TOPIC: same player state produces the same topic on two calls."""
        result1 = _call_next(player, db_session)
        result2 = _call_next(player, db_session)
        assert result1["topic"] == result2["topic"], (
            f"topic must be deterministic for the same player; "
            f"got {result1['topic']!r} then {result2['topic']!r}"
        )
