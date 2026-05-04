"""
Context compaction tests — llm/tests/test_context_compact.py

Invariants
----------
COMPACT-01  should_compact returns False for short histories
COMPACT-02  should_compact returns True at and above COMPACT_THRESHOLD
COMPACT-03  compact_history returns fewer turns than the input
COMPACT-04  compact_history preserves exactly COMPACT_KEEP_RECENT recent turns verbatim
COMPACT-05  blunder mentions in old turns survive in summary (mistake data not lost)
COMPACT-06  strength mentions in old turns survive in summary (strength data not lost)
COMPACT-07  topic mentions in old turns survive in summary (topic data not lost)
COMPACT-08  multiple distinct mistake types are all preserved
COMPACT-09  compacted representation is measurably shorter (token-saving effectiveness)
COMPACT-10  generate_chat_reply handles a history that exceeds COMPACT_THRESHOLD
COMPACT-11  summary turn has role="system"
COMPACT-12  compact_history is idempotent on short lists (no-op below threshold)
COMPACT-13  _build_compact_summary header contains the correct turn count
COMPACT-14  old turns with no patterns produce "No specific patterns" summary
"""

from __future__ import annotations

from llm.seca.coach.chat_pipeline import ChatTurn
from llm.seca.coach.context_compact import (
    COMPACT_KEEP_RECENT,
    COMPACT_THRESHOLD,
    _build_compact_summary,
    _collect_tags,
    _MISTAKE_PATTERNS,
    _STRENGTH_PATTERNS,
    _TOPIC_PATTERNS,
    compact_history,
    should_compact,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _turns(n: int, content_template: str = "Turn {i} content here.") -> list[ChatTurn]:
    """Create n alternating user/assistant turns."""
    role_cycle = ["user", "assistant"]
    return [
        ChatTurn(role=role_cycle[i % 2], content=content_template.format(i=i + 1))
        for i in range(n)
    ]


def _total_chars(turns: list) -> int:
    """Sum of content lengths across all turns."""
    return sum(len(getattr(t, "content", "")) for t in turns)


# ===========================================================================
# COMPACT-01 / COMPACT-02  should_compact threshold
# ===========================================================================


class TestShouldCompact:
    """COMPACT-01/02: Threshold logic."""

    def test_false_for_empty(self):
        assert not should_compact([])

    def test_false_below_threshold(self):
        assert not should_compact(_turns(COMPACT_THRESHOLD - 1))

    def test_false_one_below_threshold(self):
        assert not should_compact(_turns(COMPACT_THRESHOLD - 1))

    def test_true_at_threshold(self):
        assert should_compact(_turns(COMPACT_THRESHOLD))

    def test_true_above_threshold(self):
        assert should_compact(_turns(COMPACT_THRESHOLD + 10))


# ===========================================================================
# COMPACT-03  compact_history reduces list length
# ===========================================================================


class TestCompactHistoryLength:
    """COMPACT-03: After compaction the list is strictly shorter."""

    def test_compacted_list_is_shorter(self):
        original = _turns(COMPACT_THRESHOLD + 4)
        compacted = compact_history(original)
        assert len(compacted) < len(original)

    def test_compacted_length_equals_keep_recent_plus_one(self):
        """Compacted list = 1 summary turn + COMPACT_KEEP_RECENT recent turns."""
        original = _turns(COMPACT_THRESHOLD + 8)
        compacted = compact_history(original)
        assert len(compacted) == COMPACT_KEEP_RECENT + 1


# ===========================================================================
# COMPACT-04  recent turns preserved verbatim
# ===========================================================================


class TestRecentTurnsPreserved:
    """COMPACT-04: Last COMPACT_KEEP_RECENT turns survive unchanged."""

    def test_recent_turns_content_identical(self):
        original = _turns(COMPACT_THRESHOLD + 2, "Unique content turn {i}.")
        compacted = compact_history(original)
        # First element is the summary; the rest are recent turns.
        verbatim = compacted[1:]
        expected = original[-COMPACT_KEEP_RECENT:]
        for got, want in zip(verbatim, expected):
            assert got.content == want.content, (
                f"Recent turn content changed after compaction: {got.content!r} != {want.content!r}"
            )

    def test_recent_turns_roles_identical(self):
        original = _turns(COMPACT_THRESHOLD + 2)
        compacted = compact_history(original)
        verbatim = compacted[1:]
        expected = original[-COMPACT_KEEP_RECENT:]
        for got, want in zip(verbatim, expected):
            assert got.role == want.role


# ===========================================================================
# COMPACT-05  blunder data preserved in summary
# ===========================================================================


class TestMistakeDataPreserved:
    """COMPACT-05: Blunder/mistake/inaccuracy mentions from old turns survive."""

    def test_blunder_in_old_turns_appears_in_summary(self):
        old_content = ["You blundered the queen on move 12."] + ["filler"] * (COMPACT_THRESHOLD - 2)
        turns = [ChatTurn(role="user", content=c) for c in old_content]
        # Add enough recent turns so old turns get compacted.
        turns += _turns(COMPACT_KEEP_RECENT)
        compacted = compact_history(turns)
        summary_content = compacted[0].content
        assert "blunder" in summary_content.lower(), (
            f"Blunder data lost after compaction. Summary: {summary_content!r}"
        )

    def test_inaccuracy_in_old_turns_appears_in_summary(self):
        turns = [ChatTurn(role="user", content="That was an inaccuracy in the endgame.")]
        turns += _turns(COMPACT_THRESHOLD - 1 + COMPACT_KEEP_RECENT)
        compacted = compact_history(turns[: COMPACT_THRESHOLD + COMPACT_KEEP_RECENT])
        # Any compacted list from content containing "inaccuracy" must preserve it.
        old_turns = turns[: -(COMPACT_KEEP_RECENT)]
        summary = _build_compact_summary(old_turns)
        assert "inaccuracy" in summary.lower()

    def test_mistake_label_preserved(self):
        old_turns = [ChatTurn(role="assistant", content="This was a clear mistake.")]
        summary = _build_compact_summary(old_turns)
        assert "mistake" in summary.lower()


# ===========================================================================
# COMPACT-06  strength data preserved in summary
# ===========================================================================


class TestStrengthDataPreserved:
    """COMPACT-06: Strength mentions from old turns survive in summary."""

    def test_excellent_move_preserved(self):
        old_turns = [ChatTurn(role="assistant", content="That was an excellent move!")]
        summary = _build_compact_summary(old_turns)
        assert "excellent move" in summary.lower()

    def test_best_move_preserved(self):
        old_turns = [ChatTurn(role="user", content="Did I play the best move?"),
                     ChatTurn(role="assistant", content="Yes, that was the best move available.")]
        summary = _build_compact_summary(old_turns)
        assert "best move" in summary.lower()

    def test_well_played_preserved(self):
        old_turns = [ChatTurn(role="assistant", content="Well played — you found the winning idea.")]
        summary = _build_compact_summary(old_turns)
        assert "well played" in summary.lower()


# ===========================================================================
# COMPACT-07  topic data preserved in summary
# ===========================================================================


class TestTopicDataPreserved:
    """COMPACT-07: Topic keywords from old turns appear in summary."""

    def test_endgame_topic_preserved(self):
        old_turns = [ChatTurn(role="user", content="How should I handle this endgame?")]
        summary = _build_compact_summary(old_turns)
        assert "endgame" in summary.lower()

    def test_tactics_topic_preserved(self):
        old_turns = [ChatTurn(role="assistant", content="Watch for tactical opportunities here.")]
        summary = _build_compact_summary(old_turns)
        assert "tactic" in summary.lower()

    def test_opening_topic_preserved(self):
        old_turns = [ChatTurn(role="user", content="What opening principles apply here?")]
        summary = _build_compact_summary(old_turns)
        assert "opening" in summary.lower()

    def test_pawn_structure_topic_preserved(self):
        old_turns = [ChatTurn(role="assistant", content="The pawn structure favours White.")]
        summary = _build_compact_summary(old_turns)
        assert "pawn structure" in summary.lower()


# ===========================================================================
# COMPACT-08  multiple distinct mistake types all preserved
# ===========================================================================


class TestMultipleMistakesPreserved:
    """COMPACT-08: All distinct mistake types mentioned survive compaction."""

    def test_blunder_and_inaccuracy_both_preserved(self):
        old_turns = [
            ChatTurn(role="assistant", content="You blundered the rook."),
            ChatTurn(role="assistant", content="That inaccuracy cost you a pawn."),
        ]
        summary = _build_compact_summary(old_turns)
        assert "blunder" in summary.lower()
        assert "inaccuracy" in summary.lower()

    def test_mistake_and_hanging_both_preserved(self):
        old_turns = [
            ChatTurn(role="assistant", content="The knight is hanging and that was a mistake."),
        ]
        summary = _build_compact_summary(old_turns)
        assert "mistake" in summary.lower()
        assert "hanging" in summary.lower()


# ===========================================================================
# COMPACT-09  token-saving effectiveness (character count proxy)
# ===========================================================================


class TestTokenReduction:
    """COMPACT-09: Compacted representation is significantly shorter than original."""

    def test_compacted_chars_less_than_half_of_original(self):
        # 30 turns × 200 chars each = 6000 chars total.
        long_content = "a" * 200
        original = [ChatTurn(role="user" if i % 2 == 0 else "assistant", content=long_content)
                    for i in range(30)]
        compacted = compact_history(original)

        original_chars = _total_chars(original)
        compacted_chars = _total_chars(compacted)

        assert compacted_chars < original_chars * 0.50, (
            f"Compaction reduced chars from {original_chars} to {compacted_chars} "
            f"({compacted_chars / original_chars:.0%}) — expected < 50% of original."
        )

    def test_compacted_turn_count_fraction(self):
        """Compacted list must be at most 40% the size of the original."""
        original = _turns(30)
        compacted = compact_history(original)
        ratio = len(compacted) / len(original)
        assert ratio <= 0.40, (
            f"Compaction reduced turn count from {len(original)} to {len(compacted)} "
            f"(ratio {ratio:.2f}) — expected ≤ 0.40."
        )


# ===========================================================================
# COMPACT-10  generate_chat_reply handles long history
# ===========================================================================


class TestGenerateChatReplyIntegration:
    """COMPACT-10: generate_chat_reply does not crash with histories > COMPACT_THRESHOLD."""

    def test_generate_chat_reply_handles_long_history(self):
        from llm.seca.coach.chat_pipeline import generate_chat_reply

        fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        messages = _turns(COMPACT_THRESHOLD + 5)
        # Ensure the last message is a user turn.
        messages.append(ChatTurn(role="user", content="What should I do now?"))

        result = generate_chat_reply(fen=fen, messages=messages)

        assert result.reply, "generate_chat_reply returned empty reply for long history"
        assert result.engine_signal, "engine_signal missing from reply"
        assert result.mode == "CHAT_V1"

    def test_generate_chat_reply_handles_short_history_unchanged(self):
        """Histories below threshold must not be compacted."""
        from llm.seca.coach.chat_pipeline import generate_chat_reply

        fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        messages = _turns(3)
        messages.append(ChatTurn(role="user", content="Explain the position."))

        result = generate_chat_reply(fen=fen, messages=messages)
        assert result.reply
        assert result.mode == "CHAT_V1"


# ===========================================================================
# COMPACT-11  summary turn has role="system"
# ===========================================================================


class TestSummaryTurnRole:
    """COMPACT-11: Summary injected with role='system'."""

    def test_summary_turn_role_is_system(self):
        original = _turns(COMPACT_THRESHOLD + 2)
        compacted = compact_history(original)
        assert compacted[0].role == "system", (
            f"Expected summary turn role 'system', got {compacted[0].role!r}"
        )


# ===========================================================================
# COMPACT-12  compact_history is no-op on short lists
# ===========================================================================


class TestNoOpOnShortList:
    """COMPACT-12: Lists shorter than COMPACT_KEEP_RECENT are returned unchanged."""

    def test_very_short_list_unchanged(self):
        original = _turns(3)
        result = compact_history(original)
        assert len(result) == len(original)
        for got, want in zip(result, original):
            assert got.content == want.content


# ===========================================================================
# COMPACT-13  summary header contains correct turn count
# ===========================================================================


class TestSummaryHeader:
    """COMPACT-13: Header accurately reports how many turns were compacted."""

    def test_header_contains_old_turn_count(self):
        n_old = 14
        old_turns = _turns(n_old)
        summary = _build_compact_summary(old_turns)
        assert f"{n_old} earlier turns" in summary, (
            f"Expected '{n_old} earlier turns' in summary. Got: {summary!r}"
        )


# ===========================================================================
# COMPACT-14  empty / no-pattern old turns produce fallback message
# ===========================================================================


class TestNoPatternFallback:
    """COMPACT-14: No-pattern turns produce 'No specific patterns identified'."""

    def test_neutral_turns_produce_no_pattern_message(self):
        old_turns = [ChatTurn(role="user", content="Hello."),
                     ChatTurn(role="assistant", content="Hi, I am your chess coach.")]
        summary = _build_compact_summary(old_turns)
        assert "No specific patterns identified" in summary, (
            f"Expected fallback message for neutral turns. Got: {summary!r}"
        )
