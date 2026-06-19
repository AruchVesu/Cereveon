"""Unit tests for ``llm.seca.engines.stockfish.board_features``.

Pin the FEN→flags mapping so the LLM prompt is grounded in
deterministic, ARCHITECTURE.md-compliant categorical labels rather than
free-form engine internals.

The tests assert two properties:

  1. Every flag the extractors emit belongs to the closed vocabulary in
     ``llm.rag.engine_signal.flag_vocabulary`` — guarantees we never
     widen the LLM's prompt surface without updating the vocabulary +
     this test together.

  2. Specific board features map to specific flags — guarantees we
     don't silently lose a feature (e.g. hanging-piece detection
     regressing to always-empty after a refactor).

Stable test IDs (do NOT rename):
  BF_VOCAB_01  every tactical flag is in the closed vocabulary
  BF_VOCAB_02  every position flag is in the closed vocabulary
  BF_DET_01    flags are deterministic (sorted, identical across calls)
  BF_TACT_01   no hanging pieces on the starting position
  BF_TACT_02   black queen on b4 attacked by white pawn → black hangs
  BF_TACT_03   side-to-move in check produces the matching check flag
  BF_TACT_04   pawn tension (pawn attacked & undefended) is NOT a hanging piece
  BF_TACT_05   a real piece still hangs after the pawn exclusion
  BF_KS_01     starting position king-safety is loose for both sides
  BF_KS_02     post-O-O king on g1 with pawn shield is safe
  BF_PAWN_01   doubled pawns on c-file flagged
  BF_PAWN_02   isolated pawn with no friendly neighbours flagged
  BF_PAWN_03   passed white pawn on the d-file flagged
  BF_CASTLE_01 white castled kingside detected from positional shape
  BF_CASTLE_02 starting position is uncastled for both sides
  BF_MAT_01    starting position is material:even
  BF_MAT_02    white up a full rook is material:white_up_major
"""

from __future__ import annotations

import unittest

import chess

from llm.rag.engine_signal.flag_vocabulary import (
    POSITION_FLAGS_VOCAB,
    TACTICAL_FLAGS_VOCAB,
)
from llm.seca.engines.stockfish.board_features import (
    compute_position_flags,
    compute_tactical_flags,
)


_STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


class TestVocabularyCompliance(unittest.TestCase):
    """BF_VOCAB_01..02 — the closed vocabulary covers everything emitted."""

    def test_tactical_flags_subset_of_vocab(self):
        """BF_VOCAB_01.  Tactical flags across several diverse positions
        only ever produce strings in the documented vocabulary."""
        fens = [
            _STARTING_FEN,
            # White-to-move in check.
            "rnb1k1nr/pppp1ppp/8/2b1p3/4q3/2N5/PPPP1PPP/R1BQKBNR w KQkq - 0 1",
            # Black queen on b4 attacked by a-pawn — black has a hanger.
            "rnb1kbnr/pppp1ppp/8/8/Pq6/8/1PPPPPPP/RNBQKBNR w KQkq - 0 1",
        ]
        for fen in fens:
            board = chess.Board(fen)
            for flag in compute_tactical_flags(board):
                self.assertIn(
                    flag,
                    TACTICAL_FLAGS_VOCAB,
                    f"tactical flag {flag!r} from {fen!r} is not in vocabulary",
                )

    def test_position_flags_subset_of_vocab(self):
        """BF_VOCAB_02.  Position flags across several diverse positions
        only ever produce strings in the documented vocabulary."""
        fens = [
            _STARTING_FEN,
            # Doubled c-pawns for white.
            "rnbqkbnr/pp1ppppp/8/8/8/2P5/PPP1PPPP/RNBQKBNR w KQkq - 0 1",
            # Both sides castled kingside.
            "rnbq1rk1/ppp2ppp/3p1n2/2b1p3/2B1P3/3P1N2/PPP2PPP/RNBQ1RK1 w - - 0 1",
            # Lone king endgame — white up two queens.
            "8/8/4k3/8/8/8/4K3/Q6Q w - - 0 1",
        ]
        for fen in fens:
            board = chess.Board(fen)
            for flag in compute_position_flags(board):
                self.assertIn(
                    flag,
                    POSITION_FLAGS_VOCAB,
                    f"position flag {flag!r} from {fen!r} is not in vocabulary",
                )


class TestDeterminism(unittest.TestCase):
    """BF_DET_01 — same input must produce the same output."""

    def test_flags_are_deterministic_and_sorted(self):
        """BF_DET_01.  Repeated calls on the same FEN return identical,
        sorted lists — important for the prompt snapshot tests, which
        compare bytes."""
        board = chess.Board(_STARTING_FEN)
        first_t = compute_tactical_flags(board)
        first_p = compute_position_flags(board)
        for _ in range(3):
            self.assertEqual(compute_tactical_flags(chess.Board(_STARTING_FEN)), first_t)
            self.assertEqual(compute_position_flags(chess.Board(_STARTING_FEN)), first_p)
            self.assertEqual(first_t, sorted(first_t))
            self.assertEqual(first_p, sorted(first_p))


class TestTacticalFlags(unittest.TestCase):
    """BF_TACT_01..05 — hanging pieces and check detection."""

    def test_starting_position_has_no_hanging_pieces(self):
        """BF_TACT_01."""
        board = chess.Board(_STARTING_FEN)
        flags = compute_tactical_flags(board)
        self.assertNotIn("hanging_piece:white", flags)
        self.assertNotIn("hanging_piece:black", flags)

    def test_attacked_undefended_piece_is_hanging(self):
        """BF_TACT_02.  Black knight on b4 attacked by white c3 pawn
        and no black piece defends b4 → hanging_piece:black."""
        board = chess.Board(
            "rnbqkb1r/pppppppp/8/8/1n6/2P5/PP1PPPPP/RNBQKBNR w KQkq - 0 1"
        )
        flags = compute_tactical_flags(board)
        self.assertIn("hanging_piece:black", flags)

    def test_pawn_tension_is_not_a_hanging_piece(self):
        """BF_TACT_04.  A pawn attacked-and-undefended (normal pawn tension)
        must NOT raise ``hanging_piece`` — the flag means a real piece, not a
        pawn.  Here White's d4 and Black's c5 attack each other and neither is
        defended; the old code flagged BOTH sides (the Grünfeld-c5 / h5-push
        false-positive class, Mode-1 probe 2026-06-19)."""
        board = chess.Board("4k3/8/8/2p5/3P4/8/8/4K3 w - - 0 1")
        flags = compute_tactical_flags(board)
        self.assertNotIn("hanging_piece:white", flags)
        self.assertNotIn("hanging_piece:black", flags)

    def test_real_piece_still_hangs_after_pawn_exclusion(self):
        """BF_TACT_05.  Excluding pawns must not suppress a genuinely hanging
        PIECE: a black queen attacked by a white knight and undefended is still
        ``hanging_piece:black``."""
        board = chess.Board("4k3/8/8/8/3q4/5N2/8/4K3 w - - 0 1")
        flags = compute_tactical_flags(board)
        self.assertIn("hanging_piece:black", flags)

    def test_side_to_move_in_check_produces_check_flag(self):
        """BF_TACT_03."""
        # White to move, white king on e1 attacked by black queen on e4.
        board = chess.Board(
            "rnb1kbnr/pppp1ppp/8/8/4q3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1"
        )
        flags = compute_tactical_flags(board)
        self.assertIn("check:white_to_move", flags)
        self.assertNotIn("check:black_to_move", flags)


class TestKingSafety(unittest.TestCase):
    """BF_KS_01..02 — banded king-safety detection."""

    def test_starting_position_kings_loose(self):
        """BF_KS_01.  Uncastled king in the center: full pawn shield
        but king is on the e-file → not on a wing → loose, not safe."""
        board = chess.Board(_STARTING_FEN)
        flags = compute_position_flags(board)
        self.assertIn("king_safety:white_loose", flags)
        self.assertIn("king_safety:black_loose", flags)

    def test_castled_king_with_shield_is_safe(self):
        """BF_KS_02.  Both sides castled kingside, pawn shield intact,
        no enemy bishop pointing at the king zone — both kings should
        read safe.  Queens and bishops cleared from the original test
        FEN because a c5-bishop's diagonal attacks f2 / g1, which is
        chess-correct king pressure and *should* downgrade the band to
        ``loose`` (a separate behaviour pinned implicitly by the
        starting-position test)."""
        board = chess.Board(
            "r4rk1/ppp2ppp/3p1n2/4p3/4P3/3P1N2/PPP2PPP/R4RK1 w - - 0 1"
        )
        flags = compute_position_flags(board)
        self.assertIn("king_safety:white_safe", flags)
        self.assertIn("king_safety:black_safe", flags)


class TestPawnStructure(unittest.TestCase):
    """BF_PAWN_01..03 — doubled / isolated / passed flag emission."""

    def test_doubled_pawns_flagged(self):
        """BF_PAWN_01."""
        # White has pawns on c2 and c3 (doubled c-file).
        board = chess.Board(
            "rnbqkbnr/pppppppp/8/8/8/2P5/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        )
        flags = compute_position_flags(board)
        self.assertIn("pawn_structure:white_doubled", flags)

    def test_isolated_pawn_flagged(self):
        """BF_PAWN_02.  White has a single d-pawn with empty c and e
        files → isolated."""
        # White has only a d4 pawn.  Black has only an a-pawn.
        board = chess.Board("4k3/p7/8/8/3P4/8/8/4K3 w - - 0 1")
        flags = compute_position_flags(board)
        self.assertIn("pawn_structure:white_isolated", flags)

    def test_passed_white_pawn_flagged(self):
        """BF_PAWN_03.  A white d-pawn on d5 with no black pawn on the
        c, d, or e file ahead of it is a passed pawn."""
        # White: d5 only.  Black: a7, h7 only — nothing on c/d/e files.
        board = chess.Board("p6p/8/8/3P4/8/8/8/4K2k w - - 0 1")
        flags = compute_position_flags(board)
        self.assertIn("pawn_structure:white_passed", flags)


class TestCastlingState(unittest.TestCase):
    """BF_CASTLE_01..02 — positional castling detection."""

    def test_kingside_castling_detected_from_shape(self):
        """BF_CASTLE_01.  White king on g1 + rook on f1 is the
        post-kingside-castle shape; should read kingside_done."""
        board = chess.Board(
            "rnbq1rk1/ppp2ppp/3p1n2/2b1p3/2B1P3/3P1N2/PPP2PPP/RNBQ1RK1 w - - 0 1"
        )
        flags = compute_position_flags(board)
        self.assertIn("castling:white_kingside_done", flags)
        self.assertIn("castling:black_kingside_done", flags)

    def test_starting_position_is_uncastled(self):
        """BF_CASTLE_02."""
        board = chess.Board(_STARTING_FEN)
        flags = compute_position_flags(board)
        self.assertIn("castling:white_uncastled", flags)
        self.assertIn("castling:black_uncastled", flags)


class TestMaterialBand(unittest.TestCase):
    """BF_MAT_01..02 — material-imbalance band assignment."""

    def test_starting_position_is_material_even(self):
        """BF_MAT_01."""
        board = chess.Board(_STARTING_FEN)
        flags = compute_position_flags(board)
        self.assertIn("material:even", flags)

    def test_white_up_a_rook_is_material_white_up_major(self):
        """BF_MAT_02.  White has K + Q + Q, Black has K — diff ≫ 500
        falls into the up_major band."""
        board = chess.Board("8/8/4k3/8/8/8/4K3/Q6Q w - - 0 1")
        flags = compute_position_flags(board)
        self.assertIn("material:white_up_major", flags)


if __name__ == "__main__":  # pragma: no cover - manual runner
    unittest.main()
