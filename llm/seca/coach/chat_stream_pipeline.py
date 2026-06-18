"""Streaming Mode-2 chat pipeline — *validate-before-emit* real token streaming.

Companion to ``chat_pipeline.generate_chat_reply`` (the non-streaming path,
which validates the FULL reply before returning).  This path streams
DeepSeek tokens through, but upholds the trust boundary by running the
**FORBID** gates on the growing buffer and only forwarding text that has
already passed them.

Design (see ``docs/ARCHITECTURE.md`` → "Output Validation" → streaming):

  * Tokens accumulate into ``buffer``.  After every token the FORBID gates
    (output firewall + Mode-2 negative + structure + semantic-forbid) run
    on the whole buffer.  A violation aborts the stream BEFORE the
    offending text is forwarded.
  * Only text at least ``_LOOKAHEAD_WORDS`` words behind the frontier is
    emitted, so the leading word of a multi-word forbidden phrase
    (``best move``, ``mate in 3``, ``with perfect play``) can never escape
    before the phrase completes and the gate fires.
  * The REQUIRE gates (mate must say "inevitable"; missing-data must say
    "missing") are whole-reply properties, so they run once at stream end.
    If unmet, the stream aborts.
  * Any abort (forbid violation mid-stream, require failure at end, empty
    reply, or transport error) yields ``StreamAbort``; the route then
    serves the deterministic fallback.  No forbidden content reaches the
    client; the only thing the user might briefly see and have replaced is
    clean-but-incomplete text on the rare mate/missing-data abort.

This module deliberately reuses ``chat_pipeline._build_chat_prompt`` so the
streamed prompt is byte-identical to the non-streamed one (no parity drift),
and ``_mode_2_validators.validate_mode_2_or_raise`` for the final full pass.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

from llm.seca.coach.context_compact import compact_history, should_compact
from llm.seca.coach.chat_pipeline import ChatTurn, _build_chat_prompt, _chat_engine_signal

logger = logging.getLogger(__name__)

# Hold the last N whitespace-delimited words back from emission until enough
# following context proves they are not the start of a forbidden phrase.
# The longest forbidden phrase today is 3 words ("with perfect play",
# "mate in 3", "the engine wants", "lack of planning"); 5 leaves margin.
# If a longer forbidden phrase is ever added, bump this.
_LOOKAHEAD_WORDS = 5

try:
    from llm.seca.coach.explain_pipeline import call_llm_stream as _call_llm_stream  # type: ignore[import]
    from llm.seca.coach._mode_2_validators import validate_mode_2_or_raise  # type: ignore[import]
    from llm.rag.safety.output_firewall import (  # type: ignore[import]
        check_output as _check_output,
        OutputFirewallError as _OutputFirewallError,
    )
    from llm.rag.validators.mode_2_negative import validate_mode_2_negative as _validate_neg  # type: ignore[import]
    from llm.rag.validators.mode_2_structure import validate_mode_2_structure as _validate_struct  # type: ignore[import]
    from llm.rag.validators.mode_2_semantic import (  # type: ignore[import]
        validate_mode_2_semantic as _validate_sem,
        Mode2Violation as _Mode2Violation,
    )

    _STREAM_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001
    logger.warning("Streaming LLM imports unavailable — /chat/stream will fall back: %s", _exc)
    _STREAM_AVAILABLE = False


@dataclass(frozen=True)
class StreamChunk:
    """A confirmed-safe slice of the reply to forward to the client."""

    text: str


@dataclass(frozen=True)
class StreamDone:
    """Terminal event for a fully-validated streamed reply."""

    engine_signal: dict
    mode: str
    reply: str  # full assembled reply (for persistence) — already validated


@dataclass(frozen=True)
class StreamAbort:
    """Terminal event: the stream could not be safely completed.  The route
    must serve the deterministic fallback.  ``reason`` is for logging only."""

    reason: str


StreamEvent = StreamChunk | StreamDone | StreamAbort


def _validate_forbid(text: str, engine_signal: dict) -> None:
    """Run only the FORBID gates on a partial buffer.

    Same gates as ``validate_mode_2_or_raise`` EXCEPT the semantic
    mate-inevitability REQUIRE (skipped via ``check_mate_require=False`` —
    a partial buffer legitimately may not contain "inevitable" yet).
    Raises OutputFirewallError / AssertionError / Mode2Violation on a
    violation, exactly like the non-streaming gates.
    """
    _check_output(text)
    _validate_neg(text)
    _validate_struct(text)
    _validate_sem(text, engine_signal, check_mate_require=False)


def _safe_emit_boundary(buffer: str, lookahead_words: int) -> int:
    """Index up to which ``buffer`` is safe to emit (exclusive).

    Returns the position of the ``lookahead_words``-th space from the end, so
    everything before it (all but the trailing ``lookahead_words`` words) can
    be forwarded; the trailing words stay held pending more context.  Returns
    0 while the buffer has fewer than ``lookahead_words`` words.
    """
    pos = len(buffer)
    for _ in range(lookahead_words):
        sp = buffer.rfind(" ", 0, pos)
        if sp == -1:
            return 0
        pos = sp
    return pos


def stream_chat_reply(
    fen: str,
    messages: list[ChatTurn],
    player_profile: dict | None = None,
    past_mistakes: list[str] | None = None,
    move_count: int | None = None,  # accepted for route symmetry; unused in the LLM prompt
    coach_voice: str | None = None,
    last_move: str | None = None,
    stockfish_json: dict | None = None,
) -> Iterator[StreamEvent]:
    """Yield validated coaching reply chunks as DeepSeek generates them.

    Always terminates with exactly one ``StreamDone`` (success) or one
    ``StreamAbort`` (the route then serves the deterministic fallback).
    Never raises to the caller — every failure path becomes a ``StreamAbort``.
    """
    engine_signal = _chat_engine_signal(fen, stockfish_json)

    if should_compact(messages):
        messages = compact_history(messages)

    if not _STREAM_AVAILABLE:
        yield StreamAbort("stream_unavailable")
        return

    try:
        prompt = _build_chat_prompt(
            fen,
            messages,
            player_profile,
            engine_signal,
            past_mistakes=past_mistakes,
            coach_voice=coach_voice,
            last_move=last_move,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Mode-2 stream prompt build failed (%s: %s); fallback", type(exc).__name__, exc)
        yield StreamAbort("prompt_error")
        return

    buffer = ""
    emitted = 0
    try:
        for token in _call_llm_stream(prompt):
            buffer += token
            # FORBID gates on the whole buffer; raises -> abort below.
            _validate_forbid(buffer, engine_signal)
            boundary = _safe_emit_boundary(buffer, _LOOKAHEAD_WORDS)
            if boundary > emitted:
                yield StreamChunk(buffer[emitted:boundary])
                emitted = boundary
    except (_OutputFirewallError, AssertionError, _Mode2Violation) as exc:
        # A forbidden token/phrase appeared.  Because of the lookahead it
        # was never forwarded — abort cleanly to the deterministic fallback.
        logger.info("Mode-2 stream FORBID violation (%s); serving fallback", str(exc)[:120])
        yield StreamAbort("forbid")
        return
    except Exception as exc:  # noqa: BLE001
        # Transport / timeout / provider error mid-stream.
        logger.warning("Mode-2 stream transport error (%s: %s); serving fallback", type(exc).__name__, exc)
        yield StreamAbort("transport")
        return

    reply = buffer.strip()
    if not reply:
        yield StreamAbort("empty")
        return

    # Final FULL validation on the complete reply — same gates the
    # non-streaming path runs (``validate_mode_2_or_raise``: firewall +
    # negative + structure + semantic).  The only thing this adds over the
    # incremental FORBID pass is the semantic mate-inevitability REQUIRE
    # (``check_mate_require`` defaults True here), which can only be judged
    # on the complete reply.
    try:
        validate_mode_2_or_raise(reply, engine_signal)
    except (_OutputFirewallError, AssertionError, _Mode2Violation) as exc:
        logger.info("Mode-2 stream end-validation failed (%s); serving fallback", str(exc)[:120])
        yield StreamAbort("require")
        return

    # Flush the held tail (validated above) and finish.
    if emitted < len(buffer):
        yield StreamChunk(buffer[emitted:])
    yield StreamDone(engine_signal=engine_signal, mode="CHAT_V1", reply=reply)
