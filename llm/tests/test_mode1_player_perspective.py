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


if __name__ == "__main__":  # pragma: no cover - manual runner
    unittest.main()
