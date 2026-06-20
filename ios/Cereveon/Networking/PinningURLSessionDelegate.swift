import Foundation

/// URLSession delegate that enforces TLS certificate pinning for cereveon.com
/// via `CertificatePinning`. Server-trust challenges are accepted only when the
/// chain passes system validation AND (pre-expiry) matches a pin; everything
/// else (proxy auth, client certs, …) uses default handling.
///
/// Share one instance across the app's API clients (it is stateless).
final class PinningURLSessionDelegate: NSObject, URLSessionDelegate {
    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let serverTrust = challenge.protectionSpace.serverTrust else {
            completionHandler(.performDefaultHandling, nil)
            return
        }

        if CertificatePinning.allows(serverTrust: serverTrust, host: challenge.protectionSpace.host) {
            completionHandler(.useCredential, URLCredential(trust: serverTrust))
        } else {
            completionHandler(.cancelAuthenticationChallenge, nil)
        }
    }
}
