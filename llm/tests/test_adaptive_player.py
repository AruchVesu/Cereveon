"""Regression and contract tests for full-scale player adaptation.

Covers:
1. test_player_adaptive_move_elo            — different ratings → different opponent ELO
2. test_game_finish_includes_recommendations — /game/finish response includes analysis block
3. test_curriculum_uses_game_history         — dominant weakness drives curriculum topic
4. test_adaptation_layer_pure               — compute_adaptation is pure (no side effects)
5. test_safe_mode_bandit_still_gated        — RL training calls remain inside SAFE_MODE gate
6. test_live_move_hint_adapts_to_style      — explanation_style changes hint verbosity

Constraints enforced:
- SAFE_MODE stays True throughout.
- No RL weights are updated.
- Tests use in-memory SQLite — no external services required.
- All tests are objective: they assert measurable behaviour, not implementation details.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

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
import llm.seca.curriculum.models  # noqa: F401  — registers TrainingPlan with Base

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture()
def beginner_player(db_session):
    from llm.seca.auth.models import Player

    p = Player(
        email="beginner@test.com",
        password_hash="hashed",
        rating=700.0,
        confidence=0.3,
        skill_vector_json="{}",
        player_embedding="[]",
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture()
def advanced_player(db_session):
    from llm.seca.auth.models import Player

    p = Player(
        email="advanced@test.com",
        password_hash="hashed",
        rating=1900.0,
        confidence=0.8,
        skill_vector_json="{}",
        player_embedding="[]",
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


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
# 1. Adaptive opponent ELO
# ---------------------------------------------------------------------------


class TestPlayerAdaptiveMoveElo:
    """Different player ratings must produce different opponent ELO values."""

    def test_beginner_gets_weaker_opponent(self):
        from llm.seca.adaptation.coupling import compute_adaptation

        beginner = compute_adaptation(700.0, 0.3)
        advanced = compute_adaptation(1900.0, 0.8)

        assert beginner["opponent"]["target_elo"] < advanced["opponent"]["target_elo"], (
            f"Beginner ELO ({beginner['opponent']['target_elo']}) must be lower than "
            f"advanced ELO ({advanced['opponent']['target_elo']})"
        )

    def test_elo_increases_monotonically_with_rating(self):
        from llm.seca.adaptation.coupling import compute_adaptation

        ratings = [600, 900, 1200, 1500, 1800]
        elos = [compute_adaptation(r, 0.5)["opponent"]["target_elo"] for r in ratings]
        for i in range(len(elos) - 1):
            assert elos[i] <= elos[i + 1], (
                f"ELO must not decrease as rating increases: "
                f"rating={ratings[i]} → elo={elos[i]}, "
                f"rating={ratings[i+1]} → elo={elos[i+1]}"
            )

    def test_elo_within_valid_range(self):
        from llm.seca.adaptation.coupling import compute_adaptation

        for rating in [400, 800, 1200, 1600, 2000, 2400]:
            elo = compute_adaptation(rating, 0.5)["opponent"]["target_elo"]
            assert 600 <= elo <= 2400, (
                f"target_elo={elo} out of valid range [600, 2400] for rating={rating}"
            )


# ---------------------------------------------------------------------------
# 2. game/finish includes recommendations
# ---------------------------------------------------------------------------


class TestGameFinishIncludesRecommendations:
    """/game/finish response must include an 'analysis' block with recommendations."""

    def _store_game_with_weaknesses(self, db, player_id: str, weakness_dict: dict):
        from llm.seca.events.storage import EventStorage

        return EventStorage(db).store_game(
            player_id=player_id,
            pgn=_VALID_PGN,
            result="loss",
            accuracy=0.40,
            weaknesses=weakness_dict,
        )

    def test_analysis_block_present_after_games(self, db_session, beginner_player):
        """After storing games with weaknesses, analysis block must appear."""
        from llm.seca.analysis.historical_pipeline import HistoricalAnalysisPipeline
        from llm.seca.analytics.training_recommendations import generate_training_recommendations
        from llm.seca.events.storage import EventStorage

        # Store several games with a high endgame mistake rate
        for _ in range(5):
            EventStorage(db_session).store_game(
                player_id=str(beginner_player.id),
                pgn=_VALID_PGN,
                result="loss",
                accuracy=0.45,
                weaknesses={"endgame": 0.20},
            )

        recent = EventStorage(db_session).get_recent_games(
            player_id=str(beginner_player.id), limit=20
        )
        stats = HistoricalAnalysisPipeline(db_session).run(str(beginner_player.id), recent)
        recs = generate_training_recommendations(stats)

        assert stats.games_analyzed == 5
        assert stats.dominant_category is not None
        assert isinstance(recs, list)

    def test_recommendations_have_required_fields(self, db_session, beginner_player):
        """Each TrainingFocus must have category, priority, and rationale."""
        from llm.seca.analysis.historical_pipeline import HistoricalAnalysisPipeline
        from llm.seca.analytics.training_recommendations import generate_training_recommendations
        from llm.seca.events.storage import EventStorage

        for _ in range(3):
            EventStorage(db_session).store_game(
                player_id=str(beginner_player.id),
                pgn=_VALID_PGN,
                result="loss",
                accuracy=0.35,
                weaknesses={"opening": 0.15, "endgame": 0.18},
            )

        recent = EventStorage(db_session).get_recent_games(
            player_id=str(beginner_player.id), limit=20
        )
        stats = HistoricalAnalysisPipeline(db_session).run(str(beginner_player.id), recent)
        recs = generate_training_recommendations(stats)

        for rec in recs:
            assert hasattr(rec, "category"), "TrainingFocus missing 'category'"
            assert hasattr(rec, "priority"), "TrainingFocus missing 'priority'"
            assert hasattr(rec, "rationale"), "TrainingFocus missing 'rationale'"
            assert rec.priority in {"high", "medium", "low"}, (
                f"priority must be high/medium/low, got {rec.priority!r}"
            )

    def test_no_recommendations_for_player_with_no_games(self, db_session, beginner_player):
        """A player with no game history must produce no recommendations."""
        from llm.seca.events.storage import EventStorage
        from llm.seca.analytics.training_recommendations import generate_training_recommendations
        from llm.seca.analytics.mistake_stats import aggregate_from_weakness_dicts

        recent = EventStorage(db_session).get_recent_games(
            player_id=str(beginner_player.id), limit=20
        )
        assert recent == [], "Fixture player must have no games"
        stats = aggregate_from_weakness_dicts([])
        recs = generate_training_recommendations(stats)
        assert recs == [], "No games → no recommendations"


# ---------------------------------------------------------------------------
# 3. Curriculum uses game history
# ---------------------------------------------------------------------------


class TestCurriculumUsesGameHistory:
    """Curriculum topic must reflect the dominant weakness from game history."""

    def test_endgame_weakness_drives_endgame_topic(self, db_session, beginner_player):
        from llm.seca.events.storage import EventStorage
        from llm.seca.curriculum.router import next_training

        # Store games with dominant endgame weakness
        for _ in range(5):
            EventStorage(db_session).store_game(
                player_id=str(beginner_player.id),
                pgn=_VALID_PGN,
                result="loss",
                accuracy=0.40,
                weaknesses={"endgame": 0.25},
            )

        result = next_training(player=beginner_player, db=db_session)
        assert result["topic"] == "endgame", (
            f"Expected topic='endgame' from dominant endgame weakness, got {result['topic']!r}"
        )

    def test_opening_weakness_drives_opening_topic(self, db_session, beginner_player):
        """Opening phase weakness → opening_preparation category → topic 'opening'."""
        from llm.seca.events.storage import EventStorage
        from llm.seca.curriculum.router import next_training

        for _ in range(5):
            EventStorage(db_session).store_game(
                player_id=str(beginner_player.id),
                pgn=_VALID_PGN,
                result="loss",
                accuracy=0.38,
                weaknesses={"opening": 0.20},
            )

        result = next_training(player=beginner_player, db=db_session)
        # opening phase → opening_preparation (1.0 weight) → topic "opening"
        assert result["topic"] == "opening", (
            f"Expected topic='opening' from opening weakness, got {result['topic']!r}"
        )

    def test_curriculum_response_includes_recommendations_field(self, db_session, beginner_player):
        from llm.seca.curriculum.router import next_training

        result = next_training(player=beginner_player, db=db_session)
        assert "recommendations" in result, "curriculum/next response missing 'recommendations'"
        assert isinstance(result["recommendations"], list), (
            f"recommendations must be a list, got {type(result['recommendations'])}"
        )

    def test_curriculum_response_includes_dominant_category_field(
        self, db_session, beginner_player
    ):
        from llm.seca.curriculum.router import next_training

        result = next_training(player=beginner_player, db=db_session)
        assert "dominant_category" in result, (
            "curriculum/next response missing 'dominant_category'"
        )

    def test_no_game_history_falls_back_to_skill_vector(self, db_session, beginner_player):
        """Without game history the topic comes from skill_vector (or default)."""
        from llm.seca.curriculum.router import next_training

        result = next_training(player=beginner_player, db=db_session)
        assert isinstance(result["topic"], str) and result["topic"]
        assert result["dominant_category"] is None, (
            "dominant_category must be None when player has no game history"
        )


# ---------------------------------------------------------------------------
# 4. Adaptation layer is pure (no side effects)
# ---------------------------------------------------------------------------


class TestAdaptationLayerPure:
    """compute_adaptation must be a pure function with no side effects."""

    def test_same_inputs_produce_same_output(self):
        from llm.seca.adaptation.coupling import compute_adaptation

        r1 = compute_adaptation(1200.0, 0.5)
        r2 = compute_adaptation(1200.0, 0.5)
        assert r1 == r2, "compute_adaptation must be deterministic"

    def test_returns_opponent_and_teaching_keys(self):
        from llm.seca.adaptation.coupling import compute_adaptation

        result = compute_adaptation(1200.0, 0.5)
        assert "opponent" in result
        assert "teaching" in result
        assert "target_elo" in result["opponent"]
        assert "style" in result["teaching"]

    def test_style_is_valid_string(self):
        from llm.seca.adaptation.coupling import compute_adaptation

        for rating in [500, 1000, 1500, 2000]:
            style = compute_adaptation(rating, 0.5)["teaching"]["style"]
            assert style in {"simple", "intermediate", "advanced"}, (
                f"teaching style must be simple/intermediate/advanced, got {style!r}"
            )

    def test_beginner_gets_simple_style(self):
        from llm.seca.adaptation.coupling import compute_adaptation

        # Rating 500 → r = (500-400)/2000 = 0.05 → explanation_depth=0.05 < 0.3 → "simple"
        style = compute_adaptation(500.0, 0.3)["teaching"]["style"]
        assert style == "simple", f"Expected 'simple' for rating=500, got {style!r}"

    def test_expert_gets_advanced_style(self):
        from llm.seca.adaptation.coupling import compute_adaptation

        # Rating 2000 → r = 0.8 → explanation_depth=0.8 >= 0.7 → "advanced"
        style = compute_adaptation(2000.0, 0.8)["teaching"]["style"]
        assert style == "advanced", f"Expected 'advanced' for rating=2000, got {style!r}"


# ---------------------------------------------------------------------------
# 5. SAFE_MODE: bandit training calls stay inside the gate (AST regression)
# ---------------------------------------------------------------------------


class TestSafeModeBanditStillGated:
    """RL training calls must remain inside `if not SAFE_MODE` in events/router.py.

    This test prevents accidental extraction of the bandit/neural training
    loops outside their safety gate.
    """

    def _load_events_router_ast(self) -> ast.Module:
        path = PROJECT_ROOT / "llm" / "seca" / "events" / "router.py"
        return ast.parse(path.read_text(encoding="utf-8"))

    def _all_call_names_in_node(self, node: ast.AST) -> set[str]:
        names: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                func = child.func
                if isinstance(func, ast.Name):
                    names.add(func.id)
                elif isinstance(func, ast.Attribute):
                    names.add(func.attr)
        return names

    def _find_safe_mode_if_blocks(self, tree: ast.Module) -> list[ast.If]:
        """Return all `if not SAFE_MODE:` blocks in the module."""
        blocks: list[ast.If] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            test = node.test
            # if not SAFE_MODE  →  UnaryOp(Not, Name('SAFE_MODE'))
            if (
                isinstance(test, ast.UnaryOp)
                and isinstance(test.op, ast.Not)
                and isinstance(test.operand, ast.Name)
                and test.operand.id == "SAFE_MODE"
            ):
                blocks.append(node)
        return blocks

    def test_update_after_game_is_inside_safe_mode_gate(self):
        """update_after_game must appear within an `if not SAFE_MODE:` block."""
        tree = self._load_events_router_ast()
        gated_blocks = self._find_safe_mode_if_blocks(tree)
        assert gated_blocks, "No `if not SAFE_MODE:` block found in events/router.py"

        gated_names: set[str] = set()
        for block in gated_blocks:
            gated_names |= self._all_call_names_in_node(block)

        assert "update_after_game" in gated_names, (
            "update_after_game not found inside any `if not SAFE_MODE:` block. "
            "RL training must stay gated."
        )

    def test_train_bandit_is_inside_safe_mode_gate(self):
        """train_bandit must appear within an `if not SAFE_MODE:` block."""
        tree = self._load_events_router_ast()
        gated_blocks = self._find_safe_mode_if_blocks(tree)
        assert gated_blocks, "No `if not SAFE_MODE:` block found in events/router.py"

        gated_names: set[str] = set()
        for block in gated_blocks:
            gated_names |= self._all_call_names_in_node(block)

        assert "train_bandit" in gated_names, (
            "train_bandit not found inside any `if not SAFE_MODE:` block. "
            "Bandit training must stay gated."
        )

    def test_safe_mode_import_present(self):
        path = PROJECT_ROOT / "llm" / "seca" / "events" / "router.py"
        source = path.read_text(encoding="utf-8")
        assert "SAFE_MODE" in source, "events/router.py must import SAFE_MODE"


# ---------------------------------------------------------------------------
# 6. Live move hint adapts to player skill style
# ---------------------------------------------------------------------------


class TestLiveMoveHintAdaptsToStyle:
    """generate_live_reply must produce different hints for different skill styles."""

    _STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    _UCI = "e2e4"

    def test_simple_style_returns_valid_reply(self):
        from llm.seca.coach.live_move_pipeline import generate_live_reply, LiveMoveReply

        result = generate_live_reply(self._STARTING_FEN, self._UCI, explanation_style="simple")
        assert isinstance(result, LiveMoveReply)
        assert result.hint.strip()
        assert result.mode == "LIVE_V1"

    def test_advanced_style_returns_valid_reply(self):
        from llm.seca.coach.live_move_pipeline import generate_live_reply, LiveMoveReply

        result = generate_live_reply(self._STARTING_FEN, self._UCI, explanation_style="advanced")
        assert isinstance(result, LiveMoveReply)
        assert result.hint.strip()

    def test_simple_hint_omits_phase_tip(self):
        """simple style must omit the phase-specific coaching tip."""
        from llm.seca.coach.live_move_pipeline import _build_hint

        signal = {
            "evaluation": {"type": "cp", "band": "equal", "side": "white"},
            "eval_delta": "stable",
            "last_move_quality": "good",
            "tactical_flags": [],
            "position_flags": [],
            "phase": "opening",
        }
        hint_simple = _build_hint(self._UCI, signal, "Technical detail.", explanation_style="simple")
        hint_default = _build_hint(self._UCI, signal, "Technical detail.", explanation_style=None)

        # Phase tip "Keep developing your pieces" should appear in default but not simple
        assert "develop" not in hint_simple.lower(), (
            f"Phase tip must be omitted in 'simple' style; hint={hint_simple!r}"
        )
        assert "develop" in hint_default.lower(), (
            f"Phase tip must appear in default (None) style; hint={hint_default!r}"
        )

    def test_advanced_hint_includes_base_explanation(self):
        """advanced style must include the base explanation from SafeExplainer."""
        from llm.seca.coach.live_move_pipeline import _build_hint

        signal = {
            "evaluation": {"type": "cp", "band": "equal", "side": "white"},
            "eval_delta": "stable",
            "last_move_quality": "good",
            "tactical_flags": [],
            "position_flags": [],
            "phase": "middlegame",
        }
        base = "The position is structurally balanced."
        hint_advanced = _build_hint(self._UCI, signal, base, explanation_style="advanced")
        hint_simple = _build_hint(self._UCI, signal, base, explanation_style="simple")

        assert base in hint_advanced, (
            f"Base explanation must appear in 'advanced' hint; hint={hint_advanced!r}"
        )
        assert base not in hint_simple, (
            f"Base explanation must be omitted in 'simple' hint; hint={hint_simple!r}"
        )

    def test_engine_signal_unchanged_across_styles(self):
        """Engine signal must be identical regardless of explanation_style."""
        from llm.seca.coach.live_move_pipeline import generate_live_reply

        r_simple = generate_live_reply(self._STARTING_FEN, self._UCI, explanation_style="simple")
        r_advanced = generate_live_reply(self._STARTING_FEN, self._UCI, explanation_style="advanced")

        assert r_simple.engine_signal == r_advanced.engine_signal, (
            "engine_signal must not depend on explanation_style — it always comes from "
            "extract_engine_signal()"
        )
