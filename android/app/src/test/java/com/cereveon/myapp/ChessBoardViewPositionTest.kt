package com.cereveon.myapp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Pure-Kotlin tests for [ChessBoardView]'s FEN-field parsers — the
 * companions behind [ChessBoardView.loadPosition], the full state
 * re-seed the puzzle/drill surfaces use.  The view itself can't run on
 * the host JVM, but the companions are plain functions (same pattern as
 * [BoardStyleContractTest]).
 *
 * These pin the fix for the trainer freeze: a board reused across
 * puzzles inherited the previous position's game-over latch, spent
 * castling flags, and en-passant target because bare ``setFEN`` never
 * re-derived them.  ``loadPosition`` re-derives castling + en passant
 * from the FEN via these parsers (and clears the latch/history, which
 * only an instrumented test can observe directly).
 *
 * Invariants pinned
 * -----------------
 *  1. CASTLING_FULL_RIGHTS      "KQkq" → nothing marked moved.
 *  2. CASTLING_NO_RIGHTS        "-" → everything marked moved (rights denied).
 *  3. CASTLING_PARTIAL          each letter maps to exactly its king+rook pair.
 *  4. CASTLING_ABSENT_FIELD     a bare "<board> <side>" FEN reads as no rights
 *                               (defensive; puzzle callers pass 6-field FENs).
 *  5. EP_SQUARE                 "e3"/"c6" map to the board's (row, col) frame.
 *  6. EP_NONE                   "-", absent field, malformed → null.
 */
class ChessBoardViewPositionTest {

    private fun fenWithFields(castling: String, ep: String): String =
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w $castling $ep 0 1"

    @Test
    fun `CASTLING_FULL_RIGHTS - KQkq leaves every flag unmoved`() {
        val flags = ChessBoardView.parseCastlingFlags(fenWithFields("KQkq", "-"))
        assertEquals(
            ChessBoardView.CastlingFlags(
                whiteKingMoved = false,
                blackKingMoved = false,
                whiteRookAMoved = false,
                whiteRookHMoved = false,
                blackRookAMoved = false,
                blackRookHMoved = false,
            ),
            flags,
        )
    }

    @Test
    fun `CASTLING_NO_RIGHTS - dash denies castling everywhere`() {
        val flags = ChessBoardView.parseCastlingFlags(fenWithFields("-", "-"))
        assertEquals(
            ChessBoardView.CastlingFlags(
                whiteKingMoved = true,
                blackKingMoved = true,
                whiteRookAMoved = true,
                whiteRookHMoved = true,
                blackRookAMoved = true,
                blackRookHMoved = true,
            ),
            flags,
        )
    }

    @Test
    fun `CASTLING_PARTIAL - each letter grants exactly its pair`() {
        // Only White short: white king + h-rook unmoved, everything else moved.
        val kOnly = ChessBoardView.parseCastlingFlags(fenWithFields("K", "-"))
        assertEquals(false, kOnly.whiteKingMoved)
        assertEquals(false, kOnly.whiteRookHMoved)
        assertEquals(true, kOnly.whiteRookAMoved)
        assertEquals(true, kOnly.blackKingMoved)
        assertEquals(true, kOnly.blackRookAMoved)
        assertEquals(true, kOnly.blackRookHMoved)

        // Only Black long: black king + a-rook unmoved.
        val qOnly = ChessBoardView.parseCastlingFlags(fenWithFields("q", "-"))
        assertEquals(false, qOnly.blackKingMoved)
        assertEquals(false, qOnly.blackRookAMoved)
        assertEquals(true, qOnly.blackRookHMoved)
        assertEquals(true, qOnly.whiteKingMoved)

        // Both White rights, no Black: the black pair reads moved.
        val kq = ChessBoardView.parseCastlingFlags(fenWithFields("KQ", "-"))
        assertEquals(false, kq.whiteKingMoved)
        assertEquals(false, kq.whiteRookAMoved)
        assertEquals(false, kq.whiteRookHMoved)
        assertEquals(true, kq.blackKingMoved)
    }

    @Test
    fun `CASTLING_ABSENT_FIELD - short FEN reads as no rights`() {
        val flags = ChessBoardView.parseCastlingFlags(
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w",
        )
        assertEquals(true, flags.whiteKingMoved)
        assertEquals(true, flags.blackKingMoved)
    }

    @Test
    fun `EP_SQUARE - algebraic target maps to the board frame`() {
        // e3 = col 4 ('e'), row 5 (8 - 3) — the square behind a white
        // double push.
        assertEquals(
            5 to 4,
            ChessBoardView.parseEnPassantTarget(fenWithFields("KQkq", "e3")),
        )
        // c6 = col 2, row 2 — behind a black double push.
        assertEquals(
            2 to 2,
            ChessBoardView.parseEnPassantTarget(fenWithFields("KQkq", "c6")),
        )
    }

    @Test
    fun `EP_NONE - dash, absent, or malformed field yields null`() {
        assertNull(ChessBoardView.parseEnPassantTarget(fenWithFields("KQkq", "-")))
        assertNull(
            ChessBoardView.parseEnPassantTarget(
                "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq",
            ),
        )
        assertNull(ChessBoardView.parseEnPassantTarget(fenWithFields("KQkq", "z9")))
        assertNull(ChessBoardView.parseEnPassantTarget(fenWithFields("KQkq", "e33")))
    }
}
