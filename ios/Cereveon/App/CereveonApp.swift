import SwiftUI

@main
struct CereveonApp: App {
    /// Owns the auth/onboarding state machine for the whole app. The
    /// networking + Keychain logic lives inside AuthViewModel (authored
    /// separately); the UI only observes published state and calls intents.
    @StateObject private var auth = AuthViewModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(auth)
                .preferredColorScheme(.dark)
        }
    }
}
