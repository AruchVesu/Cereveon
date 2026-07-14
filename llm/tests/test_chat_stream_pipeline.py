"""Tests for the validate-before-emit streaming pipeline (chat_stream_pipeline).

The contract under test (see docs/ARCHITECTURE.md streaming section):
  * a clean reply streams in chunks and ends with StreamDone whose `.reply`
    equals the assembled text;
  * NO forbidden content is ever emitted — not even the leading word of a
    multi-word forbidden phrase (the lookahead guarantees this);
  * a single-word forbidden token never leaks;
  * the mate-inevitability REQUIRE is enforced at stream END (abort if a
    mate position's reply never says "inevitable"/"forced");
  * transport / empty errors abort to the deterministic fallback.

`_call_llm_stream` and (for the mate case) `extract_engine_signal` are
monkeypatched, so these run with no network and no API key.
"""

from __future__ import annotations

import httpx
import pytest

from llm.seca.coach import chat_stream_pipeline as csp
from llm.seca.coach.chat_stream_pipeline import (
    ChatTurn,
    StreamAbort,
    StreamChunk,
    StreamDone,
    StreamRecovered,
    stream_chat_reply,
)

# Quiet equal opening: arms the equal-band + invented-tactic FORBID gates.
_FEN = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2P5/2N2N2/PP1PPPPP/R1BQKB1R w KQkq - 4 4"


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch):
    """Zero the inter-attempt sleep so retry tests don't slow the suite."""
    monkeypatch.setattr(csp, "_RETRY_DELAY_SECONDS", 0)


def _fake_stream(tokens):
    """Return a callable that yields the given tokens (like _call_llm_stream).

    Accepts ``**kwargs`` because the production call site passes
    ``max_completion_tokens=`` (the output-spend cap) alongside the prompt.
    """

    def _gen(_prompt, **_kwargs):
        for t in tokens:
            yield t

    return _gen


def _fake_stream_sequence(*token_lists, prompts=None):
    """A ``_call_llm_stream`` stand-in whose output differs per call: call N
    yields ``token_lists[N]`` (the last list repeats if called more).  When a
    ``prompts`` list is supplied, each call's prompt is appended to it so
    tests can assert on the retry hint."""

    calls = {"n": 0}

    def _gen(prompt, **_kwargs):
        if prompts is not None:
            prompts.append(prompt)
        idx = min(calls["n"], len(token_lists) - 1)
        calls["n"] += 1
        for t in token_lists[idx]:
            yield t

    _gen.calls = calls  # type: ignore[attr-defined]
    return _gen


def _drain(fen, tokens, monkeypatch, *, esv=None):
    monkeypatch.setattr(csp, "_call_llm_stream", _fake_stream(tokens))
    if esv is not None:
        # The stream now sources its signal via _chat_engine_signal (material
        # eval + board-feature flags); patch that to inject a synthetic ESV.
        monkeypatch.setattr(csp, "_chat_engine_signal", lambda *a, **k: esv)
    chunks, terminal = [], None
    for ev in stream_chat_reply(fen, [ChatTurn(role="user", content="What should I do?")]):
        if isinstance(ev, StreamChunk):
            chunks.append(ev.text)
        else:
            terminal = ev
    return chunks, terminal


def _words(*ws):
    """Tokenise to space-suffixed word tokens (each yielded separately)."""
    return [w + " " for w in ws]


# ---------------------------------------------------------------------------
# Clean reply streams and completes.
# ---------------------------------------------------------------------------
def test_clean_reply_streams_and_done(monkeypatch):
    tokens = _words(
        "Develop", "your", "pieces", "toward", "the", "centre", "and", "keep",
        "your", "king", "safe", "while", "you", "finish", "development",
    )
    chunks, terminal = _drain(_FEN, tokens, monkeypatch)
    assert isinstance(terminal, StreamDone)
    assert "".join(chunks) == "".join(tokens)           # every word forwarded, in order
    assert terminal.reply == "".join(tokens).strip()
    assert terminal.mode == "CHAT_V1"


def test_streaming_is_progressive(monkeypatch):
    # With > lookahead words, some chunks must arrive BEFORE the final flush
    # (i.e. emission is not all-at-the-end).
    tokens = _words(*[f"word{i}" for i in range(20)])
    monkeypatch.setattr(csp, "_call_llm_stream", _fake_stream(tokens))
    seen_chunk_before_done = False
    done = False
    for ev in stream_chat_reply(_FEN, [ChatTurn(role="user", content="hi")]):
        if isinstance(ev, StreamChunk) and not done:
            seen_chunk_before_done = True
        if isinstance(ev, StreamDone):
            done = True
    assert seen_chunk_before_done and done


# ---------------------------------------------------------------------------
# Forbidden content never leaks.
# ---------------------------------------------------------------------------
def test_multiword_forbidden_phrase_never_leaks_leading_word(monkeypatch):
    # "recommended move" is a 2-word structural forbid.  The leading word
    # "recommended" must NOT be emitted before the phrase completes + fires.
    tokens = _words("The", "recommended", "move", "keeps", "the", "initiative")
    chunks, terminal = _drain(_FEN, tokens, monkeypatch)
    assert isinstance(terminal, StreamAbort)
    assert terminal.reason == "forbid"
    emitted = "".join(chunks).lower()
    assert "recommended" not in emitted, f"leaked leading word of forbidden phrase: {emitted!r}"
    assert "move" not in emitted


def test_singleword_notation_never_leaks(monkeypatch):
    tokens = _words("You", "should", "play", "Nf3", "to", "develop")
    chunks, terminal = _drain(_FEN, tokens, monkeypatch)
    assert isinstance(terminal, StreamAbort)
    assert terminal.reason == "forbid"
    assert "Nf3" not in "".join(chunks)


def test_equal_band_advantage_claim_aborts(monkeypatch):
    tokens = _words("White", "is", "clearly", "winning", "this", "position")
    chunks, terminal = _drain(_FEN, tokens, monkeypatch)
    assert isinstance(terminal, StreamAbort)
    assert "winning" not in "".join(chunks).lower()


# ---------------------------------------------------------------------------
# REQUIRE gate (mate inevitability) enforced at stream END.
# ---------------------------------------------------------------------------
def _mate_esv():
    """A COMPLETE engine signal (all keys the prompt builder reads) with the
    evaluation overridden to a mate so the mate-REQUIRE gate is armed."""
    from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal

    esv = extract_engine_signal({}, fen=_FEN)
    esv["evaluation"]["type"] = "mate"
    esv["evaluation"]["band"] = "decisive_advantage"
    esv["tactical_flags"] = ["mate_threat"]
    return esv


def test_mate_without_inevitability_aborts_at_end(monkeypatch):
    # No forbidden words, so FORBID passes incrementally and chunks stream;
    # but the reply never says "inevitable"/"forced", so the end REQUIRE fails.
    tokens = _words("Your", "attack", "is", "very", "strong", "and", "the", "king", "is", "exposed")
    chunks, terminal = _drain(_FEN, tokens, monkeypatch, esv=_mate_esv())
    assert isinstance(terminal, StreamAbort)
    assert terminal.reason == "require"


def test_mate_with_inevitability_completes(monkeypatch):
    tokens = _words("The", "mate", "is", "inevitable", "and", "cannot", "be", "stopped", "now", "here")
    chunks, terminal = _drain(_FEN, tokens, monkeypatch, esv=_mate_esv())
    assert isinstance(terminal, StreamDone)
    assert "inevitable" in terminal.reply.lower()


# ---------------------------------------------------------------------------
# Transport / empty failures abort.
# ---------------------------------------------------------------------------
def test_transport_error_aborts(monkeypatch):
    def _boom(_prompt, **_kwargs):
        yield "The position "
        raise httpx.HTTPError("connection reset")

    monkeypatch.setattr(csp, "_call_llm_stream", _boom)
    terminal = None
    for ev in stream_chat_reply(_FEN, [ChatTurn(role="user", content="hi")]):
        terminal = ev
    assert isinstance(terminal, StreamAbort)
    assert terminal.reason == "transport"


def test_empty_stream_aborts(monkeypatch):
    chunks, terminal = _drain(_FEN, [], monkeypatch)
    assert isinstance(terminal, StreamAbort)
    assert terminal.reason == "empty"
    assert chunks == []


# ---------------------------------------------------------------------------
# Retry parity with the non-streaming loop (2026-07-09): a validator
# rejection retries with the targeted hint instead of aborting straight to
# the deterministic fallback.  Contract under test:
#   * violation AFTER chunks were emitted → silent buffered retry →
#     StreamRecovered carrying the full validated replacement reply;
#   * violation BEFORE any emission → the retry streams LIVE (chunks +
#     StreamDone), indistinguishable from a clean first attempt;
#   * exhaustion (every attempt rejected) → StreamAbort, exactly
#     _MAX_STREAM_RETRIES + 1 LLM calls, nothing forbidden ever emitted;
#   * the retry prompt carries the targeted rephrase hint;
#   * empty streams retry (2026-07-06 incident: transient empty responses);
#   * firewall blocks NEVER retry (parity: working-as-intended safety event).
# ---------------------------------------------------------------------------

_CLEAN_RETRY_REPLY = _words(
    "Give", "up", "material", "only", "when", "your", "pieces", "spring",
    "to", "life", "and", "the", "enemy", "king", "becomes", "exposed",
)


def _drain_events(fen, fake, monkeypatch, *, esv=None):
    monkeypatch.setattr(csp, "_call_llm_stream", fake)
    if esv is not None:
        monkeypatch.setattr(csp, "_chat_engine_signal", lambda *a, **k: esv)
    chunks, terminal = [], None
    for ev in stream_chat_reply(fen, [ChatTurn(role="user", content="When should I sacrifice?")]):
        if isinstance(ev, StreamChunk):
            chunks.append(ev.text)
        else:
            terminal = ev
    return chunks, terminal


def test_forbid_after_emission_recovers_buffered(monkeypatch):
    # Attempt 1 emits >0 chunks (violation lands beyond the lookahead),
    # then trips the invented-tactic gate on the bare noun "sacrifice" —
    # the exact prod trip from the 2026-07-09 report.  Attempt 2 is clean,
    # so the terminal must be StreamRecovered with the FULL clean reply.
    #
    # The clean prefix MUST exceed _LOOKAHEAD_WORDS so at least one word
    # clears the emit boundary before "sacrifice" arrives — otherwise the
    # violation is caught pre-emission and the retry streams live
    # (StreamDone), which is test_forbid_before_emission_retries_live's
    # case.  Kept comfortably above the current lookahead (10).
    bad = _words(
        "Knowing", "when", "to", "give", "up", "material", "in", "a",
        "tough", "spot", "matters,", "and", "sometimes", "a", "sacrifice",
    )
    assert len(bad) > csp._LOOKAHEAD_WORDS + 1, "prefix must clear the lookahead to emit first"
    fake = _fake_stream_sequence(bad, _CLEAN_RETRY_REPLY)
    chunks, terminal = _drain_events(_FEN, fake, monkeypatch)
    assert isinstance(terminal, StreamRecovered), terminal
    assert terminal.reply == "".join(_CLEAN_RETRY_REPLY).strip()
    assert terminal.mode == "CHAT_V1"
    assert chunks, "violation beyond the lookahead must have emitted chunks first"
    emitted = "".join(chunks).lower()
    assert "sacrifice" not in emitted, f"forbidden noun leaked: {emitted!r}"
    assert fake.calls["n"] == 2


def test_forbid_before_emission_retries_live(monkeypatch):
    # Attempt 1 violates within the lookahead (nothing emitted), so the
    # retry may stream live: chunks + StreamDone, like a clean first try.
    bad = _words("White", "is", "clearly", "winning")
    fake = _fake_stream_sequence(bad, _CLEAN_RETRY_REPLY)
    chunks, terminal = _drain_events(_FEN, fake, monkeypatch)
    assert isinstance(terminal, StreamDone), terminal
    assert "".join(chunks) == "".join(_CLEAN_RETRY_REPLY)
    assert "winning" not in "".join(chunks).lower()
    assert fake.calls["n"] == 2


def test_retries_exhausted_aborts_with_no_leak(monkeypatch):
    bad = _words(
        "Knowing", "when", "to", "give", "up", "material", "matters,",
        "and", "a", "sacrifice",
    )
    fake = _fake_stream_sequence(bad)  # same violation every attempt
    chunks, terminal = _drain_events(_FEN, fake, monkeypatch)
    assert isinstance(terminal, StreamAbort)
    assert terminal.reason == "forbid"
    assert fake.calls["n"] == csp._MAX_STREAM_RETRIES + 1
    assert "sacrifice" not in "".join(chunks).lower()


def test_require_failure_recovers_buffered(monkeypatch):
    # Mate ESV; attempt 1 streams a clean reply that never says
    # "inevitable" (REQUIRE fails at stream end, after chunks were
    # emitted); attempt 2 satisfies the REQUIRE → StreamRecovered.
    from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal

    esv = extract_engine_signal({}, fen=_FEN)
    esv["evaluation"]["type"] = "mate"
    esv["evaluation"]["band"] = "decisive_advantage"
    esv["tactical_flags"] = ["mate_threat"]

    no_require = _words(
        "Your", "attack", "is", "very", "strong", "and", "the", "king",
        "is", "badly", "exposed", "now",
    )
    with_require = _words(
        "The", "mate", "is", "inevitable", "and", "cannot", "be",
        "stopped", "now", "here",
    )
    fake = _fake_stream_sequence(no_require, with_require)
    chunks, terminal = _drain_events(_FEN, fake, monkeypatch, esv=esv)
    assert isinstance(terminal, StreamRecovered), terminal
    assert "inevitable" in terminal.reply.lower()
    assert fake.calls["n"] == 2


def test_empty_stream_retries_then_succeeds(monkeypatch):
    fake = _fake_stream_sequence([], _CLEAN_RETRY_REPLY)
    chunks, terminal = _drain_events(_FEN, fake, monkeypatch)
    # Nothing was emitted by the empty attempt, so the retry streams live.
    assert isinstance(terminal, StreamDone), terminal
    assert "".join(chunks) == "".join(_CLEAN_RETRY_REPLY)
    assert fake.calls["n"] == 2


def test_retry_prompt_carries_targeted_hint(monkeypatch):
    prompts: list[str] = []
    bad = _words(
        "Knowing", "when", "to", "give", "up", "material", "matters,",
        "and", "a", "sacrifice",
    )
    fake = _fake_stream_sequence(bad, _CLEAN_RETRY_REPLY, prompts=prompts)
    _drain_events(_FEN, fake, monkeypatch)
    assert len(prompts) == 2
    assert "sacrificing a piece" in prompts[1], (
        "retry prompt must carry the invented-tactic verb-form hint"
    )
    assert "sacrificing a piece" not in prompts[0]


def test_firewall_block_never_retries(monkeypatch):
    # Category P (prompt-leak) trigger — a working-as-intended safety
    # event must abort immediately, exactly like the non-streaming loop.
    bad = _words("I", "am", "instructed", "to", "avoid", "this")
    fake = _fake_stream_sequence(bad, _CLEAN_RETRY_REPLY)
    chunks, terminal = _drain_events(_FEN, fake, monkeypatch)
    assert isinstance(terminal, StreamAbort)
    assert terminal.reason == "forbid"
    assert fake.calls["n"] == 1, "firewall blocks must not be retried"
    assert "instructed" not in "".join(chunks).lower()


def test_lookahead_covers_longest_per_token_pattern():
    """The per-token FORBID gate runs BOTH the Mode-2 lexical rules
    (longest phrase: 3 words) and the output firewall, whose longest
    multi-word pattern spans ~8 whitespace words ("as an AI language
    model without any restrictions", ``output_firewall._CAT_B``).  The
    emit hold-back must be at least that long, or the leading words of
    a firewall phrase are already on the wire when the gate fires —
    breaching the "no forbidden content is ever emitted" invariant this
    module's docstring promises.  Do NOT lower this without
    re-measuring every pattern set the per-token gate runs."""
    assert csp._LOOKAHEAD_WORDS >= 10
