import Foundation

/// Thrown by `BaseHTTPClient.streamingLines` when the stream's response carries a
/// non-success status code (the non-streaming `request` maps this to
/// `.httpError(code)` instead; a streaming caller catches this and emits its own
/// terminal event).
struct HTTPStatusError: Error { let code: Int }

/// Shared HTTP helper for the production API clients, mirroring the Android
/// `BaseHttpClient`:
///
/// - sets `X-API-Version` on every request;
/// - sets `Content-Type: application/json` automatically when a body is present;
/// - treats `successCodes` (default `{200}`) as success, any other code as
///   `.httpError(code)`;
/// - maps a request-timeout to `.timeout` and every other transport/decoding
///   failure to `.networkError`;
/// - exposes an `onResponse` hook called after a successful response but before
///   decoding, used to consume the `X-Auth-Token` rotation header.
struct BaseHTTPClient {
    let baseURL: String
    let session: URLSession

    /// - Parameters:
    ///   - delegate: hosts TLS certificate pinning (Phase 1b). `nil` uses the
    ///     default system-CA trust evaluation.
    init(baseURL: String,
         readTimeout: TimeInterval = AppConfig.readTimeout,
         delegate: URLSessionDelegate? = nil,
         configuration: URLSessionConfiguration? = nil) {
        self.baseURL = baseURL
        // `configuration` lets tests inject a custom `URLProtocol` stub; production
        // passes nil and gets an ephemeral session (no on-disk cache/cookies).
        let cfg = configuration ?? .ephemeral
        cfg.timeoutIntervalForRequest = readTimeout
        cfg.waitsForConnectivity = false
        cfg.httpShouldSetCookies = false
        self.session = URLSession(configuration: cfg, delegate: delegate, delegateQueue: nil)
    }

    func request<T>(
        path: String,
        method: String,
        headers: [String: String] = [:],
        body: Data? = nil,
        successCodes: Set<Int> = [200],
        onResponse: ((HTTPURLResponse) -> Void)? = nil,
        decode: (Data) throws -> T
    ) async -> APIResult<T> {
        guard let url = URL(string: baseURL + path) else {
            return .networkError(URLError(.badURL))
        }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue(AppConfig.apiVersion, forHTTPHeaderField: AppConfig.apiVersionHeader)
        for (key, value) in headers {
            req.setValue(value, forHTTPHeaderField: key)
        }
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = body
        }

        do {
            let (data, response) = try await session.data(for: req)
            guard let http = response as? HTTPURLResponse else {
                return .networkError(URLError(.badServerResponse))
            }
            guard successCodes.contains(http.statusCode) else {
                // Body is intentionally not decoded on non-success (parity with
                // the Android client, which reads the body only on success).
                return .httpError(http.statusCode)
            }
            onResponse?(http)
            do {
                return .success(try decode(data))
            } catch {
                return .networkError(error)
            }
        } catch let error as URLError where error.code == .timedOut {
            return .timeout
        } catch {
            return .networkError(error)
        }
    }

    /// Open a streaming request and yield the response body line-by-line — used
    /// for the SSE `/chat/stream` endpoint. Sets the same `X-API-Version` header
    /// and `Content-Type` (on a body) as `request`, plus `Accept:
    /// text/event-stream`. The stream finishes normally at end-of-body, throws
    /// `HTTPStatusError` on a non-success status, or rethrows the transport error
    /// (`URLError`/`CancellationError`). `onResponse` fires once after a success
    /// status line and before the first line, so the `X-Auth-Token` rotation is
    /// consumed even if the user backgrounds mid-stream. Cancelling the consuming
    /// task cancels the underlying URLSession task (`onTermination`).
    func streamingLines(
        path: String,
        method: String,
        headers: [String: String] = [:],
        body: Data? = nil,
        successCodes: Set<Int> = [200],
        onResponse: ((HTTPURLResponse) -> Void)? = nil
    ) -> AsyncThrowingStream<String, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    guard let url = URL(string: baseURL + path) else { throw URLError(.badURL) }
                    var req = URLRequest(url: url)
                    req.httpMethod = method
                    req.setValue(AppConfig.apiVersion, forHTTPHeaderField: AppConfig.apiVersionHeader)
                    req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    for (key, value) in headers { req.setValue(value, forHTTPHeaderField: key) }
                    if let body {
                        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
                        req.httpBody = body
                    }

                    let (bytes, response) = try await session.bytes(for: req)
                    guard let http = response as? HTTPURLResponse else {
                        throw URLError(.badServerResponse)
                    }
                    guard successCodes.contains(http.statusCode) else {
                        throw HTTPStatusError(code: http.statusCode)
                    }
                    onResponse?(http)
                    for try await line in bytes.lines {
                        continuation.yield(line)
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    /// Convenience for endpoints whose success body is not needed
    /// (POST /auth/logout, POST /auth/change-password).
    func requestVoid(
        path: String,
        method: String,
        headers: [String: String] = [:],
        body: Data? = nil,
        successCodes: Set<Int> = [200],
        onResponse: ((HTTPURLResponse) -> Void)? = nil
    ) async -> APIResult<Void> {
        await request(
            path: path,
            method: method,
            headers: headers,
            body: body,
            successCodes: successCodes,
            onResponse: onResponse,
            decode: { _ in () }
        )
    }
}
