import Foundation

/// Hand any non-blank `X-Auth-Token` rotation header to the sink (mirrors the
/// Android `consumeRefreshedToken`).
private func consumeChatRotation(_ response: HTTPURLResponse, _ sink: ((String) -> Void)?) {
    guard let sink,
          let token = response.value(forHTTPHeaderField: AppConfig.authTokenRefreshHeader),
          !token.trimmingCharacters(in: .whitespaces).isEmpty
    else { return }
    sink(token)
}

/// X-Api-Key always; Bearer JWT when present. `/chat[/stream]` and `/chat/history`
/// are all `Depends(get_current_player)`, so the Bearer is required.
private func chatHeaders(token: String?) -> [String: String] {
    var headers = [AppConfig.apiKeyHeader: AppConfig.apiKey]
    if let token { headers["Authorization"] = "Bearer \(token)" }
    return headers
}

/// The coach-chat surface: a blocking `/chat`, the SSE `/chat/stream`, and the
/// server-authoritative `/chat/history` seed. Mirrors the subset of Android's
/// `CoachApiClient` the iOS chat panel needs. `moveCount` gives the backend
/// game-phase context during mid-game chat; `gameId` scopes the exchange to a
/// per-game thread; `lastMove` (UCI) lets the coach name the move in plain
/// English. `coachVoice` / `playerProfile` / `pastMistakes` are deferred (no
/// Settings screen / rating-source on iOS yet) and sent as nil.
protocol ChatClient {
    func chat(fen: String,
              messages: [ChatMessageDTO],
              moveCount: Int?,
              gameId: String?,
              lastMove: String?,
              coachVoice: String?,
              token: String) async -> APIResult<ChatResponse>

    func history(limit: Int,
                 gameId: String?,
                 token: String) async -> APIResult<ChatHistoryResponse>

    func streamChat(fen: String,
                    messages: [ChatMessageDTO],
                    moveCount: Int?,
                    gameId: String?,
                    lastMove: String?,
                    coachVoice: String?,
                    token: String) -> AsyncStream<ChatStreamEvent>
}

/// Production `ChatClient`. Uses the chat-length read timeout (LLM latency) and
/// consumes the `X-Auth-Token` rotation on every endpoint.
final class HTTPChatClient: ChatClient {
    private let http: BaseHTTPClient
    private let tokenSink: ((String) -> Void)?

    init(baseURL: String = AppConfig.apiBase,
         delegate: URLSessionDelegate? = nil,
         configuration: URLSessionConfiguration? = nil,
         tokenSink: ((String) -> Void)? = nil) {
        http = BaseHTTPClient(baseURL: baseURL,
                              readTimeout: AppConfig.chatReadTimeout,
                              delegate: delegate,
                              configuration: configuration)
        self.tokenSink = tokenSink
    }

    func chat(fen: String,
              messages: [ChatMessageDTO],
              moveCount: Int?,
              gameId: String?,
              lastMove: String?,
              coachVoice: String? = nil,
              token: String) async -> APIResult<ChatResponse> {
        let request = ChatRequest(fen: fen, messages: messages,
                                  moveCount: moveCount, coachVoice: coachVoice,
                                  gameId: gameId, lastMove: lastMove)
        let body = try? APIJSON.encode(request)
        return await http.request(
            path: "/chat", method: "POST",
            headers: chatHeaders(token: token), body: body,
            onResponse: { [tokenSink] in consumeChatRotation($0, tokenSink) },
            decode: { try APIJSON.decode(ChatResponse.self, from: $0) }
        )
    }

    func history(limit: Int,
                 gameId: String?,
                 token: String) async -> APIResult<ChatHistoryResponse> {
        // Scope to the current game when present (per-game threads); omit
        // game_id → player-global history (server default).
        var path = "/chat/history?limit=\(limit)"
        if let gameId, !gameId.isEmpty,
           let encoded = gameId.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) {
            path += "&game_id=\(encoded)"
        }
        return await http.request(
            path: path, method: "GET",
            headers: chatHeaders(token: token),
            onResponse: { [tokenSink] in consumeChatRotation($0, tokenSink) },
            decode: { try APIJSON.decode(ChatHistoryResponse.self, from: $0) }
        )
    }

    func streamChat(fen: String,
                    messages: [ChatMessageDTO],
                    moveCount: Int?,
                    gameId: String?,
                    lastMove: String?,
                    coachVoice: String? = nil,
                    token: String) -> AsyncStream<ChatStreamEvent> {
        AsyncStream { continuation in
            let task = Task { [tokenSink] in
                let request = ChatRequest(fen: fen, messages: messages,
                                          moveCount: moveCount, coachVoice: coachVoice,
                                          gameId: gameId, lastMove: lastMove)
                let body = try? APIJSON.encode(request)
                let lines = http.streamingLines(
                    path: "/chat/stream", method: "POST",
                    headers: chatHeaders(token: token), body: body,
                    onResponse: { consumeChatRotation($0, tokenSink) }
                )
                do {
                    for try await line in lines {
                        // SSE: only `data:` lines carry payloads; blank separators
                        // and any comment/keep-alive lines are skipped.
                        let trimmed = line.trimmingCharacters(in: .whitespaces)
                        guard trimmed.hasPrefix("data:") else { continue }
                        let payload = String(trimmed.dropFirst("data:".count))
                            .trimmingCharacters(in: .whitespaces)
                        guard !payload.isEmpty, let event = ChatStreamEvent.parse(payload) else { continue }
                        continuation.yield(event)
                    }
                    continuation.finish()
                } catch let error as HTTPStatusError {
                    continuation.yield(.error("HTTP \(error.code)"))
                    continuation.finish()
                } catch let error as URLError where error.code == .timedOut {
                    continuation.yield(.error("Timeout"))
                    continuation.finish()
                } catch is CancellationError {
                    continuation.finish()
                } catch {
                    continuation.yield(.error("Network error"))
                    continuation.finish()
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}
