import SwiftUI

/// Cereveon · Atrium · Enter.
///
/// Mirrors `LoginActivity` + `activity_login.xml`: centred kicker + italic
/// title + ornament, two hairline-bordered fields, an inline error row, and the
/// Atrium primary "Sign in" / secondary "Create account" actions.
///
/// Field semantics copied from Android: email is trimmed, both fields must be
/// non-empty before either action enables. `auth.phase` drives the spinner and
/// the inline error; the view never reaches the network itself.
struct LoginView: View {
    @EnvironmentObject private var auth: AuthViewModel

    @State private var email = ""
    @State private var password = ""

    private enum Field { case email, password }
    @FocusState private var focusedField: Field?

    private var isWorking: Bool {
        if case .working = auth.phase { return true }
        return false
    }

    private var errorMessage: String? {
        if case let .failed(message) = auth.phase { return message }
        return nil
    }

    /// Both fields non-empty (email trimmed), matching the Android guard.
    private var canSubmit: Bool {
        !email.trimmingCharacters(in: .whitespaces).isEmpty
            && !password.isEmpty
            && isValidEmail(email)
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                AtriumHeader(kicker: "Cereveon · Enter", title: "Welcome")
                    .padding(.top, AtriumSpacing.space44)
                    .padding(.bottom, AtriumSpacing.space32)

                AtriumTextField(
                    hint: "Email",
                    text: $email,
                    keyboard: .emailAddress,
                    textContentType: .username,
                    submitLabel: .next,
                    onSubmit: { focusedField = .password }
                )
                .focused($focusedField, equals: .email)
                .padding(.bottom, AtriumSpacing.space12)

                AtriumTextField(
                    hint: "Password",
                    text: $password,
                    isSecure: true,
                    textContentType: .password,
                    submitLabel: .go,
                    onSubmit: { if canSubmit { signIn() } }
                )
                .focused($focusedField, equals: .password)
                .padding(.bottom, AtriumSpacing.space24)

                if let errorMessage {
                    errorRow(errorMessage)
                        .padding(.bottom, AtriumSpacing.space16)
                }

                AtriumPrimaryButton(title: "Sign in", isLoading: isWorking) {
                    signIn()
                }
                .disabled(!canSubmit || isWorking)

                AtriumSecondaryButton(title: "Create account") {
                    createAccount()
                }
                .disabled(!canSubmit || isWorking)
                .padding(.top, AtriumSpacing.space12)
            }
            .padding(.horizontal, AtriumSpacing.textPaddingHorizontal)
            .padding(.bottom, AtriumSpacing.space32)
            .frame(maxWidth: .infinity)
        }
        .scrollDismissesKeyboard(.interactively)
        // Editing after a failure clears the inline error so the form doesn't
        // keep showing a stale message while the user corrects their input.
        .onChange(of: email) { _ in dismissErrorIfNeeded() }
        .onChange(of: password) { _ in dismissErrorIfNeeded() }
    }

    private func dismissErrorIfNeeded() {
        if case .failed = auth.phase { auth.clearError() }
    }

    // MARK: Actions

    private func signIn() {
        focusedField = nil
        let trimmed = email.trimmingCharacters(in: .whitespaces)
        Task { await auth.login(email: trimmed, password: password) }
    }

    private func createAccount() {
        focusedField = nil
        let trimmed = email.trimmingCharacters(in: .whitespaces)
        Task { await auth.register(email: trimmed, password: password) }
    }

    // MARK: Error row — italic amber on a surface tile (mirrors tvError).

    private func errorRow(_ message: String) -> some View {
        Text(message)
            .atriumStyle(AtriumTypography.bodyItalic)
            .foregroundStyle(AtriumColors.accentAmber)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(AtriumSpacing.space12)
            .background(AtriumColors.bgSurface)
            .clipShape(RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius))
    }

    /// Lightweight, presentation-only email sanity check (gates the buttons).
    /// The server remains the authority on whether the address is real.
    private func isValidEmail(_ raw: String) -> Bool {
        let value = raw.trimmingCharacters(in: .whitespaces)
        guard let at = value.firstIndex(of: "@"), at != value.startIndex else { return false }
        let domain = value[value.index(after: at)...]
        return domain.contains(".") && !domain.hasPrefix(".") && !domain.hasSuffix(".")
    }
}
