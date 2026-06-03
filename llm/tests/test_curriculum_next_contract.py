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
13. CURR_FALLBACK_WEAKEST_UNIT  CurriculumPolicy.choose_topic returns the highest-weakness key.
14. CURR_FALLBACK_DEFAULT       an empty skill vector falls back to 'opening_principles'.
15. CURR_FALLBACK_WEAKEST_E2E   /curriculum/next fallback (no game history) returns the weakest area.

**Fallback topic semantics — regression guard for the choose_topic inversion**

``skill_vector_json`` stores weakness magnitudes written by ``SkillUpdater``
(an EWMA of the per-phase mistake rate): a HIGHER value means a WEAKER area.
The skill-vector fallback (used when there is no game history, when the
dominant category has no topic mapping, or when the analysis pipeline raises)
must therefore train the area with the MAXIMUM weakness, mirroring
``HistoricalAnalysisPipeline.dominant_category``.  Invariants 13/15 pin this
against a regression to the inverted ``min(...)`` selection, which trained the
player's STRONGEST area.
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
from llm.seca.curriculum.policy import CurriculumPolicy
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


class TestCurriculumFallbackSelectsWeakestArea:
    """Skill-vector fallback must train the player's WEAKEST area.

    ``skill_vector_json`` stores weakness magnitudes written by SkillUpdater
    (an EWMA of the per-phase mistake rate) — a HIGHER value means a WEAKER
    area, mirroring ``HistoricalAnalysisPipeline.dominant_category`` (the
    highest-score category) and SkillUpdater's dominant-weakness action
    (``max(weaknesses, ...)``).  The weakest area is therefore the argmax, not
    the argmin.  These tests pin the fix for the inverted ``min(...)``
    selection in ``CurriculumPolicy.choose_topic`` that trained the player's
    STRONGEST area.
    """

    # opening = strongest (0.10), middlegame = weakest (0.55), endgame = 0.20
    _SKILL_VECTOR = '{"opening": 0.10, "middlegame": 0.55, "endgame": 0.20}'
    _WEAKEST = "middlegame"

    def test_choose_topic_returns_max_weakness_key(self):
        """CURR_FALLBACK_WEAKEST_UNIT: choose_topic picks the highest-weakness key.

        Directly exercises the policy unit so the assertion does not depend on
        router/DB plumbing.  Fails against the old ``min(...)`` (which would
        return 'opening', the STRONGEST area).
        """
        skill_vector = {"opening": 0.10, "middlegame": 0.55, "endgame": 0.20}
        topic = CurriculumPolicy().choose_topic(skill_vector)
        assert topic == "middlegame", (
            f"choose_topic must select the WEAKEST area (max weakness magnitude); "
            f"expected 'middlegame' (0.55), got {topic!r}. A result of 'opening' "
            f"(0.10) means the inverted min() selection regressed."
        )

    def test_choose_topic_empty_vector_returns_default(self):
        """CURR_FALLBACK_DEFAULT: an empty skill vector returns the default topic."""
        assert CurriculumPolicy().choose_topic({}) == "opening_principles"

    def test_next_training_fallback_selects_weakest_topic(self, db_session):
        """CURR_FALLBACK_WEAKEST_E2E: with no game history the topic is the weakest area.

        A player with a populated skill_vector but zero GameEvents drives the
        router's skill-vector fallback: ``recent_games == []`` ⇒ ``dominant_topic``
        is None ⇒ ``choose_topic(skill_vector)``.  The returned topic must be the
        highest-weakness area.  Fails against the old ``min(...)`` (returns 'opening').
        """
        p = Player(
            email="fallback@test.com",
            password_hash="hashed",
            rating=1350.0,
            confidence=0.6,
            skill_vector_json=self._SKILL_VECTOR,
            player_embedding="[]",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        result = _call_next(p, db_session)

        assert result["dominant_category"] is None, (
            "precondition: no game history ⇒ dominant_category is None (fallback path), "
            f"got {result['dominant_category']!r}"
        )
        assert result["topic"] == self._WEAKEST, (
            f"fallback topic must be the weakest area {self._WEAKEST!r} (0.55), "
            f"got {result['topic']!r}"
        )
