"""
Regression guard: natural coaching language must NOT be over-rejected by
the Mode-2 validator stack — while the hard safety guards still reject.

Why this file exists
--------------------
For weeks the Mode-2 coach returned the templated deterministic fallback
on almost every question, because the validator stack (run on UNTRUSTED
LLM output by ``validate_mode_2_or_raise``) vetoed ordinary coaching
words.  The fixes (2026-05..06-07): retire over-broad patterns
(``should``/``plan``/``if it``/``consider``/``line``/``likely``/
``probably``/``might``/``wants to``/``better``/``attack``/``threat``/
``pressure``/``initiative``) and switch the semantic gate from substring
to WORD-BOUNDARY matching (so ``pin`` stops firing inside "develoPINg").
Verified against the real model: 10/10 questions then passed first try.

This test pins that win DETERMINISTICALLY (no API key, runs every push):

  * COACHING_CORPUS — realistic, prompt-compliant coach answers (no
    notation, no concrete-move advice) that deliberately use the
    previously-vetoed vocabulary.  Each MUST pass the full stack on the
    HARSHEST signal (equal band + no tactical flags), where the semantic
    gate is fully armed.  If someone re-adds an over-broad pattern, the
    matching sample starts failing here.

  * HARD_GUARDS — the architecture's real invariants.  Each MUST still
    raise.  If someone guts a guard, the matching case stops raising.

  * Word-boundary regression — "developing"/"stepping"/"keeping" must
    not trip the ``pin`` motif.

See [[feedback-mode-2-validator-overrejection]] for the full history.
"""

from __future__ import annotations

import pytest

from llm.rag.safety.output_firewall import OutputFirewallError
from llm.rag.validators.mode_2_semantic import Mode2Violation
from llm.seca.coach._mode_2_validators import validate_mode_2_or_raise


# Harshest realistic signal: equal band + NO tactical flags arms BOTH the
# equal-advantage check and the invented-tactic check.  An answer that
# passes here passes on every easier signal.
_EQUAL_QUIET = {
    "evaluation": {"band": "equal", "type": "cp", "side": "white"},
    "tactical_flags": [],
    "phase": "opening",
}

# A position the engine reports as sharp: tactics + advantage vocabulary
# is legitimate here, so these gates are NOT armed.
_ADV_TACTICAL = {
    "evaluation": {"band": "small_advantage", "type": "cp", "side": "white"},
    "tactical_flags": ["initiative", "fork"],
    "phase": "middlegame",
}


# ---------------------------------------------------------------------------
# COACHING_CORPUS — realistic answers that MUST pass (equal + no flags).
# Each is packed with vocabulary that USED to be over-rejected: attack,
# threats, pressure, developing/stepping/keeping (the "pin" substring
# trap), your plan, consider, might, likely, line, better (comparative).
# None contains notation, a concrete move, a mate claim, an advantage
# claim ("winning"/"slight advantage"), or a named motif (fork/pin/
# sacrifice) — those are correctly guarded below.
# ---------------------------------------------------------------------------
COACHING_CORPUS = [
    # King safety
    "Your king is slightly exposed because the pawns shielding it have "
    "advanced, leaving some soft squares nearby. If you keep loosening that "
    "cover, your opponent can build an attack and aim threats at those "
    "squares. Your priority is to finish developing and tuck your king into "
    "safety before the centre opens.",
    # Plan / strategy (uses 'plan', 'might', 'consider', 'pressure')
    "A sound plan here is to improve your least-active piece and contest the "
    "central squares. You might consider rerouting a knight toward a stronger "
    "post, and once your forces are coordinated you can start to build "
    "pressure on the side where you are more active. Keep your structure "
    "healthy and your pieces working together.",
    # Weaknesses (uses 'likely', 'pressure', 'target')
    "The main thing to watch is the slightly loose pawn structure on the "
    "kingside, which can become a long-term target. Your opponent will likely "
    "try to apply pressure there, so keep an eye on those squares and avoid "
    "creating fresh weaknesses. Trading off your more passive pieces will help "
    "keep things solid.",
    # Tactics talk on a QUIET position (uses 'tactics', 'line', 'threats' but
    # invents no concrete motif) — the case that most needs to pass.
    "In a balanced position like this, tactics usually appear only when "
    "someone slips, such as an undefended piece or an overloaded defender. "
    "There is nothing forcing right now, but stay alert: if a piece is left "
    "loose or a line opens toward your king, you may be able to create "
    "threats. Until then, keep developing and improving your pieces.",
    # Attack question (uses 'attack', 'pressure')
    "Attacking well is not about throwing pieces forward — it is about "
    "building up first. Develop everything, secure your king, then look for "
    "the side where you have more space and pressure. Once your pieces are "
    "aimed at your opponent's king and the position opens, your attack will "
    "have real force behind it.",
    # Endgame (avoids 'calculation' which is still forbidden)
    "In an endgame like this your king becomes a fighting piece, so bring it "
    "toward the centre and use it actively. You will want to create a passed "
    "pawn and shepherd it forward while keeping your opponent's king at bay. "
    "Careful technique and good timing matter most here, so take your time "
    "with each move.",
    # General chess concept ("what does controlling the centre mean?")
    "Controlling the centre means your pawns and pieces influence the key "
    "central squares, which gives your forces more mobility and makes it "
    "harder for your opponent to find good squares. Early on, the simplest "
    "way to do it is to develop toward the centre and avoid moving the same "
    "piece twice. Strong central control usually leads to easier, more active "
    "play later.",
    # Improvement ("how do I get better?") — uses 'should', 'attack', 'better'
    "To improve from here, you should prioritise healthy development and "
    "central control rather than rushing an attack. Complete your "
    "development, get your king to safety, and only then look for active "
    "plans. A patient, better-coordinated setup beats a premature assault "
    "almost every time.",
]


@pytest.mark.parametrize("answer", COACHING_CORPUS, ids=range(len(COACHING_CORPUS)))
def test_natural_coaching_passes_on_equal_quiet(answer: str) -> None:
    """Each realistic coaching answer passes the FULL boundary stack on the
    harshest signal (equal band + no tactical flags).  A failure here means
    an over-broad pattern was (re-)introduced — grep the exception's token."""
    validate_mode_2_or_raise(answer, _EQUAL_QUIET)


@pytest.mark.parametrize("answer", COACHING_CORPUS, ids=range(len(COACHING_CORPUS)))
def test_natural_coaching_passes_on_sharp_position(answer: str) -> None:
    """Same corpus also passes when the engine reports advantage + tactics
    (those gates not armed) — sanity that nothing else over-rejects."""
    validate_mode_2_or_raise(answer, _ADV_TACTICAL)


# ---------------------------------------------------------------------------
# Word-boundary regression — the "pin"-in-"developing" bug (2026-06-07).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "answer",
    [
        "We are developing and stepping carefully while keeping the pieces coordinated.",
        "Keep developing, keep improving, and keep your king safe.",
        "Stepping back to regroup is fine when you are still developing.",
    ],
)
def test_pin_does_not_match_ping_words(answer: str) -> None:
    """``pin`` (a tactical motif) must NOT fire inside develoPINg / stepPINg /
    keePINg even with no tactical flags.  Pins the substring->word-boundary
    fix in mode_2_semantic."""
    validate_mode_2_or_raise(answer, _EQUAL_QUIET)


# ---------------------------------------------------------------------------
# HARD_GUARDS — the architecture's real invariants MUST still reject.
# (text, engine_signal, expected exception type)
# ---------------------------------------------------------------------------
HARD_GUARDS = [
    # Move notation — piece, pawn, castling
    ("You should play your pawn to e4 and then develop.", _EQUAL_QUIET, AssertionError),
    ("Bring the knight to f3 to develop with tempo.", _EQUAL_QUIET, AssertionError),
    ("Castle O-O to get your king to safety quickly.", _EQUAL_QUIET, AssertionError),
    # Advisory move-section prose
    ("Recommended move: trade the queens and simplify.", _EQUAL_QUIET, AssertionError),
    ("White can simply push the passed pawn to promote.", _EQUAL_QUIET, AssertionError),
    # Engine / analysis leakage
    ("The calculation shows a clear edge after deep analysis.", _EQUAL_QUIET, AssertionError),
    ("The engine prefers this quiet setup for the long game.", _EQUAL_QUIET, Mode2Violation),
    # Mate misframing without engine support
    ("This is checkmate in a few moves no matter what.", _EQUAL_QUIET, AssertionError),
    # Engine-voice speculation (the speculative forms deliberately KEPT)
    ("I think your position is the more comfortable one.", _EQUAL_QUIET, AssertionError),
    ("Your opponent plans to win the exchange next.", _EQUAL_QUIET, AssertionError),
    # Contradicting the engine: advantage claim on an EQUAL board
    ("Despite appearances you are simply winning here.", _EQUAL_QUIET, Mode2Violation),
    ("You hold a slight advantage in this position.", _EQUAL_QUIET, Mode2Violation),
    # Inventing a concrete motif with NO tactical flag
    ("There is a fork available that wins a whole piece.", _EQUAL_QUIET, Mode2Violation),
    ("Set up a pin against the king to win material.", _EQUAL_QUIET, Mode2Violation),
]


@pytest.mark.parametrize(
    "text, signal, exc",
    HARD_GUARDS,
    ids=[t[:38] for t, _s, _e in HARD_GUARDS],
)
def test_hard_guards_still_reject(text: str, signal: dict, exc: type) -> None:
    """Each architecture invariant still raises.  A failure here means a
    safety guard was weakened — restore it (do NOT relax the test)."""
    with pytest.raises((exc, OutputFirewallError)):
        validate_mode_2_or_raise(text, signal)
