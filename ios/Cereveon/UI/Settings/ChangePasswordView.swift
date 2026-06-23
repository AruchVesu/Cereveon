import SwiftUI

/// Change-password form, pushed from `SettingsView`. Current + new + confirm,
/// validated client-side then sent via `AuthViewModel.changePassword`; the server
/// is authoritative (a weak/incorrect password comes back as a mapped message).
struct ChangePasswordView: View {
    @EnvironmentObject private var auth: AuthViewModel
    @Environment(\.dismiss) private var dismiss

    @State private var current = ""
    @State private var newPassword = ""
    @State private var confirm = ""
    @State private var error: String?
    @State private var working = false
    @State private var done = false

    private static let minLength = 8

    private var mismatch: Bool { !confirm.isEmpty && confirm != newPassword }

    private var canSubmit: Bool {
        !current.isEmpty
            && newPassword.count >= Self.minLength
            && newPassword == confirm
            && !working
            && !done
    }

    var body: some View {
        ZStack {
            AtriumBackground()

            ScrollView {
                VStack(alignment: .leading, spacing: AtriumSpacing.space20) {
                    Text("Choose a new password — at least \(Self.minLength) characters.")
                        .atriumStyle(AtriumTypography.bodyItalic)
                        .foregroundStyle(AtriumColors.muted)
                        .fixedSize(horizontal: false, vertical: true)

                    AtriumTextField(hint: "Current password", text: $current,
                                    isSecure: true, textContentType: .password)
                    AtriumTextField(hint: "New password", text: $newPassword,
                                    isSecure: true, textContentType: .newPassword)
                    AtriumTextField(hint: "Confirm new password", text: $confirm,
                                    isSecure: true, textContentType: .newPassword,
                                    submitLabel: .done, onSubmit: submit)

                    if mismatch {
                        message("The new passwords don't match.", color: AtriumColors.accentAmber)
                    }
                    if let error {
                        message(error, color: AtriumColors.accentAmber)
                    }
                    if done {
                        message("Password updated.", color: AtriumColors.accentCyan)
                    }

                    AtriumPrimaryButton(title: "Update password", isLoading: working) { submit() }
                        .disabled(!canSubmit)
                        .padding(.top, AtriumSpacing.space4)
                }
                .padding(AtriumSpacing.space24)
            }
        }
        .navigationTitle("Change password")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(AtriumColors.bgBase, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
    }

    private func message(_ text: String, color: Color) -> some View {
        Text(text)
            .atriumStyle(AtriumTypography.inline)
            .foregroundStyle(color)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func submit() {
        guard canSubmit else { return }
        working = true
        error = nil
        Task {
            let failure = await auth.changePassword(current: current, new: newPassword)
            working = false
            if let failure {
                error = failure
            } else {
                done = true
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                dismiss()
            }
        }
    }
}
