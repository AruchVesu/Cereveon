import SwiftUI

// Shared Atrium presentation primitives for the auth + onboarding screens.
// These mirror the Android Atrium.* styles (themes.xml) and the
// bg_atrium_gradient drawable, expressed with the iOS DesignSystem tokens.
// Nothing here is networking — purely visual building blocks.

/// Full-bleed Atrium backdrop: a radial wash biased to the bottom-centre over
/// the flat base, matching `res/drawable/bg_atrium_gradient.xml`
/// (`radial-gradient(ellipse at 50% 100%, #16141f 0%, #0a0a10 70%)`).
struct AtriumBackground: View {
    var body: some View {
        GeometryReader { proxy in
            let radius = max(proxy.size.width, proxy.size.height) * 0.95
            AtriumColors.bgBase
                .overlay(
                    RadialGradient(
                        gradient: Gradient(colors: [
                            AtriumColors.bgGradientTop,
                            AtriumColors.bgBase,
                        ]),
                        center: .init(x: 0.5, y: 1.0),
                        startRadius: 0,
                        endRadius: radius
                    )
                )
        }
        .ignoresSafeArea()
    }
}

/// Centred mono kicker over an italic Cormorant display title, with the
/// ✦ ornament rule beneath — the shared header of every onboarding/auth screen.
struct AtriumHeader: View {
    let kicker: String
    let title: String
    /// When true the ornament is a centred glyph between two hairlines
    /// (Welcome/Complete); when false it's a lone glyph (Login/Calibration).
    var ruledOrnament: Bool = false
    var titleStyle: AtriumTextStyle = AtriumTypography.display

    var body: some View {
        VStack(spacing: AtriumSpacing.space8) {
            Text(kicker.uppercased())
                .atriumStyle(AtriumTypography.kicker)
                .foregroundStyle(AtriumColors.accentCyan)

            Text(title)
                .atriumStyle(titleStyle)
                .foregroundStyle(AtriumColors.ink)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)

            if ruledOrnament {
                AtriumOrnamentRule()
                    .padding(.top, AtriumSpacing.space4)
            } else {
                Text("\u{2726}") // ✦
                    .atriumStyle(AtriumTypography.body)
                    .foregroundStyle(AtriumColors.muted)
                    .padding(.top, AtriumSpacing.space4)
            }
        }
        .frame(maxWidth: .infinity)
    }
}

/// ✦ flanked by two hairline rules — the "ornament rule" used on the
/// Welcome and Complete screens.
struct AtriumOrnamentRule: View {
    var body: some View {
        HStack(spacing: AtriumSpacing.space8) {
            hairline
            Text("\u{2726}")
                .atriumStyle(AtriumTypography.body)
                .foregroundStyle(AtriumColors.muted)
            hairline
        }
    }

    private var hairline: some View {
        Rectangle()
            .fill(AtriumColors.hairlineStrong)
            .frame(height: AtriumSpacing.hairlineThickness)
    }
}

/// Atrium primary button: cyan-bordered, hairline-thin surface tile with a mono
/// label — mirrors `Atrium.Button.Primary`. Disables to a dimmed state and can
/// host a trailing progress spinner.
struct AtriumPrimaryButton: View {
    let title: String
    var isLoading: Bool = false
    let action: () -> Void

    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        Button(action: action) {
            ZStack {
                Text(title.uppercased())
                    .atriumStyle(AtriumTypography.kicker)
                    .foregroundStyle(isEnabled ? AtriumColors.ink : AtriumColors.dim)
                    .opacity(isLoading ? 0 : 1)

                if isLoading {
                    ProgressView()
                        .progressViewStyle(.circular)
                        .tint(AtriumColors.accentCyan)
                }
            }
            .frame(maxWidth: .infinity)
            .frame(height: AtriumSpacing.tapTarget)
            .background(AtriumColors.bgSurface)
            .overlay(
                RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius)
                    .stroke(
                        isEnabled ? AtriumColors.accentCyan55 : AtriumColors.hairline,
                        lineWidth: AtriumSpacing.hairlineThickness
                    )
            )
            .clipShape(RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius))
        }
        .buttonStyle(.plain)
    }
}

/// Atrium secondary/text button: muted mono label, no fill — mirrors
/// `Atrium.Button.Secondary` and the "Create account" / "Skip" affordances.
struct AtriumSecondaryButton: View {
    let title: String
    let action: () -> Void

    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        Button(action: action) {
            Text(title.uppercased())
                .atriumStyle(AtriumTypography.kicker)
                .foregroundStyle(isEnabled ? AtriumColors.muted : AtriumColors.dim)
                .frame(maxWidth: .infinity)
                .frame(height: AtriumSpacing.tapTarget)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// A ✦-prefixed Cormorant-italic line — the marketing hook row reused from the
/// paywall bullet primitive on the Welcome screen.
struct AtriumBullet: View {
    let text: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: AtriumSpacing.space12) {
            Text("\u{2726}")
                .atriumStyle(AtriumTypography.body)
                .foregroundStyle(AtriumColors.accentCyan)
            Text(text)
                .atriumStyle(AtriumTypography.bodyItalic)
                .foregroundStyle(AtriumColors.ink)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
    }
}

/// An Atrium-styled outlined text field: hairline box, cyan focus ring, mono
/// floating-ish hint above. Mirrors the Material OutlinedBox fields in
/// `activity_login.xml` (Cormorant input text, dim hint, cyan stroke).
struct AtriumTextField: View {
    let hint: String
    @Binding var text: String
    var isSecure: Bool = false
    var keyboard: UIKeyboardType = .default
    var textContentType: UITextContentType? = nil
    var submitLabel: SubmitLabel = .next
    var onSubmit: () -> Void = {}

    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space8) {
            Text(hint.uppercased())
                .atriumStyle(AtriumTypography.kicker)
                .foregroundStyle(AtriumColors.dim)

            Group {
                if isSecure {
                    SecureField("", text: $text)
                } else {
                    TextField("", text: $text)
                }
            }
            .atriumStyle(AtriumTypography.body)
            .foregroundStyle(AtriumColors.ink)
            .tint(AtriumColors.accentCyan)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled(true)
            .keyboardType(keyboard)
            .textContentType(textContentType)
            .submitLabel(submitLabel)
            .focused($focused)
            .onSubmit(onSubmit)
            .padding(.horizontal, AtriumSpacing.space12)
            .frame(height: AtriumSpacing.tapTarget)
            .background(AtriumColors.bgSurface)
            .overlay(
                RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius)
                    .stroke(
                        focused ? AtriumColors.accentCyan : AtriumColors.hairlineStrong,
                        lineWidth: AtriumSpacing.hairlineThickness
                    )
            )
            .clipShape(RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius))
        }
    }
}
