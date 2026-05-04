package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for [TrainingSessionBottomSheet.Companion] pure helpers.
 *
 * All functions are pure (no Android context required) and must satisfy the
 * following invariants:
 *
 *  TRAIN_TOPIC_FORMAT       formatTopic capitalises first letter and replaces underscores.
 *  TRAIN_TYPE_FORMAT        formatExerciseType capitalises first letter.
 *  TRAIN_DIFF_FORMAT        formatDifficulty converts 0.0–1.0 to percent string.
 *  TRAIN_DIFF_PROGRESS      difficultyProgress maps 0.0–1.0 to 0–100.
 *  TRAIN_DIFF_CLAMP         difficultyProgress clamps outside 0–1 range.
 *  TRAIN_SEED_CONTAINS_TOPIC   buildSeedPrompt includes human-readable topic.
 *  TRAIN_SEED_CONTAINS_TYPE    buildSeedPrompt includes exercise type.
 *  TRAIN_SEED_CONTAINS_DIFF    buildSeedPrompt includes difficulty percentage.
 *  TRAIN_SEED_DETERMINISTIC    same input always produces same seed prompt.
 */
class TrainingSessionBottomSheetTest {

    private fun rec(
        topic: String = "endgame_technique",
        exerciseType: String = "drill",
        difficulty: Float = 0.7f,
    ) = CurriculumRecommendation(topic = topic, exerciseType = exerciseType, difficulty = difficulty)

    // ─────────────────────────────────────────────────────────────────────────
    // formatTopic
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `TRAIN_TOPIC_FORMAT - capitalises first letter and replaces underscores`() {
        assertEquals("Topic: Endgame technique", TrainingSessionBottomSheet.formatTopic("endgame_technique"))
        assertEquals("Topic: Tactics", TrainingSessionBottomSheet.formatTopic("tactics"))
    }

    // ─────────────────────────────────────────────────────────────────────────
    // formatExerciseType
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `TRAIN_TYPE_FORMAT - capitalises first letter of exercise type`() {
        assertEquals("Type: Drill", TrainingSessionBottomSheet.formatExerciseType("drill"))
        assertEquals("Type: Puzzle", TrainingSessionBottomSheet.formatExerciseType("puzzle"))
    }

    // ─────────────────────────────────────────────────────────────────────────
    // formatDifficulty
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `TRAIN_DIFF_FORMAT - converts fraction to percent string`() {
        assertEquals("Difficulty: 70%", TrainingSessionBottomSheet.formatDifficulty(0.7f))
        assertEquals("Difficulty: 0%",  TrainingSessionBottomSheet.formatDifficulty(0.0f))
        assertEquals("Difficulty: 100%", TrainingSessionBottomSheet.formatDifficulty(1.0f))
    }

    // ─────────────────────────────────────────────────────────────────────────
    // difficultyProgress
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `TRAIN_DIFF_PROGRESS - 0_7 maps to 70`() {
        assertEquals(70, TrainingSessionBottomSheet.difficultyProgress(0.7f))
    }

    @Test
    fun `TRAIN_DIFF_CLAMP - values outside 0 to 1 are clamped`() {
        assertEquals(0,   TrainingSessionBottomSheet.difficultyProgress(-0.1f))
        assertEquals(100, TrainingSessionBottomSheet.difficultyProgress(1.5f))
    }

    // ─────────────────────────────────────────────────────────────────────────
    // buildSeedPrompt
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `TRAIN_SEED_CONTAINS_TOPIC - seed prompt includes human-readable topic`() {
        val prompt = TrainingSessionBottomSheet.buildSeedPrompt(rec(topic = "king_and_pawn"))
        assertTrue("Seed must contain 'king and pawn', got: $prompt", "king and pawn" in prompt)
    }

    @Test
    fun `TRAIN_SEED_CONTAINS_TYPE - seed prompt includes exercise type`() {
        val prompt = TrainingSessionBottomSheet.buildSeedPrompt(rec(exerciseType = "puzzle"))
        assertTrue("Seed must mention exercise type, got: $prompt", "Puzzle" in prompt || "puzzle" in prompt)
    }

    @Test
    fun `TRAIN_SEED_CONTAINS_DIFF - seed prompt includes difficulty percentage`() {
        val prompt = TrainingSessionBottomSheet.buildSeedPrompt(rec(difficulty = 0.6f))
        assertTrue("Seed must mention difficulty percentage, got: $prompt", "60%" in prompt)
    }

    @Test
    fun `TRAIN_SEED_DETERMINISTIC - same input always yields identical seed`() {
        val r = rec()
        assertEquals(
            TrainingSessionBottomSheet.buildSeedPrompt(r),
            TrainingSessionBottomSheet.buildSeedPrompt(r),
        )
    }
}
