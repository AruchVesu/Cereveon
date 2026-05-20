from llm.rag.validators.mode_2_negative import validate_mode_2_negative
from llm.rag.validators.mode_2_structure import validate_mode_2_structure
from llm.rag.validators.mode_2_semantic import Mode2Violation, validate_mode_2_semantic
from llm.rag.contracts.validate_output import validate_output
from llm.rag.validators.sanitize import mask_chess_notation
from llm.rag.validators._rules import (
    ADVISORY_KEYWORDS,
    ENGINE_LEXICAL_PHRASES,
    MATE_CLAIM_KEYWORDS,
    STRUCTURAL_KEYWORDS,
)
from llm.rag.llm.config import MAX_MODE_2_RETRIES
from llm.rag.llm.fake import FakeLLM
import re
import logging

logger = logging.getLogger(__name__)


# Pre-compiled regexes built from the canonical keyword sets in
# ``_rules.py``.  Centralising the construction here means a change to
# the keyword set propagates everywhere: the validator surface (already
# consuming _rules.py directly) AND the repair-loop sanitization
# (consuming via these regex objects).
#
# Pre-2026-05-20, the repair loop open-coded these alternation regexes
# six times; that drift caused the latent over-rejection where the
# aggressive path kept stripping ``should`` from LLM output even after
# PR #170 retired ``\bshould\b`` from SPECULATIVE_PATTERNS.  Pinned by
# ``test_validator_taxonomy_invariants``.
_ADVISORY_RE = re.compile(
    r"\b(" + "|".join(ADVISORY_KEYWORDS) + r")\b",
    re.IGNORECASE,
)
_MATE_CLAIM_RE = re.compile(
    r"(?i)\b(checkmate|forced mate)\b",
)
_MATE_IN_N_RE = re.compile(
    r"(?i)\bmate in \d+\b",
)
# The engine-phrase regex strips a *subset* of ENGINE_LEXICAL_PHRASES
# from candidate text.  ``best move`` is omitted here because it is
# already covered by ``_ADVISORY_RE`` running earlier on the same
# string in the aggressive path.
_ENGINE_PHRASES_FOR_STRIP: tuple[str, ...] = tuple(
    p for p in ENGINE_LEXICAL_PHRASES if p != "best move"
)
_ENGINE_PHRASE_RE = re.compile(
    r"\b(" + "|".join(re.escape(p) for p in _ENGINE_PHRASES_FOR_STRIP) + r")\b",
    re.IGNORECASE,
)


def _extract_pattern_from_error(err: AssertionError) -> str:
    msg = str(err)
    m = re.search(r"pattern `(.+?)`", msg)
    if m:
        return m.group(1)

    # Backward-compatible parser for "detected: <regex>"
    m = re.search(r"detected:\s*(.+)$", msg)
    if m:
        return m.group(1).strip()
    return ""


def _is_structural_pattern(pattern: str) -> bool:
    if not pattern:
        return False
    return any(ind in pattern for ind in STRUCTURAL_KEYWORDS)


def _validate_all(text: str, case_type: str, engine_signal: dict) -> None:
    # Validator order — must match the chat / live-move pipelines' boundary
    # chain (negative → structure → semantic → output_firewall/output) so a
    # refactor here can't silently introduce parity drift with the other
    # Mode-2 callers.  See [[project-mode-pipelines-validator-parity]].
    # ``Mode2Violation`` is translated into ``AssertionError`` so the retry
    # loop's existing ``except AssertionError`` machinery keeps catching
    # every validator failure uniformly.
    validate_mode_2_negative(text)
    validate_mode_2_structure(text)
    try:
        validate_mode_2_semantic(text, engine_signal)
    except Mode2Violation as exc:
        raise AssertionError(f"Mode-2 semantic violation: {exc}") from exc
    validate_output(text, case_type=case_type)


def _attempt_remove_forbidden_sections(
    llm, prompt: str, text: str, case_type: str, engine_signal: dict
) -> str:
    # Attempt to remove forbidden structural sections via an LLM rewrite
    if isinstance(llm, FakeLLM):
        raise AssertionError("Mode-2 structural violation could not be auto-fixed for FakeLLM")

    rewrite_prompt = (
        prompt
        + "\n\nREWRITE INSTRUCTIONS:\n"
        + "Remove any sections or phrasing that look like recommended moves, example moves, explicit 'white can'/'black can' suggestions, plans expressed as step lists, or speculative 'if it' clauses. Do NOT add new information.\n\n"
        + "TEXT TO REWRITE:\n"
        + text
    )

    rewritten = llm.generate(rewrite_prompt)
    _validate_all(rewritten, case_type=case_type, engine_signal=engine_signal)
    return rewritten


def run_mode_2(llm, prompt: str, case_type: str, *, engine_signal: dict) -> str:
    """Run mode 2: ask the LLM for an explanation and validate the output.

    Behavior:
    - Generate output and run the Mode-2 validator chain inside `_validate_all`
      (negative → structure → semantic → output) — same order the chat /
      live-move pipelines run inside their retry loops.
    - If a Mode-2 violation is detected due to CHESS NOTATION, attempt one
      automated rewrite (sanitize notation and ask the LLM to rewrite to
      remove notation and prescriptive language).
    - If a violation is due to advisory/prescriptive language, attempt a
      rewrite only for non-Fake LLMs.
    - For other violations or when using FakeLLM, raise immediately.

    ``engine_signal`` is the engine-derived ESV dict produced by
    ``extract_engine_signal`` and is required because
    ``validate_mode_2_semantic`` is ESV-conditioned (rejects equal-band
    advantage claims, requires mate-inevitability vocabulary on
    ``evaluation.type == "mate"``, blocks invented tactical motifs when
    ``tactical_flags`` is empty).  Required (kw-only) to prevent a future
    caller from silently re-introducing the parity gap that this function
    closed on 2026-05-15.

    Returns the LLM-generated explanation string when it passes all validators.
    Raises AssertionError when a validator detects a violation.
    """

    # First attempt
    output = llm.generate(prompt)

    def _validate_all(text: str):
        validate_mode_2_negative(text)
        validate_mode_2_structure(text)
        try:
            validate_mode_2_semantic(text, engine_signal)
        except Mode2Violation as exc:
            raise AssertionError(f"Mode-2 semantic violation: {exc}") from exc
        validate_output(text, case_type=case_type)

    try:
        _validate_all(output)
        return output
    except AssertionError as err:
        pattern = _extract_pattern_from_error(err)

        # For FakeLLM, preserve immediate failure behavior (tests rely on it)
        if isinstance(llm, FakeLLM):
            raise

        logger.debug("run_mode_2: initial validation failed, pattern=%s", pattern)

        # Helper to run a model rewrite with instructions
        def model_rewrite(instructions: str, text: str) -> str:
            prompt2 = (
                prompt
                + "\n\nREWRITE INSTRUCTIONS:\n"
                + instructions
                + "\n\nTEXT TO REWRITE:\n"
                + text
            )
            return llm.generate(prompt2)

        # Helper sanitization and rewrite utilities
        def text_sanitize(t: str) -> str:
            s = mask_chess_notation(t)
            s = _MATE_CLAIM_RE.sub("decisive advantage", s)
            s = _MATE_IN_N_RE.sub("decisive advantage", s)
            return s

        def rewrite_remove_notation(t: str) -> str:
            s = mask_chess_notation(t)
            return model_rewrite(
                "Remove ALL chess notation and coordinates, and remove any prescriptive/advisory language. Do NOT add new information.",
                s,
            )

        def rewrite_remove_advisory(t: str) -> str:
            advisory_examples = ", ".join(f"'{k}'" for k in ADVISORY_KEYWORDS)
            return model_rewrite(
                f"Remove ALL advisory or prescriptive language (words like {advisory_examples}). Do NOT add new information.",
                t,
            )

        def rewrite_remove_mate(t: str) -> str:
            return model_rewrite(
                "Remove absolute mate claims (e.g., 'checkmate', 'mate in N', 'forced mate'). Describe the evaluation and its implications without adding new factual assertions. Do NOT add new information.",
                t,
            )

        def text_remove_advisory(t: str) -> str:
            # Remove advisory/prescriptive trigger words conservatively.
            # ``should`` was retired from ADVISORY_KEYWORDS in PR #170
            # (2026-05-16); see _rules.DUAL_USE_TOKENS["should"].
            s = _ADVISORY_RE.sub("", t)
            # collapse multiple spaces and tidy commas
            s = re.sub(r"\s+", " ", s).strip()
            return s

        def text_remove_structure(t: str) -> str:
            # Remove forbidden section headings and short trigger phrases conservatively
            s = re.sub(r"(?im)^\s*(recommended move|example move|plan)[:\s]?.*$", "", t)
            s = re.sub(r"(?i)\b(white can|black can|if it|consider)\b", "", s)
            s = re.sub(r"\s+", " ", s).strip()
            return s

        def rewrite_remove_structure(t: str) -> str:
            return model_rewrite(
                "Remove headings or sections such as 'Plan', 'Recommended Move', 'Example Move', and avoid phrasing like 'White can' or 'Black can'. Present the analysis as continuous, evaluative prose without headings. Do NOT add new information.",
                t,
            )

        # If mate claims are the initial issue or the validator message references a mate,
        # try an immediate deterministic sanitization. Also handle forbidden phrase failures
        # that may prevent pattern extraction (validate_output may raise first).
        err_text = str(err).lower()
        # ``re.search`` form matches the *substring* keywords with word
        # boundaries — same membership semantics as ``in pattern`` for
        # the bare keyword set, but applied to free text.
        _mate_kw_re = re.compile(
            r"(?i)\b(" + "|".join(re.escape(k) for k in MATE_CLAIM_KEYWORDS) + r")\b"
        )

        if (
            (pattern and any(k in pattern for k in MATE_CLAIM_KEYWORDS))
            or _mate_kw_re.search(output)
            or _mate_kw_re.search(err_text)
        ):
            logger.debug("run_mode_2: attempting quick mate sanitization")
            quick = mask_chess_notation(output)
            quick = _MATE_CLAIM_RE.sub("decisive advantage", quick)
            quick = _MATE_IN_N_RE.sub("decisive advantage", quick)
            try:
                _validate_all(quick)
                logger.debug("run_mode_2: quick mate sanitization succeeded")
                return quick
            except AssertionError as err2:
                logger.debug("run_mode_2: quick mate sanitization failed: %s", err2)
                last_err = err2

        # If the initial failure was due to forbidden phrases (like 'stockfish' or 'best move'),
        # try a deterministic sanitization that removes those tokens and any mate claims.
        if "stockfish" in err_text or "best move" in err_text:
            logger.debug("run_mode_2: attempting quick forbidden-phrase sanitization")
            quick2 = mask_chess_notation(output)
            quick2 = re.sub(r"(?i)\bstockfish\b", "", quick2)
            quick2 = re.sub(r"(?i)\bbest move\b", "", quick2)
            quick2 = _MATE_CLAIM_RE.sub("decisive advantage", quick2)
            quick2 = _MATE_IN_N_RE.sub("decisive advantage", quick2)
            quick2 = re.sub(r"\s+", " ", quick2).strip()
            try:
                _validate_all(quick2)
                logger.debug("run_mode_2: quick forbidden-phrase sanitization succeeded")
                return quick2
            except AssertionError as err2:
                logger.debug("run_mode_2: quick forbidden-phrase sanitization failed: %s", err2)
                last_err = err2

        # Iterative repair loop: react to the latest validation error and transform the candidate accordingly
        candidate = output
        attempts = 0
        last_err = err

        while attempts < MAX_MODE_2_RETRIES:
            attempts += 1
            pattern = _extract_pattern_from_error(last_err)
            logger.debug("run_mode_2: attempt=%s, pattern=%s", attempts, pattern)

            # If advisory language flagged, try advisory rewrite first.
            # ``should`` is no longer in ADVISORY_KEYWORDS (PR #170,
            # 2026-05-16) — if a validator reports a pattern containing
            # ``should``, it's a different ADVISORY trigger (must, needs
            # to, best move) co-occurring with it.
            if pattern and any(k in pattern for k in ADVISORY_KEYWORDS):
                # 1) Try LLM advisory-only rewrite
                try:
                    candidate = rewrite_remove_advisory(candidate)
                    _validate_all(candidate)
                    return candidate
                except AssertionError as err2:
                    last_err = err2

                # 2) Try deterministic text-level removal of advisory words
                try:
                    candidate = text_remove_advisory(candidate)
                    _validate_all(candidate)
                    return candidate
                except AssertionError as err2:
                    last_err = err2
                    # loop and react to new error
                    continue

            # If structural violation flagged, try structure rewrite then deterministic removal
            if pattern and any(k in pattern for k in STRUCTURAL_KEYWORDS):
                try:
                    candidate = rewrite_remove_structure(candidate)
                    _validate_all(candidate)
                    return candidate
                except AssertionError as err2:
                    last_err = err2

                try:
                    candidate = text_remove_structure(candidate)
                    _validate_all(candidate)
                    return candidate
                except AssertionError as err2:
                    last_err = err2
                    continue

            # If notation flagged, try notation-sanitized rewrite first
            if pattern and "[a-h][1-8]" in pattern:
                try:
                    candidate = rewrite_remove_notation(candidate)
                    _validate_all(candidate)
                    return candidate
                except AssertionError as err2:
                    last_err = err2
                    continue

            # If mate claims flagged, try mate-only rewrite
            if pattern and any(k in pattern for k in MATE_CLAIM_KEYWORDS):
                try:
                    candidate = rewrite_remove_mate(candidate)
                    _validate_all(candidate)
                    return candidate
                except AssertionError as err2:
                    last_err = err2
                    continue

            # As a general fallback: try text-level sanitization then combined rewrite
            try:
                candidate = text_sanitize(candidate)
                _validate_all(candidate)
                return candidate
            except AssertionError as err2:
                last_err = err2

            try:
                candidate = model_rewrite(
                    "Remove ALL chess notation and coordinates, remove ALL advisory or prescriptive language, and remove absolute mate claims. Do NOT add new information.",
                    candidate,
                )
                _validate_all(candidate)
                return candidate
            except AssertionError as err2:
                last_err = err2
                continue

        # Final aggressive sanitization attempt before giving up.
        # Notes on what is and isn't stripped:
        #   * Mate claims are softened to "decisive advantage".
        #   * ``ADVISORY_KEYWORDS`` are replaced with ``[REDACTED]`` —
        #     ``should`` is NOT in this set (PR #170 retired it because
        #     coaching imperative ≠ speculative; stripping it here was
        #     the latent over-rejection the centralisation closes).
        #   * Engine vocabulary is fully removed.
        aggressive = mask_chess_notation(candidate)
        aggressive = _MATE_CLAIM_RE.sub("decisive advantage", aggressive)
        aggressive = _MATE_IN_N_RE.sub("decisive advantage", aggressive)
        aggressive = _ADVISORY_RE.sub("[REDACTED]", aggressive)
        # Remove any remaining forbidden engine phrases to maximise the
        # chance of passing validators on the last attempt.
        aggressive = _ENGINE_PHRASE_RE.sub("", aggressive)
        aggressive = re.sub(r"\s+", " ", aggressive).strip()

        try:
            _validate_all(aggressive)
            return aggressive
        except AssertionError as aggressive_err:
            # If aggressive sanitization still fails, surface that error explicitly
            raise aggressive_err

        # If none succeeded, raise the last seen error
        raise last_err
