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
from llm.confidence_language_controller import build_language_controller_block
from llm.rag.prompts.input_sanitizer import sanitize_user_query
from llm.rag.safety.output_firewall import check_output, OutputFirewallError

_ollama_base = os.getenv("COACH_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_URL = f"{_ollama_base}/api/generate"
MODEL_NAME = os.getenv("COACH_OLLAMA_MODEL", "qwen2.5:7b-instruct-q2_K")
# Explicit context window size sent to Ollama on every request.
# Without this, quantized models may default to 4K–8K, silently truncating history.
NUM_CTX = int(os.getenv("COACH_OLLAMA_NUM_CTX", "32768"))

MAX_RETRIES = 2
_RETRY_DELAY_SECONDS = 0.5


# ---------------------------------------------------------
# LLM CALL
# ---------------------------------------------------------


def call_llm(prompt: str) -> str:
    response = httpx.post(
        OLLAMA_URL,
        json={
            "model": MODEL_NAME,
            "prompt": prompt,
            "stream": False,
            "options": {"num_ctx": NUM_CTX},
        },
        timeout=120,
    )

    response.raise_for_status()
    return response.json()["response"].strip()


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
