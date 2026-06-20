import Foundation

/// Result of an API call, mirroring the Android `ApiResult<T>` sealed class:
/// callers never see raw exceptions. `httpError` carries a non-success status
/// code; `timeout` is a request-deadline expiry; `networkError` wraps transport
/// or decoding failures (Android folds decode failures into `NetworkError` too).
enum APIResult<T> {
    case success(T)
    case httpError(Int)
    case timeout
    case networkError(Error)
}

extension APIResult {
    var value: T? {
        if case let .success(v) = self { return v }
        return nil
    }

    var isSuccess: Bool {
        if case .success = self { return true }
        return false
    }

    /// Transform the success payload, preserving every error variant.
    func map<U>(_ transform: (T) -> U) -> APIResult<U> {
        switch self {
        case let .success(v): return .success(transform(v))
        case let .httpError(code): return .httpError(code)
        case .timeout: return .timeout
        case let .networkError(e): return .networkError(e)
        }
    }
}
