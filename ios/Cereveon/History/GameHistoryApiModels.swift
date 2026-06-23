import Foundation

// Models for the past-games surface: GET /game/history and
// GET /game/{id}/positions. These use APIJSON (convertFromSnakeCase) — no
// dictionary fields here, so the snake→camel mapping is safe (unlike
// /player/progress).

/// One finished game in the history list.
struct GameHistoryItem: Decodable, Equatable {
    let id: String
    /// The live games.id this maps to (for fetching its coaching chat); nil for
    /// legacy / imported / pre-game_id rows.
    let gameId: String?
    /// Final mainline move (SAN), e.g. "Qxf7"; nil for moveless/legacy rows.
    let lastMove: String?
    /// Winning side's final move (SAN); nil for draws/ongoing/moveless.
    let winnerMove: String?
    let result: String          // "win" | "loss" | "draw" (or legacy)
    let createdAt: String?

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = (try? c.decode(String.self, forKey: .id)) ?? ""
        gameId = try? c.decode(String.self, forKey: .gameId)
        lastMove = try? c.decode(String.self, forKey: .lastMove)
        winnerMove = try? c.decode(String.self, forKey: .winnerMove)
        result = (try? c.decode(String.self, forKey: .result)) ?? ""
        createdAt = try? c.decode(String.self, forKey: .createdAt)
    }

    private enum CodingKeys: String, CodingKey {
        case id, gameId, lastMove, winnerMove, result, createdAt
    }
}

struct GameHistoryResponse: Decodable {
    let games: [GameHistoryItem]

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        games = (try? c.decode([GameHistoryItem].self, forKey: .games)) ?? []
    }

    private enum CodingKeys: String, CodingKey { case games }
}

/// Per-ply replay positions: `positions[0]` is the start, `positions[i]` is the
/// board after ply i; `moves[i]` is the SAN that produced `positions[i+1]`.
struct GamePositionsResponse: Decodable {
    let positions: [String]
    let moves: [String]

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        positions = (try? c.decode([String].self, forKey: .positions)) ?? []
        moves = (try? c.decode([String].self, forKey: .moves)) ?? []
    }

    private enum CodingKeys: String, CodingKey { case positions, moves }
}
