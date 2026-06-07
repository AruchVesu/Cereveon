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
    stream_chat_reply,
)

# Quiet equal opening: arms the equal-band + invented-tactic FORBID gates.
_FEN = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2P5/2N2N2/PP1PPPPP/R1BQKB1R w KQkq - 4 4"


def _fake_stream(tokens):
    """Return a no-arg callable that yields the given tokens (like _call_llm_stream)."""

    def _gen(_prompt):
        for t in tokens:
            yield t

    return _gen


def _drain(fen, tokens, monkeypatch, *, esv=None):
    monkeypatch.setattr(csp, "_call_llm_stream", _fake_stream(tokens))
    if esv is not None:
        monkeypatch.setattr(csp, "extract_engine_signal", lambda *a, **k: esv)
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
    def _boom(_prompt):
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
