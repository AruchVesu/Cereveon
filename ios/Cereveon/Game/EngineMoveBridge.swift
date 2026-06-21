import Foundation

extension AIMove {
    /// The native engine encodes X = board row, Y = board col (see `CRVAIMove`
    /// and the C++ `Move` struct). These expose that as board `Square`s.
    var isValid: Bool { fromX >= 0 }
    var fromSquare: Square { Square(row: fromX, col: fromY) }
    var toSquare: Square { Square(row: toX, col: toY) }
}

/// Maps the native engine's raw move coordinates onto the actual board.
///
/// `CereveonEngine` returns a move in the engine's own frame (it computes Black's
/// move regardless of the side to move). This tries all 8 board symmetries and
/// returns the first transform that is legal in the FEN position — a verbatim
/// port of Android's `JniMoveBridge`. The legality check here is deliberately
/// simplified (no castling / en-passant): the engine only ever returns ordinary
/// piece moves, and matching Android's checker exactly keeps iOS and Android
/// selecting the same transform.
enum EngineMoveBridge {

    static func normalize(_ move: AIMove, fen: String) -> AIMove? {
        guard let position = Position(fen: fen) else { return move.isValid ? move : nil }
        guard move.isValid else { return nil }

        // Same iteration order as Android (identity first), returning the first
        // legal transform.
        for swapAxes in [false, true] {
            for flipRows in [false, true] {
                for flipCols in [false, true] {
                    let candidate = transform(move, swapAxes: swapAxes, flipRows: flipRows, flipCols: flipCols)
                    if position.isLegal(candidate) { return candidate }
                }
            }
        }
        return nil
    }

    private static func transform(_ move: AIMove, swapAxes: Bool, flipRows: Bool, flipCols: Bool) -> AIMove {
        func mapSquare(_ row: Int, _ col: Int) -> (Int, Int) {
            var r = row, c = col
            if swapAxes { r = col; c = row }
            if flipRows { r = 7 - r }
            if flipCols { c = 7 - c }
            return (r, c)
        }
        let (fr, fc) = mapSquare(move.fromX, move.fromY)
        let (tr, tc) = mapSquare(move.toX, move.toY)
        return AIMove(fromX: fr, fromY: fc, toX: tr, toY: tc, promotion: move.promotion)
    }

    /// Simplified position used only to disambiguate the engine's coordinate
    /// frame. Verbatim port of `JniMoveBridge.Position` (no castling/en-passant).
    private final class Position {
        private var board: [[Character]]
        private let whiteToMove: Bool

        init?(fen: String) {
            let parts = fen.trimmingCharacters(in: .whitespaces)
                .split(separator: " ", omittingEmptySubsequences: false)
            guard let placement = parts.first else { return nil }
            let rows = placement.split(separator: "/", omittingEmptySubsequences: false)
            guard rows.count == 8 else { return nil }
            var b = Array(repeating: Array(repeating: Character("."), count: 8), count: 8)
            for (r, rowStr) in rows.enumerated() {
                var c = 0
                for ch in rowStr {
                    if ch.isNumber, let n = ch.wholeNumberValue {
                        c += n
                    } else {
                        guard (0...7).contains(c) else { return nil }
                        b[r][c] = ch
                        c += 1
                    }
                }
                guard c == 8 else { return nil }
            }
            board = b
            whiteToMove = parts.count > 1 ? parts[1].lowercased() == "w" : false
        }

        func isLegal(_ move: AIMove) -> Bool {
            guard move.isValid else { return false }
            let fr = move.fromX, fc = move.fromY, tr = move.toX, tc = move.toY
            guard (0...7).contains(fr), (0...7).contains(fc),
                  (0...7).contains(tr), (0...7).contains(tc) else { return false }
            let piece = board[fr][fc]
            if piece == "." || piece.isUppercase != whiteToMove { return false }
            if !isLegalGeometry(piece, fr, fc, tr, tc) { return false }
            let target = board[tr][tc]
            board[tr][tc] = piece
            board[fr][fc] = "."
            let leavesKingInCheck = isInCheck(piece.isUppercase)
            board[fr][fc] = piece
            board[tr][tc] = target
            return !leavesKingInCheck
        }

        private func isLegalGeometry(_ piece: Character, _ fr: Int, _ fc: Int, _ tr: Int, _ tc: Int) -> Bool {
            if fr == tr && fc == tc { return false }
            let target = board[tr][tc]
            if target != "." && target.isUppercase == piece.isUppercase { return false }
            let dr = abs(tr - fr), dc = abs(tc - fc)
            switch piece.lowercased() {
            case "p":
                let dir = piece.isUppercase ? -1 : 1
                let startRow = piece.isUppercase ? 6 : 1
                if fc == tc && tr == fr + dir && target == "." { return true }
                if fc == tc && fr == startRow && tr == fr + 2 * dir && target == "."
                    && board[fr + dir][fc] == "." { return true }
                if dc == 1 && tr == fr + dir && target != "." && target.isUppercase != piece.isUppercase { return true }
                return false
            case "r": return (fr == tr || fc == tc) && pathClear(fr, fc, tr, tc)
            case "n": return (dr == 2 && dc == 1) || (dr == 1 && dc == 2)
            case "b": return dr == dc && pathClear(fr, fc, tr, tc)
            case "q": return (dr == dc || fr == tr || fc == tc) && pathClear(fr, fc, tr, tc)
            case "k": return dr <= 1 && dc <= 1
            default: return false
            }
        }

        private func pathClear(_ fr: Int, _ fc: Int, _ tr: Int, _ tc: Int) -> Bool {
            let dr = max(-1, min(1, tr - fr))
            let dc = max(-1, min(1, tc - fc))
            var r = fr + dr, c = fc + dc
            while r != tr || c != tc {
                if board[r][c] != "." { return false }
                r += dr; c += dc
            }
            return true
        }

        private func isInCheck(_ white: Bool) -> Bool {
            let king: Character = white ? "K" : "k"
            var kr = -1, kc = -1
            outer: for r in 0...7 {
                for c in 0...7 where board[r][c] == king { kr = r; kc = c; break outer }
            }
            if kr == -1 { return false }
            return isSquareAttacked(kr, kc, !white)
        }

        private func isSquareAttacked(_ row: Int, _ col: Int, _ byWhite: Bool) -> Bool {
            for r in 0...7 {
                for c in 0...7 {
                    let p = board[r][c]
                    if p != "." && p.isUppercase == byWhite && isLegalGeometry(p, r, c, row, col) {
                        return true
                    }
                }
            }
            return false
        }
    }
}
