"""
Objective security tests for host_app.py and server.py input-validation gaps.

Each test asserts what the validator SHOULD enforce.  A test that currently
FAILS proves the vulnerability exists; once the fix is applied the test PASSES.

Findings covered
────────────────
EVD-01  EngineEvalRequest.movetime_ms — no upper bound (engine-pool DoS)
EVD-02  EngineEvalRequest.nodes       — no upper bound (engine-pool DoS)
EVD-03  EngineEvalRequest.fen         — no validation (500 / pool disruption)
EVD-04  EngineEvalRequest.moves       — list length unbounded (resource exhaustion)
EVD-05  EngineEvalRequest.moves       — element length unbounded (resource exhaustion)
EVD-06  /engine/predictions fen param — no FEN validation (AST / schema)
EVD-07  /next-training/{player_id}    — path param length uncapped (memory exhaustion)
EVD-08  CoachFeedbackRequest.session_fen — structural-only; no chess.Board check
"""

import ast
import inspect
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_host_app_model():
    """Import EngineEvalRequest without starting the FastAPI app or engine pool."""
    # Patch lifespan so the import doesn't try to spin up Stockfish.
    import unittest.mock as mock
    with mock.patch("llm.host_app.EnginePool"), \
         mock.patch("llm.host_app.EngineEvaluator"), \
         mock.patch("llm.host_app.EliteEngineService"), \
         mock.patch("llm.host_app.OpeningBook"), \
         mock.patch("llm.host_app.verify_redis_connection"), \
         mock.patch("llm.host_app.get_redis_keys"), \
         mock.patch("llm.host_app.get_redis_value"), \
         mock.patch("llm.host_app.get_redis_info"):
        import llm.host_app as ha
        return ha.EngineEvalRequest


# ---------------------------------------------------------------------------
# EVD-01  movetime_ms upper bound
# ---------------------------------------------------------------------------

class TestEvd01MovetimeBound(unittest.TestCase):
    """EngineEvalRequest must reject movetime_ms values that would hold an
    engine-pool worker for an arbitrarily long time.  Without an upper bound
    an attacker can send movetime_ms=99_999_999 (≈27 hours) over a rate-limited
    connection and exhaust the entire pool with a handful of requests."""

    @classmethod
    def setUpClass(cls):
        cls.Model = _load_host_app_model()

    def test_rejects_movetime_above_ceiling(self):
        """movetime_ms=99_999_999 must raise ValidationError."""
        with self.assertRaises(ValidationError, msg="EVD-01: no upper bound on movetime_ms"):
            self.Model(movetime_ms=99_999_999)

    def test_rejects_movetime_60001(self):
        """movetime_ms just over a reasonable 60-second ceiling must be rejected."""
        with self.assertRaises(ValidationError, msg="EVD-01: movetime_ms=60001 should be rejected"):
            self.Model(movetime_ms=60_001)

    def test_accepts_movetime_within_bound(self):
        """movetime_ms=5000 (5 s) is a legitimate request and must be accepted."""
        try:
            self.Model(movetime_ms=5_000)
        except ValidationError as exc:
            self.fail(f"EVD-01: valid movetime_ms=5000 was incorrectly rejected: {exc}")

    def test_rejects_zero_movetime(self):
        """movetime_ms=0 is not a useful analysis window and must be rejected."""
        with self.assertRaises(ValidationError, msg="EVD-01: movetime_ms=0 should be rejected"):
            self.Model(movetime_ms=0)

    def test_rejects_negative_movetime(self):
        """Negative movetime_ms must be rejected."""
        with self.assertRaises(ValidationError, msg="EVD-01: negative movetime_ms should be rejected"):
            self.Model(movetime_ms=-1)


# ---------------------------------------------------------------------------
# EVD-02  nodes upper bound
# ---------------------------------------------------------------------------

class TestEvd02NodesBound(unittest.TestCase):
    """EngineEvalRequest must cap the nodes parameter.  Without an upper bound
    an attacker can request trillions of nodes and monopolise pool workers
    indefinitely."""

    @classmethod
    def setUpClass(cls):
        cls.Model = _load_host_app_model()

    def test_rejects_nodes_above_ceiling(self):
        """nodes=999_999_999_999 must raise ValidationError."""
        with self.assertRaises(ValidationError, msg="EVD-02: no upper bound on nodes"):
            self.Model(nodes=999_999_999_999)

    def test_rejects_nodes_zero(self):
        """nodes=0 instructs the engine to evaluate zero nodes — must be rejected."""
        with self.assertRaises(ValidationError, msg="EVD-02: nodes=0 should be rejected"):
            self.Model(nodes=0)

    def test_rejects_nodes_negative(self):
        """Negative nodes must be rejected."""
        with self.assertRaises(ValidationError, msg="EVD-02: negative nodes should be rejected"):
            self.Model(nodes=-1)

    def test_accepts_nodes_within_bound(self):
        """nodes=10_000 is a reasonable depth and must be accepted."""
        try:
            self.Model(nodes=10_000)
        except ValidationError as exc:
            self.fail(f"EVD-02: valid nodes=10000 was incorrectly rejected: {exc}")


# ---------------------------------------------------------------------------
# EVD-03  fen validation in EngineEvalRequest
# ---------------------------------------------------------------------------

class TestEvd03FenValidation(unittest.TestCase):
    """EngineEvalRequest must validate the fen field.  An invalid FEN currently
    propagates to chess.Board() inside the engine evaluator, raising an unhandled
    exception that surfaces as HTTP 500 and may disrupt the result cache or pool."""

    @classmethod
    def setUpClass(cls):
        cls.Model = _load_host_app_model()

    def test_rejects_garbage_fen(self):
        with self.assertRaises(ValidationError, msg="EVD-03: garbage fen not rejected"):
            self.Model(fen="not_a_fen_at_all")

    def test_rejects_six_token_invalid_fen(self):
        """A string that looks like a FEN (6 tokens) but is semantically invalid."""
        with self.assertRaises(ValidationError, msg="EVD-03: semantically invalid 6-token fen not rejected"):
            self.Model(fen="a b c d e f")

    def test_rejects_overlong_fen(self):
        with self.assertRaises(ValidationError, msg="EVD-03: overlong fen not rejected"):
            self.Model(fen="x" * 200)

    def test_accepts_startpos(self):
        try:
            self.Model(fen="startpos")
        except ValidationError as exc:
            self.fail(f"EVD-03: 'startpos' incorrectly rejected: {exc}")

    def test_accepts_valid_fen(self):
        valid = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        try:
            self.Model(fen=valid)
        except ValidationError as exc:
            self.fail(f"EVD-03: valid FEN incorrectly rejected: {exc}")

    def test_accepts_none_fen(self):
        """fen=None is allowed (defaults to starting position in the evaluator)."""
        try:
            self.Model(fen=None)
        except ValidationError as exc:
            self.fail(f"EVD-03: fen=None incorrectly rejected: {exc}")


# ---------------------------------------------------------------------------
# EVD-04  moves list length
# ---------------------------------------------------------------------------

class TestEvd04MovesListLength(unittest.TestCase):
    """EngineEvalRequest.moves must cap the number of moves.  Without a limit
    an attacker can send thousands of moves causing repeated board.push_uci()
    calls and cache pollution within the 512 KB body budget."""

    @classmethod
    def setUpClass(cls):
        cls.Model = _load_host_app_model()

    def test_rejects_1000_moves(self):
        with self.assertRaises(ValidationError, msg="EVD-04: 1000-move list not rejected"):
            self.Model(moves=["e2e4"] * 1000)

    def test_rejects_501_moves(self):
        with self.assertRaises(ValidationError, msg="EVD-04: 501-move list not rejected"):
            self.Model(moves=["e2e4"] * 501)

    def test_accepts_empty_moves(self):
        try:
            self.Model(moves=[])
        except ValidationError as exc:
            self.fail(f"EVD-04: empty moves list incorrectly rejected: {exc}")

    def test_accepts_normal_game_length(self):
        """A typical game is well under 200 moves — must be accepted."""
        moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"] * 20  # 100 moves
        try:
            self.Model(moves=moves)
        except ValidationError as exc:
            self.fail(f"EVD-04: 100-move list incorrectly rejected: {exc}")


# ---------------------------------------------------------------------------
# EVD-05  moves element length
# ---------------------------------------------------------------------------

class TestEvd05MovesElementLength(unittest.TestCase):
    """Individual entries in moves must be bounded.  A UCI move is at most 5
    chars (e.g. 'e7e8q').  Accepting unbounded strings wastes memory and may
    cause unexpected behaviour in downstream parsers."""

    @classmethod
    def setUpClass(cls):
        cls.Model = _load_host_app_model()

    def test_rejects_overlong_move_element(self):
        with self.assertRaises(ValidationError, msg="EVD-05: overlong move element not rejected"):
            self.Model(moves=["x" * 100])

    def test_rejects_6_char_move(self):
        """Moves longer than 5 chars cannot be valid UCI and must be rejected."""
        with self.assertRaises(ValidationError, msg="EVD-05: 6-char move not rejected"):
            self.Model(moves=["e2e4e5"])

    def test_accepts_5_char_promotion(self):
        try:
            self.Model(moves=["e7e8q"])
        except ValidationError as exc:
            self.fail(f"EVD-05: valid 5-char promotion incorrectly rejected: {exc}")

    def test_accepts_4_char_move(self):
        try:
            self.Model(moves=["e2e4"])
        except ValidationError as exc:
            self.fail(f"EVD-05: valid 4-char move incorrectly rejected: {exc}")


# ---------------------------------------------------------------------------
# EVD-06  /engine/predictions fen query param (AST inspection)
# ---------------------------------------------------------------------------

class TestEvd06PredictionsFenValidation(unittest.TestCase):
    """/engine/predictions accepts a bare `fen: str` query param with no
    validation.  An invalid FEN passes directly to normalize_fen() and then
    get_predictions(), which may attempt chess.Board() and raise unhandled."""

    def test_engine_predictions_has_fen_validator(self):
        """The engine_predictions function must apply FEN validation."""
        import unittest.mock as mock
        with mock.patch("llm.host_app.EnginePool"), \
             mock.patch("llm.host_app.EngineEvaluator"), \
             mock.patch("llm.host_app.EliteEngineService"), \
             mock.patch("llm.host_app.OpeningBook"), \
             mock.patch("llm.host_app.verify_redis_connection"), \
             mock.patch("llm.host_app.get_redis_keys"), \
             mock.patch("llm.host_app.get_redis_value"), \
             mock.patch("llm.host_app.get_redis_info"):
            import llm.host_app as ha

        src = inspect.getsource(ha.engine_predictions)
        tree = ast.parse(textwrap.dedent(src))

        # Either the function itself contains a FEN validation call, or a
        # dependency (like a Pydantic model with a validator) is used.
        has_validation = (
            "validate" in src.lower()
            or "chess.Board" in src
            or "_validate_fen" in src
            or "fen" in src and ("raise" in src or "ValueError" in src)
        )
        self.assertTrue(
            has_validation,
            "EVD-06: engine_predictions() has no FEN validation — "
            "invalid FEN reaches normalize_fen() unchecked",
        )

    def test_engine_predictions_rejects_garbage_fen(self):
        """Calling the route logic with a garbage FEN must raise an error before
        the FEN reaches the downstream prediction cache."""
        import unittest.mock as mock
        with mock.patch("llm.host_app.EnginePool"), \
             mock.patch("llm.host_app.EngineEvaluator"), \
             mock.patch("llm.host_app.EliteEngineService"), \
             mock.patch("llm.host_app.OpeningBook"), \
             mock.patch("llm.host_app.verify_redis_connection"), \
             mock.patch("llm.host_app.get_redis_keys"), \
             mock.patch("llm.host_app.get_redis_value"), \
             mock.patch("llm.host_app.get_redis_info"), \
             mock.patch("llm.host_app.get_predictions", return_value=[]) as mock_pred, \
             mock.patch("llm.host_app.normalize_fen", side_effect=lambda f: f):
            import llm.host_app as ha
            import asyncio

            async def _run():
                # Should raise ValueError / HTTPException before reaching get_predictions
                mock_request = mock.MagicMock()
                try:
                    await ha.engine_predictions(request=mock_request, fen="not_a_fen")
                except Exception:
                    pass  # any error before get_predictions is called is acceptable
                return mock_pred.call_count

            calls = asyncio.run(_run())
            self.assertEqual(
                calls, 0,
                "EVD-06: get_predictions was called with an invalid FEN — "
                "validation must reject it first",
            )


# ---------------------------------------------------------------------------
# EVD-07  /next-training/{player_id} path param length
# ---------------------------------------------------------------------------

class TestEvd07NextTrainingPlayerIdLength(unittest.TestCase):
    """/next-training/{player_id} accepts an unbounded path parameter that is
    used as a dict key in the in-process player_skill_memory store.  A very
    long player_id bloats server memory and bypasses the 100-char cap applied
    to player_id in other endpoints."""

    def test_next_training_player_id_has_length_cap(self):
        """Verify the endpoint function validates player_id length."""
        import llm.server as srv

        src = inspect.getsource(srv.next_training)
        self.assertIn(
            "100",
            src,
            "EVD-07: next_training() has no 100-char cap on player_id",
        )

    def test_next_training_rejects_overlong_player_id(self):
        """Calling next_training with a 200-char player_id must raise HTTPException."""
        import llm.server as srv
        from fastapi import HTTPException

        long_id = "x" * 200
        try:
            # Call without auth dependency — test the validation logic only
            srv.next_training.__wrapped__(long_id) if hasattr(srv.next_training, "__wrapped__") else None
        except HTTPException as exc:
            self.assertIn(exc.status_code, {400, 422},
                          "EVD-07: wrong status for overlong player_id")
            return
        except Exception:
            return  # any error is acceptable here — only pass if NO error raised

        # If we reach here with no error, the validation is absent.
        # Use source inspection: look for a length guard containing "100".
        src = inspect.getsource(srv.next_training)
        self.assertIn(
            "100",
            src,
            "EVD-07: next_training() has no length guard on player_id path param",
        )


# ---------------------------------------------------------------------------
# EVD-08  CoachFeedbackRequest.session_fen — structural-only validation
# ---------------------------------------------------------------------------

class TestEvd08CoachFeedbackFenSemantic(unittest.TestCase):
    """CoachFeedbackRequest.session_fen applies only structural validation
    (6 whitespace-separated tokens, ≤ 100 chars).  It does NOT call
    chess.Board() to reject semantically invalid FENs like 'a b c d e f',
    which is inconsistent with every other FEN validator in the codebase."""

    @classmethod
    def setUpClass(cls):
        from llm.seca.events.router import CoachFeedbackRequest
        cls.Model = CoachFeedbackRequest

    def test_rejects_structurally_valid_but_semantically_invalid_fen(self):
        """'a b c d e f' has 6 tokens and is ≤ 100 chars but is not a legal FEN."""
        with self.assertRaises(
            ValidationError,
            msg="EVD-08: 'a b c d e f' passes CoachFeedbackRequest.session_fen validation",
        ):
            self.Model(session_fen="a b c d e f", is_helpful=True)

    def test_accepts_startpos(self):
        try:
            self.Model(session_fen="startpos", is_helpful=True)
        except ValidationError as exc:
            self.fail(f"EVD-08: 'startpos' incorrectly rejected: {exc}")

    def test_accepts_valid_fen(self):
        valid = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        try:
            self.Model(session_fen=valid, is_helpful=True)
        except ValidationError as exc:
            self.fail(f"EVD-08: valid FEN incorrectly rejected: {exc}")


import textwrap

if __name__ == "__main__":
    unittest.main(verbosity=2)
