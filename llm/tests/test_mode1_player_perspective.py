"""
Unit tests for the Mode-1 player-perspective plumbing —
llm/tests/test_mode1_player_perspective.py

Pins the contract that ``/live/move``'s LLM hint addresses the player
from the player's perspective, not the engine's neutral side.  Without
this plumbing, DeepSeek defaulted to ``"you have a decisive advantage"``
even when the engine signal said ``side=white`` and the player was
Black being mated on f7 (production probe 2026-05-10, fixed in PR #88).

Stable test IDs (do NOT rename):
  MODE1_PC_01  _derive_player_color: "w" side-to-move → player is black
  MODE1_PC_02  _derive_player_color: "b" side-to-move → player is white
  MODE1_PC_03  _derive_player_color: "startpos" / empty / malformed → "unknown"
  MODE1_PC_04  _frame_player_perspective: same side as player → "you have ..."
  MODE1_PC_05  _frame_player_perspective: opposite side → "your opponent has ..."
  MODE1_PC_06  _frame_player_perspective: equal band → "the position is equal"
  MODE1_PC_07  _frame_player_perspective: mate for player → "you are about to deliver mate"
  MODE1_PC_08  _frame_player_perspective: mate against player → "you are about to be mated"
  MODE1_PC_09  _frame_player_perspective: unknown player → falls back to neutral
  MODE1_PC_10  render_mode_1_prompt: includes "Player colour" line and authoritative framing
"""

from __future__ import annotations

import unittest

from llm.rag.prompts.mode_1.render import (
    _frame_player_perspective,
    render_mode_1_prompt,
)
from llm.seca.coach.live_move_pipeline import _derive_player_color


# ---------------------------------------------------------------------------
# _derive_player_color
# ---------------------------------------------------------------------------


class TestDerivePlayerColor(unittest.TestCase):
    """MODE1_PC_01..03."""

    def test_w_side_to_move_means_player_is_black(self):
        """MODE1_PC_01.  After Black's move the FEN says White's turn.
        The player is therefore Black."""
        fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1"
        self.assertEqual(_derive_player_color(fen), "black")

    def test_b_side_to_move_means_player_is_white(self):
        """MODE1_PC_02.  After White's move the FEN says Black's turn.
        The player is therefore White."""
        fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        self.assertEqual(_derive_player_color(fen), "white")

    def test_startpos_or_malformed_returns_unknown(self):
        """MODE1_PC_03.  startpos / empty / malformed FENs return
        "unknown" so the renderer falls back to side-neutral framing."""
        self.assertEqual(_derive_player_color(""), "unknown")
        self.assertEqual(_derive_player_color("startpos"), "unknown")
        self.assertEqual(_derive_player_color("not even close"), "unknown")
        self.assertEqual(_derive_player_color("only-position-no-side"), "unknown")


# ---------------------------------------------------------------------------
# _frame_player_perspective
# ---------------------------------------------------------------------------


class TestFramePlayerPerspective(unittest.TestCase):
    """MODE1_PC_04..09."""

    def test_player_advantage(self):
        """MODE1_PC_04.  Engine side == player colour → "you have ..."."""
        out = _frame_player_perspective(
            eval_type="cp", band="decisive_advantage", side="white", player_color="white"
        )
        self.assertEqual(out, "you have a decisive advantage")

    def test_opponent_advantage(self):
        """MODE1_PC_05.  Engine side != player colour → "your opponent has ..."."""
        out = _frame_player_perspective(
            eval_type="cp", band="decisive_advantage", side="white", player_color="black"
        )
        self.assertEqual(out, "your opponent has a decisive advantage")

    def test_equal_band(self):
        """MODE1_PC_06.  Equal band normalises to "the position is equal" regardless
        of player colour or engine-side field."""
        for player in ("white", "black", "unknown"):
            out = _frame_player_perspective(
                eval_type="cp", band="equal", side="white", player_color=player
            )
            self.assertEqual(out, "the position is equal", f"player={player}")

    def test_mate_for_player(self):
        """MODE1_PC_07.  Mate with engine side == player colour → "deliver mate"."""
        out = _frame_player_perspective(
            eval_type="mate", band="decisive_advantage", side="white", player_color="white"
        )
        self.assertEqual(out, "you are about to deliver mate")

    def test_mate_against_player(self):
        """MODE1_PC_08.  Mate with engine side != player colour → "be mated"."""
        out = _frame_player_perspective(
            eval_type="mate", band="decisive_advantage", side="white", player_color="black"
        )
        self.assertEqual(out, "you are about to be mated")

    def test_unknown_player_falls_back_to_neutral(self):
        """MODE1_PC_09.  When player_color is "unknown" the renderer
        cannot do the perspective comparison and falls back to the raw
        side-keyed phrasing (preserves pre-PR-#88 behaviour)."""
        out = _frame_player_perspective(
            eval_type="cp", band="decisive_advantage", side="white", player_color="unknown"
        )
        self.assertEqual(out, "white has a decisive advantage")


# ---------------------------------------------------------------------------
# render_mode_1_prompt — end-to-end pinning
# ---------------------------------------------------------------------------


class TestRenderMode1Prompt(unittest.TestCase):
    """MODE1_PC_10."""

    def test_prompt_includes_player_colour_and_authoritative_framing(self):
        """MODE1_PC_10.  The rendered prompt MUST contain the player
        colour, the pre-framed perspective line, and the directive
        telling DeepSeek to use that framing instead of re-deriving
        from engine_signal.side.

        Without all three, DeepSeek can drift back to the wrong
        ``side``-keyed interpretation that PR #88 was written to fix.
        """
        engine_signal = {
            "evaluation": {"type": "cp", "band": "decisive_advantage", "side": "white"},
            "eval_delta": "stable",
            "last_move_quality": "blunder",
            "tactical_flags": [],
            "position_flags": [],
            "phase": "opening",
        }
        prompt = render_mode_1_prompt(
            system_prompt="<SYSTEM PROMPT>",
            engine_signal=engine_signal,
            fen="r1bqkb1r/ppp2ppp/2np1n2/4p1N1/2B1P3/8/PPPP1PPP/RNBQK2R w KQkq - 0 5",
            explanation_style="intermediate",
            rag_docs=None,
            player_color="black",
        )

        # Player colour visible in the context block.
        self.assertIn("Player colour: black", prompt)

        # Pre-framed perspective line resolves to "your opponent has..."
        # because engine side=white and player_color=black.
        self.assertIn("your opponent has a decisive advantage", prompt)

        # The directive must tell the LLM to use the "After the player's
        # move" line authoritatively.  Without this, the LLM tends to
        # also read engine_signal.side and disagree with itself.
        self.assertIn("After the player's move", prompt)
        self.assertIn('do NOT re-derive', prompt)


class TestRenderMode1EngineFacts(unittest.TestCase):
    """The Mode-1 prompt carries the same authoritative ENGINE FACTS grounding
    as Mode-2 chat, so the LLM can't invent tactics on complex positions.  The
    facts are player-perspective (flipped for a Black player) and the eval is
    NOT duplicated (POSITION CONTEXT already frames it)."""

    _SIGNAL = {
        "evaluation": {"type": "cp", "band": "small_advantage", "side": "white"},
        "eval_delta": "stable",
        "last_move_quality": "unknown",
        "tactical_flags": [],
        "position_flags": ["king_safety:black_exposed", "material:white_up_pawn"],
        "phase": "middlegame",
    }
    # FEN side-to-move "b" => player just moved => player is White.
    _FEN_PLAYER_WHITE = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
    # FEN side-to-move "w" => player is Black.
    _FEN_PLAYER_BLACK = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1"

    def test_facts_block_present_and_white_relative(self):
        prompt = render_mode_1_prompt(
            system_prompt="<SYS>",
            engine_signal=self._SIGNAL,
            fen=self._FEN_PLAYER_WHITE,
            explanation_style="intermediate",
            player_color="white",
        )
        self.assertIn("ENGINE FACTS", prompt)
        self.assertIn("do NOT invent", prompt)
        self.assertIn("Your opponent's king is exposed.", prompt)  # black_exposed -> opponent
        self.assertIn("You are up a pawn.", prompt)  # white_up_pawn -> you

    def test_facts_block_flips_for_black_player(self):
        prompt = render_mode_1_prompt(
            system_prompt="<SYS>",
            engine_signal=self._SIGNAL,
            fen=self._FEN_PLAYER_BLACK,
            explanation_style="intermediate",
            player_color="black",
        )
        # Same board flags, but the player is Black: facts must flip.
        self.assertIn("Your king is exposed.", prompt)  # black_exposed -> you
        self.assertIn("Your opponent is up a pawn.", prompt)  # white_up_pawn -> opponent
        self.assertNotIn("You are up a pawn.", prompt)

    def test_transient_check_fact_is_suppressed(self):
        """A check the player just delivered is answered by the engine's forced
        reply, so by the time the post-move hint is read the king is no longer
        in check.  Mode-1 must NOT ground the LLM on it (it rendered as a
        phantom "opponent's king is in check" on the post-reply board).

        Precision note (2026-07-10): the assertion is scoped to the
        GROUNDING sections (everything before the TASK header).  The TASK
        block deliberately QUOTES the phrase while forbidding it ("Never
        write ... 'the king is in check'"), which is the opposite of
        grounding on it.  The original whole-prompt assertNotIn tripped on
        that instruction quote — a latent failure invisible until this file
        was added to run_ci_suite (it was never in TEST_TARGETS)."""
        signal = {
            "evaluation": {"type": "cp", "band": "small_advantage", "side": "white"},
            "eval_delta": "stable",
            "last_move_quality": "unknown",
            "tactical_flags": ["check:black_to_move"],
            "position_flags": ["material:white_up_pawn"],
            "phase": "middlegame",
        }
        prompt = render_mode_1_prompt(
            system_prompt="<SYS>",
            engine_signal=signal,
            fen=self._FEN_PLAYER_WHITE,
            explanation_style="intermediate",
            player_color="white",
        )
        # The TASK header exists and splits grounding from instructions.
        self.assertIn("\nTASK\n", prompt)
        grounding = prompt.split("\nTASK\n", 1)[0]
        # Neither the prose fact nor the raw flag may reach the grounding.
        self.assertNotIn("king is in check", grounding)
        self.assertNotIn("check:black_to_move", grounding)
        # The TASK block's only mention is the forbidding instruction.
        self.assertIn('"the king is in check"', prompt)
        # Non-transient facts are still grounded.
        self.assertIn("You are up a pawn.", grounding)

    def test_threat_line_from_last_move_uci(self):
        """describe_threats grounds what the last move attacks (the #253 case:
        4.Ng5 hits f7 near the Black king)."""
        import chess

        board = chess.Board()
        for uci in ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6", "f3g5"]:
            board.push(chess.Move.from_uci(uci))
        prompt = render_mode_1_prompt(
            system_prompt="<SYS>",
            engine_signal={
                "evaluation": {"type": "cp", "band": "equal", "side": "white"},
                "tactical_flags": [],
                "position_flags": [],
                "phase": "opening",
            },
            fen=board.fen(),
            explanation_style="intermediate",
            player_color="white",
            last_move_uci="f3g5",
        )
        self.assertIn("ENGINE FACTS", prompt)
        self.assertIn("knight", prompt.lower())
        self.assertIn("around the opponent's king", prompt)

    def test_no_flags_no_threat_means_no_facts_block(self):
        """Back-compat: empty flags + no last_move_uci => no ENGINE FACTS block,
        so existing callers (and prompt snapshots) are unchanged."""
        prompt = render_mode_1_prompt(
            system_prompt="<SYS>",
            engine_signal={
                "evaluation": {"type": "cp", "band": "equal", "side": "white"},
                "tactical_flags": [],
                "position_flags": [],
                "phase": "opening",
            },
            fen=self._FEN_PLAYER_WHITE,
            explanation_style="intermediate",
            player_color="white",
        )
        self.assertNotIn("ENGINE FACTS", prompt)


class TestRenderMode1MoveQualityGuidance(unittest.TestCase):
    """When move quality is known (the handler graded the eval swing), the
    prompt tells the LLM to judge the MOVE by that grade rather than by who is
    currently ahead — the move-blame fix.  Dormant when quality is 'unknown'."""

    # FEN side-to-move "b" => player just moved => player is White.  Engine
    # side=black means the OPPONENT is ahead — the move-blame scenario.
    _FEN = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"

    @staticmethod
    def _signal(quality: str) -> dict:
        return {
            "evaluation": {"type": "cp", "band": "small_advantage", "side": "black"},
            "eval_delta": "stable",
            "last_move_quality": quality,
            "tactical_flags": [],
            "position_flags": [],
            "phase": "opening",
        }

    def test_known_quality_adds_judge_by_move_guidance(self):
        prompt = render_mode_1_prompt(
            system_prompt="<SYS>",
            engine_signal=self._signal("good"),
            fen=self._FEN,
            explanation_style="intermediate",
            player_color="white",
        )
        self.assertIn("Move quality: good", prompt)
        self.assertIn("Judge the move by THAT", prompt)

    def test_unknown_quality_omits_guidance(self):
        prompt = render_mode_1_prompt(
            system_prompt="<SYS>",
            engine_signal=self._signal("unknown"),
            fen=self._FEN,
            explanation_style="intermediate",
            player_color="white",
        )
        self.assertNotIn("Judge the move by THAT", prompt)


if __name__ == "__main__":  # pragma: no cover - manual runner
    unittest.main()
