import SwiftUI

/// Cereveon · Atrium · Openings · Repertoire (iOS port of the Android
/// OpeningsActivity). Cards (tap = set active, long-press = delete), an add
/// sheet, and a self-rated "Drill active line" outcome. The on-board drill is a
/// follow-up (5b). Presented full-screen from the Home "Openings" row.
struct OpeningsView: View {
    @StateObject private var vm: OpeningsViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var showAdd = false
    @State private var showDrill = false

    init(auth: AuthViewModel) {
        _vm = StateObject(wrappedValue: OpeningsViewModel(
            client: HTTPRepertoireClient(delegate: PinningURLSessionDelegate()),
            token: { auth.bearerToken }
        ))
    }

    var body: some View {
        NavigationStack {
            ZStack {
                AtriumBackground()
                switch vm.state {
                case .loading:
                    ProgressView().tint(AtriumColors.accentCyan)
                case .error:
                    message("Couldn't load your openings.", "Try again in a moment.")
                case let .loaded(openings):
                    loaded(openings)
                }
            }
            .navigationTitle("Openings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") { dismiss() }.foregroundStyle(AtriumColors.accentCyan)
                }
            }
            .toolbarBackground(AtriumColors.bgBase, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
        }
        .tint(AtriumColors.accentCyan)
        .task { await vm.load() }
        .sheet(isPresented: $showAdd) {
            AddOpeningSheet { eco, name, line in
                Task { await vm.add(eco: eco, name: name, line: line) }
            }
        }
        .confirmationDialog("How did the drill go?", isPresented: $showDrill, titleVisibility: .visible) {
            Button("Nailed it") { drill(1.0) }
            Button("Mostly") { drill(0.6) }
            Button("Forgot it") { drill(0.2) }
            Button("Cancel", role: .cancel) {}
        } message: {
            if let active = vm.activeOpening {
                Text("Active line: \(active.eco) · \(active.name)")
            }
        }
    }

    private func drill(_ outcome: Double) {
        guard let eco = vm.activeOpening?.eco else { return }
        Task { await vm.recordDrill(eco, outcome: outcome) }
    }

    private func loaded(_ openings: [RepertoireOpening]) -> some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: AtriumSpacing.space16) {
                    statsHeader(openings)
                    if let banner = vm.banner {
                        Text(banner)
                            .atriumStyle(AtriumTypography.inline)
                            .foregroundStyle(AtriumColors.accentAmber)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    ForEach(openings) { opening in
                        card(opening)
                    }
                }
                .padding(AtriumSpacing.space24)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            footer
        }
    }

    private func statsHeader(_ openings: [RepertoireOpening]) -> some View {
        HStack(spacing: AtriumSpacing.space32) {
            stat("Lines", "\(openings.count)")
            stat("Avg depth", "\(OpeningsViewModel.avgDepth(openings))")
            Spacer()
        }
    }

    private func stat(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space4) {
            Text(value).atriumStyle(AtriumTypography.display).foregroundStyle(AtriumColors.ink)
            Text(label.uppercased()).atriumStyle(AtriumTypography.kicker).foregroundStyle(AtriumColors.muted)
        }
    }

    private func card(_ o: RepertoireOpening) -> some View {
        Button { Task { await vm.setActive(o.eco) } } label: {
            VStack(alignment: .leading, spacing: AtriumSpacing.space8) {
                HStack {
                    Text(o.eco)
                        .atriumStyle(AtriumTypography.kicker)
                        .foregroundStyle(o.isActive ? AtriumColors.accentCyan : AtriumColors.dim)
                    if o.isActive {
                        Text("Active".uppercased())
                            .atriumStyle(AtriumTypography.kicker)
                            .foregroundStyle(AtriumColors.accentCyan)
                    }
                    Spacer()
                    Text(Self.formatMastery(o.mastery))
                        .atriumStyle(AtriumTypography.kicker)
                        .foregroundStyle(o.isActive ? AtriumColors.accentCyan : AtriumColors.dim)
                }
                Text(o.name).atriumStyle(AtriumTypography.body).foregroundStyle(AtriumColors.ink)
                Text(o.line)
                    .atriumStyle(AtriumTypography.inline)
                    .foregroundStyle(AtriumColors.muted)
                    .fixedSize(horizontal: false, vertical: true)
                masteryBar(o)
            }
            .padding(AtriumSpacing.cardPadding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(o.isActive ? AtriumColors.accentCyan22 : AtriumColors.bgSurface)
            .overlay(
                RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius)
                    .stroke(o.isActive ? AtriumColors.accentCyan55 : AtriumColors.hairline,
                            lineWidth: AtriumSpacing.hairlineThickness)
            )
            .clipShape(RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .contextMenu {
            Button(role: .destructive) {
                Task { await vm.delete(o.eco) }
            } label: {
                Label("Delete \(o.eco)", systemImage: "trash")
            }
        }
    }

    private func masteryBar(_ o: RepertoireOpening) -> some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(AtriumColors.hairline)
                Capsule().fill(o.isActive ? AtriumColors.accentCyan : AtriumColors.muted)
                    .frame(width: max(0, geo.size.width * min(max(o.mastery, 0), 1)))
            }
        }
        .frame(height: 4)
    }

    private var footer: some View {
        VStack(spacing: AtriumSpacing.space12) {
            AtriumPrimaryButton(title: "Drill active line") { showDrill = true }
                .disabled(vm.activeOpening == nil || vm.busy)
            AtriumSecondaryButton(title: "Add opening") { showAdd = true }
        }
        .padding(.horizontal, AtriumSpacing.space24)
        .padding(.vertical, AtriumSpacing.space16)
        .background(AtriumColors.bgBase)
    }

    private func message(_ title: String, _ subtitle: String) -> some View {
        VStack(spacing: AtriumSpacing.space8) {
            Text(title).atriumStyle(AtriumTypography.display).foregroundStyle(AtriumColors.ink)
            Text(subtitle).atriumStyle(AtriumTypography.bodyItalic).foregroundStyle(AtriumColors.muted)
                .multilineTextAlignment(.center)
        }
        .padding(AtriumSpacing.space32)
    }

    static func formatMastery(_ mastery: Double) -> String {
        "\(Int((min(max(mastery, 0), 1) * 100).rounded()))%"
    }
}

/// Add-opening form (ECO / name / line), presented as a sheet.
private struct AddOpeningSheet: View {
    @Environment(\.dismiss) private var dismiss
    let onAdd: (String, String, String) -> Void

    @State private var eco = ""
    @State private var name = ""
    @State private var line = ""

    private var canSave: Bool {
        !eco.trimmingCharacters(in: .whitespaces).isEmpty
            && !name.trimmingCharacters(in: .whitespaces).isEmpty
            && !line.trimmingCharacters(in: .whitespaces).isEmpty
    }

    var body: some View {
        NavigationStack {
            ZStack {
                AtriumBackground()
                ScrollView {
                    VStack(alignment: .leading, spacing: AtriumSpacing.space16) {
                        AtriumTextField(hint: "ECO (e.g. C84)", text: $eco)
                        AtriumTextField(hint: "Name", text: $name)
                        AtriumTextField(hint: "Line (e.g. 1.e4 e5 2.Nf3)", text: $line,
                                        submitLabel: .done)
                        AtriumPrimaryButton(title: "Save") {
                            onAdd(eco, name, line)
                            dismiss()
                        }
                        .disabled(!canSave)
                        .padding(.top, AtriumSpacing.space4)
                    }
                    .padding(AtriumSpacing.space24)
                }
            }
            .navigationTitle("Add opening")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("Cancel") { dismiss() }.foregroundStyle(AtriumColors.muted)
                }
            }
            .toolbarBackground(AtriumColors.bgBase, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
        }
        .tint(AtriumColors.accentCyan)
    }
}
