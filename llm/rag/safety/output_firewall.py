"""
Post-LLM output safety firewall.

Scans LLM-generated text for signals that the model has been manipulated into
unsafe behaviour — prompt leakage, harmful content, bypass claims, or PII
disclosure.  Raises OutputFirewallError for any violation.

This is a post-LLM defence layer that complements the pre-LLM input sanitizer
in llm/rag/prompts/input_sanitizer.py and the chess-specific validators in
llm/rag/validators/.

Design principles
-----------------
- Fail closed: any uncertain pattern triggers rejection.
- No silencing: violations are logged and raised, never swallowed.
- Stateless: pure function over the response text; no external calls.
- Layered: called after the chess validators so chess-domain checks run first.

Violation categories
--------------------
P. Prompt/system leakage  — model discloses its instructions or training
H. Harmful content        — instructions for harm, self-harm, or illegal acts
B. Bypass / role escape   — model claims unrestricted or alternate-persona mode
I. Identity confusion      — model claims to be a different AI or human
D. PII / credential leak  — emails, API keys, or password-like strings in output
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


class OutputFirewallError(ValueError):
    """Raised when the LLM output violates safety constraints."""

    def __init__(self, category: str, patterns: list[str]) -> None:
        self.category = category
        self.patterns = patterns
        super().__init__(
            f"Output firewall violation [{category}]: "
            f"{len(patterns)} pattern(s) matched — response blocked."
        )


# ---------------------------------------------------------------------------
# Category P — Prompt / system leakage
# ---------------------------------------------------------------------------
_CAT_P: list[re.Pattern[str]] = [
    re.compile(r"(my\s+)?system\s+prompt\s+(is|says|states?|contains?|reads?)\b", re.IGNORECASE),
    re.compile(r"I\s+(am|was)\s+instructed\s+to\b", re.IGNORECASE),
    re.compile(r"my\s+instructions?\s+(are|say|include|tell\s+me)\b", re.IGNORECASE),
    re.compile(r"I\s+have\s+been\s+(told|configured|programmed|instructed)\b", re.IGNORECASE),
    re.compile(r"here\s+(are|is)\s+my\s+(system\s+)?(instructions?|rules?|prompt)\b", re.IGNORECASE),
    re.compile(r"the\s+system\s+prompt\s+(says?|tells?|instructs?|reads?)\b", re.IGNORECASE),
    re.compile(r"I\s+cannot\s+reveal\s+my\s+(instructions?|prompt|rules?)\b", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Category H — Harmful content
# ---------------------------------------------------------------------------
_CAT_H: list[re.Pattern[str]] = [
    re.compile(r"how\s+to\s+(make|build|create|synthesize)\s+(a\s+)?(bomb|explosive|weapon|malware|exploit|poison|drug)\b", re.IGNORECASE),
    re.compile(r"(step[- ]by[- ]step|instructions?\s+for)\s+(hacking|attacking|exploiting|harming)\b", re.IGNORECASE),
    re.compile(r"(self[- ]harm|suicide)\s+(method|instruction|guide|how\s+to)\b", re.IGNORECASE),
    re.compile(r"how\s+to\s+(hurt|kill|harm|attack)\s+(someone|a\s+person|yourself)\b", re.IGNORECASE),
    re.compile(r"(illegal\s+drug|controlled\s+substance)\s+(synthesis|recipe|formula)\b", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Category B — Bypass / role escape
# ---------------------------------------------------------------------------
_CAT_B: list[re.Pattern[str]] = [
    re.compile(r"as\s+(an?\s+)?AI\s+(language\s+model\s+)?without\s+(any\s+)?(restrictions?|limits?|safety)\b", re.IGNORECASE),
    re.compile(r"as\s+DAN\b|in\s+DAN\s+mode\b", re.IGNORECASE),
    re.compile(r"developer\s+mode\s+(enabled|activated|is\s+on)\b", re.IGNORECASE),
    re.compile(r"I\s+can\s+(now\s+)?(help|do|say)\s+(anything|whatever)\b", re.IGNORECASE),
    re.compile(r"(restrictions?|safety\s+filters?)\s+(have\s+been\s+)?(removed|disabled|bypassed|turned\s+off)\b", re.IGNORECASE),
    re.compile(r"in\s+(unrestricted|uncensored|jailbreak)\s+mode\b", re.IGNORECASE),
    re.compile(r"I\s+will\s+now\s+ignore\s+(my\s+)?(guidelines?|rules?|restrictions?|safety)\b", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Category I — Identity confusion
# ---------------------------------------------------------------------------
_CAT_I: list[re.Pattern[str]] = [
    re.compile(r"I\s+am\s+(not\s+an?\s+AI|a\s+human|a\s+real\s+person)\b", re.IGNORECASE),
    re.compile(r"I\s+am\s+(GPT|ChatGPT|Gemini|Llama|Mistral|Claude|Copilot)\b", re.IGNORECASE),
    re.compile(r"(I'm|I\s+am)\s+pretending\s+to\s+be\b", re.IGNORECASE),
    re.compile(r"as\s+my\s+new\s+(persona|character|role)\b", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Category D — PII / credential leak
# ---------------------------------------------------------------------------

# Email pattern (strict: requires TLD)
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]{2,}@[A-Za-z0-9.\-]{2,}\.[A-Za-z]{2,}\b"
)

# API key / secret-looking token heuristic:
# starts with a recognizable prefix followed by a long alphanumeric string
_APIKEY_RE = re.compile(
    r"\b(sk|pk|api|key|secret|token|bearer|auth)[_\-]?[A-Za-z0-9]{16,}\b",
    re.IGNORECASE,
)

# Password assignment pattern — matches "password: value", "password is: value",
# "password = value" etc.
_PASSWORD_RE = re.compile(
    r"\b(password|passwd|pwd)\b.{0,15}[:=]\s*\S{6,}",
    re.IGNORECASE,
)

# API key/secret with equals-sign assignment (api_key=sk_live_...)
_APIKEY_ASSIGN_RE = re.compile(
    r"\b(api[_\-]?key|secret[_\-]?key|access[_\-]?token)\s*[:=]\s*[A-Za-z0-9_\-]{12,}",
    re.IGNORECASE,
)

_CAT_D: list[re.Pattern[str]] = [_EMAIL_RE, _APIKEY_RE, _APIKEY_ASSIGN_RE, _PASSWORD_RE]

# ---------------------------------------------------------------------------
# All categories in check order
# ---------------------------------------------------------------------------

_CATEGORIES: list[tuple[str, list[re.Pattern[str]]]] = [
    ("PROMPT_LEAK", _CAT_P),
    ("HARMFUL", _CAT_H),
    ("BYPASS", _CAT_B),
    ("IDENTITY", _CAT_I),
    ("PII_CREDENTIAL", _CAT_D),
]


def check_output(text: str) -> None:
    """Assert that *text* is free of safety violations.

    Raises:
        OutputFirewallError: if any violation is detected.  The error carries
            the violation category and the specific matched patterns.
    """
    if not text:
        return

    for category, patterns in _CATEGORIES:
        matched = [p.pattern for p in patterns if p.search(text)]
        if matched:
            logger.warning("Output firewall blocked response due to safety policy.")
            raise OutputFirewallError(category=category, patterns=matched)
