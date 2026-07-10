"""Tests for ``llm.seca.review.moments`` — deterministic moment selection.

Stable test IDs (do NOT rename):
  REVIEW_MOM_BAND_*    band mapping + ESV cutoff alignment pins
  REVIEW_MOM_REC_*     MoveRecord building from PGN + eval series
  REVIEW_MOM_CLS_*     per-move classification
  REVIEW_MOM_SEL_*     top-3 selection rules (diversity/distance/fallback)
"""

from __future__ import annotations

import chess
import pytest

from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
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
    MoveRecord,
    REVIEW_MODE_STANDARD,
    REVIEW_MODE_STRATEGIC,
    band_for_player_cp,
    build_player_move_records,
    select_critical_moments,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record(
    ply: int,
    *,
    before: int = 0,
    after: int = 0,
    swing: int = 0,
    phase: str = "middlegame",
) -> MoveRecord:
    """Synthetic player-move record; loss derived like the builder does."""
    return MoveRecord(
        ply=ply,
        move_number=(ply + 1) // 2,
        san="Nf3",
        fen_before=chess.STARTING_FEN,
        fen_after=chess.STARTING_FEN,
        phase=phase,
        before_cp=before,
        after_cp=after,
        loss_cp=max(0, before - after),
        opp_prior_swing_cp=swing,
        clock_remaining_s=None,
    )


def _esv_step_for_white_cp(cp: int) -> str:
    """Project extract_engine_signal's {band, side} onto the 5 display
    steps for a WHITE player — the reference the review banding must
    agree with."""
    esv = extract_engine_signal({"evaluation": {"type": "cp", "value": cp}})
    band = esv["evaluation"]["band"]
    side = esv["evaluation"]["side"]
    if band == "equal":
        return BAND_EQUAL
    player_ahead = side == "white"
    if band == "decisive_advantage":
        return BAND_WINNING if player_ahead else BAND_LOSING
    return BAND_BETTER if player_ahead else BAND_WORSE


# ---------------------------------------------------------------------------
# REVIEW_MOM_BAND — banding
# ---------------------------------------------------------------------------


class TestBandAlignment:
    """REVIEW_MOM_BAND: the review's 5-step banding stays aligned with
    the ESV extraction cutoffs (equal ≤ 20 < small ≤ 60 < clear ≤ 120 <
    decisive) — the same drift-pin pattern the phase classifier uses."""

    @pytest.mark.parametrize(
        "cp",
        [0, 5, 20, 21, -21, 60, 61, -61, 120, 121, -121, 500, -500, 10000, -10000],
    )
    def test_banding_matches_esv_cutoffs(self, cp: int):
        """REVIEW_MOM_BAND_ALIGN: for a White player, player POV == White
        POV, so band_for_player_cp must agree with the ESV projection."""
        assert band_for_player_cp(cp) == _esv_step_for_white_cp(cp)

    def test_band_is_player_relative(self):
        """REVIEW_MOM_BAND_POV: the same White eval reads inverted for a
        Black player (the caller projects sign before banding)."""
        assert band_for_player_cp(300) == BAND_WINNING
        assert band_for_player_cp(-300) == BAND_LOSING
        assert band_for_player_cp(40) == BAND_BETTER
        assert band_for_player_cp(-40) == BAND_WORSE


# ---------------------------------------------------------------------------
# REVIEW_MOM_REC — record building
# ---------------------------------------------------------------------------


def _pgn(moves_san: list[str], *, result: str = "*", clocks: list[str] | None = None) -> str:
    if clocks is None:
        body = " ".join(moves_san)
    else:
        body = " ".join(
            f"{san} {{[%clk {clk}]}}" for san, clk in zip(moves_san, clocks)
        )
    return f'[Event "T"]\n[Result "{result}"]\n\n{body} {result}\n'


class TestRecordBuilding:
    """REVIEW_MOM_REC: PGN walk pairs player moves with the series."""

    def test_white_player_records(self):
        """REVIEW_MOM_REC_WHITE: White's plies (1, 3) are recorded with
        White-POV evals passed through unchanged."""
        pgn = _pgn(["e4", "e5", "Nf3", "Nc6"])
        series = (10, 30, 20, 40, 35)
        records = build_player_move_records(pgn, series, player_is_white=True)
        assert [r.ply for r in records] == [1, 3]
        assert records[0].before_cp == 10
        assert records[0].after_cp == 30
        assert records[0].loss_cp == 0
        assert records[1].before_cp == 20
        assert records[1].after_cp == 40
        # Opponent's ply 2 swung 20 -> 30 is... ply2 swing = 20-30 = -10
        # player-POV: e5 moved the eval 30 -> 20, i.e. -10 for White.
        assert records[1].opp_prior_swing_cp == -10

    def test_black_player_sign_projection(self):
        """REVIEW_MOM_REC_BLACK: for a Black player the White series is
        negated — a rising White eval is a Black loss."""
        pgn = _pgn(["e4", "e5", "Nf3", "Nc6"])
        series = (0, 0, 200, 200, 200)
        records = build_player_move_records(pgn, series, player_is_white=False)
        assert [r.ply for r in records] == [2, 4]
        # Black's e5: White eval 0 -> 200 == Black POV 0 -> -200 = 200 loss.
        assert records[0].before_cp == 0
        assert records[0].after_cp == -200
        assert records[0].loss_cp == 200

    def test_clock_annotations_captured(self):
        """REVIEW_MOM_REC_CLOCK: [%clk] comments land as remaining
        seconds; absent clocks yield None."""
        pgn = _pgn(
            ["e4", "e5", "Nf3", "Nc6"],
            clocks=["0:03:00", "0:02:58", "0:02:41", "0:02:30"],
        )
        series = (0, 0, 0, 0, 0)
        records = build_player_move_records(pgn, series, player_is_white=True)
        assert records[0].clock_remaining_s == 180
        assert records[1].clock_remaining_s == 161

        bare = build_player_move_records(
            _pgn(["e4", "e5"]), (0, 0, 0), player_is_white=True
        )
        assert bare[0].clock_remaining_s is None

    def test_truncated_series_stops_walk(self):
        """REVIEW_MOM_REC_TRUNC: plies beyond the eval series (the
        accuracy recompute's max_plies cap) are not recorded."""
        pgn = _pgn(["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"])
        series = (0, 0, 0)  # start + 2 plies only
        records = build_player_move_records(pgn, series, player_is_white=True)
        assert [r.ply for r in records] == [1]

    def test_unparseable_pgn_returns_empty(self):
        """REVIEW_MOM_REC_BADPGN: junk input builds nothing (legacy-row
        guard; the import stream rejects unreplayable PGNs upstream)."""
        assert build_player_move_records("not a pgn", (0,), player_is_white=True) == []


# ---------------------------------------------------------------------------
# REVIEW_MOM_CLS — classification (probed through selection)
# ---------------------------------------------------------------------------


class TestClassification:
    """REVIEW_MOM_CLS: type thresholds and precedence."""

    def test_blunder_type(self):
        moments, mode = select_critical_moments([_record(10, before=0, after=-350)])
        assert mode == REVIEW_MODE_STANDARD
        assert moments[0].moment_type == MOMENT_BLUNDER

    def test_missed_win_takes_precedence_over_blunder(self):
        """REVIEW_MOM_CLS_PRECEDENCE: a ≥300cp loss from a winning
        position is the missed-win lesson, not a generic blunder."""
        moments, _ = select_critical_moments([_record(10, before=250, after=-100)])
        assert moments[0].moment_type == MOMENT_MISSED_WIN

    def test_mistake_type(self):
        moments, _ = select_critical_moments([_record(10, before=0, after=-200)])
        assert moments[0].moment_type == MOMENT_MISTAKE

    def test_punished_mistake_requires_swing_and_hold(self):
        """REVIEW_MOM_CLS_PUNISH: opponent error (≥150 swing) + player
        holds it (≤50 loss) → positive moment; giving it back doesn't
        qualify."""
        held, _ = select_critical_moments([_record(10, before=180, after=170, swing=200)])
        assert held[0].moment_type == MOMENT_PUNISHED_MISTAKE

        returned, mode = select_critical_moments(
            [_record(10, before=180, after=60, swing=200)]
        )
        # 120cp loss: not a hold, not a mistake (<150) → no typed
        # candidate → strategic fallback picks it as a lesson.
        assert mode == REVIEW_MODE_STRATEGIC
        assert returned[0].moment_type == MOMENT_STRATEGIC


# ---------------------------------------------------------------------------
# REVIEW_MOM_SEL — selection rules
# ---------------------------------------------------------------------------


class TestSelection:
    """REVIEW_MOM_SEL: opening skip, distance, diversity, fallback."""

    def test_opening_plies_skipped(self):
        """REVIEW_MOM_SEL_OPENING: a huge blunder on ply 8 is theory-
        exempt; ply 9 is eligible."""
        early = _record(8, before=0, after=-500)
        later = _record(9, before=0, after=-400)
        moments, _ = select_critical_moments([early, later])
        assert [m.record.ply for m in moments] == [9]

    def test_distance_rule_keeps_higher_scored(self):
        """REVIEW_MOM_SEL_DISTANCE: two candidates 2 plies apart — only
        the higher-scored (bigger loss) survives."""
        a = _record(11, before=0, after=-400)
        b = _record(13, before=0, after=-600)
        moments, _ = select_critical_moments([a, b])
        assert [m.record.ply for m in moments] == [13]

    def test_diversity_cap_prefers_positive_third(self):
        """REVIEW_MOM_SEL_DIVERSITY: with 3 blunders + 1 punished
        available, the selection is 2 blunders + the positive moment."""
        blunders = [
            _record(11, before=0, after=-400),
            _record(21, before=0, after=-500),
            _record(31, before=0, after=-600),
        ]
        positive = _record(41, before=100, after=90, swing=300)
        moments, _ = select_critical_moments(blunders + [positive])
        types = [m.moment_type for m in moments]
        assert types.count(MOMENT_PUNISHED_MISTAKE) == 1
        assert types.count(MOMENT_BLUNDER) == 2
        # Chronological card order regardless of score order.
        assert [m.record.ply for m in moments] == sorted(m.record.ply for m in moments)

    def test_all_negative_when_no_positive_exists(self):
        """REVIEW_MOM_SEL_FILL: three negatives beat two cards when no
        positive candidate exists (pass-2 fill)."""
        blunders = [
            _record(11, before=0, after=-400),
            _record(21, before=0, after=-500),
            _record(31, before=0, after=-600),
        ]
        moments, _ = select_critical_moments(blunders)
        assert len(moments) == 3

    def test_strategic_fallback_on_quiet_game(self):
        """REVIEW_MOM_SEL_STRATEGIC: no move clears any threshold →
        highest-loss moves surface with review_mode='strategic'."""
        quiet = [
            _record(11, before=0, after=-30),
            _record(21, before=-30, after=-90),
            _record(31, before=-90, after=-100),
            _record(41, before=-100, after=-140),
        ]
        moments, mode = select_critical_moments(quiet)
        assert mode == REVIEW_MODE_STRATEGIC
        assert len(moments) == 3
        assert all(m.moment_type == MOMENT_STRATEGIC for m in moments)
        # The three biggest losses (60, 40, 30) — the 10cp move is out.
        assert {m.record.ply for m in moments} == {11, 21, 41}

    def test_payload_is_wire_safe(self):
        """REVIEW_MOM_SEL_WIRE: to_payload carries band strings and no
        raw centipawn values — the no-numeric-eval invariant is enforced
        at the wire."""
        moments, _ = select_critical_moments([_record(10, before=250, after=-100)])
        payload = moments[0].to_payload()
        assert payload["band_before"] == BAND_WINNING
        assert payload["band_after"] == BAND_WORSE
        forbidden_keys = {"before_cp", "after_cp", "loss_cp", "score", "eval"}
        assert forbidden_keys.isdisjoint(payload.keys())
        for value in payload.values():
            # ply / move_number / clock are the only ints, and none of
            # them is an evaluation.
            assert not isinstance(value, float)
