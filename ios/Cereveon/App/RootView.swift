import SwiftUI

/// Top-level router. Mirrors the Android launch decision tree
/// (`LoginActivity.launchPostAuth` → Welcome → Calibration → Complete → Home):
///
///   - unauthenticated                         → LoginView
///   - authenticated, onboarding incomplete    → onboarding flow
///   - authenticated, onboarding complete       → HomeView
///
/// The onboarding flow is a local NavigationStack so Welcome → Calibration →
/// Complete advance forward only (no Back to Login), matching the Android
/// "no real Back path" intent. `submitCalibration` / `skipOnboarding` flip
/// `auth.isOnboardingComplete`, which collapses the stack into HomeView.
struct RootView: View {
    @EnvironmentObject private var auth: AuthViewModel

    var body: some View {
        ZStack {
            AtriumBackground()

            switch auth.authState {
            case .unauthenticated:
                LoginView()
                    .transition(.opacity)

            case .authenticated:
                if auth.isOnboardingComplete {
                    HomeView()
                        .transition(.opacity)
                } else {
                    OnboardingFlowView()
                        .transition(.opacity)
                }
            }
        }
        .animation(.easeInOut(duration: 0.25), value: auth.authState)
        .animation(.easeInOut(duration: 0.25), value: auth.isOnboardingComplete)
    }
}

/// The post-registration onboarding sequence. Step 2/3 (calibration) reports
/// completion through the view model; steps 1 and 3 are local navigation only.
private struct OnboardingFlowView: View {
    @EnvironmentObject private var auth: AuthViewModel
    @State private var path: [OnboardingStep] = []

    var body: some View {
        NavigationStack(path: $path) {
            OnboardingWelcomeView { path.append(.calibration) }
                .navigationDestination(for: OnboardingStep.self) { step in
                    switch step {
                    case .calibration:
                        CalibrationView { path.append(.complete) }
                    case .complete:
                        // "Start" finalises onboarding via the view model,
                        // which routes the whole tree to HomeView.
                        OnboardingCompleteView()
                    }
                }
        }
        .tint(AtriumColors.accentCyan)
    }
}

private enum OnboardingStep: Hashable {
    case calibration
    case complete
}
