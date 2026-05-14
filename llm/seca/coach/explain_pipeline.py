import json
import os
import time

import httpx

from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.rag.retriever.retriever import retrieve
from llm.rag.documents import ALL_RAG_DOCUMENTS
from llm.rag.prompts.mode_2.render import render_mode_2_prompt
from llm.rag.prompts.system_v2_mode_2 import SYSTEM_PROMPT
from llm.rag.validators.mode_2_negative import validate_mode_2_negative
from llm.rag.validators.explain_response_schema import EngineSignalSchema, ExplainSchemaError
from llm.seca.coach.confidence_language_controller import build_language_controller_block
from llm.rag.prompts.input_sanitizer import sanitize_user_query
from llm.rag.safety.output_firewall import check_output, OutputFirewallError

# ---------------------------------------------------------
# DeepSeek configuration
#
# We talk to DeepSeek via its OpenAI-compatible chat-completions
# endpoint at {COACH_DEEPSEEK_API_BASE}/chat/completions.  All three
# values are env-overridable so a future model bump or alternate
# provider with the same wire format (Together, Groq, OpenAI proper)
# can be reached by changing env without editing code.
#
#   COACH_DEEPSEEK_API_BASE   default ``https://api.deepseek.com``
#   COACH_DEEPSEEK_MODEL      default ``deepseek-chat`` (V3)
#   COACH_DEEPSEEK_API_KEY    required at runtime (no default)
#
# The Ollama variant of this module previously read
# ``COACH_OLLAMA_URL`` / ``COACH_OLLAMA_MODEL`` / ``COACH_OLLAMA_NUM_CTX``;
# those have been removed.  The deterministic fallback in
# chat_pipeline.py / live_move_pipeline.py keeps coaching available
# during DeepSeek outages, exactly as it did during Ollama outages.
# ---------------------------------------------------------

DEEPSEEK_API_BASE = os.getenv("COACH_DEEPSEEK_API_BASE", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_URL = f"{DEEPSEEK_API_BASE}/chat/completions"
MODEL_NAME = os.getenv("COACH_DEEPSEEK_MODEL", "deepseek-chat")

MAX_RETRIES = 2
_RETRY_DELAY_SECONDS = 0.5

# Defense-in-depth cap on assembled streaming-response bytes.  The
# 120s timeout is the only other bound on a compromised provider's
# response size; a hostile DeepSeek-equivalent streaming tokens at
# near-line-rate could OOM the api container.  100 kB is ~25 k tokens
# — two orders of magnitude above the realistic Mode-2 response
# (sub-1 kB) but well below the api container's memory budget.
# Truncation at the cap is safe by construction: validators downstream
# (validate_mode_2_negative / structure / semantic / output_firewall)
# will either accept the truncated text on contract or reject it and
# fall through to the deterministic builder.  See PR 8 reviewer note
# for the threat-model rationale.
_MAX_STREAM_RESPONSE_BYTES = 100_000


# ---------------------------------------------------------
# LLM CALL
# ---------------------------------------------------------


class LLMConfigError(RuntimeError):
    """Raised when the LLM provider is misconfigured (e.g. missing API key)."""


def call_llm(prompt: str) -> str:
    """Single-shot LLM completion against DeepSeek's chat-completions API.

    The Mode-2 prompt that arrives here is already a fully-rendered
    string (system + style + RAG + history + user query, assembled by
    ``render_mode_2_prompt``).  Rather than re-architect the prompt
    layer to use OpenAI-style multi-turn ``messages``, we wrap the
    rendered prompt as a single user message — the model receives the
    same content it would have received via Ollama's ``/api/generate``,
    just over a different wire format.

    Returns the assistant's reply text, stripped.  Raises:
      - ``LLMConfigError`` when ``COACH_DEEPSEEK_API_KEY`` is unset.
      - ``httpx.HTTPStatusError`` on non-2xx (caught upstream by the
        retry loop in ``generate_validated_explanation`` and the
        deterministic-fallback path in chat_pipeline / live_move).
      - ``httpx.HTTPError`` on empty / malformed streaming responses.

    Architecture: this function is the LLM Layer's only outbound
    point per ``docs/ARCHITECTURE.md`` ("LLM Generation
    (untrusted)").  Output validators downstream (Mode-2 negative,
    output firewall, ESV grounding) are unaffected by the provider
    swap — they validate the returned text regardless of where it
    came from.

    Wire format: as of 2026-05-14 (PR 8) the request uses
    ``stream: True`` and consumes DeepSeek's SSE response server-side
    rather than waiting for the full response in one shot.  Two
    benefits:

    - The HTTP connection to DeepSeek receives bytes continuously
      throughout generation — middleboxes / reverse proxies are less
      likely to close it as idle.
    - Server-side time-to-finish improves slightly on long responses
      because we process tokens as they arrive instead of waiting for
      the model to finalise the full body.

    The return type and downstream contract are unchanged: callers
    receive the full assembled string only after the stream ends.  The
    architecture's "validators run on full output before bytes reach
    the client" invariant is preserved end-to-end — see
    ``docs/ARCHITECTURE.md`` "Output Validation".  True token-level
    streaming to the client is intentionally not done here; see the
    PR 8 description for the architecture-trade-off discussion.
    """
    api_key = os.getenv("COACH_DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise LLMConfigError(
            "COACH_DEEPSEEK_API_KEY is unset; cannot call DeepSeek. "
            "Set it in .env.prod (or shell env in dev) and restart the api container."
        )

    request_body = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    chunks: list[str] = []
    total_bytes = 0
    with httpx.stream(
        "POST",
        DEEPSEEK_URL,
        json=request_body,
        headers=headers,
        timeout=120,
    ) as response:
        response.raise_for_status()
        for raw_line in response.iter_lines():
            line = raw_line.strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
                delta = event["choices"][0].get("delta", {})
            except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                # Skip individual malformed chunks rather than crashing
                # the whole call — the OpenAI-compatible SSE format
                # occasionally interleaves keep-alive comments and
                # zero-content delta frames that should not abort the
                # stream.  A truly malformed response surfaces below
                # via the empty-chunks check.
                continue
            content = delta.get("content")
            if content:
                chunks.append(content)
                total_bytes += len(content)
                if total_bytes > _MAX_STREAM_RESPONSE_BYTES:
                    # Defense-in-depth: stop consuming before the
                    # response can OOM the container.  Downstream
                    # validators will see truncated text and either
                    # accept on contract or fall through to the
                    # deterministic builder.
                    break

    assembled = "".join(chunks).strip()
    if not assembled:
        raise httpx.HTTPError(
            "DeepSeek streaming response yielded no content; "
            "expected at least one non-empty delta.content frame."
        )
    return assembled


# ---------------------------------------------------------
# SINGLE EXPLANATION ATTEMPT
# ---------------------------------------------------------


def generate_once(fen: str, stockfish_json: dict, user_query: str) -> tuple[str, dict]:
    """Generate one explanation attempt. *user_query* must already be sanitized
    by the caller (generate_validated_explanation is the authoritative point)."""
    esv = extract_engine_signal(stockfish_json, fen=fen)

    rag_docs = retrieve(esv, ALL_RAG_DOCUMENTS)

    style_block = build_language_controller_block(esv)
    prompt = render_mode_2_prompt(
        system_prompt=SYSTEM_PROMPT + "\n\n" + style_block,
        engine_signal=esv,
        rag_docs=rag_docs,
        fen=fen,
        user_query=user_query,
    )

    explanation = call_llm(prompt)

    # Post-LLM safety check — block before returning to caller
    check_output(explanation)

    return explanation, esv


# ---------------------------------------------------------
# VALIDATED EXPLANATION WITH RETRY
# ---------------------------------------------------------


def generate_validated_explanation(
    fen: str,
    stockfish_json: dict,
    user_query: str | None = "",
):
    # Authoritative sanitization point for the LLM pipeline.
    #
    # Forward-looking: generate_validated_explanation is not yet wired to an
    # HTTP endpoint — the /explain route currently uses SafeExplainer.  When
    # user_query is plumbed through to the LLM, this guard ensures injection
    # is blocked before any LLM call.
    #
    # server.py schema validation is an independent early-rejection layer
    # (returns HTTP 422 before reaching here).  Both call sites are
    # intentional defence-in-depth; sanitize_user_query is idempotent for
    # clean inputs so the double-call is harmless.
    try:
        clean_query = sanitize_user_query(user_query or "")
    except ValueError:
        # Injection detected — return safe fallback without calling the LLM.
        esv = extract_engine_signal(stockfish_json, fen=fen)
        return (
            "I cannot process this request.",
            esv,
        )

    last_error = None
    esv: dict = {}

    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            time.sleep(_RETRY_DELAY_SECONDS)
        try:
            explanation, esv = generate_once(fen, stockfish_json, clean_query)
        except OutputFirewallError:
            # Output firewall blocked the response — treat as a hard failure,
            # no retry (the model produced unsafe content; another attempt is
            # unlikely to be safe).
            esv = extract_engine_signal(stockfish_json, fen=fen)
            return (
                "I cannot process this request.",
                esv,
            )

        try:
            validate_mode_2_negative(explanation)
            # Validate engine signal structural integrity before returning.
            # The ESV is produced by extract_engine_signal(); if the schema
            # check fails here it is a programming error (never from LLM text).
            EngineSignalSchema.model_validate(esv)
            return explanation, esv  # ✅ success
        except AssertionError as e:
            last_error = str(e)

            # Retry hint appended to query
            clean_query = clean_query + (
                "\n\nIMPORTANT: Follow MODE-2 rules strictly. "
                "Do NOT speculate, invent moves, or mention engine intentions."
            )

    # If all retries failed → return safe fallback
    return (
        "I cannot provide a fully reliable explanation for this position "
        "without violating analysis constraints. Please try another position.",
        esv,
    )
