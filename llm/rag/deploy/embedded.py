import os
import time
import inspect

from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.rag.retriever import retrieve
from llm.rag.documents import ALL_RAG_DOCUMENTS
from llm.rag.prompts.render_mode_2 import render_mode_2_prompt
from llm.rag.prompts.system_v2_mode_2 import SYSTEM_PROMPT
from llm.rag.prompts.input_sanitizer import sanitize_user_query
from llm.rag.safety.output_firewall import check_output, OutputFirewallError
from llm.rag.llm.ollama import OllamaLLM
from llm.rag.llm.run_mode_2 import run_mode_2
from llm.rag.meta.case_classifier import infer_case_type
from llm.rag.telemetry.event import Mode2TelemetryEvent
from llm.rag.telemetry.sink import emit

# ---- Load deployment config (edge only) ----

DEPLOYMENT_MODE = os.getenv("DEPLOYMENT_MODE", "DEV")
LLM_MODEL = os.getenv("LLM_MODEL")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
VALIDATION = os.getenv("MODE_2_VALIDATION") == "ENFORCED"


if not LLM_MODEL:
    raise RuntimeError("LLM_MODEL must be set")


# ---- Initialize real LLM once ----

if LLM_MODEL and LLM_MODEL.startswith("fake"):
    # Support selecting a FakeLLM with an optional mode via LLM_MODEL, e.g.:
    #   LLM_MODEL=fake             -> FakeLLM(mode="compliant")
    #   LLM_MODEL=fake:mate_softening -> FakeLLM(mode="mate_softening")
    from llm.rag.llm.fake import FakeLLM

    parts = LLM_MODEL.split(":", 1)
    mode = parts[1] if len(parts) > 1 else "compliant"
    _REAL_LLM = FakeLLM(mode=mode)
else:
    _REAL_LLM = OllamaLLM(
        model=LLM_MODEL,
        temperature=LLM_TEMPERATURE,
    )


# ---- Embedded public API ----


def explain_position(payload: dict) -> dict:
    if "engine_json" not in payload:
        raise ValueError("payload must include engine_json")

    start_time = time.time()
    explanation_text = None
    confidence = "unknown"
    case_type = "unknown"

    try:
        # Sanitize user_query at the embedded entry point — this is the
        # authoritative guard for the edge/host-app deployment path.
        try:
            user_query = sanitize_user_query(payload.get("user_query", "") or "")
        except ValueError:
            esv = extract_engine_signal(payload["engine_json"])
            return {
                "explanation": "I cannot process this request.",
                "confidence": "low",
                "tags": [],
            }

        esv = extract_engine_signal(payload["engine_json"])

        rag_docs = retrieve(esv, ALL_RAG_DOCUMENTS)

        sig = inspect.signature(render_mode_2_prompt).parameters
        prompt_kwargs = {
            "engine_signal": esv,
            "fen": payload["fen"],
            "user_query": user_query,
        }
        if "system_prompt" in sig:
            prompt_kwargs["system_prompt"] = SYSTEM_PROMPT
        if "rag_docs" in sig:
            prompt_kwargs["rag_docs"] = rag_docs
        if "rag_context" in sig:
            prompt_kwargs["rag_context"] = rag_docs

        prompt = render_mode_2_prompt(**prompt_kwargs)

        case_type = payload.get("case_type") or infer_case_type(esv)

        explanation_text = run_mode_2(
            llm=_REAL_LLM,
            prompt=prompt,
            case_type=case_type,
        )

        # Post-LLM output safety check — block before returning to caller.
        try:
            check_output(explanation_text)
        except OutputFirewallError:
            explanation_text = "I cannot process this request."

        from llm.rag.quality.explanation_score import score_explanation
        from llm.rag.llm.config import MIN_QUALITY_SCORE

        score = score_explanation(text=explanation_text, engine_signal=esv)
        confidence = "high" if score >= MIN_QUALITY_SCORE else "low"

        return {
            "explanation": explanation_text,
            "confidence": confidence,
            "tags": [],
        }

    finally:
        latency_ms = int((time.time() - start_time) * 1000)

        event = Mode2TelemetryEvent(
            success=explanation_text is not None,
            retry_used=False,  # intentionally fixed for now
            latency_ms=latency_ms,
            validator_failures=[],
            output_length=len(explanation_text) if explanation_text else 0,
            case_type=case_type,
            confidence=confidence,
            model=LLM_MODEL,
        )

        emit(event)
