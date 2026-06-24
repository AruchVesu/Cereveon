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

    // ── Castling / en-passant: the native engine emits these as bare king /
    // pawn moves with no special flag.  Before the bridge recognised their
    // shapes, normalize() returned null and the engine silently "skipped" its
    // reply.  See the engine-move-drop fix. ────────────────────────────────

    @Test
    fun `normalize keeps a black kingside castle`() {
        // Black king e8 -> g8, rook h8, f8/g8 empty.
        val fen = "4k2r/8/8/8/8/8/8/4K3 b k - 0 1"
        val rawMove = AIMove(0, 4, 0, 6)

        val normalized = JniMoveBridge.normalize(rawMove, fen)

        assertEquals(AIMove(0, 4, 0, 6), normalized)
    }

    @Test
    fun `normalize keeps a black queenside castle`() {
        // Black king e8 -> c8, rook a8, b8/c8/d8 empty.
        val fen = "r3k3/8/8/8/8/8/8/4K3 b q - 0 1"
        val rawMove = AIMove(0, 4, 0, 2)

        val normalized = JniMoveBridge.normalize(rawMove, fen)

        assertEquals(AIMove(0, 4, 0, 2), normalized)
    }

    @Test
    fun `normalize keeps a black en passant capture`() {
        // Black d4 pawn captures a white e4 pawn that just double-stepped;
        // EP target e3.  Diagonal move onto an empty square.
        val fen = "4k3/8/8/8/3pP3/8/8/4K3 b - e3 0 1"
        val rawMove = AIMove(4, 3, 5, 4)

        val normalized = JniMoveBridge.normalize(rawMove, fen)

        assertEquals(AIMove(4, 3, 5, 4), normalized)
    }

    @Test
    fun `normalize rejects a 2-square king move with no rook to castle`() {
        // King "castles" but there is no rook on the corner — not a real castle,
        // and no other transform is a legal 1-square king move.
        val fen = "4k3/8/8/8/8/8/8/4K3 b - - 0 1"
        val rawMove = AIMove(0, 4, 0, 6)

        val normalized = JniMoveBridge.normalize(rawMove, fen)

        assertNull(normalized)
    }
}
