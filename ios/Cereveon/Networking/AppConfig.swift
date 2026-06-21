import Foundation

/// Backend configuration and HTTP-contract constants. Mirrors the Android
/// `BuildConfig` (`COACH_API_BASE` / `COACH_API_KEY`) and the package-root
/// constants (`COACH_API_VERSION`, `X-API-Version`, `X-Auth-Token`).
enum AppConfig {
    /// Production backend — matches the Android release default. (Env/xcconfig
    /// override support can be layered on later, like Android's BuildConfig.)
    static let apiBase = "https://cereveon.com"

    /// Semi-public rate-limit shield — NOT authentication (real auth is the JWT
    /// from `/auth/login`). Matches the Android `dev-key` default; a real key is
    /// injected for release builds.
    static let apiKey = "dev-key"

    /// HTTP API schema version. The backend rejects a mismatch with HTTP 400;
    /// a missing header is tolerated (server lenient mode).
    static let apiVersion = "2"

    // Header names — single source of truth (mirrors ApiVersion.kt / TokenRefresh.kt).
    static let apiVersionHeader = "X-API-Version"
    static let apiKeyHeader = "X-Api-Key"
    static let authTokenRefreshHeader = "X-Auth-Token"

    /// Request deadline. URLSession has no separate connect timeout, so the
    /// read timeout bounds the whole exchange (Android: connect 8 s, read 15 s).
    static let readTimeout: TimeInterval = 15

    /// Longer deadline for the coach chat endpoints (`/chat`, `/chat/stream`):
    /// the LLM reply can take far longer than an engine call. Matches the
    /// Android `CHAT_READ_TIMEOUT_MS = 60_000`.
    static let chatReadTimeout: TimeInterval = 60

    // MARK: TLS certificate pinning (cereveon.com)
    //
    // Captured verbatim from the Android `network_security_config.xml`.
    // Enforced by `CertificatePinning` + `PinningURLSessionDelegate` (matched
    // against any cert in the validated chain; system-CA trust still applies).
    //
    // SPKI SHA-256 (base64). Pin semantics: accept if ANY pin matches ANY cert
    // in the validated chain.
    //   - Let's Encrypt E8 ECDSA intermediate (current leaf chains through it)
    //   - ISRG Root X1 (RSA, valid until 2030)
    //   - ISRG Root X2 (ECDSA, valid until 2035) — backup
    static let pinnedSPKISHA256: Set<String> = [
        "iFvwVyJSxnQdyaUvUERIf+8qk7gRze3612JMwoO3zdU=",
        "C5+lpZ7tcVwmwQIMcRtPbsQtWLABXhQzejna0wHFr8M=",
        "diGVwiVYbubAI3RW4hB9xU8e/CH2GnkuvVFZE8zmgzI=",
    ]

    /// Graceful-fallback floor (matches the Android pin-set `expiration`): after
    /// this date, fall back to system-CA trust so a missed pin rotation can't
    /// brick the app. ISO `yyyy-MM-dd`.
    static let pinExpiration = "2028-05-20"
}
