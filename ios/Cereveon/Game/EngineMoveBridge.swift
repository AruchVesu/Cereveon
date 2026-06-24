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
/// port of Android's `JniMoveBridge`. The legality check is simplified (it only
/// has to disambiguate the coordinate frame; `ChessGame.applyAIMove` re-validates
/// and executes the move), but it DOES recognise castling (a bare 2-square king
/// move) and en passant (a diagonal pawn move onto the FEN en-passant square):
/// the engine emits both with no special flag, and dropping them made the engine
/// silently skip its reply. Kept in lock-step with Android's checker so both
/// platforms select the same transform.
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
    /// frame. Verbatim port of `JniMoveBridge.Position`; recognises castling and
    /// en-passant move shapes so those replies aren't dropped (full legality is
    /// re-checked by `ChessGame.applyAIMove`).
    private final class Position {
        private var board: [[Character]]
        private let whiteToMove: Bool
        private let enPassant: (row: Int, col: Int)?

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
            // FEN field 4 (index 3) is the en-passant target ("-" or e.g. "e3").
            // Absent on short placement-only FENs, which disables EP recognition.
            enPassant = parts.count > 3 ? Self.square(algebraic: parts[3]) : nil
        }

        /// Parse a FEN square ("e3") to (row, col), row 0 == rank 8.  Returns
        /// nil for "-" or malformed input.
        private static func square(algebraic s: Substring) -> (row: Int, col: Int)? {
            let chars = Array(s)
            guard chars.count == 2,
                  let fileByte = chars[0].asciiValue, fileByte >= 97, fileByte <= 104,
                  let rank = chars[1].wholeNumberValue, rank >= 1, rank <= 8
            else { return nil }
            return (8 - rank, Int(fileByte) - 97)
        }

        func isLegal(_ move: AIMove) -> Bool {
            guard move.isValid else { return false }
            let fr = move.fromX, fc = move.fromY, tr = move.toX, tc = move.toY
            guard (0...7).contains(fr), (0...7).contains(fc),
                  (0...7).contains(tr), (0...7).contains(tc) else { return false }
            let piece = board[fr][fc]
            if piece == "." || piece.isUppercase != whiteToMove { return false }
            if !isLegalGeometry(piece, fr, fc, tr, tc, allowCastle: true) { return false }
            let target = board[tr][tc]
            board[tr][tc] = piece
            board[fr][fc] = "."
            let leavesKingInCheck = isInCheck(piece.isUppercase)
            board[fr][fc] = piece
            board[tr][tc] = target
            return !leavesKingInCheck
        }

        // `allowCastle` is true only on the actual move being normalised; the
        // attack-detection path (isSquareAttacked) passes false so a king never
        // counts as "attacking" its 2-square castle-target square.
        private func isLegalGeometry(
            _ piece: Character, _ fr: Int, _ fc: Int, _ tr: Int, _ tc: Int, allowCastle: Bool
        ) -> Bool {
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
                // En passant: diagonal onto the empty FEN en-passant square.
                if dc == 1, tr == fr + dir, target == ".",
                   let ep = enPassant, ep.row == tr, ep.col == tc { return true }
                return false
            case "r": return (fr == tr || fc == tc) && pathClear(fr, fc, tr, tc)
            case "n": return (dr == 2 && dc == 1) || (dr == 1 && dc == 2)
            case "b": return dr == dc && pathClear(fr, fc, tr, tc)
            case "q": return (dr == dc || fr == tr || fc == tc) && pathClear(fr, fc, tr, tc)
            // Castling: a bare 2-square king move (engine sends no castle flag).
            case "k": return (dr <= 1 && dc <= 1)
                || (allowCastle && dr == 0 && dc == 2 && isCastleShape(piece, fr, fc, tc))
            default: return false
            }
        }

        /// A 2-square king move that lands as a standard castle on this board:
        /// a same-coloured rook on the corner toward `destCol`, with empty
        /// squares between king and rook.  Disambiguates the coordinate frame
        /// only — `ChessGame.applyAIMove` re-checks rights / through-check.
        private func isCastleShape(_ king: Character, _ row: Int, _ kingCol: Int, _ destCol: Int) -> Bool {
            let rook: Character = king.isUppercase ? "R" : "r"
            let rookCol = destCol > kingCol ? 7 : 0
            if board[row][rookCol] != rook { return false }
            return pathClear(row, kingCol, row, rookCol)
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
                    if p != "." && p.isUppercase == byWhite
                        && isLegalGeometry(p, r, c, row, col, allowCastle: false) {
                        return true
                    }
                }
            }
            return false
        }
    }
}
