"""LLM-generated coach verdict for the per-mistake study plan.

Phase 2 of the LLM coaching agent: a single-shot LLM call that
produces a (theme, verdict) tuple for one mistake.  Theme is one of
a fixed vocabulary (so downstream library lookup in phase 3 can key
off it); verdict is a ≤ 60-word retrospective the Android Home-screen
``TodaysDrillCard`` (phase 4) renders above the puzzle board.

Trust posture
-------------
The LLM is untrusted per ``docs/ARCHITECTURE.md`` § LLM Layer.  Every
verdict we accept runs the same gates the ``/explain`` and ``/chat``
pipelines use:

* ``validate_mode_2_negative`` — rejects engine mentions, algebraic
  move references, advisory phrases, mate claims (the Mode-2 lexical
  filter).  This is why the verdict's text does NOT name the player's
  played move: the move identity lives in the deterministic
  ``MistakeStudyPuzzle.expected_move_uci`` field that the
  ``MistakeReplayBottomSheet`` renders, NOT in the LLM verdict.
* ``output_firewall.check_output`` — PII / prompt-leak / bypass /
  harmful content scan.  Same firewall ``/chat`` and ``/explain``
  use.

The semantic and structure validators are intentionally NOT run here:
the verdict is a short free-form reflection on the player's own
position, not a Mode-2 explanation grounded in a specific ESV claim
band, so the equality-band / mate-inevitability ESV-conditioned
checks don't apply.  If a future phase wires the verdict into the
``/chat`` retry loop the full Mode-2 chain applies there;
phase 2's surface is narrower.

Fallback on any unrecoverable error
-----------------------------------
The verdict is best-effort.  If anything along the chain fails — LLM
provider unreachable, JSON parse fails, the response trips a
validator after the single retry — the agent falls back to
``("generic", "")``.  That matches the phase-1 stub shape, so the
caller (``agent.generate_plan``) doesn't need to special-case the
failure path — it just commits whichever (theme, verdict) the
verdict writer returned.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from llm.rag.contracts.validate_output import validate_output
from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.rag.safety.output_firewall import OutputFirewallError, check_output
from llm.rag.validators.mode_2_negative import validate_mode_2_negative

if TYPE_CHECKING:  # pragma: no cover — type-only import, never executed at runtime
    from llm.rag.llm.base import BaseLLM

logger = logging.getLogger(__name__)


# The fixed theme vocabulary the LLM may choose from.  Anything outside
# this set collapses to ``"generic"`` so the downstream library lookup
# in phase 3 can key off a small, known set.  Names align with how
# coaches typically talk about mistake categories — beginner-friendly,
# not internal jargon.
THEME_VOCABULARY: frozenset[str] = frozenset(
    {
        "king_safety",
        "fork",
        "pin",
        "back_rank",
        "hung_piece",
        "queen_safety",
        "tempo",
        "opening_principles",
        "endgame_technique",
        "generic",
    }
)


# Maximum length (chars) of the verdict the validator pipeline will
# accept.  60 words ≈ 360 chars; we cap at 500 to give the LLM some
# slack but reject hard if it tries to write a paragraph.  Matches the
# Android Home-screen card's intended one-glance footprint.
_VERDICT_MAX_CHARS: int = 500


# The default fallback the caller commits when the LLM path can't
# produce a clean verdict (provider down, validator-rejected twice,
# JSON parse failed, etc.).  Same shape as the phase-1 stub the agent
# already writes on a fresh plan row.
_FALLBACK: tuple[str, str] = ("generic", "")


def skill_hint_for_rating(rating: float) -> str:
    """Map a player's current rating to a coarse skill band for tone
    shaping in the LLM prompt.

    Bands:

    * ``< 1200``  → ``"beginner"``   (short sentences, no jargon)
    * ``1200-1800`` → ``"intermediate"`` (one technical term OK)
    * ``> 1800``  → ``"advanced"``   (positional vocabulary OK)

    Phase 3+ may replace this with a ``Player.skill_vector_json``-derived
    feature vector; phase 2 ships the simplest rating-only mapping
    because that's the field every Player row already carries.
    """
    if rating < 1200:
        return "beginner"
    if rating < 1800:
        return "intermediate"
    return "advanced"


def generate_verdict(
    *,
    mistake_fen: str,
    played_uci: str,
    player_skill_hint: str,
    llm: "BaseLLM",
) -> tuple[str, str]:
    """Produce a ``(theme, verdict)`` tuple for one mistake.

    Parameters
    ----------
    mistake_fen:
        Position the player faced before the bad move.  Same string
        the detector wrote to ``FirstMistake.fen_before`` and the
        agent wrote to ``MistakeStudyPuzzle.fen`` for day 0.
    played_uci:
        The move the player actually played (UCI).  Passed to the
        LLM as a hint about what the player chose to do, NOT to be
        echoed in the verdict.  The Mode-2 validator will reject any
        algebraic / UCI move reference in the LLM output.
    player_skill_hint:
        ``"beginner"`` / ``"intermediate"`` / ``"advanced"`` — from
        ``skill_hint_for_rating``.  Shapes the LLM's vocabulary; does
        NOT change the validator gates.
    llm:
        The ``BaseLLM`` instance to call.  Production uses
        ``DeepseekLLM``; tests inject a ``FakeLLM`` with rigged output.

    Returns
    -------
    ``(theme, verdict)``:

    * ``theme`` ∈ ``THEME_VOCABULARY``.  Out-of-vocabulary LLM output
      collapses to ``"generic"``.
    * ``verdict`` — short retrospective string.  Empty string when the
      LLM path failed unrecoverably; caller should treat empty as
      "render the card without the verdict line".

    The function NEVER raises.  Errors land in logs; the caller
    receives the fallback tuple.
    """
    # ``extract_engine_signal`` is lenient on FEN parse failures: it
    # returns a default ESV (band="equal", phase="middlegame", empty
    # tactical_flags) rather than raising.  The detector already
    # validated the FEN at /game/finish time anyway, so even malformed
    # input lands the agent on a defensible-default ESV that the LLM
    # can still write a generic verdict against.  No try/except needed.
    engine_signal = extract_engine_signal({}, fen=mistake_fen)

    base_prompt = _build_prompt(
        engine_signal=engine_signal,
        played_uci=played_uci,
        player_skill_hint=player_skill_hint,
    )

    # First attempt.
    try:
        raw = llm.generate(base_prompt)
    except Exception:  # noqa: BLE001
        # LLM provider unreachable (network, billing, model gone) or
        # internal failure.  The user-facing /game/finish response has
        # already shipped — log + fall back.
        logger.exception("verdict: initial llm.generate raised; falling back")
        return _FALLBACK

    parsed = _parse_and_validate(raw)
    if parsed is not None:
        return parsed

    # One retry with an explicit "remove forbidden content" instruction.
    # Same shape as ``run_mode_2``'s rewrite path, but inlined because
    # we only need the negative validator here (not the full Mode-2
    # chain).
    retry_prompt = (
        base_prompt
        + "\n\nThe previous response was rejected because it contained "
        + "engine mentions, specific moves, or advisory phrasing.  "
        + "Rewrite the verdict to remove all of those.  Keep the JSON "
        + "envelope and the same theme."
    )
    try:
        raw_retry = llm.generate(retry_prompt)
    except Exception:  # noqa: BLE001
        logger.exception("verdict: retry llm.generate raised; falling back")
        return _FALLBACK

    parsed_retry = _parse_and_validate(raw_retry)
    if parsed_retry is not None:
        return parsed_retry

    # Both attempts failed validation.  Log + fall back to the empty
    # verdict (the plan stays usable; the Home card will just hide the
    # coach-note line).
    logger.warning("verdict: both attempts failed validation; falling back to empty verdict")
    return _FALLBACK


def _build_prompt(
    *,
    engine_signal: dict,
    played_uci: str,
    player_skill_hint: str,
) -> str:
    """Compose the structured-output prompt fed to ``BaseLLM.generate``.

    Kept inline rather than borrowing the full Mode-2 prompt renderer
    because the verdict surface is much narrower: no conversation
    history, no RAG retrieval, no engine-signal verbatim block.  The
    LLM just needs the ESV summary + the theme vocabulary + the
    skill-band hint, in a tight JSON-emitting frame.
    """
    # The ESV is a small dict — embed verbatim so the LLM sees the
    # phase / tactical_flags / evaluation band.  We do NOT pass the
    # raw stockfish_json (we don't have a Stockfish call result here
    # — phase 2 is engine-call-free for the verdict).  The
    # extract_engine_signal({}, fen=...) path already filled in FEN-
    # derived material + phase.
    esv_block = json.dumps(engine_signal, sort_keys=True, default=str)

    themes = sorted(THEME_VOCABULARY)
    themes_block = "\n".join(f"  - {t}" for t in themes)

    return (
        "You are an after-game chess coach explaining ONE mistake to a "
        f"{player_skill_hint} player.\n\n"
        "ENGINE SIGNAL (the only chess truth you may rely on):\n"
        f"{esv_block}\n\n"
        f"The player chose UCI move: {played_uci}\n\n"
        "Your task — produce a strict JSON object with two fields:\n\n"
        "  theme: ONE of the following tags (choose the one that best\n"
        "         names the mistake category; if none fit, use 'generic'):\n"
        f"{themes_block}\n\n"
        "  verdict: a CONCEPTUAL retrospective of <= 60 words explaining\n"
        "           WHAT WENT WRONG.  Hard constraints:\n"
        "           * Do NOT mention specific moves (no algebraic notation,\n"
        "             no UCI, no 'Kf2', no 'g3-h4', no 'the knight on c6').\n"
        "           * Do NOT mention the engine, evaluation, centipawns,\n"
        "             depth, or any computational notion.\n"
        "           * Do NOT prescribe a move ('should have', 'could have',\n"
        "             'try playing X').\n"
        "           * Focus on the THEME — what idea or principle was\n"
        "             violated and why it cost the player the position.\n\n"
        "Respond with the JSON object ONLY — no prose before or after, no\n"
        "code fences, no markdown.\n\n"
        'Example shape (illustrative only): {"theme": "king_safety", '
        '"verdict": "Bringing the king toward the centre with pieces '
        "still on the board exposes it to a quick attack; the resulting "
        'tempo loss let your opponent build pressure faster than you could defend."}'
    )


def _parse_and_validate(  # pylint: disable=too-many-return-statements
    raw: str,
) -> tuple[str, str] | None:
    """Parse one LLM response and return ``(theme, verdict)`` on success.

    Returns ``None`` on any failure (JSON parse error, missing field,
    out-of-bound length, validator rejection).  The caller decides
    whether to retry or fall back based on which attempt this was.

    The ``too-many-return-statements`` pylint warning is intentionally
    suppressed: this function is structured as a flat fail-fast chain
    so each rejection class can log its own granular reason without
    bloating the happy path into a deep nested ``if`` ladder.  Every
    branch logs once and returns ``None``; the happy path is the only
    non-``None`` return.
    """
    cleaned = raw.strip() if isinstance(raw, str) else ""
    if not cleaned:
        return None

    # Strip a leading / trailing code fence the model may have added
    # despite the prompt instruction.  ``json.loads`` is fussier than
    # we need; a small pre-pass handles the common stray-fence case.
    if cleaned.startswith("```"):
        # Drop the first line (e.g. ``` or ```json) and any trailing fence.
        cleaned = "\n".join(cleaned.splitlines()[1:])
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()

    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("verdict: LLM output failed JSON parse")
        return None

    if not isinstance(obj, dict):
        logger.warning("verdict: LLM JSON parsed to non-dict (got %s)", type(obj).__name__)
        return None

    theme_raw = obj.get("theme")
    verdict_raw = obj.get("verdict")
    if not isinstance(theme_raw, str) or not isinstance(verdict_raw, str):
        logger.warning("verdict: missing/wrong-type theme or verdict in LLM JSON")
        return None

    theme = theme_raw.strip()
    verdict = verdict_raw.strip()

    if not verdict:
        return None
    if len(verdict) > _VERDICT_MAX_CHARS:
        logger.warning(
            "verdict: verdict exceeds %d chars (got %d); rejecting",
            _VERDICT_MAX_CHARS,
            len(verdict),
        )
        return None

    # Collapse out-of-vocab themes to "generic" — we don't reject the
    # whole response over a theme typo because the verdict text itself
    # may still be useful, and the downstream phase-3 library lookup
    # treats "generic" as the catch-all bucket anyway.
    if theme not in THEME_VOCABULARY:
        logger.info(
            "verdict: out-of-vocabulary theme %r; collapsing to 'generic'",
            theme,
        )
        theme = "generic"

    # Mode-2 lexical filter — same gate ``/explain`` and ``/chat`` run.
    # AssertionError is the documented failure type (mode_2_negative
    # uses ``raise AssertionError`` instead of a custom exception so
    # the rest of the validator chain can catch it uniformly).  This
    # catches the narrow regex set: speculative phrases, algebraic
    # move references, and the four engine-lexical regexes
    # (calculate / calculation / variation / line as word-bounded
    # matches).
    try:
        validate_mode_2_negative(verdict)
    except AssertionError as exc:
        logger.warning("verdict: failed validate_mode_2_negative — %s", exc)
        return None

    # Broader Mode-2 contract filter — catches engine-mention vocabulary
    # ("stockfish", "engine", "best move", "depth", "calculate",
    # "variation") via simple substring containment.  Distinct from
    # ``validate_mode_2_negative``'s anchored regex check; the two are
    # designed to complement each other and the standard /chat path
    # runs both.  ``case_type="reflection"`` skips the case-type-
    # conditioned require-phrase loops (forced_mate, missing_data) —
    # the verdict is a free-form retrospective, not a response in
    # either of those modes.
    try:
        validate_output(verdict, case_type="reflection")
    except AssertionError as exc:
        logger.warning("verdict: failed validate_output — %s", exc)
        return None

    # Output firewall — PII / prompt-leak / harmful content scan.
    try:
        check_output(verdict)
    except OutputFirewallError as exc:
        logger.warning("verdict: failed output firewall — category=%s", exc)
        return None

    return (theme, verdict)
