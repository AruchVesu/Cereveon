import SwiftUI

/// Cereveon · Atrium · Onboarding · Skill calibration (step 2 of 3).
///
/// Mirrors `OnboardingActivity` + `activity_onboarding.xml`: a rating slider
/// over abstract skill-band labels (Beginner · Casual · Strong · Expert) and a
/// three-option confidence choice (Sure of it · Guessing · Rusty). "Continue"
/// submits the calibration through the view model; "Skip" defers to the view
/// model's default-handling `skipOnboarding()`.
///
/// Slider range + default + confidence weights are copied verbatim from
/// `OnboardingActivity`: 800…2600, default 1500, sure=0.85 / guessing=0.50 /
/// rusty=0.25. The numeric Elo readout is deliberately hidden (Android hid it
/// too); only the abstract bands show. `submitCalibration` expects rating in
/// (0,4000] and confidence in [0,1] — both satisfied here.
struct CalibrationView: View {
    @EnvironmentObject private var auth: AuthViewModel

    /// Advances to the completion step once submission begins.
    let onContinue: () -> Void

    // Range / default mirror OnboardingActivity.DEFAULT_RATING + slider bounds.
    private let ratingRange: ClosedRange<Double> = 800...2600
    private let ratingStep: Double = 10

    @State private var rating: Double = 1500
    @State private var confidence: Confidence = .guessing

    private var isWorking: Bool {
        if case .working = auth.phase { return true }
        return false
    }

    private var errorMessage: String? {
        if case let .failed(message) = auth.phase { return message }
        return nil
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                header

                ratingSection
                    .padding(.top, AtriumSpacing.space24)

                hairline
                    .padding(.vertical, AtriumSpacing.space16)

                confidenceSection

                if let errorMessage {
                    errorRow(errorMessage)
                        .padding(.top, AtriumSpacing.space16)
                }

                footer
                    .padding(.top, AtriumSpacing.space32)
            }
            .padding(.horizontal, AtriumSpacing.textPaddingHorizontal)
            .padding(.bottom, AtriumSpacing.space24)
            .frame(maxWidth: .infinity)
        }
        .background(AtriumBackground())
        .navigationBarBackButtonHidden(true)
        .toolbar(.hidden, for: .navigationBar)
    }

    // MARK: Header

    private var header: some View {
        VStack(spacing: AtriumSpacing.space8) {
            Text("Cereveon · Step 2 of 3".uppercased())
                .atriumStyle(AtriumTypography.kicker)
                .foregroundStyle(AtriumColors.accentCyan)
                .padding(.top, AtriumSpacing.space44)

            Text("How do you play?")
                .atriumStyle(AtriumTypography.display)
                .foregroundStyle(AtriumColors.ink)

            Text("Drag the dot to where you'd place your skill.")
                .atriumStyle(AtriumTypography.bodyItalic)
                .foregroundStyle(AtriumColors.muted)
                .multilineTextAlignment(.center)

            Text("\u{2726}")
                .atriumStyle(AtriumTypography.body)
                .foregroundStyle(AtriumColors.muted)
                .padding(.top, AtriumSpacing.space4)
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: Rating slider + abstract band labels

    private var ratingSection: some View {
        VStack(spacing: AtriumSpacing.space4) {
            Slider(
                value: $rating,
                in: ratingRange,
                step: ratingStep
            )
            .tint(AtriumColors.accentCyan)
            .disabled(isWorking)
            .accessibilityLabel("Skill estimate")

            HStack(spacing: 0) {
                bandLabel("Beginner", alignment: .leading)
                bandLabel("Casual", alignment: .center)
                bandLabel("Strong", alignment: .center)
                bandLabel("Expert", alignment: .trailing)
            }
        }
    }

    private func bandLabel(_ text: String, alignment: Alignment) -> some View {
        Text(text.uppercased())
            .atriumStyle(AtriumTypography.kicker)
            .foregroundStyle(AtriumColors.dim)
            .frame(maxWidth: .infinity, alignment: alignment)
    }

    // MARK: Confidence choice

    private var confidenceSection: some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space12) {
            Text("How sure are you?".uppercased())
                .atriumStyle(AtriumTypography.kicker)
                .foregroundStyle(AtriumColors.muted)

            ForEach(Confidence.allCases) { option in
                confidenceRow(option)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func confidenceRow(_ option: Confidence) -> some View {
        let selected = option == confidence
        return Button {
            confidence = option
        } label: {
            HStack(spacing: AtriumSpacing.space12) {
                radioDot(selected: selected)
                Text(option.title)
                    .atriumStyle(AtriumTypography.body)
                    .foregroundStyle(AtriumColors.ink)
                Spacer(minLength: AtriumSpacing.space12)
                Text(option.subtitle)
                    .atriumStyle(AtriumTypography.inline)
                    .foregroundStyle(AtriumColors.muted)
            }
            .frame(height: AtriumSpacing.tapTarget)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(isWorking)
    }

    /// Hollow vs filled cyan ring — mirrors atrium_radio_unselected/selected.
    private func radioDot(selected: Bool) -> some View {
        ZStack {
            Circle()
                .stroke(
                    selected ? AtriumColors.accentCyan : AtriumColors.hairlineStrong,
                    lineWidth: AtriumSpacing.hairlineThickness
                )
                .frame(width: 16, height: 16)
            if selected {
                Circle()
                    .fill(AtriumColors.accentCyan)
                    .frame(width: 8, height: 8)
            }
        }
        .frame(width: 16, height: 16)
    }

    // MARK: Footer — Skip (secondary) + Continue (primary)

    private var footer: some View {
        HStack(spacing: AtriumSpacing.space12) {
            AtriumSecondaryButton(title: "Skip") {
                auth.skipOnboarding()
            }
            .fixedSize(horizontal: true, vertical: false)
            .disabled(isWorking)

            AtriumPrimaryButton(title: "Continue", isLoading: isWorking) {
                submit()
            }
            .disabled(isWorking)
        }
    }

    private func submit() {
        Task {
            await auth.submitCalibration(
                rating: rating,
                confidence: confidence.weight
            )
            // Only advance once the submit settled without error. On success
            // the view model may already have flipped isOnboardingComplete —
            // RootView then routes straight to Home and this push is moot
            // (it lands under the swapped-out tree). If the flag is instead
            // finalised on the Complete screen, this push surfaces it. On
            // failure we stay put so the inline error is visible here.
            if case .failed = auth.phase { return }
            onContinue()
        }
    }

    // MARK: Bits

    private var hairline: some View {
        Rectangle()
            .fill(AtriumColors.hairline)
            .frame(height: AtriumSpacing.hairlineThickness)
    }

    private func errorRow(_ message: String) -> some View {
        Text(message)
            .atriumStyle(AtriumTypography.bodyItalic)
            .foregroundStyle(AtriumColors.accentAmber)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(AtriumSpacing.space12)
            .background(AtriumColors.bgSurface)
            .clipShape(RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius))
    }
}

/// The three confidence options. Weights mirror
/// `OnboardingActivity.confidenceFromKey` (sure 0.85 / guessing 0.50 / rusty
/// 0.25); all land in the contract's [0,1] band.
private enum Confidence: String, CaseIterable, Identifiable {
    case sure
    case guessing
    case rusty

    var id: String { rawValue }

    var title: String {
        switch self {
        case .sure: return "Sure of it"
        case .guessing: return "Guessing"
        case .rusty: return "Rusty"
        }
    }

    var subtitle: String {
        switch self {
        case .sure: return "I rate often"
        case .guessing: return "A rough sense"
        case .rusty: return "Out of practice"
        }
    }

    var weight: Double {
        switch self {
        case .sure: return 0.85
        case .guessing: return 0.50
        case .rusty: return 0.25
        }
    }
}
