import Foundation
import Security
import CryptoKit

/// TLS certificate pinning for cereveon.com, mirroring the Android
/// `network_security_config.xml`:
///
/// - The server chain must FIRST pass normal system-CA validation (pinning is
///   additive, never a replacement).
/// - Until `AppConfig.pinExpiration`, the chain must ALSO present a certificate
///   whose SPKI SHA-256 matches one of `AppConfig.pinnedSPKISHA256` (match if
///   ANY pin matches ANY cert in the chain — leaf / intermediate / root).
/// - After the expiry date it falls back to system-CA trust only: a missed pin
///   rotation must degrade gracefully, never brick the app.
/// - Only cereveon.com (+ subdomains) is pinned; other hosts use system trust.
enum CertificatePinning {

    static let pinnedHost = "cereveon.com"

    /// Decide whether to trust `serverTrust` presented for `host`.
    static func allows(serverTrust: SecTrust, host: String, now: Date = Date()) -> Bool {
        // 1. System-CA validation always applies first.
        var error: CFError?
        guard SecTrustEvaluateWithError(serverTrust, &error) else { return false }

        // 2. Only the pinned host is pinned; trust others on system CA alone.
        guard isPinnedHost(host) else { return true }

        // 3. Graceful expiry: after the floor, system-CA trust is sufficient.
        if let expiry = expiryDate(), now >= expiry { return true }

        // 4. Require one chain certificate's SPKI to match a configured pin.
        for certificate in chain(of: serverTrust) {
            if let pin = spkiSHA256Base64(for: certificate),
               AppConfig.pinnedSPKISHA256.contains(pin) {
                return true
            }
        }
        return false
    }

    /// cereveon.com or any subdomain (`includeSubdomains="true"` on Android).
    static func isPinnedHost(_ host: String) -> Bool {
        let h = host.lowercased()
        return h == pinnedHost || h.hasSuffix("." + pinnedHost)
    }

    /// The graceful-fallback floor (`AppConfig.pinExpiration`, `yyyy-MM-dd`, UTC).
    static func expiryDate() -> Date? {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd"
        return f.date(from: AppConfig.pinExpiration)
    }

    private static func chain(of trust: SecTrust) -> [SecCertificate] {
        (SecTrustCopyCertificateChain(trust) as? [SecCertificate]) ?? []
    }

    /// SHA-256 of the certificate's DER SubjectPublicKeyInfo, base64-encoded —
    /// the exact value Android pins. Apple's `SecKeyCopyExternalRepresentation`
    /// returns the *raw* key (PKCS#1 for RSA, the EC point for ECDSA), so the
    /// SPKI is reconstructed by prefixing the ASN.1 algorithm header for the
    /// key's type and size. Verified against the published ISRG X1 (RSA-4096)
    /// and X2 (EC-P384) pins in CertificatePinningTests.
    static func spkiSHA256Base64(for certificate: SecCertificate) -> String? {
        guard let publicKey = SecCertificateCopyKey(certificate),
              let keyData = SecKeyCopyExternalRepresentation(publicKey, nil) as Data?,
              let attributes = SecKeyCopyAttributes(publicKey) as? [CFString: Any],
              let header = asn1Header(for: attributes) else {
            return nil
        }
        var spki = Data(header)
        spki.append(keyData)
        let digest = SHA256.hash(data: spki)
        return Data(digest).base64EncodedString()
    }

    private static func asn1Header(for attributes: [CFString: Any]) -> [UInt8]? {
        guard let keyType = attributes[kSecAttrKeyType] as? String,
              let keySize = attributes[kSecAttrKeySizeInBits] as? Int else {
            return nil
        }
        switch (keyType, keySize) {
        case (kSecAttrKeyTypeRSA as String, 2048): return rsa2048Header
        case (kSecAttrKeyTypeRSA as String, 4096): return rsa4096Header
        case (kSecAttrKeyTypeECSECPrimeRandom as String, 256): return ecdsaSecp256r1Header
        case (kSecAttrKeyTypeECSECPrimeRandom as String, 384): return ecdsaSecp384r1Header
        default: return nil   // unknown key type → no pin (won't match; chain has covered types)
        }
    }

    // ASN.1 SubjectPublicKeyInfo algorithm headers for the key types LE / ISRG
    // use. Standard constants (see TrustKit / OWASP certificate pinning).
    private static let rsa2048Header: [UInt8] = [
        0x30, 0x82, 0x01, 0x22, 0x30, 0x0d, 0x06, 0x09, 0x2a, 0x86, 0x48, 0x86,
        0xf7, 0x0d, 0x01, 0x01, 0x01, 0x05, 0x00, 0x03, 0x82, 0x01, 0x0f, 0x00,
    ]
    private static let rsa4096Header: [UInt8] = [
        0x30, 0x82, 0x02, 0x22, 0x30, 0x0d, 0x06, 0x09, 0x2a, 0x86, 0x48, 0x86,
        0xf7, 0x0d, 0x01, 0x01, 0x01, 0x05, 0x00, 0x03, 0x82, 0x02, 0x0f, 0x00,
    ]
    private static let ecdsaSecp256r1Header: [UInt8] = [
        0x30, 0x59, 0x30, 0x13, 0x06, 0x07, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x02,
        0x01, 0x06, 0x08, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x03, 0x01, 0x07, 0x03,
        0x42, 0x00,
    ]
    private static let ecdsaSecp384r1Header: [UInt8] = [
        0x30, 0x76, 0x30, 0x10, 0x06, 0x07, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x02,
        0x01, 0x06, 0x05, 0x2b, 0x81, 0x04, 0x00, 0x22, 0x03, 0x62, 0x00,
    ]
}
