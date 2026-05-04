package ai.chesscoach.app

internal object JniMoveBridge {
    fun normalize(move: AIMove, fen: String): AIMove? {
        val position = Position.fromFen(fen) ?: return move.takeIf { it.isValid() }
        if (!move.isValid()) return null

        val candidates = linkedSetOf<AIMove>()
        for (swapAxes in listOf(false, true)) {
            for (flipRows in listOf(false, true)) {
                for (flipCols in listOf(false, true)) {
                    candidates += move.transform(swapAxes, flipRows, flipCols)
                }
            }
        }

        return candidates.firstOrNull(position::isLegal)
    }

    private fun AIMove.transform(swapAxes: Boolean, flipRows: Boolean, flipCols: Boolean): AIMove {
        fun mapSquare(row: Int, col: Int): Pair<Int, Int> {
            var mappedRow = row
            var mappedCol = col
            if (swapAxes) {
                mappedRow = col
                mappedCol = row
            }
            if (flipRows) mappedRow = 7 - mappedRow
            if (flipCols) mappedCol = 7 - mappedCol
            return mappedRow to mappedCol
        }

        val (mappedFr, mappedFc) = mapSquare(fr, fc)
        val (mappedTr, mappedTc) = mapSquare(tr, tc)
        return AIMove(mappedFr, mappedFc, mappedTr, mappedTc)
    }

    private class Position(
        private val board: Array<CharArray>,
        private val whiteToMove: Boolean
    ) {
        fun isLegal(move: AIMove): Boolean {
            if (!move.isValid()) return false
            if (move.fr !in 0..7 || move.fc !in 0..7 || move.tr !in 0..7 || move.tc !in 0..7) {
                return false
            }

            val piece = board[move.fr][move.fc]
            if (piece == '.' || piece.isUpperCase() != whiteToMove) return false
            if (!isLegalGeometry(piece, move.fr, move.fc, move.tr, move.tc)) return false

            val target = board[move.tr][move.tc]
            board[move.tr][move.tc] = piece
            board[move.fr][move.fc] = '.'
            val leavesKingInCheck = isInCheck(piece.isUpperCase())
            board[move.fr][move.fc] = piece
            board[move.tr][move.tc] = target
            return !leavesKingInCheck
        }

        private fun isLegalGeometry(piece: Char, fr: Int, fc: Int, tr: Int, tc: Int): Boolean {
            if (fr == tr && fc == tc) return false
            val target = board[tr][tc]
            if (target != '.' && target.isUpperCase() == piece.isUpperCase()) return false

            val dr = kotlin.math.abs(tr - fr)
            val dc = kotlin.math.abs(tc - fc)
            return when (piece.lowercaseChar()) {
                'p' -> {
                    val dir = if (piece.isUpperCase()) -1 else 1
                    val startRow = if (piece.isUpperCase()) 6 else 1
                    when {
                        fc == tc && tr == fr + dir && target == '.' -> true
                        fc == tc &&
                            fr == startRow &&
                            tr == fr + 2 * dir &&
                            target == '.' &&
                            board[fr + dir][fc] == '.' -> true
                        dc == 1 && tr == fr + dir && target != '.' && target.isUpperCase() != piece.isUpperCase() -> true
                        else -> false
                    }
                }
                'r' -> (fr == tr || fc == tc) && pathClear(fr, fc, tr, tc)
                'n' -> (dr == 2 && dc == 1) || (dr == 1 && dc == 2)
                'b' -> dr == dc && pathClear(fr, fc, tr, tc)
                'q' -> (dr == dc || fr == tr || fc == tc) && pathClear(fr, fc, tr, tc)
                'k' -> dr <= 1 && dc <= 1
                else -> false
            }
        }

        private fun pathClear(fr: Int, fc: Int, tr: Int, tc: Int): Boolean {
            val dr = (tr - fr).coerceIn(-1, 1)
            val dc = (tc - fc).coerceIn(-1, 1)
            var row = fr + dr
            var col = fc + dc
            while (row != tr || col != tc) {
                if (board[row][col] != '.') return false
                row += dr
                col += dc
            }
            return true
        }

        private fun isInCheck(white: Boolean): Boolean {
            val king = if (white) 'K' else 'k'
            var kingRow = -1
            var kingCol = -1
            loop@ for (row in 0..7) {
                for (col in 0..7) {
                    if (board[row][col] == king) {
                        kingRow = row
                        kingCol = col
                        break@loop
                    }
                }
            }
            if (kingRow == -1) return false
            return isSquareAttacked(kingRow, kingCol, !white)
        }

        private fun isSquareAttacked(row: Int, col: Int, byWhite: Boolean): Boolean {
            for (sourceRow in 0..7) {
                for (sourceCol in 0..7) {
                    val piece = board[sourceRow][sourceCol]
                    if (piece != '.' && piece.isUpperCase() == byWhite) {
                        if (isLegalGeometry(piece, sourceRow, sourceCol, row, col)) return true
                    }
                }
            }
            return false
        }

        companion object {
            fun fromFen(fen: String): Position? {
                val parts = fen.trim().split(" ")
                if (parts.isEmpty()) return null
                val rows = parts[0].split("/")
                if (rows.size != 8) return null

                val board = Array(8) { CharArray(8) { '.' } }
                for (row in rows.indices) {
                    var col = 0
                    for (symbol in rows[row]) {
                        if (symbol.isDigit()) {
                            col += symbol.digitToInt()
                        } else {
                            if (col !in 0..7) return null
                            board[row][col] = symbol
                            col++
                        }
                    }
                    if (col != 8) return null
                }

                val whiteToMove = parts.getOrNull(1)?.equals("w", ignoreCase = true) ?: false
                return Position(board, whiteToMove)
            }
        }
    }
}
