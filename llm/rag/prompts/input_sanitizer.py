"""
Prompt injection firewall for user-supplied input.

Defence layers
--------------
1. Strip null bytes and non-printable control characters (keep \\n, \\r, \\t).
2. Normalize unicode to collapse homoglyph/lookalike substitution attacks.
3. Detect known injection patterns across six attack categories and raise
   ValueError so the caller can reject the request early (HTTP 422 at schema
   validation) or route to a safe fallback at pipeline entry — rather than
   relying on the LLM to ignore the injected instruction.
4. Truncate to the max allowed length as a last-resort safeguard.

Attack categories covered
-------------------------
A. Instruction override   — "ignore/disregard/forget previous instructions"
B. Persona / role hijack  — "pretend you are", "roleplay as", "act as"
C. Prompt extraction      — "show your system prompt", "what are your rules"
D. Jailbreak / bypass     — DAN, developer mode, safety bypass
E. Format-token injection — ChatML, Llama, Mistral special tokens
F. Encoding attacks       — base64 instruction smuggling

This is a *pre-LLM* defence. Post-LLM output validation is handled by
llm/rag/safety/output_firewall.py, llm/rag/validators/, and run_mode_2.py.

Idempotency guarantee
---------------------
sanitize_user_query is safe to call at multiple defence points:
  - Clean inputs   : pass through unchanged on every call (idempotent).
  - Injected inputs: raise ValueError on the *first* call; the request is
    rejected before it can reach a second call site.
Two call sites are intentional:
  1. server.py schema validation  → early API rejection (HTTP 422).
  2. explain_pipeline.py entry    → pipeline-level guard for direct calls.
Both are independent; the idempotency guarantee keeps double-call harmless.
"""

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

MAX_USER_QUERY_LENGTH = 2000

# ---------------------------------------------------------------------------
# Category A — Instruction override
# ---------------------------------------------------------------------------
_CAT_A: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions?", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?(previous\s+)?instructions?", re.IGNORECASE),
    re.compile(r"forget\s+.{0,30}instructions?", re.IGNORECASE),
    re.compile(r"override\s+(your\s+)?(instructions?|rules?|prompt|guidelines?|constraints?)", re.IGNORECASE),
    re.compile(r"(new|updated?|replace\s+your)\s+system\s+prompt\b", re.IGNORECASE),
    re.compile(r"change\s+your\s+(instructions?|rules?|guidelines?|behavior)\b", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\b", re.IGNORECASE),
    re.compile(r"from\s+now\s+on\s+you\b", re.IGNORECASE),
    re.compile(r"(your\s+)?(actual|real|true|hidden)\s+(goal|purpose|instructions?|rules?|prompt)\b", re.IGNORECASE),
    re.compile(r"(disregard|bypass|skip|ignore)\s+(your\s+)?(safety|restrictions?|rules?|limits?|guidelines?|constraints?)", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Category B — Persona / role hijack
# ---------------------------------------------------------------------------
_CAT_B: list[re.Pattern[str]] = [
    re.compile(r"act\s+as\s+(a\s+)?(normal\s+|different\s+|unrestricted\s+)?assistant", re.IGNORECASE),
    re.compile(r"pretend\s+(you\s+are|to\s+be)\b", re.IGNORECASE),
    re.compile(r"role[\s\-]?play\s+as\b", re.IGNORECASE),
    re.compile(r"simulate\s+(being|a)\b", re.IGNORECASE),
    re.compile(r"in\s+(the\s+)?character\s+of\b", re.IGNORECASE),
    re.compile(r"you\s+are\s+(now\s+)?(a\s+|an\s+)?(different|new|other|another)\b", re.IGNORECASE),
    re.compile(r"switch\s+(to\s+)?(a\s+)?different\s+(mode|persona|role|character)\b", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Category C — Prompt / data extraction
# ---------------------------------------------------------------------------
_CAT_C: list[re.Pattern[str]] = [
    re.compile(r"reveal\s+.{0,30}system\s+prompt", re.IGNORECASE),
    re.compile(r"(show|print|output|display|expose|leak|dump|repeat|tell\s+me)\s+(your\s+|the\s+)?(system\s+prompt|instructions?|training\s+data|confidential|private|internal\s+rules?)", re.IGNORECASE),
    re.compile(r"what\s+(are|is)\s+your\s+(system\s+prompt|instructions?|rules?|guidelines?|constraints?|prompt)\b", re.IGNORECASE),
    re.compile(r"repeat\s+(the\s+)?internal\s+instructions?", re.IGNORECASE),
    re.compile(r"print\s+(the\s+)?raw\s+engine\s+analysis", re.IGNORECASE),
    re.compile(r"(output|dump)\s+(the\s+)?retrieved\s+context", re.IGNORECASE),
    # Narrowed verbatim: only flag command-style uses
    re.compile(r"(repeat|output|print|dump)\b.{0,60}\bverbatim\b", re.IGNORECASE),
    re.compile(r"(tell|show|give)\s+me\s+(your|all\s+the)\s+(training|system|hidden|internal)\b", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Category D — Jailbreak / bypass
# ---------------------------------------------------------------------------
_CAT_D: list[re.Pattern[str]] = [
    re.compile(r"\bDAN\b"),  # "Do Anything Now"
    re.compile(r"\bjailbreak\b", re.IGNORECASE),
    re.compile(r"developer\s+mode\b", re.IGNORECASE),
    re.compile(r"do\s+anything\s+now\b", re.IGNORECASE),
    re.compile(r"without\s+(any\s+)?(restrictions?|limits?|safety|constraints?)\b", re.IGNORECASE),
    re.compile(r"unrestricted\s+(mode|assistant|AI)\b", re.IGNORECASE),
    re.compile(r"(remove|turn\s+off|disable)\s+(your\s+)?(safety|filter|restrictions?|guardrails?)\b", re.IGNORECASE),
    re.compile(r"safe\s*guard\s*bypass\b", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Category E — Format-token injection (model-specific control sequences)
# ---------------------------------------------------------------------------
_CAT_E: list[re.Pattern[str]] = [
    # ChatML / Qwen2.5
    re.compile(r"<\|im_start\|>", re.IGNORECASE),
    re.compile(r"<\|im_end\|>", re.IGNORECASE),
    re.compile(r"<\|system\|>", re.IGNORECASE),
    # Llama / Mistral / Vicuna
    re.compile(r"\[\s*(INST|SYS|SYSTEM|USER|ASSISTANT)\s*\]", re.IGNORECASE),
    re.compile(r"<<SYS>>|<\/s>(?!\w)", re.IGNORECASE),
    # Generic role-tag injection
    re.compile(r"<\s*(system|user|assistant|instruction)\s*>", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Category F — Encoding / obfuscation attacks
# ---------------------------------------------------------------------------
_CAT_F: list[re.Pattern[str]] = [
    # Base64-encoded instruction smuggling (≥8 base64 chars)
    re.compile(r"base64\b.{0,30}\b(instructions?|prompt|command)\b", re.IGNORECASE),
    # Hex-encoded control characters used to escape the turn
    re.compile(r"\\x0[0-9a-f]", re.IGNORECASE),
    # URL-encoded special chars used to smuggle tokens
    re.compile(r"%3c%7c|%7c%3e", re.IGNORECASE),  # <| |>
]

_ALL_PATTERNS: list[re.Pattern[str]] = (
    _CAT_A + _CAT_B + _CAT_C + _CAT_D + _CAT_E + _CAT_F
)

# Control characters except TAB (0x09), LF (0x0A), CR (0x0D)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _normalize_unicode(text: str) -> str:
    """NFC-normalize and strip unicode category Cf (format characters).

    Collapses most homoglyph/lookalike substitution attacks by normalizing to
    composed form (NFC) so patterns match the canonical character sequence.
    Cf characters (zero-width space, soft hyphen, etc.) are stripped because
    they are invisible and used to break keyword detection.
    """
    normalized = unicodedata.normalize("NFC", text)
    return "".join(c for c in normalized if unicodedata.category(c) != "Cf")


def sanitize_user_query(text: str) -> str:
    """Return a sanitized copy of *text* safe to embed in an LLM prompt.

    Steps:
    1. Strip dangerous control characters.
    2. Normalize unicode to defeat homoglyph attacks.
    3. Raise ValueError if any injection pattern fires.
    4. Truncate to MAX_USER_QUERY_LENGTH.

    Raises:
        ValueError: if prompt-injection patterns are detected in *text*.
    """
    if not text:
        return text

    # 1. Strip control characters
    cleaned = _CONTROL_CHAR_RE.sub("", text)

    # 2. Unicode normalization (defeats invisible-char and homoglyph tricks)
    cleaned = _normalize_unicode(cleaned)

    # 3. Detect injection patterns — reject rather than label
    detected = [p.pattern for p in _ALL_PATTERNS if p.search(cleaned)]
    if detected:
        logger.warning(
            "Prompt injection detected in user_query — request rejected: %s",
            detected,
        )
        raise ValueError(
            f"Prompt injection detected ({len(detected)} pattern(s) matched). Request rejected."
        )

    # 4. Truncate
    if len(cleaned) > MAX_USER_QUERY_LENGTH:
        cleaned = cleaned[:MAX_USER_QUERY_LENGTH]

    return cleaned
