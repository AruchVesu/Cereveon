package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class JniMoveBridgeTest {
    private val blackToMoveAfterE4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b"

    @Test
    fun `normalize keeps an already legal move`() {
        val rawMove = AIMove(1, 4, 3, 4)

        val normalized = JniMoveBridge.normalize(rawMove, blackToMoveAfterE4)

        assertEquals(rawMove, normalized)
    }

    @Test
    fun `normalize fixes swapped row and column encoding`() {
        val rawMove = AIMove(4, 1, 4, 3)

        val normalized = JniMoveBridge.normalize(rawMove, blackToMoveAfterE4)

        assertEquals(AIMove(1, 4, 3, 4), normalized)
    }

    @Test
    fun `normalize fixes vertically flipped rows`() {
        val rawMove = AIMove(6, 4, 4, 4)

        val normalized = JniMoveBridge.normalize(rawMove, blackToMoveAfterE4)

        assertEquals(AIMove(1, 4, 3, 4), normalized)
    }

    @Test
    fun `normalize rejects an impossible move`() {
        val rawMove = AIMove(7, 7, 7, 5)

        val normalized = JniMoveBridge.normalize(rawMove, blackToMoveAfterE4)

        assertNull(normalized)
    }
}
