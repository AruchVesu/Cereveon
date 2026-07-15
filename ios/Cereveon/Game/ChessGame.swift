import Foundation

enum MoveResult: Equatable { case success, promotion, failed }

enum GameResult: Equatable { case whiteWins, blackWins, draw }

/// Board coordinate. row 0 = rank 8 (top, Black's back rank), row 7 = rank 1
/// (White's back rank); col 0 = a-file … col 7 = h-file. Mirrors the Android
/// `ChessBoardView` `board[row][col]` convention exactly.
struct Square: Equatable, Hashable {
    let row: Int
    let col: Int

    var isOnBoard: Bool { (0...7).contains(row) && (0...7).contains(col) }

    /// Algebraic name, e.g. (row 6, col 4) -> "e2".
    var algebraic: String {
        let file = Character(UnicodeScalar(UInt8(97 + col)))
        return "\(file)\(8 - row)"
    }
}

/// Chess rules + game state, ported from the logic half of Android's
/// `ChessBoardView` (rendering lives in the SwiftUI board view). Pieces are FEN
/// characters: uppercase = White, lowercase = Black, "." = empty. A reference
/// type because `isLegal` make/unmakes the board in place; the owning view-model
/// signals SwiftUI after each mutation.
final class ChessGame {

    private(set) var board: [[Character]] = ChessGame.startingBoard()
    private(set) var whiteToMove = true
    private(set) var gameOver = false
    private(set) var lastMoveFrom: Square?
    private(set) var lastMoveTo: Square?

    private var enPassantTarget: Square?
    private var whiteKingMoved = false, blackKingMoved = false
    private var whiteRookAMoved = false, whiteRookHMoved = false
    private var blackRookAMoved = false, blackRookHMoved = false
    private var pendingResult: GameResult?

    private struct MoveRecord {
        let from: Square, to: Square
        let piece: Character, captured: Character
        let epTarget: Square?
        let wKM, bKM, wRAM, wRHM, bRAM, bRHM: Bool
    }
    private var history: [MoveRecord] = []

    /// Half-moves played since the last `reset` (human + AI).
    var moveCount: Int { history.count }

    init() {}

    private static func startingBoard() -> [[Character]] {
        ["rnbqkbnr", "pppppppp", "........", "........",
         "........", "........", "PPPPPPPP", "RNBQKBNR"].map(Array.init)
    }

    func piece(at square: Square) -> Character { board[square.row][square.col] }

    func reset() {
        board = ChessGame.startingBoard()
        whiteToMove = true
        gameOver = false
        enPassantTarget = nil
        lastMoveFrom = nil; lastMoveTo = nil
        whiteKingMoved = false; blackKingMoved = false
        whiteRookAMoved = false; whiteRookHMoved = false
        blackRookAMoved = false; blackRookHMoved = false
        pendingResult = nil
        history.removeAll()
    }

    /// Load board + side to move from a FEN. Mirrors Android `setFEN`: only the
    /// piece placement and active colour are read (castling rights / en-passant /
    /// clocks are NOT restored — used for display & replay, not for resuming
    /// interactive play, which is rebuilt by replaying the move list).
    func load(fen: String) {
        let parts = fen.split(separator: " ", omittingEmptySubsequences: false).map(String.init)
        guard let placement = parts.first else { return }
        var newBoard = Array(repeating: Array(repeating: Character("."), count: 8), count: 8)
        for (r, rowStr) in placement.split(separator: "/", omittingEmptySubsequences: false).enumerated() where r < 8 {
            var c = 0
            for ch in rowStr {
                if ch.isNumber, let empty = ch.wholeNumberValue {
                    for _ in 0..<empty where c < 8 { newBoard[r][c] = "."; c += 1 }
                } else if c < 8 {
                    newBoard[r][c] = ch; c += 1
                }
            }
        }
        board = newBoard
        if parts.count > 1 { whiteToMove = (parts[1] == "w") }
        lastMoveFrom = nil; lastMoveTo = nil
    }

    /// Full 6-field FEN. Ported verbatim from Android `exportFEN` so the server's
    /// strict 6-field validator accepts it (a malformed FEN silently degrades to
    /// the "coach offline" fallback). Castling rights also require the king/rook
    /// to still stand on home squares (a rook can be captured without "moving").
    func exportFEN() -> String {
        let placement = board.map { row -> String in
            var empty = 0
            var out = ""
            for ch in row {
                if ch == "." { empty += 1 }
                else {
                    if empty > 0 { out += String(empty); empty = 0 }
                    out.append(ch)
                }
            }
            if empty > 0 { out += String(empty) }
            return out
        }.joined(separator: "/")

        let side = whiteToMove ? "w" : "b"

        var castling = ""
        if !whiteKingMoved && !whiteRookHMoved && board[7][4] == "K" && board[7][7] == "R" { castling += "K" }
        if !whiteKingMoved && !whiteRookAMoved && board[7][4] == "K" && board[7][0] == "R" { castling += "Q" }
        if !blackKingMoved && !blackRookHMoved && board[0][4] == "k" && board[0][7] == "r" { castling += "k" }
        if !blackKingMoved && !blackRookAMoved && board[0][4] == "k" && board[0][0] == "r" { castling += "q" }
        if castling.isEmpty { castling = "-" }

        let enPassant = enPassantTarget?.algebraic ?? "-"

        var halfmove = 0
        for rec in history.reversed() {
            if rec.piece.lowercased() == "p" || rec.captured != "." { break }
            halfmove += 1
        }
        let fullmove = history.count / 2 + 1
        return "\(placement) \(side) \(castling) \(enPassant) \(halfmove) \(fullmove)"
    }

    // MARK: - Move application

    /// Apply a human move. `.failed` if illegal; `.promotion` when a pawn reaches
    /// the last rank (the turn is NOT flipped — the caller completes it via
    /// `promote(at:to:)`); otherwise `.success`.
    @discardableResult
    func move(from: Square, to: Square) -> MoveResult {
        guard !gameOver, isLegal(from: from, to: to) else { return .failed }
        let piece = board[from.row][from.col]
        let isPromotion = piece.lowercased() == "p" && (to.row == 0 || to.row == 7)
        executeMove(from: from, to: to)
        if isPromotion { return .promotion }
        whiteToMove.toggle()
        checkAndRecordGameOver()
        return .success
    }

    /// Apply an engine move (already coordinate-normalised by `EngineMoveBridge`).
    /// Returns the captured piece ("." if none), or nil if the move is illegal /
    /// out of bounds.
    @discardableResult
    func applyAIMove(from: Square, to: Square) -> Character? {
        guard from.isOnBoard, to.isOnBoard, isLegal(from: from, to: to) else { return nil }
        let captured = board[to.row][to.col]
        executeMove(from: from, to: to)
        whiteToMove.toggle()
        checkAndRecordGameOver()
        return captured
    }

    /// Complete a pawn promotion (the pawn keeps its colour). `kind` is one of
    /// q/r/b/n (any case).
    func promote(at square: Square, to kind: Character) {
        let pawn = board[square.row][square.col]
        board[square.row][square.col] = pawn.isUppercase
            ? Character(kind.uppercased())
            : Character(kind.lowercased())
        whiteToMove.toggle()
        // A promotion can deliver mate, so re-check game-over after the piece
        // is placed.  (Android matches this since PR #394 — the "Android
        // omits this" note that used to live here is stale.)
        checkAndRecordGameOver()
    }

    /// Undo the last half-move. Returns the side to move afterwards, or nil if
    /// there was nothing to undo.
    @discardableResult
    func undo() -> Bool? {
        guard let last = history.popLast() else { return nil }
        board[last.from.row][last.from.col] = last.piece
        board[last.to.row][last.to.col] = last.captured
        if last.piece.lowercased() == "k" && abs(last.to.col - last.from.col) == 2 {
            if last.to.col > last.from.col {
                board[last.from.row][7] = board[last.from.row][5]; board[last.from.row][5] = "."
            } else {
                board[last.from.row][0] = board[last.from.row][3]; board[last.from.row][3] = "."
            }
        }
        enPassantTarget = last.epTarget
        whiteKingMoved = last.wKM; blackKingMoved = last.bKM
        whiteRookAMoved = last.wRAM; whiteRookHMoved = last.wRHM
        blackRookAMoved = last.bRAM; blackRookHMoved = last.bRHM
        whiteToMove = last.piece.isUppercase
        if let prev = history.last { lastMoveFrom = prev.from; lastMoveTo = prev.to }
        else { lastMoveFrom = nil; lastMoveTo = nil }
        gameOver = false
        return whiteToMove
    }

    /// Undo a full move pair (AI reply + the human move before it).
    func undoBoth() {
        if undo() == false { _ = undo() }
    }

    /// Returns and clears the game-over result recorded by the last move (so the
    /// caller can append the final move to its history first), or nil if live.
    func consumePendingGameResult() -> GameResult? {
        defer { pendingResult = nil }
        return pendingResult
    }

    // MARK: - Legality

    func isLegal(from: Square, to: Square) -> Bool {
        let piece = board[from.row][from.col]
        guard piece != ".", piece.isUppercase == whiteToMove else { return false }
        guard isLegalGeometry(piece: piece, from: from, to: to) else { return false }
        let target = board[to.row][to.col]
        board[to.row][to.col] = piece; board[from.row][from.col] = "."
        let inCheck = isInCheck(white: piece.isUppercase)
        board[from.row][from.col] = piece; board[to.row][to.col] = target
        return !inCheck
    }

    private func isLegalGeometry(piece: Character, from: Square, to: Square) -> Bool {
        if from == to { return false }
        let target = board[to.row][to.col]
        if target != ".", target.isUppercase == piece.isUppercase { return false }
        let dr = abs(to.row - from.row), dc = abs(to.col - from.col)
        switch piece.lowercased() {
        case "p": return pawnGeometry(piece: piece, from: from, to: to)
        case "r": return (from.row == to.row || from.col == to.col) && pathClear(from: from, to: to)
        case "n": return (dr == 2 && dc == 1) || (dr == 1 && dc == 2)
        case "b": return dr == dc && pathClear(from: from, to: to)
        case "q": return (dr == dc || from.row == to.row || from.col == to.col) && pathClear(from: from, to: to)
        case "k": return (dr <= 1 && dc <= 1) || (dr == 0 && dc == 2 && canCastle(king: piece, from: from, to: to))
        default: return false
        }
    }

    private func pawnGeometry(piece: Character, from: Square, to: Square) -> Bool {
        let dir = piece.isUppercase ? -1 : 1
        let startRow = piece.isUppercase ? 6 : 1
        if from.col == to.col && to.row == from.row + dir && board[to.row][to.col] == "." { return true }
        if from.col == to.col && from.row == startRow && to.row == from.row + 2 * dir
            && board[to.row][to.col] == "." && pathClear(from: from, to: to) { return true }
        if abs(to.col - from.col) == 1 && to.row == from.row + dir
            && (board[to.row][to.col] != "." || enPassantTarget == to) { return true }
        return false
    }

    private func pathClear(from: Square, to: Square) -> Bool {
        let dr = unitStep(to.row - from.row)
        let dc = unitStep(to.col - from.col)
        var r = from.row + dr, c = from.col + dc
        while r != to.row || c != to.col {
            if board[r][c] != "." { return false }
            r += dr; c += dc
        }
        return true
    }

    private func canCastle(king: Character, from: Square, to: Square) -> Bool {
        let white = king.isUppercase
        if isInCheck(white: white) { return false }
        if white && whiteKingMoved { return false }
        if !white && blackKingMoved { return false }
        let rookCol = to.col > from.col ? 7 : 0
        if white && ((to.col > from.col && whiteRookHMoved) || (to.col < from.col && whiteRookAMoved)) { return false }
        if !white && ((to.col > from.col && blackRookHMoved) || (to.col < from.col && blackRookAMoved)) { return false }
        if !pathClear(from: from, to: Square(row: from.row, col: rookCol)) { return false }
        let step = to.col > from.col ? 1 : -1
        if isSquareAttacked(row: from.row, col: from.col + step, byWhite: !white) { return false }
        return true
    }

    private func isInCheck(white: Bool) -> Bool {
        let king: Character = white ? "K" : "k"
        var kr = -1, kc = -1
        outer: for r in 0...7 {
            for c in 0...7 where board[r][c] == king { kr = r; kc = c; break outer }
        }
        if kr == -1 { return false }
        return isSquareAttacked(row: kr, col: kc, byWhite: !white)
    }

    private func isSquareAttacked(row: Int, col: Int, byWhite: Bool) -> Bool {
        for r in 0...7 {
            for c in 0...7 {
                let p = board[r][c]
                if p != ".", p.isUppercase == byWhite,
                   isLegalGeometry(piece: p, from: Square(row: r, col: c), to: Square(row: row, col: col)) {
                    return true
                }
            }
        }
        return false
    }

    // MARK: - Execution

    private func executeMove(from: Square, to: Square) {
        let piece = board[from.row][from.col]
        let captured = board[to.row][to.col]
        history.append(MoveRecord(
            from: from, to: to, piece: piece, captured: captured, epTarget: enPassantTarget,
            wKM: whiteKingMoved, bKM: blackKingMoved,
            wRAM: whiteRookAMoved, wRHM: whiteRookHMoved,
            bRAM: blackRookAMoved, bRHM: blackRookHMoved))

        // Castling: shift the rook.
        if piece.lowercased() == "k" && abs(to.col - from.col) == 2 {
            if to.col > from.col { board[from.row][5] = board[from.row][7]; board[from.row][7] = "." }
            else { board[from.row][3] = board[from.row][0]; board[from.row][0] = "." }
        }
        // En passant: the captured pawn sits on the moving pawn's row, target col.
        if piece.lowercased() == "p" && to.col != from.col && board[to.row][to.col] == "." {
            board[from.row][to.col] = "."
        }

        board[to.row][to.col] = piece
        board[from.row][from.col] = "."
        updateCastlingFlags(piece: piece, from: from)
        enPassantTarget = (piece.lowercased() == "p" && abs(to.row - from.row) == 2)
            ? Square(row: (from.row + to.row) / 2, col: from.col) : nil
        lastMoveFrom = from; lastMoveTo = to
    }

    private func updateCastlingFlags(piece: Character, from: Square) {
        if piece == "K" { whiteKingMoved = true }
        if piece == "k" { blackKingMoved = true }
        if piece == "R" {
            if from.row == 7 && from.col == 0 { whiteRookAMoved = true }
            if from.row == 7 && from.col == 7 { whiteRookHMoved = true }
        }
        if piece == "r" {
            if from.row == 0 && from.col == 0 { blackRookAMoved = true }
            if from.row == 0 && from.col == 7 { blackRookHMoved = true }
        }
    }

    private func hasAnyLegalMove() -> Bool {
        for r in 0...7 {
            for c in 0...7 {
                let p = board[r][c]
                if p == "." || p.isUppercase != whiteToMove { continue }
                let from = Square(row: r, col: c)
                for tr in 0...7 {
                    for tc in 0...7 where isLegal(from: from, to: Square(row: tr, col: tc)) {
                        return true
                    }
                }
            }
        }
        return false
    }

    private func checkAndRecordGameOver() {
        guard !hasAnyLegalMove() else { return }
        gameOver = true
        let inCheck = isInCheck(white: whiteToMove)
        pendingResult = inCheck ? (whiteToMove ? .blackWins : .whiteWins) : .draw
    }

    private func unitStep(_ delta: Int) -> Int { delta == 0 ? 0 : (delta > 0 ? 1 : -1) }
}
