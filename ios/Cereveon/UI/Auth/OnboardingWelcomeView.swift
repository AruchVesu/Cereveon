import SwiftUI

/// Cereveon · Atrium · Onboarding · Welcome (step 1 of 3).
///
/// Mirrors `OnboardingWelcomeActivity` + `activity_onboarding_welcome.xml`:
/// a centred cyan kicker, an italic Cormorant value-prop title, a muted italic
/// paragraph, the ✦ ornament rule, three ✦-bulleted hooks, and a single primary
/// "Continue" (Android's "Begin") at the foot. Purely informational — no Back
/// path, just like the Android screen.
struct OnboardingWelcomeView: View {
    /// Advances to the calibration step (owned by the parent NavigationStack).
    let onContinue: () -> Void

    /// Copy mirrors `OnboardingWelcomeActivity.DEFAULT_HOOKS` (order preserved).
    private let hooks = [
        "Adaptive opponents at your level",
        "Coach chat grounded in your games",
        "A study that grows with you",
    ]

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                Text("Cereveon · Step 1 of 3".uppercased())
                    .atriumStyle(AtriumTypography.kicker)
                    .foregroundStyle(AtriumColors.accentCyan)
                    .padding(.top, AtriumSpacing.space44)

                Text("A coach who learns from you.")
                    .atriumStyle(AtriumTypography.displayLarge)
                    .foregroundStyle(AtriumColors.ink)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, AtriumSpacing.space12)

                Text("Cereveon studies how you play and adapts the opponent — every game tightens the calibration so the work is always at the right edge of your skill.")
                    .atriumStyle(AtriumTypography.bodyItalic)
                    .foregroundStyle(AtriumColors.muted)
                    .multilineTextAlignment(.center)
                    .lineSpacing(AtriumSpacing.space4)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: 320)
                    .padding(.top, AtriumSpacing.space12)

                AtriumOrnamentRule()
                    .padding(.top, AtriumSpacing.space24)

                VStack(spacing: AtriumSpacing.space16) {
                    ForEach(hooks, id: \.self) { AtriumBullet(text: $0) }
                }
                .padding(.top, AtriumSpacing.space24)
                .padding(.horizontal, AtriumSpacing.space8)

                AtriumPrimaryButton(title: "Continue", action: onContinue)
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
}
