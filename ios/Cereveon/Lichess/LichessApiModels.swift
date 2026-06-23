import Foundation

// Models for the Lichess integration (GET /lichess/status, POST/DELETE
// /lichess/link, POST /lichess/import + GET /lichess/import/job/{id}). Linking is
// by Lichess USERNAME (not OAuth). These use APIJSON (convertFromSnakeCase) — no
// dictionary fields, so the snake→camel mapping is safe.

/// Request body for POST /lichess/link.
struct LichessLinkRequest: Encodable {
    let username: String
}

/// GET /lichess/status. Union-shaped: when `linked` is false the server returns
/// just `{"linked": false}`; the rest is populated when linked.
struct LichessStatus: Decodable, Equatable {
    let linked: Bool
    let externalUsername: String?
    let importedGameCount: Int
    let activeImportJobId: String?

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        linked = (try? c.decode(Bool.self, forKey: .linked)) ?? false
        externalUsername = try? c.decode(String.self, forKey: .externalUsername)
        importedGameCount = (try? c.decode(Int.self, forKey: .importedGameCount)) ?? 0
        activeImportJobId = try? c.decode(String.self, forKey: .activeImportJobId)
    }

    private enum CodingKeys: String, CodingKey {
        case linked, externalUsername, importedGameCount, activeImportJobId
    }
}

/// Shared shape for the 202 from POST /lichess/import (X-API-Version: 2) and the
/// 200 from GET /lichess/import/job/{id}. status ∈ {queued, running, succeeded,
/// failed}.
struct LichessImportJob: Decodable, Equatable {
    let jobId: String
    let status: String
    let inserted: Int
    let targetMaxGames: Int
    let errorMessage: String?

    var isTerminal: Bool { status == "succeeded" || status == "failed" }
    var didFail: Bool { status == "failed" }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        jobId = (try? c.decode(String.self, forKey: .jobId)) ?? ""
        status = (try? c.decode(String.self, forKey: .status)) ?? "queued"
        inserted = (try? c.decode(Int.self, forKey: .inserted)) ?? 0
        targetMaxGames = (try? c.decode(Int.self, forKey: .targetMaxGames)) ?? 0
        errorMessage = try? c.decode(String.self, forKey: .errorMessage)
    }

    private enum CodingKeys: String, CodingKey {
        case jobId, status, inserted, targetMaxGames, errorMessage
    }
}
