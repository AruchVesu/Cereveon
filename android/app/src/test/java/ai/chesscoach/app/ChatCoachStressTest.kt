package ai.chesscoach.app

import org.junit.Assert.*
import org.junit.Test

/**
 * Area 8 — Chat Coach data model stress tests.
 *
 * Tests the pure Kotlin/JVM data model layer used by the Chat Coach:
 *   - ChatMessage creation, equality, and copy semantics
 *   - MistakeClassification enum labels and coverage
 *   - QuickCoachUpdate data class constraints
 *   - AIMove validity boundary conditions
 *
 * This suite validates all testable logic in the JVM-safe data model layer
 * without requiring an Android emulator.
 */
class ChatCoachStressTest {

    // ------------------------------------------------------------------
    // ChatMessage — Area 8 data model
    // ------------------------------------------------------------------

    @Test
    fun `ChatMessage retains role and text unchanged`() {
        val msg = ChatMessage(role = "user", text = "Hello coach!")
        assertEquals("user", msg.role)
        assertEquals("Hello coach!", msg.text)
    }

    @Test
    fun `ChatMessage equality holds for identical instances`() {
        val m1 = ChatMessage(role = "assistant", text = "Consider the pawn structure.")
        val m2 = ChatMessage(role = "assistant", text = "Consider the pawn structure.")
        assertEquals(m1, m2)
    }

    @Test
    fun `ChatMessage inequality when role differs`() {
        val m1 = ChatMessage(role = "user", text = "Same text.")
        val m2 = ChatMessage(role = "assistant", text = "Same text.")
        assertNotEquals(m1, m2)
    }

    @Test
    fun `ChatMessage inequality when text differs`() {
        val m1 = ChatMessage(role = "user", text = "Text A")
        val m2 = ChatMessage(role = "user", text = "Text B")
        assertNotEquals(m1, m2)
    }

    @Test
    fun `ChatMessage handles empty text without crash`() {
        val msg = ChatMessage(role = "user", text = "")
        assertTrue("Empty text must be accepted", msg.text.isEmpty())
    }

    @Test
    fun `ChatMessage handles 10KB text without crash`() {
        val longText = "The position is structurally complex. ".repeat(300)
        val msg = ChatMessage(role = "assistant", text = longText)
        assertTrue("10KB message must be retained", msg.text.length > 10_000)
    }

    @Test
    fun `ChatMessage handles unicode text`() {
        val unicode = "Позиция примерно равна. 位置大致相等。"
        val msg = ChatMessage(role = "assistant", text = unicode)
        assertEquals(unicode, msg.text)
    }

    @Test
    fun `ChatMessage copy semantics produce distinct objects`() {
        val original = ChatMessage(role = "user", text = "Original message.")
        val copy = original.copy(text = "Modified message.")
        assertEquals("user", copy.role)
        assertEquals("Modified message.", copy.text)
        assertNotEquals(original.text, copy.text)
    }

    @Test
    fun `100 ChatMessage instances all retain their text`() {
        val messages = (0 until 100).map {
            ChatMessage(role = "user", text = "Message number $it")
        }
        for ((i, msg) in messages.withIndex()) {
            assertEquals("Message number $i", msg.text)
        }
    }

    @Test
    fun `ChatMessage with system role is accepted`() {
        val msg = ChatMessage(role = "system", text = "FEN: rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
        assertEquals("system", msg.role)
        assertTrue(msg.text.startsWith("FEN:"))
    }

    // ------------------------------------------------------------------
    // MistakeClassification — Quick Coach dock labels
    // ------------------------------------------------------------------

    @Test
    fun `MistakeClassification has exactly 4 values`() {
        assertEquals(4, MistakeClassification.entries.size)
    }

    @Test
    fun `MistakeClassification GOOD label is GOOD`() {
        assertEquals("GOOD", MistakeClassification.GOOD.label())
    }

    @Test
    fun `MistakeClassification INACCURACY label is INACCURACY`() {
        assertEquals("INACCURACY", MistakeClassification.INACCURACY.label())
    }

    @Test
    fun `MistakeClassification MISTAKE label is MISTAKE`() {
        assertEquals("MISTAKE", MistakeClassification.MISTAKE.label())
    }

    @Test
    fun `MistakeClassification BLUNDER label is BLUNDER`() {
        assertEquals("BLUNDER", MistakeClassification.BLUNDER.label())
    }

    @Test
    fun `all MistakeClassification labels are non-empty and unique`() {
        val labels = MistakeClassification.entries.map { it.label() }
        assertTrue("All labels must be non-empty", labels.all { it.isNotEmpty() })
        assertEquals("All labels must be unique", labels.size, labels.distinct().size)
    }

    // ------------------------------------------------------------------
    // QuickCoachUpdate — structured update for Quick Coach dock
    // ------------------------------------------------------------------

    @Test
    fun `QuickCoachUpdate retains all fields`() {
        val update = QuickCoachUpdate(
            scoreText = "+0.42",
            classification = MistakeClassification.GOOD,
            explanation = "Passed pawn advantage."
        )
        assertEquals("+0.42", update.scoreText)
        assertEquals(MistakeClassification.GOOD, update.classification)
        assertEquals("Passed pawn advantage.", update.explanation)
    }

    @Test
    fun `QuickCoachUpdate allows null explanation`() {
        val update = QuickCoachUpdate(
            scoreText = "0.00",
            classification = MistakeClassification.GOOD,
            explanation = null
        )
        assertNull("explanation=null must be stored as null", update.explanation)
    }

    @Test
    fun `QuickCoachUpdate equality holds for same fields`() {
        val u1 = QuickCoachUpdate("±0.10", MistakeClassification.INACCURACY, "Minor slip.")
        val u2 = QuickCoachUpdate("±0.10", MistakeClassification.INACCURACY, "Minor slip.")
        assertEquals(u1, u2)
    }

    @Test
    fun `QuickCoachUpdate inequality when classification differs`() {
        val u1 = QuickCoachUpdate("−1.50", MistakeClassification.MISTAKE, "Dropped a piece.")
        val u2 = QuickCoachUpdate("−1.50", MistakeClassification.BLUNDER, "Dropped a piece.")
        assertNotEquals(u1, u2)
    }

    @Test
    fun `50 QuickCoachUpdates all retain correct classification`() {
        val cases = listOf(
            MistakeClassification.GOOD,
            MistakeClassification.INACCURACY,
            MistakeClassification.MISTAKE,
            MistakeClassification.BLUNDER,
        )
        repeat(50) { i ->
            val cls = cases[i % cases.size]
            val update = QuickCoachUpdate(scoreText = "$i", classification = cls, explanation = null)
            assertEquals(cls, update.classification)
        }
    }

    // ------------------------------------------------------------------
    // AIMove — coordinate validity contract
    // ------------------------------------------------------------------

    @Test
    fun `AIMove isValid returns false for negative fr`() {
        assertFalse(AIMove(fr = -1, fc = 0, tr = 1, tc = 0).isValid())
    }

    @Test
    fun `AIMove isValid returns true for fr equals zero`() {
        assertTrue(AIMove(fr = 0, fc = 0, tr = 7, tc = 7).isValid())
    }

    @Test
    fun `AIMove isValid returns true for all non-negative coordinates`() {
        assertTrue(AIMove(fr = 6, fc = 4, tr = 4, tc = 4).isValid())
    }

    @Test
    fun `AIMove equality and copy work correctly`() {
        val m1 = AIMove(1, 2, 3, 4)
        val m2 = AIMove(1, 2, 3, 4)
        assertEquals(m1, m2)
        val copy = m1.copy(tr = 5)
        assertNotEquals(m1, copy)
        assertEquals(5, copy.tr)
    }

    @Test
    fun `100 AIMove objects with random valid coords are all valid`() {
        val random = java.util.Random(42L)
        repeat(100) {
            val move = AIMove(
                fr = random.nextInt(8),
                fc = random.nextInt(8),
                tr = random.nextInt(8),
                tc = random.nextInt(8)
            )
            assertTrue("AIMove with non-negative coordinates must be valid", move.isValid())
        }
    }

    @Test
    fun `MoveResult SUCCESS PROMOTION FAILED are all distinct`() {
        val values = MoveResult.entries.map { it.name }
        assertEquals("MoveResult must have exactly 3 values", 3, values.size)
        assertEquals("All values must be unique", 3, values.distinct().size)
        assertTrue(MoveResult.SUCCESS.name == "SUCCESS")
        assertTrue(MoveResult.PROMOTION.name == "PROMOTION")
        assertTrue(MoveResult.FAILED.name == "FAILED")
    }
}
