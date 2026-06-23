import Foundation

/// Body for POST /training/verify-replay. APIJSON snake-cases `moveUci` → `move_uci`.
struct VerifyReplayRequest: Encodable {
    let fen: String
    let moveUci: String
}

/// Response from POST /training/verify-replay. `isCorrect` is true when the move
/// gives up at most the server threshold (~30cp) vs the engine's best;
/// `engineBestUci` is the engine's preferred move (for "show me"); `evalLossCp`
/// is how much the attempt lost.
struct VerifyReplayResponse: Decodable, Equatable {
    let isCorrect: Bool
    let engineBestUci: String
    let evalLossCp: Int

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        isCorrect = (try? c.decode(Bool.self, forKey: .isCorrect)) ?? false
        engineBestUci = (try? c.decode(String.self, forKey: .engineBestUci)) ?? ""
        evalLossCp = (try? c.decode(Int.self, forKey: .evalLossCp)) ?? 0
    }

    private enum CodingKeys: String, CodingKey {
        case isCorrect, engineBestUci, evalLossCp
    }
}
