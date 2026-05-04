"""
Context compaction for long chat histories — llm/seca/coach/context_compact.py

When a conversation exceeds COMPACT_THRESHOLD turns, old turns are replaced with a
structured summary that preserves mistake patterns, demonstrated strengths, and
topics covered.  Recent turns are always kept verbatim.

Compaction is fully deterministic (no LLM).  The summary is injected as a
role="system" ChatTurn so both the LLM prompt builder and the deterministic
fallback treat it as context, not as a user question or assistant answer.
"""

from __future__ import annotations

import re

# Trigger compaction when messages list reaches this length.
COMPACT_THRESHOLD: int = 20

# Number of most-recent turns kept verbatim after compaction.
COMPACT_KEEP_RECENT: int = 6

# ---------------------------------------------------------------------------
# Pattern tables  (pattern, human-readable label)
# ---------------------------------------------------------------------------

_MISTAKE_PATTERNS: list[tuple[str, str]] = [
    (r"\bblunder(ed)?\b", "blunder"),
    (r"\bmistake\b", "mistake"),
    (r"\binaccuracy\b", "inaccuracy"),
    (r"\bhanging\b", "hanging piece"),
    (r"\bdropped\b", "dropped piece"),
]

_STRENGTH_PATTERNS: list[tuple[str, str]] = [
    (r"\bexcellent move\b", "excellent move"),
    (r"\bgood move\b", "good move"),
    (r"\bbest move\b", "best move"),
    (r"\bbrilliant\b", "brilliant move"),
    (r"\bwell played\b", "well played"),
]

_TOPIC_PATTERNS: list[tuple[str, str]] = [
    (r"\bopening\b", "opening"),
    (r"\btactic(s|al)?\b", "tactics"),
    (r"\bendgame\b", "endgame"),
    (r"\bpawn structur(e|al)?\b", "pawn structure"),
    (r"\bking safety\b", "king safety"),
    (r"\bfork\b", "fork"),
    (r"\bpin\b", "pin"),
    (r"\bskewer\b", "skewer"),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def should_compact(messages: list) -> bool:
    """Return True when history is long enough to benefit from compaction."""
    return len(messages) >= COMPACT_THRESHOLD


def compact_history(messages: list) -> list:
    """Replace old turns with a structured summary; keep recent turns verbatim.

    Returns a new list: [summary_turn] + messages[-COMPACT_KEEP_RECENT:]

    The summary turn uses role="system" so it surfaces in the history block
    without being mistaken for a user question or assistant answer.
    """
    from llm.seca.coach.chat_pipeline import ChatTurn  # local import avoids circular

    if len(messages) <= COMPACT_KEEP_RECENT:
        return list(messages)

    old_turns = messages[:-COMPACT_KEEP_RECENT]
    recent_turns = messages[-COMPACT_KEEP_RECENT:]

    summary_text = _build_compact_summary(old_turns)
    summary_turn = ChatTurn(role="system", content=summary_text)

    return [summary_turn] + list(recent_turns)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _collect_tags(text: str, patterns: list[tuple[str, str]]) -> list[str]:
    """Return labels for all patterns found in text (deduped, order-preserving)."""
    found: list[str] = []
    seen: set[str] = set()
    for pat, label in patterns:
        if re.search(pat, text, re.IGNORECASE) and label not in seen:
            found.append(label)
            seen.add(label)
    return found


def _build_compact_summary(old_turns: list) -> str:
    """Build a structured coaching summary from a list of ChatTurn-like objects."""
    text = " ".join(getattr(t, "content", "") for t in old_turns)

    mistakes = _collect_tags(text, _MISTAKE_PATTERNS)
    strengths = _collect_tags(text, _STRENGTH_PATTERNS)
    topics = _collect_tags(text, _TOPIC_PATTERNS)

    parts: list[str] = [
        f"[CONTEXT SUMMARY — {len(old_turns)} earlier turns compacted]"
    ]
    if mistakes:
        parts.append(f"Recurring weaknesses: {', '.join(mistakes)}.")
    if strengths:
        parts.append(f"Demonstrated strengths: {', '.join(strengths)}.")
    if topics:
        parts.append(f"Topics covered: {', '.join(topics)}.")
    if not mistakes and not strengths and not topics:
        parts.append("No specific patterns identified.")

    return " ".join(parts)
