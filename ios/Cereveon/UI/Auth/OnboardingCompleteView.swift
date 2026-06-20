import SwiftUI

/// Cereveon · Atrium · Onboarding · Completion (step 3 of 3).
///
/// Mirrors `OnboardingCompleteActivity` + `activity_onboarding_complete.xml`:
/// a cyan kicker, the italic "Ready when you are." title, the ✦ ornament rule,
/// a cyan-bordered summary card, and a single primary action (Android's
/// "Play your first game", here "Start").
///
/// Field semantics note: the Android summary card reads the recorded rating /
/// first-opponent back, but both numeric rows are `visibility="gone"` (the
/// user-visible Elo display was removed to avoid leaking the rating). We mirror
/// the *visible* result — a confirmation that the calibration was recorded —
/// without surfacing numbers the AuthViewModel contract intentionally doesn't
/// expose.
///
/// "Start" finalises: by this point CalibrationView has already called
/// `submitCalibration`, which flips `isOnboardingComplete` and routes the tree
/// to Home. If that submission hasn't landed (raced or failed), "Start" commits
/// via `skipOnboarding()` so the user is never trapped here — the same
/// default-commit behaviour as Android's skip path.
struct OnboardingCompleteView: View {
    @EnvironmentObject private var auth: AuthViewModel

    private var isWorking: Bool {
        if case .working = auth.phase { return true }
        return false
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                Text("Cereveon · Step 3 of 3".uppercased())
                    .atriumStyle(AtriumTypography.kicker)
                    .foregroundStyle(AtriumColors.accentCyan)
                    .padding(.top, AtriumSpacing.space44)

                Text("Ready when you are.")
                    .atriumStyle(AtriumTypography.displayLarge)
                    .foregroundStyle(AtriumColors.ink)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, AtriumSpacing.space12)

                AtriumOrnamentRule()
                    .padding(.top, AtriumSpacing.space24)

                summaryCard
                    .padding(.top, AtriumSpacing.space24)

                AtriumPrimaryButton(title: "Start", isLoading: isWorking) {
                    start()
                }
                .padding(.top, AtriumSpacing.space44)
            }
            .padding(.horizontal, AtriumSpacing.textPaddingHorizontal)
            .padding(.bottom, AtriumSpacing.space24)
            .frame(maxWidth: .infinity)
        }
        .background(AtriumBackground())
        .navigationBarBackButtonHidden(true)
        .toolbar(.hidden, for: .navigationBar)
    }

    /// Cyan-bordered confirmation tile (the Android "active plan" surface,
    /// reused here to frame the recorded calibration).
    private var summaryCard: some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space8) {
            Text("Calibration recorded".uppercased())
                .atriumStyle(AtriumTypography.kicker)
                .foregroundStyle(AtriumColors.accentCyan)

            Text("Your first opponent is tuned to the level you set. Cereveon refines it from here — every game sharpens the match.")
                .atriumStyle(AtriumTypography.bodyItalic)
                .foregroundStyle(AtriumColors.ink)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(AtriumSpacing.cardPadding)
        .background(AtriumColors.bgSurface)
        .overlay(
            RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius)
                .stroke(AtriumColors.accentCyan55, lineWidth: AtriumSpacing.hairlineThickness)
        )
        .clipShape(RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius))
    }

    private func start() {
        // If submitCalibration already completed, isOnboardingComplete is true
        // and the root router has us on Home — this is a no-op acknowledgement.
        // Otherwise commit with defaults so the user always moves forward.
        if !auth.isOnboardingComplete {
            auth.skipOnboarding()
        }
    }
}
