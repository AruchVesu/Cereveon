package ai.chesscoach.app

import org.junit.Assert.*
import org.junit.Test

/**
 * JVM unit tests for the Quick Coach dock logic.
 *
 * Invariants pinned
 * -----------------
 *  1. CLASSIFICATION_QUEEN:       queen capture → BLUNDER
 *  2. CLASSIFICATION_ROOK:        rook capture  → MISTAKE
 *  3. CLASSIFICATION_BISHOP:      bishop capture → MISTAKE
 *  4. CLASSIFICATION_KNIGHT:      knight capture → MISTAKE
 *  5. CLASSIFICATION_PAWN:        pawn capture   → INACCURACY
 *  6. CLASSIFICATION_EMPTY:       empty square   → GOOD
 *  7. CLASSIFICATION_UNKNOWN:     unknown char   → GOOD
 *  8. FORMAT_SCORE_EQUAL:         near-zero balance → "Equal"
 *  9. FORMAT_SCORE_POSITIVE:      positive balance  → "+N.N"
 * 10. FORMAT_SCORE_NEGATIVE:      negative balance  → "-N.N" (no plus sign)
 * 11. FORMAT_SCORE_BOUNDARY:      ±0.05 edge cases
 * 12. EXPLANATION_NULL_FOR_GOOD:  GOOD → null explanation
 * 13. EXPLANATION_NONNULL_BLUNDER: BLUNDER → non-null explanation
 * 14. EXPLANATION_NONNULL_MISTAKE: MISTAKE → non-null explanation
 * 15. EXPLANATION_NONNULL_INACCURACY: INACCURACY → non-null explanation
 * 16. LABEL_NONEMPTY:             all MistakeClassification labels are non-empty
 * 17. MATERIAL_BALANCE_EQUAL:     starting position has equal material
 * 18. MATERIAL_BALANCE_WHITE_ADV: removing a black piece increases white advantage
 * 19. MATERIAL_BALANCE_BLACK_ADV: removing a white piece produces negative balance
 * 20. BUILD_UPDATE_FIELDS:        buildUpdate sets all fields consistently
 * 21. BUILD_UPDATE_FALLBACK_EXPLANATION: GOOD capture → null explanation in update
 * 22. DETERMINISM: identical inputs → identical QuickCoachUpdate
 * 23. CENTIPAWN_NULL: null score → "?"
 * 24. CENTIPAWN_EQUAL: score in -4..4 → "Equal"
 * 25. CENTIPAWN_POSITIVE: positive score → "+N.NN" with two decimal places
 * 26. CENTIPAWN_NEGATIVE: negative score → "-N.NN" with no plus sign
 * 27. CENTIPAWN_BOUNDARY_EXACT: ±5 cp → not "Equal" (outside boundary)
 * 28. CENTIPAWN_PAWN_UNIT: 100 cp → "+1.00"
 * 29. CENTIPAWN_LARGE: 9997 cp (mate) → "+99.97"
 * 30. BUILD_ENGINE_FIELDS: buildUpdateFromEngine sets all fields consistently
 * 31. BUILD_ENGINE_SCORE_REFLECTED: scoreText matches formatCentipawns(engineScore)
 * 32. BUILD_ENGINE_BEST_MOVE: bestMove propagated to QuickCoachUpdate
 * 33. BUILD_ENGINE_NULL_BEST_MOVE: null bestMove allowed
 * 34. BUILD_ENGINE_CLASSIFICATION: classification derived from capturedPiece
 * 35. BUILD_ENGINE_VS_HEURISTIC: engine and heuristic paths produce same classification
 */
class QuickCoachDockTest {

    // ---------------------------------------------------------------------------
    // 1–7  classifyCapture
    // ---------------------------------------------------------------------------

    @Test fun `queen capture is BLUNDER`() {
        assertEquals(MistakeClassification.BLUNDER, QuickCoachLogic.classifyCapture('Q'))
        assertEquals(MistakeClassification.BLUNDER, QuickCoachLogic.classifyCapture('q'))
    }

    @Test fun `rook capture is MISTAKE`() {
        assertEquals(MistakeClassification.MISTAKE, QuickCoachLogic.classifyCapture('R'))
        assertEquals(MistakeClassification.MISTAKE, QuickCoachLogic.classifyCapture('r'))
    }

    @Test fun `bishop capture is MISTAKE`() {
        assertEquals(MistakeClassification.MISTAKE, QuickCoachLogic.classifyCapture('B'))
        assertEquals(MistakeClassification.MISTAKE, QuickCoachLogic.classifyCapture('b'))
    }

    @Test fun `knight capture is MISTAKE`() {
        assertEquals(MistakeClassification.MISTAKE, QuickCoachLogic.classifyCapture('N'))
        assertEquals(MistakeClassification.MISTAKE, QuickCoachLogic.classifyCapture('n'))
    }

    @Test fun `pawn capture is INACCURACY`() {
        assertEquals(MistakeClassification.INACCURACY, QuickCoachLogic.classifyCapture('P'))
        assertEquals(MistakeClassification.INACCURACY, QuickCoachLogic.classifyCapture('p'))
    }

    @Test fun `empty square capture is GOOD`() {
        assertEquals(MistakeClassification.GOOD, QuickCoachLogic.classifyCapture('.'))
    }

    @Test fun `unknown char capture is GOOD`() {
        assertEquals(MistakeClassification.GOOD, QuickCoachLogic.classifyCapture('?'))
        assertEquals(MistakeClassification.GOOD, QuickCoachLogic.classifyCapture(' '))
    }

    // ---------------------------------------------------------------------------
    // 8–11  formatScore
    // ---------------------------------------------------------------------------

    @Test fun `zero balance formats as Equal`() {
        assertEquals("Equal", QuickCoachLogic.formatScore(0.0f))
    }

    @Test fun `positive balance starts with plus sign`() {
        val result = QuickCoachLogic.formatScore(3.0f)
        assertTrue("Expected '+' prefix for positive balance: $result", result.startsWith("+"))
        assertEquals("+3.0", result)
    }

    @Test fun `negative balance has no plus sign`() {
        val result = QuickCoachLogic.formatScore(-3.0f)
        assertFalse("Unexpected '+' in negative score: $result", result.startsWith("+"))
        assertEquals("-3.0", result)
    }

    @Test fun `values within boundary treated as Equal`() {
        assertEquals("Equal", QuickCoachLogic.formatScore(0.04f))
        assertEquals("Equal", QuickCoachLogic.formatScore(-0.04f))
    }

    // ---------------------------------------------------------------------------
    // 12–15  deriveExplanation
    // ---------------------------------------------------------------------------

    @Test fun `GOOD classification produces null explanation`() {
        assertNull(QuickCoachLogic.deriveExplanation(MistakeClassification.GOOD))
    }

    @Test fun `BLUNDER classification produces non-null explanation`() {
        val text = QuickCoachLogic.deriveExplanation(MistakeClassification.BLUNDER)
        assertNotNull(text)
        assertTrue(text!!.isNotBlank())
    }

    @Test fun `MISTAKE classification produces non-null explanation`() {
        val text = QuickCoachLogic.deriveExplanation(MistakeClassification.MISTAKE)
        assertNotNull(text)
        assertTrue(text!!.isNotBlank())
    }

    @Test fun `INACCURACY classification produces non-null explanation`() {
        val text = QuickCoachLogic.deriveExplanation(MistakeClassification.INACCURACY)
        assertNotNull(text)
        assertTrue(text!!.isNotBlank())
    }

    // ---------------------------------------------------------------------------
    // 16  MistakeClassification.label()
    // ---------------------------------------------------------------------------

    @Test fun `all classification labels are non-empty strings`() {
        for (c in MistakeClassification.values()) {
            assertTrue("Empty label for $c", c.label().isNotBlank())
        }
    }

    // ---------------------------------------------------------------------------
    // 17–19  materialBalance
    // ---------------------------------------------------------------------------

    private fun startingBoard(): Array<CharArray> {
        val start = arrayOf(
            "rnbqkbnr",
            "pppppppp",
            "........",
            "........",
            "........",
            "........",
            "PPPPPPPP",
            "RNBQKBNR"
        )
        return Array(8) { r -> CharArray(8) { c -> start[r][c] } }
    }

    @Test fun `starting position has balanced material`() {
        val board = startingBoard()
        assertEquals(0.0f, QuickCoachLogic.materialBalance(board), 0.01f)
    }

    @Test fun `removing a black piece increases white advantage`() {
        val board = startingBoard()
        board[0][3] = '.'  // remove black queen
        val balance = QuickCoachLogic.materialBalance(board)
        assertTrue("Expected white advantage after removing black queen, got $balance", balance > 0)
    }

    @Test fun `removing a white piece produces negative balance`() {
        val board = startingBoard()
        board[7][3] = '.'  // remove white queen
        val balance = QuickCoachLogic.materialBalance(board)
        assertTrue("Expected black advantage after removing white queen, got $balance", balance < 0)
    }

    // ---------------------------------------------------------------------------
    // 20–22  buildUpdate
    // ---------------------------------------------------------------------------

    @Test fun `buildUpdate sets all fields`() {
        val board = startingBoard()
        val update = QuickCoachLogic.buildUpdate('q', board)
        assertNotNull(update.scoreText)
        assertTrue(update.scoreText.isNotBlank())
        assertEquals(MistakeClassification.BLUNDER, update.classification)
        assertNotNull(update.explanation)
    }

    @Test fun `buildUpdate with empty capture gives null explanation`() {
        val board = startingBoard()
        val update = QuickCoachLogic.buildUpdate('.', board)
        assertEquals(MistakeClassification.GOOD, update.classification)
        assertNull(update.explanation)
    }

    @Test fun `identical inputs produce identical QuickCoachUpdate`() {
        val board = startingBoard()
        val u1 = QuickCoachLogic.buildUpdate('r', board)
        val u2 = QuickCoachLogic.buildUpdate('r', board)
        assertEquals(u1, u2)
    }

    // ---------------------------------------------------------------------------
    // 23–29  formatCentipawns
    // ---------------------------------------------------------------------------

    @Test fun `null score formats as question mark`() {
        assertEquals("?", QuickCoachLogic.formatCentipawns(null))
    }

    @Test fun `zero centipawns formats as Equal`() {
        assertEquals("Equal", QuickCoachLogic.formatCentipawns(0))
    }

    @Test fun `score within minus-four to plus-four formats as Equal`() {
        for (cp in -4..4) {
            assertEquals("formatCentipawns($cp) must be Equal", "Equal", QuickCoachLogic.formatCentipawns(cp))
        }
    }

    @Test fun `positive centipawns start with plus sign and two decimals`() {
        val result = QuickCoachLogic.formatCentipawns(152)
        assertTrue("Expected '+' prefix, got: $result", result.startsWith("+"))
        assertEquals("+1.52", result)
    }

    @Test fun `negative centipawns have no plus sign and two decimals`() {
        val result = QuickCoachLogic.formatCentipawns(-80)
        assertFalse("Unexpected '+' in negative score: $result", result.startsWith("+"))
        assertEquals("-0.80", result)
    }

    @Test fun `plus-five centipawns is not Equal`() {
        // ±5 is just outside the Equal boundary (boundary is -4..4)
        assertNotEquals("Equal", QuickCoachLogic.formatCentipawns(5))
        assertNotEquals("Equal", QuickCoachLogic.formatCentipawns(-5))
    }

    @Test fun `one-hundred centipawns formats as plus-one`() {
        assertEquals("+1.00", QuickCoachLogic.formatCentipawns(100))
    }

    @Test fun `mate score 9997 formats with correct pawn units`() {
        // 9997 cp = 99.97 pawns (engine mate representation)
        assertEquals("+99.97", QuickCoachLogic.formatCentipawns(9997))
    }

    // ---------------------------------------------------------------------------
    // 30–35  buildUpdateFromEngine
    // ---------------------------------------------------------------------------

    @Test fun `buildUpdateFromEngine sets all fields`() {
        val update = QuickCoachLogic.buildUpdateFromEngine('q', engineScore = 152, bestMove = "e2e4")
        assertNotNull(update.scoreText)
        assertTrue(update.scoreText.isNotBlank())
        assertEquals(MistakeClassification.BLUNDER, update.classification)
        assertNotNull(update.explanation)
        assertEquals("e2e4", update.bestMove)
    }

    @Test fun `buildUpdateFromEngine scoreText matches formatCentipawns`() {
        val score = -180
        val update = QuickCoachLogic.buildUpdateFromEngine('.', engineScore = score)
        assertEquals(QuickCoachLogic.formatCentipawns(score), update.scoreText)
    }

    @Test fun `buildUpdateFromEngine propagates bestMove`() {
        val update = QuickCoachLogic.buildUpdateFromEngine('.', engineScore = 30, bestMove = "d2d4")
        assertEquals("d2d4", update.bestMove)
    }

    @Test fun `buildUpdateFromEngine accepts null bestMove`() {
        val update = QuickCoachLogic.buildUpdateFromEngine('.', engineScore = 10, bestMove = null)
        assertNull(update.bestMove)
    }

    @Test fun `buildUpdateFromEngine derives classification from captured piece`() {
        assertEquals(
            MistakeClassification.BLUNDER,
            QuickCoachLogic.buildUpdateFromEngine('Q', engineScore = null).classification
        )
        assertEquals(
            MistakeClassification.GOOD,
            QuickCoachLogic.buildUpdateFromEngine('.', engineScore = 50).classification
        )
    }

    @Test fun `engine and heuristic paths produce same classification for same piece`() {
        val board = startingBoard()
        val capturedPiece = 'r'
        val heuristicUpdate = QuickCoachLogic.buildUpdate(capturedPiece, board)
        val engineUpdate = QuickCoachLogic.buildUpdateFromEngine(capturedPiece, engineScore = -50)
        assertEquals(heuristicUpdate.classification, engineUpdate.classification)
    }
}
