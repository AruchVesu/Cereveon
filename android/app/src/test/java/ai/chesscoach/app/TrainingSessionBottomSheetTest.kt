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
 *  TRAIN_TOPIC_FORMAT             formatTopic capitalises first letter and replaces underscores.
 *  TRAIN_TYPE_FORMAT              formatExerciseType capitalises first letter.
 *  TRAIN_DIFF_FORMAT_BAND         formatDifficulty capitalises the band string.
 *  TRAIN_DIFF_PROGRESS_BAND       difficultyProgress maps each known band to its fixed %.
 *  TRAIN_DIFF_PROGRESS_UNKNOWN    unknown bands fall through to the 50 % midpoint.
 *  TRAIN_DIFF_PROGRESS_CASE       difficultyProgress is case-insensitive on the band.
 *  TRAIN_SEED_CONTAINS_TOPIC      buildSeedPrompt includes human-readable topic.
 *  TRAIN_SEED_CONTAINS_TYPE       buildSeedPrompt includes exercise type.
 *  TRAIN_SEED_CONTAINS_DIFF       buildSeedPrompt includes the difficulty band word.
 *  TRAIN_SEED_DETERMINISTIC       same input always produces same seed prompt.
 */
class TrainingSessionBottomSheetTest {

    private fun rec(
        topic: String = "endgame_technique",
        exerciseType: String = "drill",
        difficulty: String = "medium",
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
    fun `TRAIN_DIFF_FORMAT_BAND - capitalises the band string`() {
        assertEquals("Difficulty: Easy",   TrainingSessionBottomSheet.formatDifficulty("easy"))
        assertEquals("Difficulty: Medium", TrainingSessionBottomSheet.formatDifficulty("medium"))
        assertEquals("Difficulty: Hard",   TrainingSessionBottomSheet.formatDifficulty("hard"))
    }

    // ─────────────────────────────────────────────────────────────────────────
    // difficultyProgress
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `TRAIN_DIFF_PROGRESS_BAND - maps each known band to its fixed percent`() {
        assertEquals(30, TrainingSessionBottomSheet.difficultyProgress("easy"))
        assertEquals(60, TrainingSessionBottomSheet.difficultyProgress("medium"))
        assertEquals(85, TrainingSessionBottomSheet.difficultyProgress("hard"))
    }

    @Test
    fun `TRAIN_DIFF_PROGRESS_UNKNOWN - unknown band falls through to the 50 percent midpoint`() {
        // Future bands shipped by the server without a coordinated Android
        // release should render at the midpoint rather than 0 (which would
        // imply "no difficulty") or throw.
        assertEquals(50, TrainingSessionBottomSheet.difficultyProgress("expert"))
        assertEquals(50, TrainingSessionBottomSheet.difficultyProgress(""))
    }

    @Test
    fun `TRAIN_DIFF_PROGRESS_CASE - band match is case-insensitive`() {
        assertEquals(30, TrainingSessionBottomSheet.difficultyProgress("EASY"))
        assertEquals(60, TrainingSessionBottomSheet.difficultyProgress("Medium"))
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
    fun `TRAIN_SEED_CONTAINS_DIFF - seed prompt includes the difficulty band word`() {
        val prompt = TrainingSessionBottomSheet.buildSeedPrompt(rec(difficulty = "hard"))
        assertTrue("Seed must mention 'hard difficulty', got: $prompt", "hard difficulty" in prompt)
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
