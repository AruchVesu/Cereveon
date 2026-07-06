"""Real-LLM stress test — hallucination / accuracy / level-style (Category G).

Probes the two production coaching pipelines (Mode-1 ``generate_live_reply``
and Mode-2 ``generate_chat_reply``) against the live DeepSeek API with a
matrix of engine-ground-truthed positions, then checks every FINAL,
user-visible reply with deterministic claim-checkers:

Hallucination axis (hard, zero tolerance)
    * phantom pieces  — the reply names a piece type that exists for
      NEITHER side (e.g. "your queen" in a king-and-pawn endgame)
    * phantom checks  — the reply claims a king is in check on a board
      where nobody is in check

Accuracy axis (hard, zero tolerance)
    * advantage direction — "you are winning / you have the advantage"
      when the engine signal says the OPPONENT is ahead (and vice versa,
      and any directional advantage claim on an equal band).  This is the
      2026-05-10 production bug class: no in-pipeline validator enforces
      direction, only the prompt pre-framing does.
    * material claims — "you are up / down material" contradicting the
      actual board material
    * move-quality contradiction (Mode-1) — praising a move the engine
      graded a mistake/blunder, or condemning a move graded good/best

Style axis (rate-based, >= STYLE_PASS_RATE)
    * Mode-1 replies obey the 1-2 sentence contract
    * beginner / simple replies avoid an advanced-jargon lexicon
    * terse coach voice stays short; chat replies stay within the
      2-4-short-paragraphs length budget

The hallucination and accuracy checkers run on the final pipeline output —
the user-facing contract — whether it came from the LLM or the deterministic
fallback (the fallback is hallucination-free by construction, so it must
trivially pass).  The style axis is measured on REAL LLM replies only: it is
a prompt contract on the model, and the deterministic templates are pinned
by their own per-push tests.  Fallback rate and per-probe latency are
reported as telemetry; a collapsed real-answer rate fails the run (validator
over-rejection stress signal, same rationale as
``test_llm_regression.test_llm_chat_path_not_overrejected``).

The hard axes mirror the ARCHITECTURE.md invariants ("never hallucinate
engine facts", "never contradict evaluations").  Style is a prompt contract
on a stochastic model, so it is thresholded like ``_MIN_REAL_RATE`` rather
than zero-tolerance.  Per project rule 5, none of these thresholds may be
weakened to make a failing run pass.

Local-only by default — gated on ``RUN_DEEPSEEK_TESTS=1`` plus a real
``COACH_DEEPSEEK_API_KEY`` in env, exactly like the Category C/D siblings.
Runs in CI only via the weekly cron (``llm-regression-cron.yml``).

Command:

    RUN_DEEPSEEK_TESTS=1 COACH_DEEPSEEK_API_KEY=sk-... \
        python -m pytest -q llm/rag/tests/llm/test_llm_stress.py

Budget: ~24 probes, 1 LLM call each plus in-pipeline retries — a few
minutes and a few cents against the DeepSeek API.

Set ``LLM_STRESS_REPORT_PATH=/path/to/report.jsonl`` to persist the
per-probe telemetry (reply, fallback flag, violations, latency) for review.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import time
from dataclasses import dataclass, field

import chess
import pytest

if os.getenv("RUN_DEEPSEEK_TESTS") != "1":
    pytest.skip(
        "DeepSeek stress tests are disabled by default. "
        "Set RUN_DEEPSEEK_TESTS=1 and COACH_DEEPSEEK_API_KEY=sk-... to enable.",
        allow_module_level=True,
    )

if not os.getenv("COACH_DEEPSEEK_API_KEY", "").strip():
    pytest.skip(
        "COACH_DEEPSEEK_API_KEY is not set; cannot run real-LLM stress test. "
        "Obtain a key at https://platform.deepseek.com and export it into the "
        "shell environment before re-running.",
        allow_module_level=True,
    )

from llm.seca.coach import chat_pipeline as _cp  # noqa: E402
from llm.seca.coach import live_move_pipeline as _lp  # noqa: E402
from llm.seca.coach.chat_pipeline import ChatTurn, generate_chat_reply  # noqa: E402
from llm.seca.coach.live_move_pipeline import generate_live_reply  # noqa: E402
from llm.seca.engines.stockfish.board_features import (  # noqa: E402
    compute_position_flags,
    compute_tactical_flags,
)

# ---------------------------------------------------------------------------
# Thresholds (fixed before any run — never tune to a failing result)
# ---------------------------------------------------------------------------

#: Style is a prompt contract on a stochastic model; same tolerance shape as
#: test_llm_regression._MIN_REAL_RATE.
STYLE_PASS_RATE = 0.80

#: The stress matrix is deliberately adversarial (mate probes force the
#: REQUIRE gates; lost positions invite forbidden vocabulary), so the
#: real-answer floor sits below the friendly-matrix 0.80 of
#: test_llm_chat_path_not_overrejected.  A collapse toward the ~25%
#: over-rejection signature still fails loudly.
MIN_REAL_RATE = 0.70


# ---------------------------------------------------------------------------
# Deterministic claim-checkers
# ---------------------------------------------------------------------------

_SENT_SPLIT_RE = re.compile(r"[.!?]+(?:\s+|$)")


def sentence_count(text: str) -> int:
    """Count sentences by terminal punctuation runs.  The coach prompts
    mandate plain prose (no headings, no abbreviated lists), so a
    punctuation split is a faithful counter for this text shape."""
    return len([s for s in _SENT_SPLIT_RE.split(text.strip()) if s.strip()])


def word_count(text: str) -> int:
    return len(text.split())


# -- phantom pieces ---------------------------------------------------------

_PIECE_WORDS: dict[str, int] = {
    "queen": chess.QUEEN,
    "rook": chess.ROOK,
    "bishop": chess.BISHOP,
    "knight": chess.KNIGHT,
}

# Legitimate ways to mention an absent piece type: promotion talk, noting the
# trade/absence itself, or "down to kings and pawns" summaries.  Matched in a
# +/-60-char window around the mention.
_ABSENT_PIECE_EXCUSE = re.compile(
    r"promot|queening|new queen|becomes? a queen|trade|exchang|swap|"
    r"off the board|com(?:e|es|ing) off|came off|no longer|no |not have|don't|do not|cannot|can't|"
    r"without|gone|missing|absence|down to|only (?:the )?kings?|just (?:the )?kings?",
    re.IGNORECASE,
)


def phantom_piece_claims(reply: str, board: chess.Board) -> list[str]:
    """Determiner/possessive mentions of a piece type that exists for
    NEITHER side, excluding promotion/trade/absence phrasing.  A coach
    telling the player about "your queen" on a queenless board is a board
    hallucination.  Bare / indefinite forms ("queens come off",
    "turn the pawn into a queen", "a queen is worth nine points") are
    generic chess teaching, not claims about THIS board — both false
    positives from the first two weekly CI runs were of that shape."""
    violations: list[str] = []
    lower = reply.lower()
    for word, piece_type in _PIECE_WORDS.items():
        if board.pieces(piece_type, chess.WHITE) or board.pieces(piece_type, chess.BLACK):
            continue
        for match in re.finditer(
            rf"\b(?:your|their|his|her|its|the|both|each|this|that)\s+{word}s?\b", lower
        ):
            window = lower[max(0, match.start() - 60) : match.end() + 60]
            if _ABSENT_PIECE_EXCUSE.search(window):
                continue
            snippet = lower[max(0, match.start() - 40) : match.end() + 40]
            violations.append(f"absent piece type '{word}' mentioned: ...{snippet}...")
    return violations


# -- phantom checks ---------------------------------------------------------

_CHECK_CLAIM = re.compile(
    r"\b(?:in check|under check|gives check|delivering check|"
    r"check(?:ing|s) your king|your king is checked)\b",
    re.IGNORECASE,
)

# Conditional / instructive uses ("if you end up in check", "avoid getting
# your king in check") are teaching, not board claims.
_CHECK_EXCUSE = re.compile(
    r"\b(?:if|when|could|would|might|avoid|prevent|risk|careful|watch|"
    r"not in check|no check|out of check|never)\b",
    re.IGNORECASE,
)


def phantom_check_claims(reply: str, board: chess.Board) -> list[str]:
    """Present-tense check claims on a board where nobody is in check.
    Boards that ARE in check accept any check language."""
    if board.is_check():
        return []
    lower = reply.lower()
    violations: list[str] = []
    for match in _CHECK_CLAIM.finditer(lower):
        window = lower[max(0, match.start() - 60) : match.end() + 20]
        if _CHECK_EXCUSE.search(window):
            continue
        snippet = lower[max(0, match.start() - 40) : match.end() + 40]
        violations.append(f"check claimed on a check-free board: ...{snippet}...")
    return violations


# -- advantage direction ----------------------------------------------------

_QUALIFIER = r"(?:a |an |the )?(?:small |slight |clear |decisive |big |significant |winning |real )?"

# A comparative followed by a sub-dimension qualifier ("better developed",
# "ahead in development", "an edge in space") is legitimate coaching about
# one aspect of the position, not an overall who-stands-better claim — the
# validator history retired bare "better" for exactly this ambiguity.  The
# lookaheads exclude those qualified forms; unqualified overall claims
# ("you are better.", "you have the advantage here") remain caught, and
# material-qualified claims belong to the material checker below.
_SUBDIM_GUARD = r"(?!\s+(?:in|on|at|with)\b|\s+(?:developed|coordinated|placed|organi[sz]ed|prepared|mobili[sz]ed|activated))"

_PLAYER_ADV_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\byou(?:'re| are) (?:clearly |much |slightly |completely |now )?winning\b",
        rf"\byou(?:'re| are) (?:clearly |much |slightly |completely |now )?(?:better|ahead)\b{_SUBDIM_GUARD}",
        rf"\byou (?:have|hold|keep|enjoy) {_QUALIFIER}(?:advantage|edge|upper hand|lead)\b{_SUBDIM_GUARD}",
        r"\bin your favou?r\b",
        rf"\byou stand better\b{_SUBDIM_GUARD}",
        r"\byour position is (?:winning|won|clearly better|much better|dominant)\b",
    )
)

_OPPONENT_ADV_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\byour opponent (?:is|stands) (?:clearly |much |slightly |completely |now )?winning\b",
        rf"\byour opponent (?:is|stands) (?:clearly |much |slightly |completely |now )?(?:better|ahead)\b{_SUBDIM_GUARD}",
        rf"\byour opponent (?:has|holds|keeps|enjoys) {_QUALIFIER}(?:advantage|edge|upper hand|lead)\b{_SUBDIM_GUARD}",
        r"\byou(?:'re| are) (?:clearly |much |slightly |completely |now )?(?:losing|lost)\b",
        rf"\byou(?:'re| are) (?:clearly |much |slightly |completely |now )?(?:worse|behind)\b{_SUBDIM_GUARD}",
        r"\byou(?:'re| are) in (?:serious |deep |real )?trouble\b",
        r"\bin your opponent'?s favou?r\b",
        r"\byou(?:'re| are) (?:about to be|getting|being) (?:check)?mated\b",
    )
)

_NEGATION_GUARD = re.compile(
    r"\b(?:not|no longer|never|isn't|aren't|don't|doesn't|hardly|far from|barely)\b[^.!?]{0,25}$",
    re.IGNORECASE,
)


def _unnegated_hits(reply: str, patterns: tuple[re.Pattern[str], ...]) -> list[str]:
    hits: list[str] = []
    for pattern in patterns:
        for match in pattern.finditer(reply):
            if _NEGATION_GUARD.search(reply[max(0, match.start() - 30) : match.start()]):
                continue
            snippet = reply[max(0, match.start() - 30) : match.end() + 30]
            hits.append(snippet.strip())
    return hits


def advantage_direction_violations(reply: str, esv: dict, player_color: str) -> list[str]:
    """Directional who-stands-better claims that contradict the engine
    signal (band + side), from the player's seat.  Covers both the cp
    bands and mate (band is decisive with a side either way).  No
    in-pipeline validator enforces direction — only the prompt framing —
    which is exactly why this is a stress axis."""
    evaluation = esv.get("evaluation", {})
    band = evaluation.get("band", "equal")
    side = evaluation.get("side", "unknown")

    violations: list[str] = []
    if band == "equal":
        for hit in _unnegated_hits(reply, _PLAYER_ADV_PATTERNS + _OPPONENT_ADV_PATTERNS):
            violations.append(f"directional advantage claim on an equal band: ...{hit}...")
        return violations

    if side not in ("white", "black") or player_color not in ("white", "black"):
        return []

    if side == player_color:
        for hit in _unnegated_hits(reply, _OPPONENT_ADV_PATTERNS):
            violations.append(
                f"opponent-advantage claim but the engine favours the player: ...{hit}..."
            )
    else:
        for hit in _unnegated_hits(reply, _PLAYER_ADV_PATTERNS):
            violations.append(
                f"player-advantage claim but the engine favours the opponent: ...{hit}..."
            )
    return violations


# -- material claims --------------------------------------------------------

_MATERIAL_NOUN = r"(?:material|a pawn|two pawns|a piece|an exchange|the exchange|a knight|a bishop|a rook|a queen)"

_PLAYER_UP_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        rf"\byou(?:'re| are) (?:now )?up {_MATERIAL_NOUN}\b",
        r"\byou(?:'re| are) ahead (?:in|on) material\b",
        r"\byou (?:have|hold) (?:a |the )?material advantage\b",
        r"\byou(?:'ve| have) won material\b",
    )
)

_PLAYER_DOWN_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        rf"\byou(?:'re| are) (?:now )?down {_MATERIAL_NOUN}\b",
        r"\byou(?:'re| are) behind (?:in|on) material\b",
        r"\byour opponent (?:has|holds) (?:a |the )?material advantage\b",
        rf"\byour opponent is (?:now )?up {_MATERIAL_NOUN}\b",
        r"\byou(?:'ve| have) lost material\b",
    )
)

_PIECE_CP = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
}


def _material_cp(board: chess.Board) -> int:
    """White-perspective material balance, same weights as the ESV extractor."""
    return sum(
        _PIECE_CP[pt] * (len(board.pieces(pt, chess.WHITE)) - len(board.pieces(pt, chess.BLACK)))
        for pt in _PIECE_CP
    )


def material_claim_violations(reply: str, board: chess.Board, player_color: str) -> list[str]:
    """Up/down-material claims contradicting the actual board count.
    "Up" claims require the player to actually be ahead in material (and
    vice versa); an even board forbids both directions."""
    if player_color not in ("white", "black"):
        return []
    balance = _material_cp(board)
    player_balance = balance if player_color == "white" else -balance

    violations: list[str] = []
    if player_balance <= 0:
        for hit in _unnegated_hits(reply, _PLAYER_UP_PATTERNS):
            violations.append(f"claims the player is up material (board says otherwise): ...{hit}...")
    if player_balance >= 0:
        for hit in _unnegated_hits(reply, _PLAYER_DOWN_PATTERNS):
            violations.append(f"claims the player is down material (board says otherwise): ...{hit}...")
    return violations


# -- move-quality contradiction (Mode-1) ------------------------------------

_PRAISE_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:great|excellent|perfect|nice|strong|brilliant|superb|fantastic|good) move\b",
        r"\bwell (?:done|played)\b",
    )
)

# Anchored to THE move just played — "the earlier blunder" / "your previous
# mistake" is legitimate context in a lost position, so only demonstrative
# present-move forms count as condemnation.
_CONDEMN_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bthat (?:move )?(?:was|is) a (?:mistake|blunder|serious error)\b",
        r"\bthis (?:move )?(?:was|is) a (?:mistake|blunder|serious error)\b",
        r"\byour move (?:was|is) a (?:mistake|blunder)\b",
        r"\b(?:bad|poor|terrible) move\b",
    )
)

_BAD_QUALITIES = frozenset(("mistake", "blunder"))
_GOOD_QUALITIES = frozenset(("good", "excellent", "best"))


def quality_contradiction_violations(reply: str, move_quality: str) -> list[str]:
    """Praise on an engine-graded mistake/blunder, or condemnation of an
    engine-graded good/excellent/best move.  The engine grade is ground
    truth (project rule 1); the reply may add context but not invert it."""
    violations: list[str] = []
    if move_quality in _BAD_QUALITIES:
        for hit in _unnegated_hits(reply, _PRAISE_PATTERNS):
            violations.append(f"praises a move graded '{move_quality}': ...{hit}...")
    elif move_quality in _GOOD_QUALITIES:
        for hit in _unnegated_hits(reply, _CONDEMN_PATTERNS):
            violations.append(f"condemns a move graded '{move_quality}': ...{hit}...")
    return violations


# -- style ------------------------------------------------------------------

#: Clearly-advanced vocabulary that has no place in a beginner/simple reply
#: (the Mode-1 prompt says "Avoid all chess jargon" for beginners).  Kept to
#: unambiguous terms — common coaching words (development, centre, castling)
#: are NOT jargon for this purpose.
_ADVANCED_JARGON = (
    "zugzwang",
    "prophylax",  # prophylaxis / prophylactic
    "zwischenzug",
    "en prise",
    "luft",
    "triangulation",
    "isolani",
    "minority attack",
    "corresponding squares",
)


def style_violations(
    reply: str,
    *,
    mode: str,
    style: str | None,
    coach_voice: str | None,
) -> list[str]:
    violations: list[str] = []
    sentences = sentence_count(reply)

    if mode == "live" and sentences > 2:
        violations.append(f"Mode-1 reply has {sentences} sentences (contract: 1-2)")

    if mode == "chat":
        if coach_voice == "terse" and sentences > 3:
            violations.append(f"terse-voice reply has {sentences} sentences (asked: 1-2)")
        words = word_count(reply)
        if words > 400:
            violations.append(f"chat reply has {words} words (contract: 2-4 short paragraphs)")

    if style in ("beginner", "simple"):
        lower = reply.lower()
        for term in _ADVANCED_JARGON:
            if term in lower:
                violations.append(f"advanced jargon '{term}' in a beginner-level reply")
    return violations


# ---------------------------------------------------------------------------
# Probe matrix
# ---------------------------------------------------------------------------


def _stockfish_json(
    fen: str,
    *,
    eval_type: str = "cp",
    value: int,
    quality: str | None = None,
    extra_tactical: tuple[str, ...] = (),
) -> dict:
    """Simulated engine analysis for a probe: the controlled evaluation is
    the ground truth under test; the tactical/position flags come from the
    same deterministic board-feature extractor production uses, so the
    ENGINE FACTS block the LLM sees is consistent with the board."""
    board = chess.Board(fen)
    payload: dict = {
        "evaluation": {"type": eval_type, "value": value},
        "tactical_flags": list(extra_tactical) + compute_tactical_flags(board),
        "position_flags": compute_position_flags(board),
    }
    if quality is not None:
        payload["errors"] = {"last_move_quality": quality}
    return payload


# Verified positions (python-chess: legality, material, flags, mate-in-1).
_FEN_EQ_OPEN = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2P5/2N2N2/PP1PPPPP/R1BQKB1R w KQkq - 4 4"
_FEN_WIN_QUEEN = "rnb1k2r/ppppbppp/5n2/8/8/8/PPPPQPPP/RNB1KB1R w KQkq - 0 7"  # +Q for N
_FEN_WIN_QUEEN_M1 = "rnb1k2r/ppppbppp/5n2/8/8/2N5/PPPPQPPP/R1B1KB1R b KQkq - 1 7"  # after Nc3
_FEN_LOSE_QUEEN = "rnbqkb1r/pppp1ppp/8/4p2n/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 0 4"  # -Q
_FEN_LOSE_QUEEN_M1 = "rnbqkb1r/pppp1ppp/8/4p2n/2B1P3/5N2/PPPP1PPP/RNB1K2R b KQkq - 1 4"  # after Nf3
_FEN_KP_END = "8/5k2/8/4K3/4P3/8/8/8 w - - 0 1"  # only kings + one pawn
_FEN_KP_END_M1 = "8/5k2/8/3K4/4P3/8/8/8 b - - 1 1"  # after Kd5
_FEN_MATE_FOR = "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1"  # Ra8# available
_FEN_MATE_AGAINST = "r5k1/5ppp/8/8/8/8/5PPP/6K1 b - - 0 1"  # ...Ra1# available
_FEN_CHECK = "rnbqk1nr/pppp1ppp/8/4P3/1b6/8/PPP1PPPP/RNBQKBNR w KQkq - 1 3"  # White in check
_FEN_AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"


@dataclass(frozen=True)
class Probe:
    probe_id: str
    mode: str  # "live" | "chat"
    fen: str
    stockfish_json: dict
    # chat fields
    question: str = ""
    skill: str | None = None  # beginner | intermediate | advanced
    coach_voice: str | None = None
    # live fields
    uci: str = ""
    style: str | None = None  # simple | intermediate | advanced
    move_quality: str = "unknown"


_CHAT_PROBES: tuple[Probe, ...] = (
    Probe("chat-eq-int", "chat", _FEN_EQ_OPEN, _stockfish_json(_FEN_EQ_OPEN, value=0),
          question="What is the most important thing to focus on right now?", skill="intermediate"),
    Probe("chat-eq-beg", "chat", _FEN_EQ_OPEN, _stockfish_json(_FEN_EQ_OPEN, value=0),
          question="Can you explain how my position looks?", skill="beginner"),
    Probe("chat-eq-adv", "chat", _FEN_EQ_OPEN, _stockfish_json(_FEN_EQ_OPEN, value=0),
          question="Can you explain how my position looks?", skill="advanced"),
    Probe("chat-eq-terse", "chat", _FEN_EQ_OPEN, _stockfish_json(_FEN_EQ_OPEN, value=0),
          question="How is my position?", skill="intermediate", coach_voice="terse"),
    Probe("chat-winq-int", "chat", _FEN_WIN_QUEEN, _stockfish_json(_FEN_WIN_QUEEN, value=550),
          question="Am I doing well in this game so far?", skill="intermediate"),
    Probe("chat-winq-beg", "chat", _FEN_WIN_QUEEN, _stockfish_json(_FEN_WIN_QUEEN, value=550),
          question="Is my position good or bad right now?", skill="beginner"),
    Probe("chat-loseq-int", "chat", _FEN_LOSE_QUEEN, _stockfish_json(_FEN_LOSE_QUEEN, value=-900),
          question="Am I doing well in this game so far?", skill="intermediate"),
    Probe("chat-loseq-adv", "chat", _FEN_LOSE_QUEEN, _stockfish_json(_FEN_LOSE_QUEEN, value=-900),
          question="How do I hold this position together?", skill="advanced"),
    Probe("chat-kp-beg", "chat", _FEN_KP_END, _stockfish_json(_FEN_KP_END, value=250),
          question="How do I make progress here?", skill="beginner"),
    Probe("chat-kp-adv", "chat", _FEN_KP_END, _stockfish_json(_FEN_KP_END, value=250),
          question="What is the main idea in this endgame?", skill="advanced"),
    Probe("chat-matefor-int", "chat", _FEN_MATE_FOR,
          _stockfish_json(_FEN_MATE_FOR, eval_type="mate", value=1, extra_tactical=("forced_mate",)),
          question="How does this game finish from here?", skill="intermediate"),
    Probe("chat-mateagainst-int", "chat", _FEN_MATE_AGAINST,
          _stockfish_json(_FEN_MATE_AGAINST, eval_type="mate", value=-1, extra_tactical=("forced_mate",)),
          question="Is there any hope left for me here?", skill="intermediate"),
    Probe("chat-check-int", "chat", _FEN_CHECK, _stockfish_json(_FEN_CHECK, value=100),
          question="My king feels unsafe right now — how bad is it?", skill="intermediate"),
)

_LIVE_PROBES: tuple[Probe, ...] = tuple(
    Probe(f"live-e4-{style}", "live", _FEN_AFTER_E4,
          _stockfish_json(_FEN_AFTER_E4, value=20, quality="good"),
          uci="e2e4", style=style, move_quality="good")
    for style in ("simple", "intermediate", "advanced")
) + (
    Probe("live-winq-int", "live", _FEN_WIN_QUEEN_M1,
          _stockfish_json(_FEN_WIN_QUEEN_M1, value=550, quality="good"),
          uci="b1c3", style="intermediate", move_quality="good"),
    # A sound move in a lost position — the move-blame probe: the grade is
    # "good", the eval is decisively against the player.
    Probe("live-loseq-simple", "live", _FEN_LOSE_QUEEN_M1,
          _stockfish_json(_FEN_LOSE_QUEEN_M1, value=-900, quality="good"),
          uci="g1f3", style="simple", move_quality="good"),
    Probe("live-loseq-adv", "live", _FEN_LOSE_QUEEN_M1,
          _stockfish_json(_FEN_LOSE_QUEEN_M1, value=-900, quality="good"),
          uci="g1f3", style="advanced", move_quality="good"),
    Probe("live-kp-simple", "live", _FEN_KP_END_M1,
          _stockfish_json(_FEN_KP_END_M1, value=250, quality="best"),
          uci="e5d5", style="simple", move_quality="best"),
    Probe("live-kp-adv", "live", _FEN_KP_END_M1,
          _stockfish_json(_FEN_KP_END_M1, value=250, quality="best"),
          uci="e5d5", style="advanced", move_quality="best"),
) + tuple(
    # The player (White) just walked into ...Ra1# — a genuine blunder.
    Probe(f"live-mate-{style}", "live", _FEN_MATE_AGAINST,
          _stockfish_json(_FEN_MATE_AGAINST, eval_type="mate", value=-1,
                          quality="blunder", extra_tactical=("forced_mate",)),
          uci="h1g1", style=style, move_quality="blunder")
    for style in ("simple", "intermediate", "advanced")
)

_ALL_PROBES: tuple[Probe, ...] = _CHAT_PROBES + _LIVE_PROBES


# ---------------------------------------------------------------------------
# Probe runner
# ---------------------------------------------------------------------------


@dataclass
class ProbeResult:
    probe: Probe
    reply: str
    engine_signal: dict
    fell_back: bool
    latency_s: float
    hallucination: list[str] = field(default_factory=list)
    accuracy: list[str] = field(default_factory=list)
    style: list[str] = field(default_factory=list)


def _run_probe(probe: Probe, fallback_flag: dict) -> ProbeResult:
    fallback_flag["hit"] = False
    start = time.monotonic()
    if probe.mode == "chat":
        result = generate_chat_reply(
            probe.fen,
            [ChatTurn(role="user", content=probe.question)],
            player_profile={"skill_estimate": probe.skill or "intermediate"},
            coach_voice=probe.coach_voice,
            stockfish_json=probe.stockfish_json,
        )
        reply, engine_signal = result.reply, result.engine_signal
        style_label = probe.skill
    else:
        result = generate_live_reply(
            probe.fen,
            probe.uci,
            player_id="stress-probe",
            explanation_style=probe.style,
            stockfish_json=probe.stockfish_json,
        )
        reply, engine_signal = result.hint, result.engine_signal
        style_label = probe.style
    latency = time.monotonic() - start

    board = chess.Board(probe.fen)
    # Chat anchors the human as White (see _build_chat_prompt); Mode-1
    # derives the player as the opposite of the post-move side-to-move.
    # Every probe FEN in this matrix resolves to a White player.
    player_color = "white"

    res = ProbeResult(
        probe=probe,
        reply=reply,
        engine_signal=engine_signal,
        fell_back=fallback_flag["hit"],
        latency_s=latency,
    )
    res.hallucination = phantom_piece_claims(reply, board) + phantom_check_claims(reply, board)
    res.accuracy = advantage_direction_violations(
        reply, engine_signal, player_color
    ) + material_claim_violations(reply, board, player_color)
    if probe.mode == "live":
        res.accuracy += quality_contradiction_violations(reply, probe.move_quality)
    res.style = style_violations(
        reply, mode=probe.mode, style=style_label, coach_voice=probe.coach_voice
    )
    return res


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(pct * (len(ordered) - 1))))
    return ordered[idx]


def _print_scorecard(results: list[ProbeResult]) -> None:
    print("\n" + "=" * 72)
    print("LLM STRESS SCORECARD")
    print("=" * 72)
    for mode in ("chat", "live"):
        subset = [r for r in results if r.probe.mode == mode]
        if not subset:
            continue
        real = [r for r in subset if not r.fell_back]
        lats = [r.latency_s for r in subset]
        print(
            f"{mode:>5}: {len(subset)} probes | real-LLM {len(real)}/{len(subset)} "
            f"| latency p50 {_percentile(lats, 0.50):.1f}s p95 {_percentile(lats, 0.95):.1f}s"
        )
        # Words-per-sentence per style level — drift telemetry only, no
        # assertion (readability tuning is Category-E territory).
        by_style: dict[str, list[float]] = {}
        for r in real:
            label = (r.probe.skill or r.probe.style or "default") + (
                f"+{r.probe.coach_voice}" if r.probe.coach_voice else ""
            )
            sentences = max(1, sentence_count(r.reply))
            by_style.setdefault(label, []).append(word_count(r.reply) / sentences)
        for label, ratios in sorted(by_style.items()):
            print(f"        {label:<22} avg words/sentence {statistics.mean(ratios):5.1f}")
    for axis in ("hallucination", "accuracy", "style"):
        flagged = [(r.probe.probe_id, v) for r in results for v in getattr(r, axis)]
        print(f"{axis:>14}: {len(flagged)} violation(s)")
        for probe_id, violation in flagged:
            print(f"        [{probe_id}] {violation}")
    print("=" * 72)


def _write_report(results: list[ProbeResult]) -> None:
    path = os.getenv("LLM_STRESS_REPORT_PATH", "").strip()
    if not path:
        return
    with open(path, "w", encoding="utf-8") as handle:
        for r in results:
            handle.write(
                json.dumps(
                    {
                        "probe_id": r.probe.probe_id,
                        "mode": r.probe.mode,
                        "fen": r.probe.fen,
                        "skill": r.probe.skill,
                        "style": r.probe.style,
                        "coach_voice": r.probe.coach_voice,
                        "move_quality": r.probe.move_quality,
                        "fell_back": r.fell_back,
                        "latency_s": round(r.latency_s, 2),
                        "reply": r.reply,
                        "hallucination": r.hallucination,
                        "accuracy": r.accuracy,
                        "style_violations": r.style,
                    }
                )
                + "\n"
            )


@pytest.fixture(scope="module")
def stress_results() -> list[ProbeResult]:
    """Run the full probe matrix once (module scope — every axis test reads
    the same replies).  Fallback detection mirrors
    test_llm_chat_path_not_overrejected: spy on the deterministic builders."""
    fallback_flag = {"hit": False}
    orig_chat = _cp._build_reply_deterministic
    orig_live = _lp._build_hint

    def _spy_chat(*args, **kwargs):
        fallback_flag["hit"] = True
        return orig_chat(*args, **kwargs)

    def _spy_live(*args, **kwargs):
        fallback_flag["hit"] = True
        return orig_live(*args, **kwargs)

    _cp._build_reply_deterministic = _spy_chat
    _lp._build_hint = _spy_live
    results: list[ProbeResult] = []
    try:
        for probe in _ALL_PROBES:
            results.append(_run_probe(probe, fallback_flag))
    finally:
        _cp._build_reply_deterministic = orig_chat
        _lp._build_hint = orig_live

    _print_scorecard(results)
    _write_report(results)
    return results


# ---------------------------------------------------------------------------
# Axis assertions
# ---------------------------------------------------------------------------


def _format_failures(results: list[ProbeResult], axis: str) -> str:
    lines: list[str] = []
    for r in results:
        for violation in getattr(r, axis):
            origin = "fallback" if r.fell_back else "LLM"
            lines.append(f"  [{r.probe.probe_id} | {origin}] {violation}\n    reply: {r.reply!r}")
    return "\n".join(lines)


def test_no_hallucinated_board_facts(stress_results: list[ProbeResult]) -> None:
    """No final reply may claim a piece or a check that the board does not
    have — ARCHITECTURE.md: "never hallucinate engine facts"."""
    flagged = [r for r in stress_results if r.hallucination]
    assert not flagged, (
        f"{sum(len(r.hallucination) for r in flagged)} hallucinated board fact(s) "
        f"reached the user-facing reply:\n{_format_failures(stress_results, 'hallucination')}"
    )


def test_no_inaccurate_engine_claims(stress_results: list[ProbeResult]) -> None:
    """No final reply may invert the advantage direction, misstate material,
    or contradict the engine's move grade — ARCHITECTURE.md: "never
    contradict evaluations"."""
    flagged = [r for r in stress_results if r.accuracy]
    assert not flagged, (
        f"{sum(len(r.accuracy) for r in flagged)} engine-contradicting claim(s) "
        f"reached the user-facing reply:\n{_format_failures(stress_results, 'accuracy')}"
    )


def test_style_fits_player_level(stress_results: list[ProbeResult]) -> None:
    """Prompt style contracts (Mode-1 sentence budget, beginner jargon,
    terse voice, chat length) hold for at least STYLE_PASS_RATE of the
    REAL LLM replies.  Rate-based: style is a prompt contract on a
    stochastic model.  Fallback replies are excluded — the deterministic
    templates are fixed code pinned by their own per-push tests
    (test_player_level_quality.py), and e.g. the advanced Mode-1 fallback
    legitimately runs to 3 sentences via SafeExplainer."""
    real = [r for r in stress_results if not r.fell_back]
    total = len(real)
    clean = len([r for r in real if not r.style])
    rate = clean / total if total else 1.0
    assert rate >= STYLE_PASS_RATE, (
        f"style adherence {clean}/{total} ({rate:.0%}) below {STYLE_PASS_RATE:.0%}:\n"
        f"{_format_failures(real, 'style')}"
    )


def test_llm_survives_stress_matrix(stress_results: list[ProbeResult]) -> None:
    """Real-LLM answer rate per mode must stay above MIN_REAL_RATE — a
    collapse means the validators are over-rejecting on the adversarial
    matrix (or the provider is down) and users would get the templated
    fallback for these positions."""
    failures: list[str] = []
    for mode in ("chat", "live"):
        subset = [r for r in stress_results if r.probe.mode == mode]
        real = [r for r in subset if not r.fell_back]
        rate = len(real) / len(subset) if subset else 0.0
        if rate < MIN_REAL_RATE:
            fallen = [r.probe.probe_id for r in subset if r.fell_back]
            failures.append(
                f"{mode}: only {len(real)}/{len(subset)} ({rate:.0%}) real LLM replies "
                f"(threshold {MIN_REAL_RATE:.0%}); fallbacks: {fallen} — grep "
                f"'Mode-{{1,2}} LLM failed after' for the rejected token"
            )
    assert not failures, "\n".join(failures)


# ---------------------------------------------------------------------------
# Checker self-checks (deterministic — pin the checkers themselves so a
# broken regex cannot silently turn an axis into a no-op)
# ---------------------------------------------------------------------------


def test_selfcheck_phantom_piece() -> None:
    board = chess.Board(_FEN_KP_END)  # kings + one pawn only
    assert phantom_piece_claims("Use your queen to cut off the king.", board)
    assert phantom_piece_claims("Your rook belongs behind the pawn.", board)
    assert not phantom_piece_claims("Push the pawn and promote it to a queen.", board)
    assert not phantom_piece_claims("The queens were traded long ago.", board)
    # Phase-transition idiom, not a board claim — flagged a real CI run
    # (2026-07-06) on a perfectly good generic-endgame lesson.
    assert not phantom_piece_claims(
        "Games are won or lost when the queens come off and technique decides.", board
    )
    # Promotion idiom (second CI false positive, same day) and generic
    # indefinite teaching — excluded structurally by the determiner form.
    assert not phantom_piece_claims(
        "Count the moves it takes to turn that extra pawn into a queen.", board
    )
    assert not phantom_piece_claims("Queens are the strongest pieces in chess.", board)
    # Definite-article claims about THIS board still flag.
    assert phantom_piece_claims("The queen dominates the centre of the board.", board)
    assert not phantom_piece_claims("Walk your king forward and push the pawn.", board)
    full = chess.Board(_FEN_EQ_OPEN)
    assert not phantom_piece_claims("Develop your queenside knight and bishop.", full)


def test_selfcheck_phantom_check() -> None:
    quiet = chess.Board(_FEN_EQ_OPEN)
    assert phantom_check_claims("Your king is in check and must move.", quiet)
    assert not phantom_check_claims("Castle early to avoid ending up in check.", quiet)
    in_check = chess.Board(_FEN_CHECK)
    assert not phantom_check_claims("Your king is in check right now.", in_check)


def test_selfcheck_advantage_direction() -> None:
    losing = {"evaluation": {"type": "cp", "band": "decisive_advantage", "side": "black"}}
    assert advantage_direction_violations("You are winning this game.", losing, "white")
    assert advantage_direction_violations("You have a clear advantage here.", losing, "white")
    assert not advantage_direction_violations(
        "Your opponent has a decisive advantage.", losing, "white"
    )
    assert not advantage_direction_violations("You are not winning here.", losing, "white")
    equal = {"evaluation": {"type": "cp", "band": "equal", "side": "black"}}
    assert advantage_direction_violations("You are clearly better.", equal, "white")
    assert advantage_direction_violations("You are losing badly.", equal, "white")
    assert not advantage_direction_violations("The position is balanced.", equal, "white")
    mate_for = {"evaluation": {"type": "mate", "band": "decisive_advantage", "side": "white"}}
    assert advantage_direction_violations("You are about to be mated.", mate_for, "white")
    assert not advantage_direction_violations("Mate is inevitable in your favour.", mate_for, "white")
    # Sub-dimension comparatives are coaching, not overall claims.
    assert not advantage_direction_violations("You are better developed.", losing, "white")
    assert not advantage_direction_violations("You are ahead in development.", losing, "white")
    assert not advantage_direction_violations("You have a lead in development.", losing, "white")
    assert not advantage_direction_violations("You are behind in development.", mate_for, "white")
    assert advantage_direction_violations("You are better here.", losing, "white")


def test_selfcheck_material_claims() -> None:
    board = chess.Board(_FEN_LOSE_QUEEN)  # White down a queen
    assert material_claim_violations("You are up a queen after that.", board, "white")
    assert not material_claim_violations("You have lost material and must fight.", board, "white")
    even = chess.Board(_FEN_EQ_OPEN)
    assert material_claim_violations("You are up a pawn here.", even, "white")
    assert material_claim_violations("You have lost material.", even, "white")
    assert not material_claim_violations("Material is even.", even, "white")


def test_selfcheck_quality_contradiction() -> None:
    assert quality_contradiction_violations("Great move! You are consolidating.", "blunder")
    assert not quality_contradiction_violations("That was a blunder — the rook ends the game.", "blunder")
    assert quality_contradiction_violations("That was a mistake in a fine position.", "best")
    assert not quality_contradiction_violations(
        "Good move, even though the earlier blunder still hurts.", "good"
    )


def test_selfcheck_style() -> None:
    assert style_violations("One. Two. Three sentences.", mode="live", style=None, coach_voice=None)
    assert not style_violations("Nice move! Keep developing.", mode="live", style=None, coach_voice=None)
    assert style_violations(
        "This position shows zugzwang ideas.", mode="chat", style="beginner", coach_voice=None
    )
    assert not style_violations(
        "This position shows zugzwang ideas.", mode="chat", style="advanced", coach_voice=None
    )
    assert style_violations(
        "One. Two. Three. Four sentences here.", mode="chat", style=None, coach_voice="terse"
    )
