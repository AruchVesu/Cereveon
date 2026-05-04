"""
Full SECA loop integration test: register → finish 3 games → /auth/me → rating changed.

Uses an in-memory SQLite database (no external services required).  All model
tables are created from SQLAlchemy metadata so the test reflects the real
schema, not mocked stubs.

The test calls the layer functions directly (EventStorage, SkillUpdater, me())
rather than the HTTP endpoint, which would require a full ASGI test client and
Coach/PostGameCoach mocks.  This approach is sufficient to pin the integration
contract: data written by SkillUpdater is readable via the /auth/me handler.

Pinned invariants
-----------------
 1. LOOP_RATING_CHANGES        rating must differ from the default (1200) after 3 wins.
 2. LOOP_RATING_INCREASES      three wins must increase the rating.
 3. LOOP_RATING_IN_ME          /auth/me returns the post-game rating, not the initial value.
 4. LOOP_CONFIDENCE_IN_ME      /auth/me returns the post-game confidence.
 5. LOOP_SKILL_VECTOR_UPDATED  /auth/me skill_vector reflects accumulated weaknesses.
 6. LOOP_ME_EMAIL_PRESERVED    /auth/me email field is unchanged after game updates.
 7. LOOP_THREE_EVENTS_STORED   exactly 3 GameEvent rows are persisted.
 8. LOOP_RATING_MONOTONE_WINS  each successive win increases the rating.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Must import all models before create_all so every table is in metadata.
from llm.seca.auth.models import Base
import llm.seca.auth.models  # noqa: F401
import llm.seca.events.models  # noqa: F401
import llm.seca.brain.models  # noqa: F401
import llm.seca.analytics.models  # noqa: F401

from llm.seca.auth.models import Player
from llm.seca.events.models import GameEvent
from llm.seca.events.storage import EventStorage
from llm.seca.skills.updater import SkillUpdater
from llm.seca.auth.router import me

_VALID_PGN = (
    '[Event "Test"]\n'
    '[Site "?"]\n'
    '[Date "2026.01.01"]\n'
    '[Round "1"]\n'
    '[White "Tester"]\n'
    '[Black "Bot"]\n'
    '[Result "1-0"]\n'
    "\n"
    "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    """In-memory SQLite session with all SECA tables — torn down after each test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _register_player(db, email: str = "loop@test.com") -> Player:
    """Insert a Player row with default rating/confidence and return it."""
    player = Player(
        email=email,
        password_hash="hashed-pw",
        rating=1200.0,
        confidence=0.5,
        skill_vector_json="{}",
        player_embedding="[]",
    )
    db.add(player)
    db.commit()
    db.refresh(player)
    return player


def _finish_game(db, player: Player, result: str, accuracy: float, weaknesses: dict):
    """Store a game event and run SkillUpdater — mirrors finish_game() core logic."""
    storage = EventStorage(db)
    event = storage.store_game(
        player_id=player.id,
        pgn=_VALID_PGN,
        result=result,
        accuracy=accuracy,
        weaknesses=weaknesses,
    )
    SkillUpdater(db).update_from_event(player.id, event)
    db.refresh(player)
    return event


# ---------------------------------------------------------------------------
# Integration test class
# ---------------------------------------------------------------------------


class TestFullLoopIntegration:
    """register → finish 3 games → /auth/me → confirm rating changed."""

    def _run_loop(self, db_session):
        """Execute the full loop and return (player, me_response)."""
        player = _register_player(db_session)
        initial_rating = player.rating  # 1200.0

        _finish_game(db_session, player, "win", accuracy=0.85, weaknesses={"tactics": 0.6})
        _finish_game(db_session, player, "win", accuracy=0.90, weaknesses={"endgame": 0.4})
        _finish_game(db_session, player, "win", accuracy=0.80, weaknesses={"tactics": 0.5})

        me_resp = me(player=player)
        return player, me_resp, initial_rating

    def test_loop_rating_changes(self, db_session):
        """LOOP_RATING_CHANGES: rating must differ from the default after 3 wins."""
        _, me_resp, initial_rating = self._run_loop(db_session)
        assert me_resp["rating"] != initial_rating, (
            f"Rating must change after 3 games; still {initial_rating}"
        )

    def test_loop_rating_increases_after_wins(self, db_session):
        """LOOP_RATING_INCREASES: three wins must increase the rating above initial."""
        _, me_resp, initial_rating = self._run_loop(db_session)
        assert me_resp["rating"] > initial_rating, (
            f"Expected rating > {initial_rating}, got {me_resp['rating']}"
        )

    def test_loop_rating_in_me_response(self, db_session):
        """LOOP_RATING_IN_ME: /auth/me returns the post-game rating, not the initial value."""
        player, me_resp, _ = self._run_loop(db_session)
        assert me_resp["rating"] == player.rating, (
            f"me() rating {me_resp['rating']} does not match player.rating {player.rating}"
        )

    def test_loop_confidence_in_me_response(self, db_session):
        """LOOP_CONFIDENCE_IN_ME: /auth/me returns the post-game confidence."""
        player, me_resp, _ = self._run_loop(db_session)
        assert me_resp["confidence"] == player.confidence, (
            f"me() confidence {me_resp['confidence']} != player.confidence {player.confidence}"
        )

    def test_loop_skill_vector_updated(self, db_session):
        """LOOP_SKILL_VECTOR_UPDATED: skill_vector contains the accumulated weaknesses."""
        _, me_resp, _ = self._run_loop(db_session)
        sv = me_resp["skill_vector"]
        assert isinstance(sv, dict), f"skill_vector must be dict, got {type(sv)}"
        assert "tactics" in sv, (
            "tactics was submitted in 2 of 3 games; must appear in skill_vector"
        )

    def test_loop_me_email_preserved(self, db_session):
        """LOOP_ME_EMAIL_PRESERVED: /auth/me email is unchanged after game updates."""
        player, me_resp, _ = self._run_loop(db_session)
        assert me_resp["email"] == player.email, (
            f"email must not change; expected {player.email!r}, got {me_resp['email']!r}"
        )

    def test_loop_three_events_stored(self, db_session):
        """LOOP_THREE_EVENTS_STORED: exactly 3 GameEvent rows are persisted."""
        player = _register_player(db_session)
        _finish_game(db_session, player, "win", 0.85, {"tactics": 0.6})
        _finish_game(db_session, player, "win", 0.90, {"endgame": 0.4})
        _finish_game(db_session, player, "win", 0.80, {"tactics": 0.5})
        rows = db_session.query(GameEvent).all()
        assert len(rows) == 3, f"Expected 3 GameEvent rows, got {len(rows)}"

    def test_loop_rating_monotone_wins(self, db_session):
        """LOOP_RATING_MONOTONE_WINS: each successive win pushes rating higher."""
        player = _register_player(db_session)
        ratings = [player.rating]

        for accuracy in (0.75, 0.80, 0.85):
            _finish_game(db_session, player, "win", accuracy, weaknesses={})
            ratings.append(player.rating)

        for i in range(1, len(ratings)):
            assert ratings[i] > ratings[i - 1], (
                f"Rating at step {i} ({ratings[i]}) must exceed step {i-1} ({ratings[i-1]})"
            )
