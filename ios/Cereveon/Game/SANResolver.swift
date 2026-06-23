import Foundation

/// Resolves Standard Algebraic Notation (SAN) into a board move against a live
/// `ChessGame` position, by matching the destination + disambiguation against the
/// legal moves the game already knows how to generate (`isLegal`). Used by the
/// opening drill to validate the player's tap-move against the book line.
///
/// Expects letters, not chess glyphs — normalise the line through
/// `OpeningLine.sanMoves` first.
enum SANResolver {

    /// Resolve `san` against `game`'s current position. Returns the from/to
    /// squares + an uppercase promotion piece (or nil), or nil when it doesn't
    /// resolve to exactly one legal move.
    static func resolve(_ san: String, in game: ChessGame) -> (from: Square, to: Square, promotion: Character?)? {
        var s = san.trimmingCharacters(in: .whitespaces)
        s.removeAll { $0 == "+" || $0 == "#" || $0 == "!" || $0 == "?" }
        guard !s.isEmpty else { return nil }

        let white = game.whiteToMove
        let backRank = white ? 7 : 0   // row of the side-to-move's back rank

        if s == "O-O" || s == "0-0" { return castle(kingside: true, backRank: backRank, game: game) }
        if s == "O-O-O" || s == "0-0-0" { return castle(kingside: false, backRank: backRank, game: game) }

        // Promotion suffix ("=Q").
        var promotion: Character?
        if let eq = s.firstIndex(of: "=") {
            promotion = s[s.index(after: eq)...].first.map { Character($0.uppercased()) }
            s = String(s[..<eq])
        }

        let core = s.replacingOccurrences(of: "x", with: "")
        guard core.count >= 2, let dest = square(algebraic: String(core.suffix(2))) else { return nil }

        // Piece type + disambiguation = everything before the 2-char destination.
        let prefix = String(core.dropLast(2))
        var pieceLetter: Character = "P"
        var disambig = prefix
        if let first = prefix.first, "KQRBN".contains(first) {
            pieceLetter = first
            disambig = String(prefix.dropFirst())
        }
        var fromFile: Int?
        var fromRank: Int?
        for ch in disambig {
            if let file = fileIndex(ch) { fromFile = file }
            else if let rank = ch.wholeNumberValue, (1...8).contains(rank) { fromRank = 8 - rank }
        }

        let wanted: Character = white ? pieceLetter : Character(pieceLetter.lowercased())

        var matches: [Square] = []
        for row in 0..<8 {
            for col in 0..<8 where game.board[row][col] == wanted {
                if let file = fromFile, col != file { continue }
                if let rank = fromRank, row != rank { continue }
                let from = Square(row: row, col: col)
                if from != dest, game.isLegal(from: from, to: dest) { matches.append(from) }
            }
        }
        guard matches.count == 1 else { return nil }
        return (matches[0], dest, promotion)
    }

    /// Castling resolves to the king's two-square move; the game validates rights
    /// + path when the move is applied (king-side col 6, queen-side col 2).
    private static func castle(kingside: Bool, backRank: Int, game: ChessGame) -> (Square, Square, Character?)? {
        let from = Square(row: backRank, col: 4)
        let king: Character = game.whiteToMove ? "K" : "k"
        guard game.piece(at: from) == king else { return nil }
        return (from, Square(row: backRank, col: kingside ? 6 : 2), nil)
    }

    /// "e2" → Square(row 6, col 4). nil on malformed input.
    static func square(algebraic: String) -> Square? {
        let chars = Array(algebraic)
        guard chars.count == 2,
              let file = fileIndex(chars[0]),
              let rank = chars[1].wholeNumberValue, (1...8).contains(rank)
        else { return nil }
        return Square(row: 8 - rank, col: file)
    }

    private static func fileIndex(_ ch: Character) -> Int? {
        guard let ascii = ch.lowercased().first?.asciiValue, (97...104).contains(ascii) else { return nil }
        return Int(ascii - 97)
    }
}

/// Opening-line helpers: normalise chess glyphs (♘→N) and tokenise a stored line
/// ("1.e4 e5 2.♘f3 ♘c6") into SAN moves (["e4","e5","Nf3","Nc6"]).
enum OpeningLine {
    private static let glyphReplacements: [(String, String)] = [
        ("♔", "K"), ("♕", "Q"), ("♖", "R"), ("♗", "B"), ("♘", "N"), ("♙", ""),
        ("♚", "K"), ("♛", "Q"), ("♜", "R"), ("♝", "B"), ("♞", "N"), ("♟", ""),
    ]

    static func normalize(_ line: String) -> String {
        var s = line
        for (glyph, letter) in glyphReplacements {
            s = s.replacingOccurrences(of: glyph, with: letter)
        }
        return s
    }

    static func sanMoves(from line: String) -> [String] {
        let resultMarkers: Set<String> = ["1-0", "0-1", "1/2-1/2", "½-½", "*"]
        return normalize(line)
            .split(whereSeparator: { $0 == " " || $0 == "\n" || $0 == "\t" })
            .compactMap { token in
                var t = String(token)
                if let range = t.range(of: #"^\d+\.+"#, options: .regularExpression) {
                    t.removeSubrange(range)   // strip "12." / "12..." move numbers (incl. glued "1.e4")
                }
                t = t.trimmingCharacters(in: .whitespaces)
                return (t.isEmpty || resultMarkers.contains(t)) ? nil : t
            }
    }
}
