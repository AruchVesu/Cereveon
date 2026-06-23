import SwiftUI

/// Cereveon · Atrium · Lichess Connect (iOS port of the Android
/// LichessConnectBottomSheet). Link by username, import games (async, with a
/// progress bar), and disconnect. Pushed from Settings → Integrations.
struct LichessConnectView: View {
    @StateObject private var vm: LichessConnectViewModel

    init(token: @escaping () -> String?) {
        _vm = StateObject(wrappedValue: LichessConnectViewModel(
            client: HTTPLichessClient(delegate: PinningURLSessionDelegate()),
            token: token
        ))
    }

    var body: some View {
        ZStack {
            AtriumBackground()
            ScrollView {
                content
                    .padding(AtriumSpacing.space24)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .navigationTitle("Lichess")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(AtriumColors.bgBase, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .task { await vm.load() }
    }

    @ViewBuilder
    private var content: some View {
        switch vm.phase {
        case .loading:
            HStack { Spacer(); ProgressView().tint(AtriumColors.accentCyan); Spacer() }
                .padding(.top, AtriumSpacing.space44)
        case let .error(message):
            errorState(message)
        case .notLinked:
            notLinked
        case let .linked(handle, count):
            linked(handle, count)
        case let .importing(inserted, target):
            importing(inserted, target)
        }
    }

    // MARK: - States

    private var notLinked: some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space16) {
            Text("Connect your Lichess account to import your games and calibrate the coach to your level.")
                .atriumStyle(AtriumTypography.bodyItalic)
                .foregroundStyle(AtriumColors.muted)
                .fixedSize(horizontal: false, vertical: true)

            bannerView

            AtriumTextField(hint: "Lichess username", text: $vm.usernameDraft,
                            textContentType: .username, submitLabel: .go,
                            onSubmit: { Task { await vm.link() } })

            AtriumPrimaryButton(title: "Connect", isLoading: vm.busy) {
                Task { await vm.link() }
            }
            .disabled(!vm.canLink)
        }
    }

    private func linked(_ handle: String, _ count: Int) -> some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space16) {
            VStack(alignment: .leading, spacing: AtriumSpacing.space4) {
                Text("Connected".uppercased())
                    .atriumStyle(AtriumTypography.kicker)
                    .foregroundStyle(AtriumColors.accentCyan)
                Text("@\(handle)")
                    .atriumStyle(AtriumTypography.display)
                    .foregroundStyle(AtriumColors.ink)
                Text("\(count) game\(count == 1 ? "" : "s") imported")
                    .atriumStyle(AtriumTypography.inline)
                    .foregroundStyle(AtriumColors.muted)
            }

            bannerView

            AtriumPrimaryButton(title: "Import games") { vm.startImport() }
            AtriumSecondaryButton(title: "Disconnect") { Task { await vm.unlink() } }
                .disabled(vm.busy)
        }
    }

    private func importing(_ inserted: Int, _ target: Int) -> some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space16) {
            Text("Importing".uppercased())
                .atriumStyle(AtriumTypography.kicker)
                .foregroundStyle(AtriumColors.accentCyan)
            Text("\(inserted) of \(target) games")
                .atriumStyle(AtriumTypography.display)
                .foregroundStyle(AtriumColors.ink)
            ProgressView(value: target > 0 ? min(Double(inserted) / Double(target), 1) : 0)
                .tint(AtriumColors.accentCyan)
            Text("This can take a moment — you can leave this screen.")
                .atriumStyle(AtriumTypography.inline)
                .foregroundStyle(AtriumColors.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func errorState(_ message: String) -> some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space12) {
            Text(message)
                .atriumStyle(AtriumTypography.bodyItalic)
                .foregroundStyle(AtriumColors.muted)
                .fixedSize(horizontal: false, vertical: true)
            AtriumSecondaryButton(title: "Retry") { Task { await vm.load() } }
        }
    }

    @ViewBuilder
    private var bannerView: some View {
        if let banner = vm.banner {
            Text(banner)
                .atriumStyle(AtriumTypography.inline)
                .foregroundStyle(AtriumColors.accentAmber)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
