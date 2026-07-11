"""Tests for ``llm.seca.review.writer`` — the review's LLM trust gates.

Stable test IDs (do NOT rename):
  REVIEW_WRT_OK_*    compliant output accepted
  REVIEW_WRT_GATE_*  violating output rejected → deterministic fallback
  REVIEW_WRT_FBK_*   fallback texts are contract-compliant by construction
"""

from __future__ import annotations

from llm.rag.contracts.validate_output import validate_output
from llm.rag.llm.base import BaseLLM
from llm.rag.safety.output_firewall import check_output
from llm.rag.validators.mode_2_negative import validate_mode_2_negative
from llm.seca.review.models import LLM_OUTCOME_FALLBACK, LLM_OUTCOME_FULL
from llm.seca.review.moments import (
    MOMENT_BLUNDER,
    MOMENT_MISSED_WIN,
    MOMENT_MISTAKE,
    MOMENT_PUNISHED_MISTAKE,
    MOMENT_STRATEGIC,
    REVIEW_MODE_STANDARD,
    REVIEW_MODE_STRATEGIC,
)
from llm.seca.review.writer import (
    SOURCE_FALLBACK,
    SOURCE_LLM,
    fallback_moment_text,
    fallback_verdict,
    write_review_texts,
)

# ---------------------------------------------------------------------------
# Scripted fake LLM
# ---------------------------------------------------------------------------


class _ScriptedLLM(BaseLLM):
    """Returns queued responses in order; repeats the last one when the
    queue runs dry (so a retry sees the same bad output).  Raising mode
    simulates a dead provider."""

    def __init__(self, responses: list[str] | None = None, *, raise_always: bool = False):
        self.responses = list(responses or [])
        self.raise_always = raise_always
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        if self.raise_always:
            raise RuntimeError("provider unreachable")
        if not self.responses:
            return ""
        if len(self.responses) == 1:
            return self.responses[0]
        return self.responses.pop(0)


_COMPLIANT_TEXT = (
    "You kept your pieces working together here and the pressure told. "
    "Next time, take one extra breath to look at your opponent's reply "
    "before committing yourself."
)

_NOTATION_TEXT = "You should have played Nf3 here to keep the initiative."

_EQUAL_ESV = {
    "evaluation": {"type": "cp", "band": "equal", "side": "black"},
    "eval_delta": "stable",
    "last_move_quality": "unknown",
    "tactical_flags": [],
    "position_flags": [],
    "phase": "middlegame",
}


def _moment(ply: int = 12, moment_type: str = MOMENT_BLUNDER) -> dict:
    return {
        "ply": ply,
        "move_number": 6,
        "san": "Bg5",
        "moment_type": moment_type,
        "phase": "middlegame",
        "band_before": "equal",
        "band_after": "losing",
        "fen_before": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "fen_after": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "clock_remaining_s": None,
    }


def _write(llm: BaseLLM, *, moments: list[dict] | None = None, esvs: dict | None = None) -> dict:
    ms = moments if moments is not None else [_moment()]
    return write_review_texts(
        llm=llm,
        moments=ms,
        engine_signals=esvs if esvs is not None else {m["ply"]: _EQUAL_ESV for m in ms},
        result="loss",
        review_mode=REVIEW_MODE_STANDARD,
        skill_hint="intermediate",
        accuracy_phrase="steady with a few sharp slips",
        weak_phases=["middlegame"],
    )


# ---------------------------------------------------------------------------
# REVIEW_WRT_OK — compliant path
# ---------------------------------------------------------------------------


class TestCompliantPath:
    def test_compliant_output_accepted(self):
        """REVIEW_WRT_OK_ACCEPT: clean coach prose passes the gates and
        lands with source='llm' and outcome='full'."""
        llm = _ScriptedLLM([_COMPLIANT_TEXT])
        payload = _write(llm)
        assert payload["outcome"] == LLM_OUTCOME_FULL
        assert payload["moments"][0]["source"] == SOURCE_LLM
        assert payload["moments"][0]["text"] == _COMPLIANT_TEXT
        assert payload["verdict"]["source"] == SOURCE_LLM
        # One call per moment + one for the verdict — no retries needed.
        assert llm.calls == 2

    def test_moment_entries_keep_card_order(self):
        """REVIEW_WRT_OK_ORDER: entries mirror the input moment order,
        keyed by ply for client-side join."""
        llm = _ScriptedLLM([_COMPLIANT_TEXT])
        moments = [_moment(ply=10), _moment(ply=20), _moment(ply=30)]
        payload = _write(llm, moments=moments)
        assert [m["ply"] for m in payload["moments"]] == [10, 20, 30]


# ---------------------------------------------------------------------------
# REVIEW_WRT_GATE — rejection → fallback
# ---------------------------------------------------------------------------


class TestGates:
    def test_notation_rejected_then_fallback(self):
        """REVIEW_WRT_GATE_NOTATION: move notation is rejected on both
        attempts → deterministic fallback text, outcome='fallback'."""
        llm = _ScriptedLLM([_NOTATION_TEXT])
        payload = _write(llm)
        moment_entry = payload["moments"][0]
        assert moment_entry["source"] == SOURCE_FALLBACK
        assert moment_entry["text"] == fallback_moment_text(_moment())
        assert payload["outcome"] == LLM_OUTCOME_FALLBACK
        assert "Nf3" not in moment_entry["text"]

    def test_retry_can_recover(self):
        """REVIEW_WRT_GATE_RETRY: a violation on attempt 1 followed by a
        clean rewrite on attempt 2 is accepted as LLM text."""
        llm = _ScriptedLLM(
            [
                _NOTATION_TEXT,  # moment attempt 1 — rejected
                _COMPLIANT_TEXT,  # moment attempt 2 — accepted
                _COMPLIANT_TEXT,  # verdict attempt 1
            ]
        )
        payload = _write(llm)
        assert payload["moments"][0]["source"] == SOURCE_LLM
        assert payload["outcome"] == LLM_OUTCOME_FULL

    def test_invented_tactic_gated_by_esv_flags(self):
        """REVIEW_WRT_GATE_SEMANTIC: 'fork' with no tactical flag is
        rejected (invented tactic); the SAME text with the flag present
        is accepted — the gate is ESV-conditioned, not lexical."""
        fork_text = (
            "You walked straight into a fork here and lost material for "
            "nothing. Slow down when short-range pieces jump toward your "
            "position."
        )
        no_flags = dict(_EQUAL_ESV)
        payload = _write(_ScriptedLLM([fork_text]), esvs={12: no_flags})
        assert payload["moments"][0]["source"] == SOURCE_FALLBACK

        flagged = dict(_EQUAL_ESV)
        flagged["tactical_flags"] = ["fork"]
        payload2 = _write(_ScriptedLLM([fork_text]), esvs={12: flagged})
        assert payload2["moments"][0]["source"] == SOURCE_LLM

    def test_provider_failure_falls_back_everywhere(self):
        """REVIEW_WRT_GATE_PROVIDER: a dead provider degrades every text
        to its deterministic fallback; nothing raises."""
        payload = _write(_ScriptedLLM(raise_always=True))
        assert payload["moments"][0]["source"] == SOURCE_FALLBACK
        assert payload["verdict"]["source"] == SOURCE_FALLBACK
        assert payload["outcome"] == LLM_OUTCOME_FALLBACK

    def test_overlong_output_rejected(self):
        """REVIEW_WRT_GATE_LENGTH: a rambling reply past the card cap is
        rejected rather than overflowing the card."""
        rambling = "This position had many interesting features. " * 40
        payload = _write(_ScriptedLLM([rambling]))
        assert payload["moments"][0]["source"] == SOURCE_FALLBACK

    def test_firewall_violation_rejected(self):
        """REVIEW_WRT_GATE_FIREWALL: identity-confusion output is caught
        by the output firewall and falls back."""
        payload = _write(_ScriptedLLM(["I am ChatGPT, a large language model."]))
        assert payload["moments"][0]["source"] == SOURCE_FALLBACK

    def test_empty_output_rejected(self):
        """REVIEW_WRT_GATE_EMPTY: an empty stream falls back."""
        payload = _write(_ScriptedLLM([""]))
        assert payload["moments"][0]["source"] == SOURCE_FALLBACK


# ---------------------------------------------------------------------------
# REVIEW_WRT_FBK — fallback compliance pins
# ---------------------------------------------------------------------------


class TestFallbackCompliance:
    """REVIEW_WRT_FBK: every deterministic fallback text must itself
    satisfy the Mode-2 lexical gate and the output firewall — the
    fallback is the safety floor, so pin it per type/mode."""

    def test_moment_fallbacks_pass_gates(self):
        for moment_type in (
            MOMENT_BLUNDER,
            MOMENT_MISSED_WIN,
            MOMENT_MISTAKE,
            MOMENT_PUNISHED_MISTAKE,
            MOMENT_STRATEGIC,
        ):
            text = fallback_moment_text(_moment(moment_type=moment_type))
            validate_mode_2_negative(text)  # raises on violation
            validate_output(text, case_type="reflection")
            check_output(text)
            assert len(text) > 40, f"fallback for {moment_type} suspiciously short"

    def test_verdict_fallbacks_pass_gates(self):
        for result in ("win", "loss", "draw"):
            for mode in (REVIEW_MODE_STANDARD, REVIEW_MODE_STRATEGIC):
                text = fallback_verdict(
                    result=result, review_mode=mode, weak_phases=["endgame"]
                )
                validate_mode_2_negative(text)
                validate_output(text, case_type="reflection")
                check_output(text)

    def test_fallbacks_never_read_llm_text(self):
        """REVIEW_WRT_FBK_ISOLATION: fallback builders take only the
        deterministic moment payload / stats — their signatures admit no
        LLM-derived argument (forbidden-widening rule from
        ARCHITECTURE.md § Deterministic Fallback)."""
        import inspect

        moment_params = set(inspect.signature(fallback_moment_text).parameters)
        assert moment_params == {"moment"}
        verdict_params = set(inspect.signature(fallback_verdict).parameters)
        assert verdict_params == {"result", "review_mode", "weak_phases"}
