package ai.chesscoach.app

import android.util.Log

class ChessEngine {

    companion object {
        init {
            try {
                // ✅ Correct: load without "lib" prefix and without ".so" extension
                System.loadLibrary("chessengine")
                Log.d("AI_TEST", "✅ Native library 'chessengine' loaded successfully")
            } catch (e: UnsatisfiedLinkError) {
                Log.e("AI_TEST", "❌ Failed to load native library 'chessengine'", e)
            }
        }
    }

    // Uppercase = White, lowercase = Black
    var board: Array<CharArray> = arrayOf(
        charArrayOf('r','n','b','q','k','b','n','r'),
        charArrayOf('p','p','p','p','p','p','p','p'),
        charArrayOf('.','.','.','.','.','.','.','.'),
        charArrayOf('.','.','.','.','.','.','.','.'),
        charArrayOf('.','.','.','.','.','.','.','.'),
        charArrayOf('.','.','.','.','.','.','.','.'),
        charArrayOf('P','P','P','P','P','P','P','P'),
        charArrayOf('R','N','B','Q','K','B','N','R')
    )

    var whiteTurn = true

    fun isWhite(piece: Char) = piece.isUpperCase()
    fun isBlack(piece: Char) = piece.isLowerCase()

    fun move(fromRow: Int, fromCol: Int, toRow: Int, toCol: Int): Boolean {
        if (fromRow !in 0..7 || fromCol !in 0..7 || toRow !in 0..7 || toCol !in 0..7) return false
        val piece = board[fromRow][fromCol]
        if (piece == '.') return false

        // Turn check
        if (whiteTurn && isBlack(piece)) return false
        if (!whiteTurn && isWhite(piece)) return false

        if (!isLegalMove(piece, fromRow, fromCol, toRow, toCol)) return false

        // Perform move
        board[toRow][toCol] = piece
        board[fromRow][fromCol] = '.'
        whiteTurn = !whiteTurn
        return true
    }

    fun exportBoardString(): String {
        val sb = StringBuilder(64)
        for (r in 0..7)
            for (c in 0..7)
                sb.append(board[r][c])
        return sb.toString()
    }

    fun applyMoveString(move: String): Boolean {
        // "e2e4"
        if (move.length < 4) return false
        val fc = move[0] - 'a'
        val fr = 8 - (move[1] - '0')
        val tc = move[2] - 'a'
        val tr = 8 - (move[3] - '0')
        return move(fr, fc, tr, tc)
    }

    private fun isLegalMove(
        piece: Char,
        fr: Int, fc: Int,
        tr: Int, tc: Int
    ): Boolean {
        val target = board[tr][tc]
        if (target != '.' && isWhite(target) == isWhite(piece)) return false

        return when (piece.lowercaseChar()) {
            'p' -> pawnMove(piece, fr, fc, tr, tc)
            'r' -> straightMove(fr, fc, tr, tc)
            'b' -> diagonalMove(fr, fc, tr, tc)
            'q' -> straightMove(fr, fc, tr, tc) || diagonalMove(fr, fc, tr, tc)
            'n' -> knightMove(fr, fc, tr, tc)
            'k' -> kingMove(fr, fc, tr, tc)
            else -> false
        }
    }

    private fun pawnMove(p: Char, fr: Int, fc: Int, tr: Int, tc: Int): Boolean {
        val dir = if (isWhite(p)) -1 else 1
        val startRow = if (isWhite(p)) 6 else 1

        // Forward
        if (fc == tc && board[tr][tc] == '.') {
            if (tr == fr + dir) return true
            if (fr == startRow && tr == fr + 2 * dir && board[fr + dir][fc] == '.') return true
        }

        // Capture
        if (kotlin.math.abs(tc - fc) == 1 && tr == fr + dir) {
            val target = board[tr][tc]
            if (target != '.' && isWhite(target) != isWhite(p)) return true
        }
        return false
    }

    private fun straightMove(fr: Int, fc: Int, tr: Int, tc: Int): Boolean {
        if (fr != tr && fc != tc) return false
        return pathClear(fr, fc, tr, tc)
    }

    private fun diagonalMove(fr: Int, fc: Int, tr: Int, tc: Int): Boolean {
        if (kotlin.math.abs(fr - tr) != kotlin.math.abs(fc - tc)) return false
        return pathClear(fr, fc, tr, tc)
    }

    private fun knightMove(fr: Int, fc: Int, tr: Int, tc: Int): Boolean {
        val dr = kotlin.math.abs(fr - tr)
        val dc = kotlin.math.abs(fc - tc)
        return (dr == 2 && dc == 1) || (dr == 1 && dc == 2)
    }

    private fun kingMove(fr: Int, fc: Int, tr: Int, tc: Int): Boolean {
        return kotlin.math.abs(fr - tr) <= 1 && kotlin.math.abs(fc - tc) <= 1
    }

    private fun pathClear(fr: Int, fc: Int, tr: Int, tc: Int): Boolean {
        val dr = (tr - fr).coerceIn(-1, 1)
        val dc = (tc - fc).coerceIn(-1, 1)

        var r = fr + dr
        var c = fc + dc
        while (r != tr || c != tc) {
            if (board[r][c] != '.') return false
            r += dr
            c += dc
        }
        return true
    }
}
