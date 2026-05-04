"""Deterministic regression suite for the coaching pipeline.

Properties verified
-------------------
1. Identical engine input → identical ESV, case_type, retrieved docs, prompt.
2. Explanation text never overrides engine evaluation (validators + score guard).
3. Severity classification (confidence, urgency, tone) is stable across calls.
4. Post-game coach decisions are deterministic and purely rule-based.
5. Coach executor produces stable, template-driven content.
6. Full pipeline with FakeLLM is repeatable end-to-end.

Design constraints
------------------
- No real LLM calls (FakeLLM only).
- No RL / adaptive components touched.
- All tested functions are pure or use only deterministic internal state.
- Tests must not weaken validators or modify production modules.
"""

from __future__ import annotations

import copy
import pytest

# ---------------------------------------------------------------------------
# SHARED GOLDEN INPUTS (5 representative positions)
# ---------------------------------------------------------------------------

# 1 ─ Tactical mistake: Black's last move dropped a piece
STOCKFISH_TACTICAL = {
    "evaluation": {"type": "cp", "value": -180},
    "eval_delta": -150,
    "errors": {"last_move_quality": "mistake"},
    "tactical_flags": ["hanging_piece"],
    "position_flags": [],
    "phase": "middlegame",
}
FEN_TACTICAL = "r1bqkbnr/pppp1ppp/2n5/4p3/3PP3/5N2/PPP2PPP/RNBQKB1R b KQkq - 2 3"

# 2 ─ Positional quiet: White has a small, stable space advantage
STOCKFISH_POSITIONAL = {
    "evaluation": {"type": "cp", "value": 35},
    "eval_delta": 0,
    "errors": {"last_move_quality": "ok"},
    "tactical_flags": [],
    "position_flags": ["space_advantage"],
    "phase": "middlegame",
}
FEN_POSITIONAL = "r2q1rk1/pp2bppp/2n1bn2/2pp4/3P4/2PBPN2/PP1N1PPP/R1BQ1RK1 w - - 0 9"

# 3 ─ Forced mate: White has forced mate
STOCKFISH_MATE = {
    "evaluation": {"type": "mate", "value": 3},
    "eval_delta": 300,
    "errors": {"last_move_quality": "excellent"},
    "tactical_flags": ["forced_mate"],
    "position_flags": [],
    "phase": "endgame",
}
FEN_MATE = "6k1/5ppp/8/8/8/8/5PPP/4R1K1 w - - 0 1"

# 4 ─ Equal: tiny eval, no flags
STOCKFISH_EQUAL = {
    "evaluation": {"type": "cp", "value": 5},
    "eval_delta": 0,
    "errors": {"last_move_quality": "ok"},
    "tactical_flags": [],
    "position_flags": [],
    "phase": "opening",
}
FEN_EQUAL = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"

# 5 ─ Blunder: decisive advantage swing
STOCKFISH_BLUNDER = {
    "evaluation": {"type": "cp", "value": 350},
    "eval_delta": -400,
    "errors": {"last_move_quality": "blunder"},
    "tactical_flags": ["hanging_piece", "fork"],
    "position_flags": [],
    "phase": "middlegame",
}
FEN_BLUNDER = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 0 4"

ALL_CASES = [
    (STOCKFISH_TACTICAL, FEN_TACTICAL),
    (STOCKFISH_POSITIONAL, FEN_POSITIONAL),
    (STOCKFISH_MATE, FEN_MATE),
    (STOCKFISH_EQUAL, FEN_EQUAL),
    (STOCKFISH_BLUNDER, FEN_BLUNDER),
]


def _esv(stockfish_json, fen):
    """Helper: extract ESV for a case (deep-copies input to prevent mutation)."""
    from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal

    return extract_engine_signal(copy.deepcopy(stockfish_json), fen=fen)


# ---------------------------------------------------------------------------
# 1. ENGINE SIGNAL EXTRACTION — determinism
# ---------------------------------------------------------------------------


class TestEngineSignalDeterminism:
    """extract_engine_signal is a pure function; same input must always produce
    exactly the same output dict."""

    @pytest.mark.parametrize("stockfish_json,fen", ALL_CASES)
    def test_repeated_calls_produce_identical_esv(self, stockfish_json, fen):
        results = [_esv(stockfish_json, fen) for _ in range(3)]
        assert results[0] == results[1] == results[2]

    @pytest.mark.parametrize("stockfish_json,fen", ALL_CASES)
    def test_all_required_keys_present(self, stockfish_json, fen):
        esv = _esv(stockfish_json, fen)
        for key in (
            "evaluation",
            "eval_delta",
            "last_move_quality",
            "tactical_flags",
            "position_flags",
            "phase",
        ):
            assert key in esv, f"Missing key: {key}"
        for key in ("type", "band", "side"):
            assert key in esv["evaluation"], f"Missing evaluation key: {key}"

    def test_mate_type_always_decisive_band(self):
        esv = _esv(STOCKFISH_MATE, FEN_MATE)
        assert esv["evaluation"]["type"] == "mate"
        assert esv["evaluation"]["band"] == "decisive_advantage"

    @pytest.mark.parametrize(
        "value,expected_band",
        [
            (0, "equal"),
            (20, "equal"),
            (-20, "equal"),
            (21, "small_advantage"),
            (60, "small_advantage"),
            (-60, "small_advantage"),
            (61, "clear_advantage"),
            (120, "clear_advantage"),
            (-120, "clear_advantage"),
            (121, "decisive_advantage"),
            (500, "decisive_advantage"),
            (-500, "decisive_advantage"),
        ],
    )
    def test_cp_band_boundaries(self, value, expected_band):
        from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal

        esv = extract_engine_signal(
            {
                "evaluation": {"type": "cp", "value": value},
                "eval_delta": 0,
                "errors": {"last_move_quality": "ok"},
            }
        )
        assert esv["evaluation"]["band"] == expected_band

    @pytest.mark.parametrize(
        "value,expected_side",
        [
            (1, "white"),
            (100, "white"),
            (-1, "black"),
            (-100, "black"),
            (0, "black"),  # convention: zero attributed to black; band "equal" is primary
        ],
    )
    def test_side_attribution_sign_convention(self, value, expected_side):
        from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal

        esv = extract_engine_signal(
            {
                "evaluation": {"type": "cp", "value": value},
                "eval_delta": 0,
                "errors": {"last_move_quality": "ok"},
            }
        )
        assert esv["evaluation"]["side"] == expected_side

    @pytest.mark.parametrize(
        "delta,expected",
        [
            (50, "increase"),
            (200, "increase"),
            (-50, "decrease"),
            (-200, "decrease"),
            (0, "stable"),
            (49, "stable"),
            (-49, "stable"),
        ],
    )
    def test_eval_delta_thresholds(self, delta, expected):
        from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal

        esv = extract_engine_signal(
            {
                "evaluation": {"type": "cp", "value": 30},
                "eval_delta": delta,
                "errors": {"last_move_quality": "ok"},
            }
        )
        assert esv["eval_delta"] == expected

    def test_input_dict_is_not_mutated(self):
        original = copy.deepcopy(STOCKFISH_TACTICAL)
        _esv(STOCKFISH_TACTICAL, FEN_TACTICAL)
        assert STOCKFISH_TACTICAL == original


# ---------------------------------------------------------------------------
# 2. MISTAKE CLASSIFICATION — stability
# ---------------------------------------------------------------------------


class TestMistakeClassificationStability:
    """infer_case_type is a pure function. Same ESV → same case type always."""

    @pytest.mark.parametrize("stockfish_json,fen", ALL_CASES)
    def test_case_type_is_stable_across_repeated_calls(self, stockfish_json, fen):
        from llm.rag.meta.case_classifier import infer_case_type

        esv = _esv(stockfish_json, fen)
        results = {infer_case_type(esv) for _ in range(5)}
        assert len(results) == 1, f"Unstable case_type results: {results}"

    def test_forced_mate_classification(self):
        from llm.rag.meta.case_classifier import infer_case_type

        esv = _esv(STOCKFISH_MATE, FEN_MATE)
        assert infer_case_type(esv) == "forced_mate"

    def test_tactical_mistake_from_mistake_quality(self):
        from llm.rag.meta.case_classifier import infer_case_type

        esv = _esv(STOCKFISH_TACTICAL, FEN_TACTICAL)
        assert infer_case_type(esv) == "tactical_mistake"

    def test_tactical_mistake_from_blunder_quality(self):
        from llm.rag.meta.case_classifier import infer_case_type

        esv = _esv(STOCKFISH_BLUNDER, FEN_BLUNDER)
        assert infer_case_type(esv) == "tactical_mistake"

    def test_positional_quiet_from_position_flags(self):
        from llm.rag.meta.case_classifier import infer_case_type

        esv = _esv(STOCKFISH_POSITIONAL, FEN_POSITIONAL)
        assert infer_case_type(esv) == "positional_quiet"

    def test_general_fallback_when_no_flags(self):
        from llm.rag.meta.case_classifier import infer_case_type

        esv = _esv(STOCKFISH_EQUAL, FEN_EQUAL)
        assert infer_case_type(esv) == "general"

    def test_mate_takes_priority_over_mistake_quality(self):
        """Mate type must dominate even when last_move_quality is 'mistake'."""
        from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
        from llm.rag.meta.case_classifier import infer_case_type

        esv = extract_engine_signal(
            {
                "evaluation": {"type": "mate", "value": 1},
                "eval_delta": 300,
                "errors": {"last_move_quality": "mistake"},
                "tactical_flags": [],
                "position_flags": [],
            }
        )
        assert infer_case_type(esv) == "forced_mate"


# ---------------------------------------------------------------------------
# 3. SEVERITY CLASSIFICATION — determinism
# ---------------------------------------------------------------------------


class TestSeverityClassificationDeterminism:
    """Confidence, urgency, tone and the language block are all pure functions."""

    def test_compute_confidence_stable(self):
        from llm.confidence_language_controller import compute_confidence

        esv = _esv(STOCKFISH_TACTICAL, FEN_TACTICAL)
        results = [compute_confidence(esv) for _ in range(5)]
        assert len(set(results)) == 1

    def test_compute_urgency_stable(self):
        from llm.confidence_language_controller import compute_urgency

        esv = _esv(STOCKFISH_BLUNDER, FEN_BLUNDER)
        results = [compute_urgency(esv) for _ in range(5)]
        assert len(set(results)) == 1

    def test_build_language_block_stable(self):
        from llm.confidence_language_controller import build_language_controller_block

        esv = _esv(STOCKFISH_TACTICAL, FEN_TACTICAL)
        blocks = [build_language_controller_block(esv) for _ in range(5)]
        assert len(set(blocks)) == 1

    def test_mate_always_produces_high_confidence(self):
        from llm.confidence_language_controller import compute_confidence

        esv = _esv(STOCKFISH_MATE, FEN_MATE)
        assert compute_confidence(esv) == "high"

    def test_blunder_always_produces_critical_urgency(self):
        from llm.confidence_language_controller import compute_urgency

        esv = _esv(STOCKFISH_BLUNDER, FEN_BLUNDER)
        assert compute_urgency(esv) == "critical"

    def test_forced_mate_flag_always_critical_urgency(self):
        from llm.confidence_language_controller import compute_urgency

        esv = _esv(STOCKFISH_MATE, FEN_MATE)
        assert compute_urgency(esv) == "critical"

    def test_critical_urgency_always_produces_urgent_tone(self):
        from llm.confidence_language_controller import compute_tone

        tone = compute_tone(player_elo=1500, confidence="high", urgency="critical")
        assert tone == "urgent"

    def test_low_elo_non_critical_always_supportive(self):
        from llm.confidence_language_controller import compute_tone

        tone = compute_tone(player_elo=800, confidence="medium", urgency="calm")
        assert tone == "supportive"

    def test_language_block_contains_tone_text(self):
        from llm.confidence_language_controller import build_language_controller_block

        esv = _esv(STOCKFISH_BLUNDER, FEN_BLUNDER)
        block = build_language_controller_block(esv)
        assert "LANGUAGE STYLE RULES:" in block
        # Critical urgency → urgent tone → "concise and urgent"
        assert "concise and urgent" in block.lower() or "urgent" in block.lower()

    @pytest.mark.parametrize("stockfish_json,fen", ALL_CASES)
    def test_language_block_always_has_three_rules(self, stockfish_json, fen):
        from llm.confidence_language_controller import build_language_controller_block

        esv = _esv(stockfish_json, fen)
        block = build_language_controller_block(esv)
        bullet_lines = [l for l in block.splitlines() if l.strip().startswith("-")]
        assert len(bullet_lines) == 3, f"Expected 3 style rules, got {len(bullet_lines)}: {block}"


# ---------------------------------------------------------------------------
# 4. RETRIEVAL — determinism
# ---------------------------------------------------------------------------


class TestRetrievalDeterminism:
    """retrieve() is deterministic: same ESV always returns the same ordered list."""

    @pytest.mark.parametrize("stockfish_json,fen", ALL_CASES)
    def test_same_esv_returns_same_document_ids(self, stockfish_json, fen):
        from llm.rag.retriever.retriever import retrieve
        from llm.rag.documents import ALL_RAG_DOCUMENTS

        esv = _esv(stockfish_json, fen)
        runs = [[d["id"] for d in retrieve(esv, ALL_RAG_DOCUMENTS)] for _ in range(3)]
        assert runs[0] == runs[1] == runs[2]

    @pytest.mark.parametrize("stockfish_json,fen", ALL_CASES)
    def test_retrieved_count_does_not_exceed_max_docs(self, stockfish_json, fen):
        from llm.rag.retriever.retriever import retrieve, MAX_DOCS
        from llm.rag.documents import ALL_RAG_DOCUMENTS

        esv = _esv(stockfish_json, fen)
        docs = retrieve(esv, ALL_RAG_DOCUMENTS)
        assert len(docs) <= MAX_DOCS

    def test_mate_and_tactical_retrieve_different_docs(self):
        from llm.rag.retriever.retriever import retrieve
        from llm.rag.documents import ALL_RAG_DOCUMENTS

        esv_mate = _esv(STOCKFISH_MATE, FEN_MATE)
        esv_pos = _esv(STOCKFISH_POSITIONAL, FEN_POSITIONAL)
        ids_mate = [d["id"] for d in retrieve(esv_mate, ALL_RAG_DOCUMENTS)]
        ids_pos = [d["id"] for d in retrieve(esv_pos, ALL_RAG_DOCUMENTS)]
        assert (
            ids_mate != ids_pos
        ), "Mate and positional positions should retrieve different doc sets"

    def test_retrieved_docs_have_required_fields(self):
        from llm.rag.retriever.retriever import retrieve
        from llm.rag.documents import ALL_RAG_DOCUMENTS

        esv = _esv(STOCKFISH_TACTICAL, FEN_TACTICAL)
        docs = retrieve(esv, ALL_RAG_DOCUMENTS)
        assert docs, "Should retrieve at least one document for a tactical mistake"
        for doc in docs:
            assert "id" in doc
            assert "content" in doc
            assert "description" in doc["content"]


# ---------------------------------------------------------------------------
# 5. PROMPT RENDERING — determinism
# ---------------------------------------------------------------------------


class TestPromptRenderingDeterminism:
    """render_mode_2_prompt is deterministic: byte-identical output for same args."""

    @pytest.mark.parametrize("stockfish_json,fen", ALL_CASES)
    def test_same_inputs_produce_identical_prompt_string(self, stockfish_json, fen):
        from llm.rag.prompts.mode_2.render import render_mode_2_prompt
        from llm.rag.retriever.retriever import retrieve
        from llm.rag.documents import ALL_RAG_DOCUMENTS
        from llm.rag.prompts.system_v2_mode_2 import SYSTEM_PROMPT

        esv = _esv(stockfish_json, fen)
        rag_docs = retrieve(esv, ALL_RAG_DOCUMENTS)

        prompts = [
            render_mode_2_prompt(
                system_prompt=SYSTEM_PROMPT,
                engine_signal=esv,
                rag_docs=rag_docs,
                fen=fen,
                user_query="What is happening in this position?",
            )
            for _ in range(3)
        ]
        assert prompts[0] == prompts[1] == prompts[2]

    @pytest.mark.parametrize("stockfish_json,fen", ALL_CASES)
    def test_prompt_embeds_fen(self, stockfish_json, fen):
        from llm.rag.prompts.mode_2.render import render_mode_2_prompt
        from llm.rag.retriever.retriever import retrieve
        from llm.rag.documents import ALL_RAG_DOCUMENTS
        from llm.rag.prompts.system_v2_mode_2 import SYSTEM_PROMPT

        esv = _esv(stockfish_json, fen)
        rag_docs = retrieve(esv, ALL_RAG_DOCUMENTS)
        prompt = render_mode_2_prompt(
            system_prompt=SYSTEM_PROMPT,
            engine_signal=esv,
            rag_docs=rag_docs,
            fen=fen,
            user_query="",
        )
        assert fen in prompt, "FEN must be present in the rendered prompt"

    @pytest.mark.parametrize("stockfish_json,fen", ALL_CASES)
    def test_prompt_embeds_engine_signal_json(self, stockfish_json, fen):
        from llm.rag.prompts.mode_2.render import render_mode_2_prompt
        from llm.rag.retriever.retriever import retrieve
        from llm.rag.documents import ALL_RAG_DOCUMENTS
        from llm.rag.prompts.system_v2_mode_2 import SYSTEM_PROMPT
        import json

        esv = _esv(stockfish_json, fen)
        rag_docs = retrieve(esv, ALL_RAG_DOCUMENTS)
        prompt = render_mode_2_prompt(
            system_prompt=SYSTEM_PROMPT,
            engine_signal=esv,
            rag_docs=rag_docs,
            fen=fen,
            user_query="",
        )
        # The ESV band must be visible to the LLM in the prompt
        assert esv["evaluation"]["band"] in prompt

    def test_prompt_embeds_user_query(self):
        from llm.rag.prompts.mode_2.render import render_mode_2_prompt
        from llm.rag.retriever.retriever import retrieve
        from llm.rag.documents import ALL_RAG_DOCUMENTS
        from llm.rag.prompts.system_v2_mode_2 import SYSTEM_PROMPT

        esv = _esv(STOCKFISH_POSITIONAL, FEN_POSITIONAL)
        rag_docs = retrieve(esv, ALL_RAG_DOCUMENTS)
        query = "Who stands slightly better here?"
        prompt = render_mode_2_prompt(
            system_prompt=SYSTEM_PROMPT,
            engine_signal=esv,
            rag_docs=rag_docs,
            fen=FEN_POSITIONAL,
            user_query=query,
        )
        assert query in prompt


# ---------------------------------------------------------------------------
# 6. ENGINE OVERRIDE GUARD — validators reject any contradiction of engine truth
# ---------------------------------------------------------------------------


class TestEngineOverrideGuard:
    """The validator stack must block explanations that override engine evaluation.

    Invariant: LLM output is rejected before the caller sees it if it contains
    invented moves, engine-internal phrases, advisory language, or mate claims
    not originating from the engine signal.
    """

    def test_invented_chess_move_fails_negative_validator(self):
        from llm.rag.validators.mode_2_negative import validate_mode_2_negative

        with pytest.raises(AssertionError, match="pattern"):
            validate_mode_2_negative("White can play Nf3 to gain activity.")

    def test_castling_notation_fails_negative_validator(self):
        from llm.rag.validators.mode_2_negative import validate_mode_2_negative

        with pytest.raises(AssertionError, match="pattern"):
            validate_mode_2_negative("White castled with 0-0 to secure the king.")

    def test_checkmate_claim_fails_negative_validator(self):
        from llm.rag.validators.mode_2_negative import validate_mode_2_negative

        with pytest.raises(AssertionError, match="pattern"):
            validate_mode_2_negative("This is checkmate in three moves.")

    def test_mate_in_n_claim_fails_negative_validator(self):
        from llm.rag.validators.mode_2_negative import validate_mode_2_negative

        with pytest.raises(AssertionError, match="pattern"):
            validate_mode_2_negative("The position allows mate in 2.")

    def test_should_advisory_fails_negative_validator(self):
        from llm.rag.validators.mode_2_negative import validate_mode_2_negative

        with pytest.raises(AssertionError, match="pattern"):
            validate_mode_2_negative("Black should capture on d4 immediately.")

    def test_stockfish_phrase_fails_contract_validator(self):
        from llm.rag.contracts.validate_output import validate_output

        with pytest.raises(AssertionError):
            validate_output(
                "Stockfish evaluates this position as +3.5.",
                case_type="general",
            )

    def test_best_move_phrase_fails_contract_validator(self):
        from llm.rag.contracts.validate_output import validate_output

        with pytest.raises(AssertionError):
            validate_output(
                "The best move here is to advance the pawn.",
                case_type="general",
            )

    def test_engine_depth_phrase_fails_contract_validator(self):
        from llm.rag.contracts.validate_output import validate_output

        with pytest.raises(AssertionError):
            validate_output(
                "At depth 20 the engine prefers the kingside attack.",
                case_type="general",
            )

    def test_clean_explanation_passes_all_validators(self):
        from llm.rag.validators.mode_2_negative import validate_mode_2_negative
        from llm.rag.validators.mode_2_structure import validate_mode_2_structure
        from llm.rag.contracts.validate_output import validate_output

        text = (
            "The evaluation indicates a decisive advantage for White. "
            "The position is unbalanced due to the material imbalance on the queenside. "
            "The assessment reflects significant pressure that leaves Black with limited options."
        )
        # Must not raise
        validate_mode_2_negative(text)
        validate_mode_2_structure(text)
        validate_output(text, case_type="general")

    def test_explanation_aligning_with_engine_band_scores_higher_than_generic_text(self):
        from llm.rag.quality.explanation_score import score_explanation

        esv = _esv(STOCKFISH_BLUNDER, FEN_BLUNDER)
        # band will be "decisive_advantage"
        band = esv["evaluation"]["band"].replace("_", " ")

        aligned = (
            f"The evaluation shows a {band} for White. "
            "The assessment reflects material loss due to the hanging piece that was forked. "
            "This explains why recovery appears extremely difficult."
        )
        generic = "The position is complex and both sides have chances. Interesting ideas abound."

        score_aligned = score_explanation(text=aligned, engine_signal=esv)
        score_generic = score_explanation(text=generic, engine_signal=esv)
        assert (
            score_aligned > score_generic
        ), f"Band-aligned explanation ({score_aligned}) should outscore generic ({score_generic})"

    def test_forbidden_soft_triggers_reduce_score(self):
        from llm.rag.quality.explanation_score import score_explanation

        esv = _esv(STOCKFISH_TACTICAL, FEN_TACTICAL)
        compliant = (
            "The evaluation indicates a clear advantage for Black. "
            "The assessment results from the hanging piece that was not defended. "
            "This reflects a significant shift in material balance."
        )
        advisory = (
            "Black should take the hanging piece. "
            "The evaluation indicates a clear advantage. "
            "Consider the best move in this position."
        )
        assert score_explanation(text=compliant, engine_signal=esv) > score_explanation(
            text=advisory, engine_signal=esv
        )

    def test_score_is_deterministic_for_same_text_and_esv(self):
        from llm.rag.quality.explanation_score import score_explanation

        esv = _esv(STOCKFISH_TACTICAL, FEN_TACTICAL)
        text = (
            "The evaluation shows a clear advantage for Black. "
            "The position results from a hanging piece that was not defended."
        )
        scores = [score_explanation(text=text, engine_signal=esv) for _ in range(5)]
        assert len(set(scores)) == 1, f"Non-deterministic score: {scores}"


# ---------------------------------------------------------------------------
# 7. FULL PIPELINE REGRESSION (FakeLLM — no real LLM)
# ---------------------------------------------------------------------------


class TestFullPipelineWithFakeLLM:
    """End-to-end pipeline using FakeLLM('compliant'). Verifies that the complete
    chain from engine JSON → explanation is deterministic and validator-clean.
    """

    def _run_pipeline(self, stockfish_json, fen, query="What is happening?"):
        from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
        from llm.rag.meta.case_classifier import infer_case_type
        from llm.rag.retriever.retriever import retrieve
        from llm.rag.documents import ALL_RAG_DOCUMENTS
        from llm.rag.prompts.mode_2.render import render_mode_2_prompt
        from llm.rag.prompts.system_v2_mode_2 import SYSTEM_PROMPT
        from llm.rag.llm.fake import FakeLLM
        from llm.rag.llm.run_mode_2 import run_mode_2

        esv = extract_engine_signal(copy.deepcopy(stockfish_json), fen=fen)
        case_type = infer_case_type(esv)
        rag_docs = retrieve(esv, ALL_RAG_DOCUMENTS)
        prompt = render_mode_2_prompt(
            system_prompt=SYSTEM_PROMPT,
            engine_signal=esv,
            rag_docs=rag_docs,
            fen=fen,
            user_query=query,
        )
        llm = FakeLLM("compliant")
        explanation = run_mode_2(llm=llm, prompt=prompt, case_type=case_type)
        return explanation, esv, case_type, rag_docs, prompt

    @pytest.mark.parametrize("stockfish_json,fen", ALL_CASES)
    def test_pipeline_is_deterministic_end_to_end(self, stockfish_json, fen):
        """Calling the pipeline twice with the same input must produce the same
        explanation, ESV, case_type, retrieved docs, and prompt."""
        result1 = self._run_pipeline(stockfish_json, fen)
        result2 = self._run_pipeline(stockfish_json, fen)
        expl1, esv1, ct1, docs1, prompt1 = result1
        expl2, esv2, ct2, docs2, prompt2 = result2

        assert expl1 == expl2, "Explanation must be identical for same input"
        assert esv1 == esv2, "ESV must be identical for same input"
        assert ct1 == ct2, "Case type must be identical for same input"
        assert [d["id"] for d in docs1] == [d["id"] for d in docs2]
        assert prompt1 == prompt2, "Rendered prompt must be identical for same input"

    @pytest.mark.parametrize("stockfish_json,fen", ALL_CASES)
    def test_fakellm_compliant_output_passes_all_validators(self, stockfish_json, fen):
        """Compliant FakeLLM output must clear the full validator stack."""
        from llm.rag.validators.mode_2_negative import validate_mode_2_negative
        from llm.rag.validators.mode_2_structure import validate_mode_2_structure

        explanation, esv, case_type, _, _ = self._run_pipeline(stockfish_json, fen)
        # Must not raise
        validate_mode_2_negative(explanation)
        validate_mode_2_structure(explanation)

    @pytest.mark.parametrize("stockfish_json,fen", ALL_CASES)
    def test_same_stockfish_json_always_produces_same_case_type(self, stockfish_json, fen):
        from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
        from llm.rag.meta.case_classifier import infer_case_type

        case_types = {
            infer_case_type(extract_engine_signal(copy.deepcopy(stockfish_json), fen=fen))
            for _ in range(5)
        }
        assert len(case_types) == 1, f"Unstable case types: {case_types}"

    @pytest.mark.parametrize("stockfish_json,fen", ALL_CASES)
    def test_same_stockfish_json_always_produces_same_retrieved_doc_ids(self, stockfish_json, fen):
        from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
        from llm.rag.retriever.retriever import retrieve
        from llm.rag.documents import ALL_RAG_DOCUMENTS

        all_ids = [
            tuple(
                d["id"]
                for d in retrieve(
                    extract_engine_signal(copy.deepcopy(stockfish_json), fen=fen),
                    ALL_RAG_DOCUMENTS,
                )
            )
            for _ in range(3)
        ]
        assert len(set(all_ids)) == 1, f"Unstable doc ids: {all_ids}"

    def test_pipeline_output_never_contains_invented_move_notation(self):
        """No matter the position, the FakeLLM pipeline must never emit move notation."""
        import re

        for stockfish_json, fen in ALL_CASES:
            explanation, *_ = self._run_pipeline(stockfish_json, fen)
            assert not re.search(
                r"\b[KQRBN][a-h][1-8]\b", explanation
            ), f"Invented move notation found in: {explanation!r}"

    def test_pipeline_output_never_contains_stockfish_phrase(self):
        for stockfish_json, fen in ALL_CASES:
            explanation, *_ = self._run_pipeline(stockfish_json, fen)
            assert (
                "stockfish" not in explanation.lower()
            ), f"'stockfish' leaked into output: {explanation!r}"

    def test_pipeline_output_never_contains_best_move_phrase(self):
        for stockfish_json, fen in ALL_CASES:
            explanation, *_ = self._run_pipeline(stockfish_json, fen)
            assert (
                "best move" not in explanation.lower()
            ), f"'best move' leaked into output: {explanation!r}"


# ---------------------------------------------------------------------------
# 8. POST-GAME COACH REGRESSION — deterministic rule-based decisions
# ---------------------------------------------------------------------------


class TestPostGameCoachRegressionSuite:
    """PostGameCoachController.decide() is purely rule-based; same GameSummary
    must always produce the same CoachAction type and reason.
    The controller must not invoke any RL or adaptive components.
    """

    def _make_game(
        self,
        *,
        rating_before=1500.0,
        rating_after=1500.0,
        confidence_before=0.7,
        confidence_after=0.7,
        weaknesses=None,
    ):
        from llm.seca.coach.live_controller import GameSummary

        return GameSummary(
            rating_before=rating_before,
            rating_after=rating_after,
            confidence_before=confidence_before,
            confidence_after=confidence_after,
            learning_delta=rating_after - rating_before,
            weaknesses=weaknesses or {},
        )

    def test_big_improvement_triggers_reflect(self):
        from llm.seca.coach.live_controller import PostGameCoachController

        game = self._make_game(rating_before=1500, rating_after=1525)
        action = PostGameCoachController().decide(game, [])
        assert action.type == "REFLECT"

    def test_big_drop_triggers_drill_on_main_weakness(self):
        from llm.seca.coach.live_controller import PostGameCoachController

        game = self._make_game(
            rating_before=1500,
            rating_after=1475,
            weaknesses={"endgame": 0.8, "tactics": 0.3},
        )
        action = PostGameCoachController().decide(game, [])
        assert action.type == "DRILL"
        assert action.weakness == "endgame"

    def test_confidence_drop_triggers_puzzle(self):
        from llm.seca.coach.live_controller import PostGameCoachController

        game = self._make_game(
            rating_before=1500,
            rating_after=1502,
            confidence_before=0.80,
            confidence_after=0.70,
        )
        action = PostGameCoachController().decide(game, [])
        assert action.type == "PUZZLE"

    def test_stable_progress_triggers_none(self):
        from llm.seca.coach.live_controller import PostGameCoachController

        game = self._make_game(
            rating_before=1500,
            rating_after=1502,
            confidence_before=0.70,
            confidence_after=0.72,
        )
        action = PostGameCoachController().decide(game, [])
        assert action.type == "NONE"

    def test_repeated_weakness_triggers_plan_update(self):
        from llm.seca.coach.live_controller import PostGameCoachController

        # learning_delta=8: |delta|>=5 bypasses Rule 4 (stable-progress guard),
        # delta<=20 avoids Rule 1, no weaknesses dict avoids Rule 2, confidence
        # stable avoids Rule 3 → Rule 5 fires on the repeated weakness list.
        game = self._make_game(rating_before=1500, rating_after=1508)
        recent = ["tactics", "tactics", "tactics"]
        action = PostGameCoachController().decide(game, recent)
        assert action.type == "PLAN_UPDATE"
        assert action.weakness == "tactics"

    @pytest.mark.parametrize(
        "delta,expected_type",
        [
            (25, "REFLECT"),  # > 20: big improvement
            (-25, "DRILL"),  # < -20: big drop (needs weaknesses)
            (3, "NONE"),  # small delta, confidence stable
        ],
    )
    def test_controller_decision_is_deterministic(self, delta, expected_type):
        from llm.seca.coach.live_controller import PostGameCoachController

        game = self._make_game(
            rating_before=1500,
            rating_after=1500 + delta,
            weaknesses={"tactics": 0.9} if delta < 0 else {},
        )
        actions = {PostGameCoachController().decide(game, []).type for _ in range(5)}
        assert len(actions) == 1
        assert actions.pop() == expected_type

    def test_reflect_boundary_exact(self):
        """learning_delta > 20 triggers REFLECT; delta == 20 does not."""
        from llm.seca.coach.live_controller import PostGameCoachController

        game_over = self._make_game(rating_before=1500, rating_after=1521)
        game_at = self._make_game(rating_before=1500, rating_after=1520)
        assert PostGameCoachController().decide(game_over, []).type == "REFLECT"
        # delta == 20 is not > 20, so it falls through
        assert PostGameCoachController().decide(game_at, []).type != "REFLECT"

    def test_drill_boundary_exact(self):
        """learning_delta < -20 triggers DRILL; delta == -20 does not."""
        from llm.seca.coach.live_controller import PostGameCoachController

        game_over = self._make_game(
            rating_before=1500, rating_after=1479, weaknesses={"tactics": 1.0}
        )
        game_at = self._make_game(
            rating_before=1500, rating_after=1480, weaknesses={"tactics": 1.0}
        )
        assert PostGameCoachController().decide(game_over, []).type == "DRILL"
        assert PostGameCoachController().decide(game_at, []).type != "DRILL"

    def test_controller_has_no_ml_attributes(self):
        """The PostGameCoachController must not hold any RL/adaptive state."""
        from llm.seca.coach.live_controller import PostGameCoachController

        controller = PostGameCoachController()
        for attr in ("model", "policy", "bandit", "learner", "optimizer", "weights"):
            assert not hasattr(
                controller, attr
            ), f"Controller unexpectedly has RL attribute '{attr}'"


# ---------------------------------------------------------------------------
# 9. COACH EXECUTOR — stable content output
# ---------------------------------------------------------------------------


class TestCoachExecutorStability:
    """CoachExecutor.execute() maps action types to fixed content templates.
    Same action → identical CoachContent on every call.
    """

    def _action(self, action_type, weakness=None):
        from types import SimpleNamespace

        return SimpleNamespace(type=action_type, weakness=weakness, reason="test")

    def test_drill_content_is_deterministic(self):
        from llm.seca.coach.executor import CoachExecutor

        action = self._action("DRILL", weakness="endgame")
        results = [CoachExecutor().execute(action) for _ in range(3)]
        assert results[0].title == results[1].title == results[2].title
        assert results[0].description == results[1].description

    def test_drill_title_references_weakness_name(self):
        from llm.seca.coach.executor import CoachExecutor

        action = self._action("DRILL", weakness="tactics")
        content = CoachExecutor().execute(action)
        assert "tactics" in content.title.lower()

    def test_reflect_content_is_deterministic(self):
        from llm.seca.coach.executor import CoachExecutor

        action = self._action("REFLECT")
        results = [CoachExecutor().execute(action) for _ in range(3)]
        assert results[0].title == results[1].title == results[2].title
        assert results[0].payload == results[1].payload

    def test_reflect_payload_has_reflection_questions(self):
        from llm.seca.coach.executor import CoachExecutor

        content = CoachExecutor().execute(self._action("REFLECT"))
        questions = content.payload.get("questions", [])
        assert len(questions) >= 1, "REFLECT should provide at least one reflection question"

    def test_rest_content_is_deterministic(self):
        from llm.seca.coach.executor import CoachExecutor

        action = self._action("REST")
        r1 = CoachExecutor().execute(action)
        r2 = CoachExecutor().execute(action)
        assert r1.title == r2.title
        assert r1.description == r2.description

    def test_default_content_is_deterministic(self):
        from llm.seca.coach.executor import CoachExecutor

        action = self._action("NONE")
        results = [CoachExecutor().execute(action) for _ in range(3)]
        assert all(r.title == results[0].title for r in results)
        assert all(r.description == results[0].description for r in results)

    def test_default_content_is_non_empty(self):
        from llm.seca.coach.executor import CoachExecutor

        content = CoachExecutor().execute(self._action("NONE"))
        assert content.title.strip()
        assert content.description.strip()

    @pytest.mark.parametrize("action_type", ["DRILL", "REFLECT", "REST", "NONE"])
    def test_executor_does_not_mutate_action(self, action_type):
        from llm.seca.coach.executor import CoachExecutor

        action = self._action(action_type, weakness="tactics")
        original_type = action.type
        original_weakness = action.weakness
        CoachExecutor().execute(action)
        assert action.type == original_type
        assert action.weakness == original_weakness
