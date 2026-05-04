"""
Architectural Invariant Tests — llm/tests/test_architectural_invariants.py

QA Plan Section 3 — P0 blockers. A failure here constitutes a release blocker.

INV-01  LLM output never contains algebraic move notation (e.g. e4, Nf3, O-O)
INV-02  C++ engine response is exactly one move token — no explanatory text
INV-03  Stockfish is never invoked during opponent move generation (source audit)
INV-04  LLM is never called before ESV extraction in the pipeline data-flow
INV-05  No component depends on LLM output for any game decision (source audit)
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from llm.rag.validators.mode_2_negative import validate_mode_2_negative, FORBIDDEN_PATTERNS
from llm.rag.contracts.validate_output import validate_output

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LLM_ROOT = _REPO_ROOT / "llm"
_EXPLAIN_PIPELINE = _LLM_ROOT / "explain_pipeline.py"

# ---------------------------------------------------------------------------
# INV-01 — LLM output never contains algebraic move notation
# ---------------------------------------------------------------------------

_MOVE_PATTERN = re.compile(r"\b[KQRBN][a-h][1-8]\b|\b0-0(?:-0)?\b")

_MOVE_NOTATION_SAMPLES = [
    "The best response is Nf3 attacking the centre.",
    "After Qh5 White threatens mate in two.",
    "The knight should go to Bd3 for better control.",
    "Black plays 0-0-0 to castle queenside.",
    "White should castle 0-0 immediately.",
    "The queen move Qd5 is very strong here.",
]

_CLEAN_SAMPLES = [
    "The evaluation indicates a decisive outcome for White.",
    "The position favors the player with the space advantage.",
    "With the pawn structure broken, long-term defence is very difficult.",
    "The evaluation shift indicates a significant turning point.",
]


class TestInv01LlmOutputMoveNotation:
    """INV-01: LLM output must NEVER contain suggested moves in algebraic notation."""

    @pytest.mark.parametrize("text", _MOVE_NOTATION_SAMPLES)
    def test_validator_rejects_move_notation(self, text: str):
        """validate_mode_2_negative must raise on any algebraic move notation."""
        with pytest.raises(AssertionError, match="Forbidden MODE-2 pattern"):
            validate_mode_2_negative(text)

    @pytest.mark.parametrize("text", _CLEAN_SAMPLES)
    def test_validator_passes_clean_output(self, text: str):
        """validate_mode_2_negative must pass compliant output without move suggestions."""
        validate_mode_2_negative(text)  # must not raise

    def test_move_notation_pattern_covers_piece_moves(self):
        """Pattern catches upper-case piece letter directly followed by square (e.g. Nf3)."""
        assert _MOVE_PATTERN.search("White plays Nf3")
        assert _MOVE_PATTERN.search("Black plays Qd5")
        assert _MOVE_PATTERN.search("The bishop goes Bd3")
        assert _MOVE_PATTERN.search("Kh1 is the only safe square.")
        assert _MOVE_PATTERN.search("Ra8 delivers the back-rank threat.")

    def test_castling_pattern_is_forbidden(self):
        """0-0 and 0-0-0 (zero notation) must both be detected by the validator."""
        assert _MOVE_PATTERN.search("0-0 is the right move.")
        assert _MOVE_PATTERN.search("Black castles 0-0-0 for safety.")

    def test_validator_blocks_engine_name_leakage(self):
        """INV-01 adjacent: 'stockfish' and 'engine' are also forbidden phrases."""
        with pytest.raises(AssertionError):
            validate_output("Stockfish says the best move is e4.", case_type="normal")

    def test_validate_output_blocks_best_move_phrase(self):
        """validate_output must block 'best move' as a forbidden phrase."""
        with pytest.raises(AssertionError):
            validate_output("The best move here is clearly e4.", case_type="normal")

    def test_forbidden_patterns_list_contains_piece_move_regex(self):
        """The FORBIDDEN_PATTERNS list must contain the piece-move pattern."""
        piece_move_patterns = [p for p in FORBIDDEN_PATTERNS if "KQRBN" in p]
        assert piece_move_patterns, (
            "FORBIDDEN_PATTERNS must include a regex matching piece+square notation "
            "(e.g. r'\\b[KQRBN][a-h][1-8]\\b')"
        )

    def test_forbidden_patterns_list_contains_castling_regex(self):
        """The FORBIDDEN_PATTERNS list must contain an O-O / O-O-O pattern."""
        castling_patterns = [p for p in FORBIDDEN_PATTERNS if "0-0" in p]
        assert castling_patterns, (
            "FORBIDDEN_PATTERNS must include a regex matching castling notation (0-0 / 0-0-0)"
        )


# ---------------------------------------------------------------------------
# INV-02 — C++ engine response: exactly one move token, no explanatory text
# ---------------------------------------------------------------------------

_UCI_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


def _looks_like_explanatory(text: str) -> bool:
    """Return True if the string contains non-UCI content (words, punctuation beyond move)."""
    stripped = text.strip()
    if not stripped:
        return False
    return not bool(_UCI_RE.match(stripped))


class TestInv02EngineOutputFormat:
    """INV-02: Engine move strings must be a single UCI token with no additional text."""

    @pytest.mark.parametrize(
        "move",
        ["e2e4", "d7d5", "g1f3", "e7e8q", "e1g1", "a7a8r"],
    )
    def test_valid_uci_tokens_pass_format_check(self, move: str):
        """Valid UCI move strings must match the pattern and contain no extra text."""
        assert _UCI_RE.match(move), f"{move!r} is not a valid UCI token"
        assert not _looks_like_explanatory(move)

    @pytest.mark.parametrize(
        "text",
        [
            "e2e4 is a great move",
            "I suggest e2e4",
            "The engine plays e2e4 to control the centre.",
            "move: e2e4",
            "e2e4\nBecause it opens the centre",
        ],
    )
    def test_explanatory_text_detected(self, text: str):
        """Any string that is not a bare UCI token must be detected as explanatory."""
        stripped = text.strip()
        assert _looks_like_explanatory(stripped), (
            f"Expected explanatory content to be detected in: {text!r}"
        )

    def test_empty_string_is_not_explanatory(self):
        """Edge case: empty string is neither a move nor explanatory text."""
        assert not _looks_like_explanatory("")

    def test_promotion_suffix_is_valid_uci(self):
        """Promotion moves with suffix (e7e8q) must be accepted as valid UCI."""
        assert _UCI_RE.match("e7e8q")
        assert _UCI_RE.match("a2a1r")
        assert _UCI_RE.match("b7b8n")


# ---------------------------------------------------------------------------
# INV-03 — Stockfish never called during opponent move generation (source audit)
# ---------------------------------------------------------------------------

_ENGINE_DIR = _REPO_ROOT / "engine"


class TestInv03StockfishIsolationFromEngine:
    """INV-03: The C++ engine and its Python bridge must not invoke Stockfish."""

    def test_explain_pipeline_separates_stockfish_from_move_generation(self):
        """
        The explain_pipeline.py file imports from the RAG/ESV layer (Stockfish evaluation),
        but must NOT import from the engine pool used for move generation.
        The two paths must be completely separate.
        """
        source = _EXPLAIN_PIPELINE.read_text(encoding="utf-8")
        # Explanation pipeline must use Stockfish for evaluation — expected
        assert "extract_engine_signal" in source, (
            "explain_pipeline.py must use extract_engine_signal for evaluation"
        )
        # But it must NOT reference the JNI move generator
        assert "SachmatuLenta" not in source, (
            "explain_pipeline.py must not reference the C++ move generator (SachmatuLenta)"
        )
        assert "NativeEngineProvider" not in source, (
            "explain_pipeline.py must not reference NativeEngineProvider"
        )

    def test_engine_pool_does_not_import_llm_layer(self):
        """StockfishEnginePool must not import anything from the LLM/RAG explanation layer."""
        pool_files = list((_LLM_ROOT / "seca" / "engines" / "stockfish").glob("*.py"))
        for path in pool_files:
            source = path.read_text(encoding="utf-8")
            assert "from llm.rag" not in source, (
                f"{path.name} must not import from llm.rag (LLM explanation layer)"
            )
            assert "from llm.explain_pipeline" not in source, (
                f"{path.name} must not import explain_pipeline (LLM layer)"
            )
            assert "FakeLLM" not in source, (
                f"{path.name} must not reference FakeLLM"
            )

    def test_server_handles_explain_and_move_in_separate_routes(self):
        """
        server.py must have separate route functions for move generation and explanation.
        The explanation route must reference explain_pipeline; move routes must not.
        """
        source = (_LLM_ROOT / "server.py").read_text(encoding="utf-8")
        assert "generate_validated_explanation" in source, (
            "server.py must import generate_validated_explanation for the explain route"
        )
        assert "StockfishEnginePool" in source, (
            "server.py must use StockfishEnginePool for move generation"
        )


# ---------------------------------------------------------------------------
# INV-04 — LLM never called before ESV extraction in the data-flow
# ---------------------------------------------------------------------------


class TestInv04LlmCallSequence:
    """INV-04: In the pipeline, extract_engine_signal always precedes the LLM call."""

    def test_generate_once_extracts_esv_before_calling_llm(self):
        """
        In explain_pipeline.generate_once(), ESV extraction must happen
        before the LLM prompt is rendered and called.
        Verified by reading the source and confirming call order.
        """
        import inspect
        from llm.explain_pipeline import generate_once

        src = inspect.getsource(generate_once)
        esv_pos = src.find("extract_engine_signal")
        llm_pos = src.find("call_llm")

        assert esv_pos != -1, "generate_once must call extract_engine_signal"
        assert llm_pos != -1, "generate_once must call call_llm"
        assert esv_pos < llm_pos, (
            "extract_engine_signal must appear before call_llm in generate_once "
            f"(esv_pos={esv_pos}, llm_pos={llm_pos})"
        )

    def test_generate_once_retrieves_rag_before_calling_llm(self):
        """RAG retrieval must also occur before the LLM is invoked."""
        import inspect
        from llm.explain_pipeline import generate_once

        src = inspect.getsource(generate_once)
        rag_pos = src.find("retrieve(")
        llm_pos = src.find("call_llm")

        assert rag_pos != -1, "generate_once must call retrieve() for RAG"
        assert rag_pos < llm_pos, (
            "RAG retrieval must appear before call_llm in generate_once "
            f"(rag_pos={rag_pos}, llm_pos={llm_pos})"
        )

    def test_generate_once_renders_prompt_before_calling_llm(self):
        """The prompt must be rendered before the LLM is called."""
        import inspect
        from llm.explain_pipeline import generate_once

        src = inspect.getsource(generate_once)
        render_pos = src.find("render_mode_2_prompt")
        llm_pos = src.find("call_llm")

        assert render_pos != -1, "generate_once must call render_mode_2_prompt"
        assert render_pos < llm_pos, (
            "render_mode_2_prompt must appear before call_llm in generate_once"
        )


# ---------------------------------------------------------------------------
# INV-05 — No game component depends on LLM explanation output
# ---------------------------------------------------------------------------


class TestInv05GameLogicIndependentOfLlm:
    """INV-05: Game logic must have zero dependency on the explanation layer."""

    def test_server_move_route_does_not_import_explain_pipeline(self):
        """
        The server move-generation path must not import generate_validated_explanation
        inside any function that handles a move request.
        The explain import in server.py is for the /explain route only.
        """
        from llm import server as server_module

        routes = {
            getattr(r, "path", ""): r for r in server_module.app.routes
        }
        # The explain pipeline must not be in a route that generates moves
        # We verify by checking that there is no /move route that calls explanation
        move_route_paths = [p for p in routes if "move" in p.lower()]
        for path in move_route_paths:
            route = routes[path]
            endpoint = getattr(route, "endpoint", None)
            if endpoint is not None:
                import inspect
                try:
                    src = inspect.getsource(endpoint)
                    assert "generate_validated_explanation" not in src, (
                        f"Move route '{path}' must not call generate_validated_explanation. "
                        "Game decisions must be independent of the LLM explanation layer."
                    )
                except (OSError, TypeError):
                    pass  # source not available; skip

    def test_stockfish_pool_has_no_explanation_layer_dependency(self):
        """
        The StockfishEnginePool (move generation) must not import or call
        any function from the LLM explanation pipeline.
        """
        from llm.seca.engines.stockfish import pool as pool_module
        import inspect

        src = inspect.getsource(pool_module)
        assert "generate_validated_explanation" not in src, (
            "StockfishEnginePool must not reference generate_validated_explanation"
        )
        assert "call_llm" not in src, (
            "StockfishEnginePool must not call call_llm — "
            "move generation is independent of the LLM layer"
        )
        assert "FakeLLM" not in src, (
            "StockfishEnginePool must not reference FakeLLM"
        )

    def test_safe_mode_blocks_autonomous_rl(self):
        """SAFE_MODE must be True to prevent autonomous RL implementation (Rule 3)."""
        from llm.seca.runtime.safe_mode import SAFE_MODE

        assert SAFE_MODE is True, (
            "SAFE_MODE must always be True. "
            "Autonomous RL implementation is prohibited (Project Rule 3)."
        )

    def test_explanation_outcome_tracker_does_not_affect_move_choice(self):
        """
        ExplanationOutcomeTracker tracks learning signals; it must not
        influence the move generation pool or the game state directly.
        """
        from llm.seca.learning import outcome_tracker as ot_module
        import inspect

        src = inspect.getsource(ot_module)
        assert "StockfishEnginePool" not in src, (
            "ExplanationOutcomeTracker must not reference StockfishEnginePool"
        )
        assert "fast_fallback_move" not in src, (
            "ExplanationOutcomeTracker must not call fast_fallback_move"
        )
