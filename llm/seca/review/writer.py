"""LLM realization of the post-game review — moment texts + verdict.

Wave 3 of the review: given the deterministically-selected critical
moments (:mod:`llm.seca.review.moments`) and their per-position engine
signals, produce the coach commentary the moment cards render, plus the
pattern-level verdict.  Modeled on the study-plan verdict writer
(``llm.seca.coach.study_plan.verdict``) — the proven "narrow LLM
surface" pattern: single-shot ``BaseLLM.generate`` calls, one retry
with an explicit rejection hint, deterministic coach-voice fallbacks on
any unrecoverable failure.  ``write_review_texts`` NEVER raises.

Trust posture
-------------
The LLM is untrusted per ``docs/ARCHITECTURE.md``.  Every accepted text
passes:

* ``validate_mode_2_negative``   — notation / engine-lexicon /
  speculative-phrase rejection (the live-boundary lexical gate).
* ``validate_output(case_type="reflection")`` — broader forbidden-
  phrase containment, same complementary pairing the study-plan
  verdict uses.
* ``validate_mode_2_semantic``   — MOMENT TEXTS ONLY, with that
  moment's ESV: rejects invented tactics ("fork"/"pin"/"sacrifice"
  with no tactical flag) and advantage claims on an equal band.  The
  game-level verdict has no single grounding position, so the semantic
  ESV-conditioned checks don't apply to it (same rationale as the
  study-plan verdict).
* ``output_firewall.check_output`` — PII / prompt-leak / bypass /
  harmful scan.

Prompts are COORDINATE-FREE by design: no SAN, no UCI, no square names
go in (the grounding lesson from PRs #247–249 — coordinates in the
prompt reliably produce notation in the output, which the gates then
reject).  The move's identity ("You played Bg5") is a game-record fact
the client renders from ``moments_json``; the LLM explains the idea.

Fallback texts are constructed from the moment type + band words only,
read as plain coaching (never engine readouts, per the deterministic-
coach-phrasing convention), and satisfy the Mode-2 contracts by
construction — no LLM-derived string is reachable through them.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from llm.rag.contracts.validate_output import validate_output
from llm.rag.safety.output_firewall import OutputFirewallError, check_output
from llm.rag.validators.mode_2_negative import validate_mode_2_negative
from llm.rag.validators.mode_2_semantic import Mode2Violation, validate_mode_2_semantic
from llm.seca.review.models import LLM_OUTCOME_FALLBACK, LLM_OUTCOME_FULL
from llm.seca.review.moments import (
    BAND_BETTER,
    BAND_EQUAL,
    BAND_LOSING,
    BAND_WINNING,
    BAND_WORSE,
    MOMENT_BLUNDER,
    MOMENT_MISSED_WIN,
    MOMENT_MISTAKE,
    MOMENT_PUNISHED_MISTAKE,
    MOMENT_STRATEGIC,
    REVIEW_MODE_STRATEGIC,
)

if TYPE_CHECKING:  # pragma: no cover — type-only import
    from llm.rag.llm.base import BaseLLM

logger = logging.getLogger(__name__)

#: Per-text length ceilings (chars).  A moment card is a 2–4 sentence
#: glance; the verdict is 3–5 sentences.  Hard-rejected past the cap so
#: a rambling model lands on the retry / fallback path instead of
#: overflowing the card.
MOMENT_MAX_CHARS = 700
VERDICT_MAX_CHARS = 900

#: Wire/persistence keys inside ``llm_json`` — shared with the router
#: serializer and the Android decoder.
KEY_MOMENTS = "moments"
KEY_VERDICT = "verdict"
KEY_OUTCOME = "outcome"

SOURCE_LLM = "llm"
SOURCE_FALLBACK = "fallback"

#: Band → coaching phrase used in prompts and fallback templates.  Kept
#: to the banded vocabulary the deterministic coach already speaks.
_BAND_PHRASE = {
    BAND_LOSING: "clearly losing",
    BAND_WORSE: "somewhat worse",
    BAND_EQUAL: "level",
    BAND_BETTER: "somewhat better",
    BAND_WINNING: "clearly winning",
}

#: Human framing per moment type, injected into the prompt so the model
#: knows what KIND of lesson this card is.
_TYPE_FRAME = {
    MOMENT_BLUNDER: "the player made the game's biggest error here",
    MOMENT_MISSED_WIN: "the player was clearly on top and let the win slip here",
    MOMENT_MISTAKE: "the player made a meaningful error here",
    MOMENT_PUNISHED_MISTAKE: (
        "the opponent had just made a real error, and the player found the "
        "strongest response and kept the reward — a genuinely good moment"
    ),
    MOMENT_STRATEGIC: (
        "no dramatic error — this was one of the game's quiet turning "
        "points, worth understanding rather than regretting"
    ),
}

_RETRY_SUFFIX = (
    "\n\nThe previous response was rejected because it contained move "
    "notation, square names, engine mentions, or advisory phrasing.  "
    "Rewrite it without any of those.  Plain coaching prose only."
)


def _tone_for_result(result: str) -> str:
    """Deterministic tone block per the spec's result calibration."""
    if result == "loss":
        return (
            "The player LOST this game.  Be empathetic and honest, never "
            "harsh; include at least one genuine positive observation."
        )
    if result == "win":
        return (
            "The player WON this game.  Be warm but honest about what "
            "could have been cleaner — a win with lessons still in it."
        )
    return "The game was a DRAW.  Stay neutral and focus on the decisions."


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_text(
    text: str,
    *,
    max_chars: int,
    engine_signal: dict | None,
    label: str,
) -> bool:
    """Run the full gate chain on one candidate text.  True = accepted."""
    cleaned = text.strip() if isinstance(text, str) else ""
    if not cleaned:
        return False
    if len(cleaned) > max_chars:
        logger.warning("review writer: %s exceeds %d chars; rejecting", label, max_chars)
        return False
    try:
        validate_mode_2_negative(cleaned)
        validate_output(cleaned, case_type="reflection")
        if engine_signal is not None:
            # ESV-conditioned semantic gates (invented tactics, advantage
            # on equal).  ``check_mate_require=False``: the mate-
            # inevitability REQUIRE is a live-position framing rule; a
            # retrospective card about a played move is not announcing a
            # current forced mate.
            validate_mode_2_semantic(cleaned, engine_signal, check_mate_require=False)
    except (AssertionError, Mode2Violation) as exc:
        # The lexical/contract gates raise AssertionError; the semantic
        # gate raises Mode2Violation — both mean "rejected".
        logger.warning("review writer: %s failed Mode-2 gates — %s", label, exc)
        return False
    try:
        check_output(cleaned)
    except OutputFirewallError as exc:
        logger.warning("review writer: %s failed output firewall — %s", label, exc)
        return False
    return True


def _generate_validated(
    llm: "BaseLLM",
    prompt: str,
    *,
    max_chars: int,
    engine_signal: dict | None,
    label: str,
) -> str | None:
    """One attempt + one hinted retry.  None = fall back."""
    for attempt, attempt_prompt in enumerate((prompt, prompt + _RETRY_SUFFIX)):
        try:
            raw = llm.generate(attempt_prompt)
        except Exception:  # noqa: BLE001 — provider failure is a fallback, never a 500
            logger.exception(
                "review writer: llm.generate raised for %s (attempt %d)", label, attempt
            )
            return None
        if _validate_text(raw, max_chars=max_chars, engine_signal=engine_signal, label=label):
            return raw.strip()
    logger.warning("review writer: both attempts rejected for %s; falling back", label)
    return None


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


def _moment_prompt(
    moment: dict,
    engine_signal: dict,
    *,
    skill_hint: str,
    result: str,
) -> str:
    band_before = _BAND_PHRASE.get(moment.get("band_before", ""), "unclear")
    band_after = _BAND_PHRASE.get(moment.get("band_after", ""), "unclear")
    frame = _TYPE_FRAME.get(moment.get("moment_type", ""), _TYPE_FRAME[MOMENT_STRATEGIC])
    esv_block = json.dumps(engine_signal, sort_keys=True, default=str)
    return (
        "You are an after-game chess coach reviewing ONE key moment with a "
        f"{skill_hint} player.\n\n"
        f"{_tone_for_result(result)}\n\n"
        "ENGINE SIGNAL for the position the player faced (the only chess "
        "truth you may rely on — do not invent tactics it does not list):\n"
        f"{esv_block}\n\n"
        "DETERMINISTIC FACTS about this moment:\n"
        f"- game phase: {moment.get('phase', 'middlegame')}\n"
        f"- what happened: {frame}\n"
        f"- the player's position went from {band_before} to {band_after} "
        "with this move\n\n"
        "Write 2 to 4 sentences explaining this moment the way a coach "
        "would: what idea or habit was at play, why the position shifted, "
        "and what to watch for next time.\n\n"
        "Hard constraints:\n"
        "* Do NOT use move notation of any kind (no algebraic, no UCI) and "
        "do NOT name squares or files.\n"
        "* Do NOT mention the engine, evaluation, centipawns, depth, or "
        "any computational notion.\n"
        "* Do NOT prescribe specific moves ('should have played...').\n"
        "* Do NOT use empty clichés ('develops a piece and controls the "
        "center', 'improves your position').\n"
        "* Plain prose only — no lists, no headers, no JSON."
    )


def _verdict_prompt(
    *,
    moments: list[dict],
    result: str,
    review_mode: str,
    skill_hint: str,
    accuracy_phrase: str,
    weak_phases: list[str],
) -> str:
    moment_lines = "\n".join(
        f"- {m.get('phase', 'middlegame')}: "
        f"{_TYPE_FRAME.get(m.get('moment_type', ''), 'a notable decision')}"
        for m in moments
    )
    softener = (
        "This was a quiet game with no dramatic errors — frame everything "
        "as strategic observations, not mistakes.\n\n"
        if review_mode == REVIEW_MODE_STRATEGIC
        else ""
    )
    weak_line = (
        f"The phase that cost the most was: {', '.join(weak_phases)}.\n"
        if weak_phases
        else ""
    )
    return (
        "You are an after-game chess coach writing the closing verdict of "
        f"a game review for a {skill_hint} player.\n\n"
        f"{_tone_for_result(result)}\n\n"
        f"{softener}"
        "DETERMINISTIC FACTS about the game:\n"
        f"- overall play was {accuracy_phrase}\n"
        f"{weak_line}"
        "- the review highlighted these moments:\n"
        f"{moment_lines}\n\n"
        "Write a 3 to 5 sentence verdict.  It must be a PATTERN-LEVEL "
        "observation — what recurring habit shaped this game — not a "
        "summary of the individual moments.  End with one forward-looking "
        "note about what to watch for in the next game.\n\n"
        "Hard constraints:\n"
        "* Do NOT use move notation and do NOT name squares or files.\n"
        "* Do NOT mention the engine, evaluation, centipawns, depth, or "
        "any computational notion.\n"
        "* Do NOT write generic filler ('you had good moments and made "
        "some mistakes, keep practicing').\n"
        "* Plain prose only."
    )


# ---------------------------------------------------------------------------
# Deterministic fallbacks (trusted by construction)
# ---------------------------------------------------------------------------


def fallback_moment_text(moment: dict) -> str:
    """Coach-voice card text built from moment type + band words only."""
    band_before = _BAND_PHRASE.get(moment.get("band_before", ""), "unclear")
    band_after = _BAND_PHRASE.get(moment.get("band_after", ""), "unclear")
    moment_type = moment.get("moment_type", MOMENT_STRATEGIC)
    if moment_type == MOMENT_MISSED_WIN:
        return (
            f"You had real winning chances here, and they slipped away — the game "
            f"went from {band_before} to {band_after} on this move. When you stand "
            "on top, look first for the choice that keeps your position stable "
            "rather than the sharpest continuation."
        )
    if moment_type == MOMENT_BLUNDER:
        return (
            f"This was the turning point: your position went from {band_before} to "
            f"{band_after} in a single move. Before committing to a move that "
            "changes the game this much, pause and ask what your opponent's "
            "strongest reply would be."
        )
    if moment_type == MOMENT_MISTAKE:
        return (
            f"A meaningful slip — the position drifted from {band_before} to "
            f"{band_after} here. Moments like this usually come from moving on "
            "general principle; take a breath and check the concrete details first."
        )
    if moment_type == MOMENT_PUNISHED_MISTAKE:
        return (
            "Your opponent's last move was a real error, and you found the response "
            f"that made it count — you came out {band_after} and kept it. That is "
            "exactly the habit to build: when the opponent slips, slow down and "
            "collect the full reward."
        )
    return (
        f"No dramatic error here, but this was one of the game's quiet turning "
        f"points — the position moved from {band_before} to {band_after}. "
        "Positions often shift through small decisions like this one; it is "
        "worth replaying this moment to feel where the game changed course."
    )


def fallback_verdict(
    *,
    result: str,
    review_mode: str,
    weak_phases: list[str],
) -> str:
    """Coach-voice verdict built from game stats only."""
    if result == "loss":
        opener = "A tough result, but the lessons in it are concrete."
    elif result == "win":
        opener = "A win — and still a game with something to tighten up."
    else:
        opener = "A balanced game with decisions worth revisiting."
    if review_mode == REVIEW_MODE_STRATEGIC:
        middle = (
            " Nothing here was a dramatic error; the game turned on quiet "
            "choices, which is exactly where longer-term habits show up."
        )
    elif weak_phases:
        middle = f" Most of the trouble came in the {weak_phases[0]}."
    else:
        middle = " The key moments above show where the game actually turned."
    closer = (
        " Next game, give yourself an extra pause when the position feels "
        "sharp — that is where this one changed course."
    )
    return opener + middle + closer


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def write_review_texts(
    *,
    llm: "BaseLLM",
    moments: list[dict],
    engine_signals: dict[int, dict],
    result: str,
    review_mode: str,
    skill_hint: str,
    accuracy_phrase: str,
    weak_phases: list[str],
) -> dict:
    """Produce the ``llm_json`` payload for one review.  Never raises.

    Parameters
    ----------
    moments:
        The wire-safe moment payload dicts (``CriticalMoment.to_payload``),
        in card order.
    engine_signals:
        ``ply → ESV`` extracted by the service from each moment's
        ``fen_before`` (with real engine output where available).  A
        missing ply falls back to notation-gates-only validation for
        that moment.
    accuracy_phrase:
        Deterministic qualitative phrase for overall play (produced by
        the service from the accuracy value — never a number).

    Returns a dict shaped::

        {"moments": [{"ply": int, "text": str, "source": "llm"|"fallback"}],
         "verdict": {"text": str, "source": ...},
         "outcome": "full" | "fallback"}
    """
    moment_entries: list[dict] = []
    any_fallback = False

    for moment in moments:
        ply = int(moment.get("ply", 0))
        esv = engine_signals.get(ply)
        prompt = _moment_prompt(
            moment,
            esv if esv is not None else {},
            skill_hint=skill_hint,
            result=result,
        )
        text = _generate_validated(
            llm,
            prompt,
            max_chars=MOMENT_MAX_CHARS,
            engine_signal=esv,
            label=f"moment ply={ply}",
        )
        if text is None:
            any_fallback = True
            moment_entries.append(
                {"ply": ply, "text": fallback_moment_text(moment), "source": SOURCE_FALLBACK}
            )
        else:
            moment_entries.append({"ply": ply, "text": text, "source": SOURCE_LLM})

    verdict_text = _generate_validated(
        llm,
        _verdict_prompt(
            moments=moments,
            result=result,
            review_mode=review_mode,
            skill_hint=skill_hint,
            accuracy_phrase=accuracy_phrase,
            weak_phases=weak_phases,
        ),
        max_chars=VERDICT_MAX_CHARS,
        engine_signal=None,
        label="verdict",
    )
    if verdict_text is None:
        any_fallback = True
        verdict_entry = {
            "text": fallback_verdict(
                result=result, review_mode=review_mode, weak_phases=weak_phases
            ),
            "source": SOURCE_FALLBACK,
        }
    else:
        verdict_entry = {"text": verdict_text, "source": SOURCE_LLM}

    return {
        KEY_MOMENTS: moment_entries,
        KEY_VERDICT: verdict_entry,
        KEY_OUTCOME: LLM_OUTCOME_FALLBACK if any_fallback else LLM_OUTCOME_FULL,
    }
