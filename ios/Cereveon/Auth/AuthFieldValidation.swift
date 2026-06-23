import Foundation

/// Field validation shared by the auth screen — extracted from `LoginView` so the
/// rules are unit-tested rather than living privately in the view. Both mirror
/// the backend's own guards, so the client can surface them inline instead of
/// letting `/auth/register` bounce a vague HTTP 400 ("Registration failed") back.
enum AuthFieldValidation {

    /// `/auth/register` rejects any password shorter than this with HTTP 400.
    static let minPasswordLength = 8

    static func isAcceptablePassword(_ password: String) -> Bool {
        password.count >= minPasswordLength
    }

    /// Lightweight, presentation-only email sanity check (gates the buttons).
    /// The server remains the authority on whether the address is real.
    static func isValidEmail(_ raw: String) -> Bool {
        let value = raw.trimmingCharacters(in: .whitespaces)
        guard let at = value.firstIndex(of: "@"), at != value.startIndex else { return false }
        let domain = value[value.index(after: at)...]
        return domain.contains(".") && !domain.hasPrefix(".") && !domain.hasSuffix(".")
    }
}
