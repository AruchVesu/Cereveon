"""DeepSeek BaseLLM adapter.

Wraps ``llm.seca.coach.explain_pipeline.call_llm`` (the production LLM call site) in
the ``BaseLLM`` interface that ``run_mode_2`` and the contract / smoke
tests consume. This keeps the test suite's "real-LLM" path identical to
production at the wire: same HTTP request, same retry behaviour, same
error types — only the test gate (``RUN_DEEPSEEK_TESTS=1`` +
``COACH_DEEPSEEK_API_KEY``) is different.

The class is intentionally minimal — per ``docs/ARCHITECTURE.md``,
``BaseLLM`` is "a language realizer only" with the single method
``generate(prompt) -> str``. Provider configuration (base URL, model,
API key) is read by ``call_llm`` from env, so swapping to a different
OpenAI-compatible provider (Groq, Together, OpenAI proper, a self-hosted
LiteLLM gateway, etc.) does not require code changes here.
"""

from __future__ import annotations

from llm.seca.coach.explain_pipeline import call_llm
from llm.rag.llm.base import BaseLLM


class DeepseekLLM(BaseLLM):
    """BaseLLM adapter that talks to DeepSeek via ``call_llm``.

    ``temperature`` is accepted for forward-compatibility with the
    legacy ``OllamaLLM`` signature but is currently a no-op — the
    DeepSeek wire format used by ``call_llm`` does not surface it; the
    production server relies on the model's default sampling
    temperature and on the validator gates downstream. Pass it for
    callers that switch back and forth, but do not expect it to change
    behaviour today.
    """

    def __init__(self, model: str | None = None, temperature: float | None = None) -> None:
        # ``model`` is also accepted for parity with the old Ollama adapter;
        # the live model is selected by ``COACH_DEEPSEEK_MODEL`` env var
        # inside ``call_llm``, which is the architectural single source of
        # truth.  Storing the constructor arg avoids surprising the caller
        # but does not override the env-driven runtime selection.
        self._model = model
        self._temperature = temperature

    def generate(self, prompt: str) -> str:
        return call_llm(prompt)
