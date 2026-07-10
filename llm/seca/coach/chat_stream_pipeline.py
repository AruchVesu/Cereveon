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
  * A validator rejection (FORBID mid-stream, REQUIRE at end) or an empty
    stream does NOT immediately fall back: the pipeline retries the LLM
    with the same targeted rephrase hints the non-streaming path uses
    (``chat_pipeline._targeted_retry_hint`` — retry-loop parity, cf. the
    validator-parity invariant).  Retry attempts run *buffered* when the
    aborted attempt already emitted chunks (emitted text cannot be
    retracted, so a recovered reply must arrive as a whole); they stream
    live when nothing was emitted yet.  A buffered recovery terminates
    with ``StreamRecovered`` — the route sends it as a full replacement,
    exactly like the fallback ``abort`` event on the wire.
  * Only firewall blocks, transport errors, and retry exhaustion yield
    ``StreamAbort``; the route then serves the deterministic fallback.
    No forbidden content ever reaches the client; the only thing the user
    might briefly see and have replaced is clean-but-incomplete text.

This module deliberately reuses ``chat_pipeline._build_chat_prompt`` so the
streamed prompt is byte-identical to the non-streamed one (no parity drift),
and ``_mode_2_validators.validate_mode_2_or_raise`` for the final full pass.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass

from llm.rag.llm.config import CHAT_MAX_COMPLETION_TOKENS as _CHAT_MAX_COMPLETION_TOKENS
from llm.seca.coach.context_compact import compact_history, should_compact
from llm.seca.coach.chat_pipeline import (
    ChatTurn,
    _build_chat_prompt,
    _chat_engine_signal,
    _CHAT_MAX_RETRIES,
    _CHAT_RETRY_DELAY_SECONDS,
    _is_spurious_refusal,
    _SPURIOUS_RETRY_HINT,
    _targeted_retry_hint,
)

logger = logging.getLogger(__name__)

# Same retry budget + pacing as the non-streaming loop in
# ``chat_pipeline.generate_chat_reply`` (module-level aliases so tests can
# monkeypatch the delay to zero without touching the shared constants).
_MAX_STREAM_RETRIES = _CHAT_MAX_RETRIES
_RETRY_DELAY_SECONDS = _CHAT_RETRY_DELAY_SECONDS

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
class StreamRecovered:
    """Terminal event: the live stream attempt was rejected by a validator,
    but a silent buffered retry produced a fully-validated reply.  The route
    must deliver it as a FULL REPLACEMENT of any partially-streamed text —
    on the wire it rides the same ``abort``-shaped payload the deterministic
    fallback uses (the client already replaces partial text with that
    payload's ``reply``), so no client change is needed."""

    engine_signal: dict
    mode: str
    reply: str  # full validated reply (for persistence + replacement)


@dataclass(frozen=True)
class StreamAbort:
    """Terminal event: the stream could not be safely completed.  The route
    must serve the deterministic fallback.  ``reason`` is for logging only."""

    reason: str


StreamEvent = StreamChunk | StreamDone | StreamRecovered | StreamAbort


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
    player_color: str = "white",
) -> Iterator[StreamEvent]:
    """Yield validated coaching reply chunks as DeepSeek generates them.

    Always terminates with exactly one ``StreamDone`` (live success),
    ``StreamRecovered`` (a silent retry succeeded after the live attempt
    was rejected), or ``StreamAbort`` (the route then serves the
    deterministic fallback).  Never raises to the caller — every failure
    path becomes a terminal event.

    Retry parity (closes the "began writing then went robotic" gap,
    2026-07-09): the non-streaming path retries validator rejections up to
    ``_CHAT_MAX_RETRIES`` times with targeted rephrase hints and usually
    recovers; this path used to abort to the deterministic fallback on the
    FIRST rejection, so /chat/stream went robotic far more often than
    /chat on identical questions.  The loop below mirrors the non-stream
    exception contract: firewall block → immediate fallback (safety event,
    never retried); AssertionError / Mode2Violation → targeted hint +
    retry; transport error → immediate fallback.  Empty streams also retry
    (deliberate divergence from the non-stream path, where empties lump
    into the generic transport branch — the 2026-07-06 incident was two
    consecutive empty DeepSeek streams, exactly the transient failure a
    retry absorbs).
    """
    engine_signal = _chat_engine_signal(fen, stockfish_json)

    if should_compact(messages):
        messages = compact_history(messages)

    if not _STREAM_AVAILABLE:
        yield StreamAbort("stream_unavailable")
        return

    retry_hint = ""
    last_exc: Exception | None = None
    last_reason = "forbid"
    any_emitted = False  # chunks already forwarded to the client (any attempt)

    for attempt in range(_MAX_STREAM_RETRIES + 1):
        if attempt > 0:
            time.sleep(_RETRY_DELAY_SECONDS)

        try:
            prompt = _build_chat_prompt(
                fen,
                messages,
                player_profile,
                engine_signal,
                past_mistakes=past_mistakes,
                retry_hint=retry_hint,
                coach_voice=coach_voice,
                last_move=last_move,
                player_color=player_color,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Mode-2 stream prompt build failed (%s: %s); fallback", type(exc).__name__, exc
            )
            yield StreamAbort("prompt_error")
            return

        # Emit live only while the client has seen nothing: once chunks are
        # forwarded they cannot be retracted, so retry attempts after any
        # emission run buffered and deliver via StreamRecovered instead.
        live = not any_emitted
        buffer = ""
        emitted = 0
        try:
            # Same output-spend cap as the non-streaming chat path (parity;
            # see llm.rag.llm.config for the headroom rationale).
            for token in _call_llm_stream(
                prompt, max_completion_tokens=_CHAT_MAX_COMPLETION_TOKENS
            ):
                buffer += token
                # FORBID gates on the whole buffer; raises -> handled below.
                _validate_forbid(buffer, engine_signal)
                if live:
                    boundary = _safe_emit_boundary(buffer, _LOOKAHEAD_WORDS)
                    if boundary > emitted:
                        yield StreamChunk(buffer[emitted:boundary])
                        emitted = boundary
                        any_emitted = True
        except _OutputFirewallError as exc:
            # Working-as-intended safety event — never retried (parity with
            # the non-streaming loop's immediate break on a firewall block).
            logger.info("Mode-2 stream firewall block (%s); serving fallback", str(exc)[:120])
            yield StreamAbort("forbid")
            return
        except (AssertionError, _Mode2Violation) as exc:
            # A forbidden token/phrase appeared.  Because of the lookahead it
            # was never forwarded — retry with a targeted rephrase hint.
            last_exc, last_reason = exc, "forbid"
            retry_hint = _targeted_retry_hint(exc)
            logger.info(
                "Mode-2 stream FORBID violation (%s); attempt %d/%d",
                str(exc)[:120],
                attempt + 1,
                _MAX_STREAM_RETRIES + 1,
            )
            continue
        except Exception as exc:  # noqa: BLE001
            # Transport / timeout / provider error mid-stream.
            logger.warning(
                "Mode-2 stream transport error (%s: %s); serving fallback",
                type(exc).__name__,
                exc,
            )
            yield StreamAbort("transport")
            return

        reply = buffer.strip()
        if not reply:
            # Transient provider failure (2026-07-06 incident: two
            # consecutive empty DeepSeek streams).  Retry without a hint —
            # emptiness isn't a phrasing problem.  Every abort reason must
            # stay greppable in one pass.
            last_exc, last_reason = None, "empty"
            logger.warning(
                "Mode-2 stream produced no content; attempt %d/%d",
                attempt + 1,
                _MAX_STREAM_RETRIES + 1,
            )
            continue

        # Final FULL validation on the complete reply — same gates the
        # non-streaming path runs (``validate_mode_2_or_raise``: firewall +
        # negative + structure + semantic).  The only thing this adds over
        # the incremental FORBID pass is the semantic mate-inevitability
        # REQUIRE (``check_mate_require`` defaults True here), which can
        # only be judged on the complete reply.
        try:
            validate_mode_2_or_raise(reply, engine_signal)
        except _OutputFirewallError as exc:
            logger.info(
                "Mode-2 stream end firewall block (%s); serving fallback", str(exc)[:120]
            )
            yield StreamAbort("require")
            return
        except (AssertionError, _Mode2Violation) as exc:
            last_exc, last_reason = exc, "require"
            retry_hint = _targeted_retry_hint(exc)
            logger.info(
                "Mode-2 stream end-validation failed (%s); attempt %d/%d",
                str(exc)[:120],
                attempt + 1,
                _MAX_STREAM_RETRIES + 1,
            )
            continue

        # Spurious-refusal backstop (parity with the non-streaming loop):
        # a canned refusal that cannot be correct here is a soft failure on
        # non-final attempts; the final attempt ships what it got (still a
        # valid Mode-2 reply).
        if attempt < _MAX_STREAM_RETRIES and _is_spurious_refusal(reply, engine_signal):
            last_exc, last_reason = None, "require"
            retry_hint = _SPURIOUS_RETRY_HINT
            logger.debug("Mode-2 stream spurious refusal; retrying with neutral hint")
            continue

        if live:
            # Flush the held tail (validated above) and finish.
            if emitted < len(buffer):
                yield StreamChunk(buffer[emitted:])
            yield StreamDone(engine_signal=engine_signal, mode="CHAT_V1", reply=reply)
        else:
            yield StreamRecovered(engine_signal=engine_signal, mode="CHAT_V1", reply=reply)
        return

    # Retries exhausted — mirror the non-streaming exhaustion WARNING so
    # operators have one greppable phrase ("LLM failed after N attempts")
    # across all Mode-2 paths.
    if last_exc is not None:
        logger.warning(
            "Mode-2 stream LLM failed after %d attempts (%s: %s); serving deterministic fallback",
            _MAX_STREAM_RETRIES + 1,
            type(last_exc).__name__,
            last_exc,
        )
    else:
        logger.warning(
            "Mode-2 stream LLM failed after %d attempts (last reason: %s); "
            "serving deterministic fallback",
            _MAX_STREAM_RETRIES + 1,
            last_reason,
        )
    yield StreamAbort(last_reason)
