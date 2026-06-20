import SwiftUI

/// Phase-0 placeholder root. Real screens (Home, Board, Coach chat) arrive in
/// later phases; this exists so the app target builds and launches while the
/// foundation (engine bridge, design system, networking) comes online.
///
/// Intentionally free of design-system / engine dependencies so it compiles
/// independently of the parallel foundation work.
struct RootView: View {
    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            VStack(spacing: 8) {
                Text("Cereveon")
                    .font(.system(.largeTitle, design: .serif).italic())
                    .foregroundStyle(.white)
                Text("iOS foundation")
                    .font(.system(.footnote, design: .monospaced))
                    .foregroundStyle(.secondary)
            }
        }
    }
}
