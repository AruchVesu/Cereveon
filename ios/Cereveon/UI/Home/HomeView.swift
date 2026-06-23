import SwiftUI

/// Cereveon · Atrium · Home / Library — the post-auth landing screen (iOS port
/// of `HomeActivity` / `activity_home.xml`).
///
/// Mirrors the Android Home: a wordmark + avatar header, an italic Cormorant
/// greeting under a mono kicker, a four-row library (Roman numerals I–IV), and
/// a bottom tab bar (Home active). It is presentation + navigation only.
///
/// What is wired here
/// ------------------
///   • **Library rows I–IV** — all route via `.fullScreenCover`: New game →
///     `PlayView`, Lessons → `LessonsView`, Openings → `OpeningsView`, Past
///     games → `GameHistoryView`.
///   • **Resume card** — restores the last in-progress local game from
///     `GameSnapshotStore`; **Today's drill** — the due per-mistake study-plan
///     puzzle, shown only when one is actually due.
///   • **Header kickers** — the "Day N" date line (local first-seen) and the
///     "Level N · X XP" line (once `/auth/me` returns training XP).
///   • **Tab bar** — Home is the only live tab; Lessons / Coach are inert
///     visuals (the Library rows already reach those destinations). **You**
///     opens Settings (coach voice, board style, preferences, account/sign out).
struct HomeView: View {
    @EnvironmentObject private var auth: AuthViewModel

    @State private var showPlay = false
    @State private var showSettings = false
    @State private var showHistory = false
    @State private var showOpenings = false
    @State private var showLessons = false
    @State private var showDrill = false
    /// Today's-drill card — loads the due study-plan puzzle, if any.
    @StateObject private var drill = TodaysDrillViewModel()
    /// Header cosmetics — the Day-N kicker (local first-seen) + the XP kicker.
    @StateObject private var headerVM = HomeHeaderViewModel()
    /// In-progress game offered by the Resume card (nil = nothing resumable).
    @State private var resumable: GameSnapshot?
    /// Snapshot to restore when launching PlayView (nil = a fresh game).
    @State private var resumeSnapshot: GameSnapshot?
    /// Inert tab selection — Home is the only live tab. Stored so the bar can
    /// show a pressed/active accent without yet routing anywhere.
    @State private var selectedTab: Tab = .home

    var body: some View {
        ZStack {
            AtriumBackground()

            VStack(spacing: 0) {
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        header

                        AtriumOrnamentRule()
                            .padding(.top, AtriumSpacing.space12)

                        // Resume — the in-progress local game, if one's still fresh.
                        if let snapshot = resumable {
                            resumeCard(snapshot)
                                .padding(.top, AtriumSpacing.space24)
                        }

                        // Today's drill — the due per-mistake study-plan puzzle,
                        // shown only when one is actually due (else hidden).
                        if let puzzle = drill.puzzle {
                            todaysDrillCard(puzzle)
                                .padding(.top, AtriumSpacing.space24)
                        }

                        librarySection
                            .padding(.top, AtriumSpacing.space24)
                    }
                    .padding(.horizontal, AtriumSpacing.space24)
                    .padding(.vertical, AtriumSpacing.space24)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }

                tabBar
            }
        }
        .fullScreenCover(isPresented: $showPlay) {
            PlayView(auth: auth, resume: resumeSnapshot)
        }
        .sheet(isPresented: $showSettings) {
            SettingsView().environmentObject(auth)
        }
        .fullScreenCover(isPresented: $showHistory) {
            GameHistoryView(auth: auth)
        }
        .fullScreenCover(isPresented: $showOpenings) {
            OpeningsView(auth: auth)
        }
        .fullScreenCover(isPresented: $showLessons) {
            LessonsView(auth: auth)
        }
        .fullScreenCover(isPresented: $showDrill) {
            if let fen = drill.puzzle?.fen, !fen.isEmpty {
                NavigationStack {
                    MistakeReplayView(positions: [fen], token: { auth.bearerToken })
                        .toolbar {
                            ToolbarItem(placement: .navigationBarLeading) {
                                Button("Close") { showDrill = false }
                                    .foregroundStyle(AtriumColors.muted)
                            }
                        }
                }
                .tint(AtriumColors.accentCyan)
            }
        }
        .task { await drill.load(token: auth.bearerToken) }
        .task { await headerVM.loadXP { await auth.trainingXP() } }
        .onAppear { resumable = GameSnapshotStore.resumable() }
        .onChange(of: showPlay) { showing in
            if !showing { resumable = GameSnapshotStore.resumable() }
        }
    }

    // MARK: - Resume

    private func resumeCard(_ snapshot: GameSnapshot) -> some View {
        Button {
            resumeSnapshot = snapshot
            showPlay = true
        } label: {
            VStack(alignment: .leading, spacing: AtriumSpacing.space8) {
                HStack {
                    Text("Resume".uppercased())
                        .atriumStyle(AtriumTypography.kicker)
                        .foregroundStyle(AtriumColors.accentCyan)
                    Spacer()
                    Text(snapshot.resumeSubtitle.uppercased())
                        .atriumStyle(AtriumTypography.kicker)
                        .foregroundStyle(AtriumColors.dim)
                }
                Text(snapshot.resumeTitle)
                    .atriumStyle(AtriumTypography.body)
                    .foregroundStyle(AtriumColors.ink)
                Text("Pick up where you left off \u{2192}")
                    .atriumStyle(AtriumTypography.inline)
                    .foregroundStyle(AtriumColors.muted)
            }
            .padding(AtriumSpacing.cardPadding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(AtriumColors.bgSurface)
            .overlay(
                RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius)
                    .stroke(AtriumColors.accentCyan55, lineWidth: AtriumSpacing.hairlineThickness)
            )
            .clipShape(RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    // MARK: - Today's drill

    private func todaysDrillCard(_ puzzle: TodayPuzzle) -> some View {
        Button { showDrill = true } label: {
            VStack(alignment: .leading, spacing: AtriumSpacing.space8) {
                HStack {
                    Text("Today's drill".uppercased())
                        .atriumStyle(AtriumTypography.kicker)
                        .foregroundStyle(AtriumColors.accentCyan)
                    Spacer()
                    Text("Day \(puzzle.dayNumber) of \(drill.plan?.totalDays ?? 3)".uppercased())
                        .atriumStyle(AtriumTypography.kicker)
                        .foregroundStyle(AtriumColors.dim)
                }
                Text(drillHeadline)
                    .atriumStyle(AtriumTypography.body)
                    .foregroundStyle(AtriumColors.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Text("Tap to solve \u{2192}")
                    .atriumStyle(AtriumTypography.inline)
                    .foregroundStyle(AtriumColors.muted)
            }
            .padding(AtriumSpacing.cardPadding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(AtriumColors.accentCyan22)
            .overlay(
                RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius)
                    .stroke(AtriumColors.accentCyan55, lineWidth: AtriumSpacing.hairlineThickness)
            )
            .clipShape(RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private var drillHeadline: String {
        let verdict = drill.plan?.verdict ?? ""
        return verdict.isEmpty ? "A position from your recent mistakes is due." : verdict
    }

    // MARK: - Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .center, spacing: AtriumSpacing.space12) {
                Text("Cereveon")
                    .atriumStyle(AtriumWordmark.title)
                    .foregroundStyle(AtriumColors.ink)
                    .frame(maxWidth: .infinity, alignment: .leading)

                avatar
            }

            // "<Weekday> · Day NNN" — N from the locally-persisted first-seen date.
            Text(headerVM.dateKicker().uppercased())
                .atriumStyle(AtriumTypography.kicker)
                .foregroundStyle(AtriumColors.muted)
                .padding(.top, AtriumSpacing.space16)

            Text("Continue your study.")
                .atriumStyle(AtriumHomeText.displayTitle)
                .foregroundStyle(AtriumColors.ink)
                .padding(.top, AtriumSpacing.space4)

            // "Level N · X XP" — appears once /auth/me returns the training XP.
            if let xpKicker = headerVM.xpKicker {
                Text(xpKicker.uppercased())
                    .atriumStyle(AtriumTypography.kicker)
                    .foregroundStyle(AtriumColors.accentCyan)
                    .padding(.top, AtriumSpacing.space8)
            }
        }
    }

    /// 32dp cyan-rimmed circle with Cormorant-italic initials derived from the
    /// JWT player id (HomeHeader.initials); "—" when there's no identity yet.
    private var avatar: some View {
        Text(HomeHeader.initials(auth.playerId))
            .atriumStyle(AtriumHomeText.avatar)
            .foregroundStyle(AtriumColors.accentCyan)
            .frame(width: 32, height: 32)
            .background(AtriumColors.accentCyan22)
            .clipShape(Circle())
            .overlay(
                Circle().stroke(AtriumColors.accentCyan55,
                                lineWidth: AtriumSpacing.hairlineThickness)
            )
    }

    // MARK: - Library

    private var librarySection: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Library".uppercased())
                .atriumStyle(AtriumTypography.kicker)
                .foregroundStyle(AtriumColors.muted)
                .padding(.bottom, AtriumSpacing.space8)

            ForEach(Array(LibraryEntry.all.enumerated()), id: \.element.id) { index, entry in
                HomeLibraryRow(entry: entry) {
                    switch entry.route {
                    case .play: resumeSnapshot = nil; showPlay = true
                    case .pastGames: showHistory = true
                    case .openings: showOpenings = true
                    case .lessons: showLessons = true
                    case nil: break
                    }
                }

                if index < LibraryEntry.all.count - 1 {
                    Rectangle()
                        .fill(AtriumColors.hairline)
                        .frame(height: AtriumSpacing.hairlineThickness)
                }
            }
        }
    }

    // MARK: - Tab bar

    private var tabBar: some View {
        VStack(spacing: 0) {
            Rectangle()
                .fill(AtriumColors.hairline)
                .frame(height: AtriumSpacing.hairlineThickness)

            HStack(alignment: .top, spacing: 0) {
                ForEach(Tab.allCases) { tab in
                    HomeTabItem(
                        tab: tab,
                        isActive: tab == .home,
                        action: { handleTab(tab) }
                    )
                    .frame(maxWidth: .infinity)
                }
            }
            .padding(.horizontal, AtriumSpacing.space24)
            .padding(.top, AtriumSpacing.space12)
            .padding(.bottom, AtriumSpacing.space16)
        }
        .background(AtriumColors.bgBase)
    }

    /// Home is the active tab; Lessons opens the lesson, Coach is inert (its panel
    /// lives on the play screen), "You" opens Settings.
    private func handleTab(_ tab: Tab) {
        switch tab {
        case .home, .coach:
            selectedTab = tab // visual only; no destination yet
        case .lessons:
            showLessons = true
        case .you:
            showSettings = true
        }
    }
}

// MARK: - Library model

/// Destination a library row opens. (`LibraryEntry.route` is `Optional` so a
/// future row can be inert with `nil`; every current row routes.)
private enum LibraryRoute { case play, pastGames, openings, lessons }

/// A single Home library row. Roman numeral + Cormorant title + italic sub +
/// chevron, mirroring `Atrium.HomeLibraryRow`.
private struct LibraryEntry: Identifiable {
    let id = UUID()
    let numeral: String
    let title: String
    let sub: String
    let route: LibraryRoute?
    var isFunctional: Bool { route != nil }

    static let all: [LibraryEntry] = [
        LibraryEntry(numeral: "I",   title: "New game",
                     sub: "Adaptive opponent",         route: .play),
        LibraryEntry(numeral: "II",  title: "Lessons",
                     sub: "Curriculum coach",          route: .lessons),
        LibraryEntry(numeral: "III", title: "Openings",
                     sub: "Repertoire trainer",        route: .openings),
        LibraryEntry(numeral: "IV",  title: "Past games",
                     sub: "Game history",              route: .pastGames),
    ]
}

// MARK: - Library row

/// One tappable Atrium library row. Every current row routes; the dim +
/// dropped-hit-test guard below stays as a latent affordance for a future
/// inert (route-less) row.
private struct HomeLibraryRow: View {
    let entry: LibraryEntry
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(alignment: .center, spacing: AtriumSpacing.space12) {
                Text(entry.numeral)
                    .atriumStyle(AtriumHomeText.numeral)
                    .foregroundStyle(AtriumColors.accentCyan)
                    .opacity(entry.isFunctional ? 0.8 : 0.45)
                    .frame(width: 28, alignment: .center)

                VStack(alignment: .leading, spacing: AtriumSpacing.space4) {
                    Text(entry.title)
                        .atriumStyle(AtriumHomeText.rowTitle)
                        .foregroundStyle(AtriumColors.ink)

                    Text(entry.sub)
                        .atriumStyle(AtriumHomeText.rowSub)
                        .foregroundStyle(AtriumColors.muted)
                }
                .frame(maxWidth: .infinity, alignment: .leading)

                Text("\u{203A}") // ›
                    .atriumStyle(AtriumHomeText.chevron)
                    .foregroundStyle(AtriumColors.dim)
            }
            .opacity(entry.isFunctional ? 1.0 : 0.7)
            .padding(.vertical, AtriumSpacing.space12)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        // A route-less row (none today) would read as inert: the tap target
        // stays for layout fidelity but the hit-test is dropped so it can't fire.
        .allowsHitTesting(entry.isFunctional)
    }
}

// MARK: - Tab item

/// Tab identity for the bottom bar. Mirrors the Android `HomeTab` set.
private enum Tab: String, CaseIterable, Identifiable {
    case home    = "Home"
    case lessons = "Lessons"
    case coach   = "Coach"
    case you     = "You"

    var id: String { rawValue }
}

/// One bottom-tab cell: Cormorant-italic label (cyan when active) with a 4pt
/// cyan dot beneath the active tab — mirrors `Atrium.HomeTab`.
private struct HomeTabItem: View {
    let tab: Tab
    let isActive: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: AtriumSpacing.space4) {
                Text(tab.rawValue)
                    .atriumStyle(AtriumHomeText.tabLabel)
                    .foregroundStyle(isActive ? AtriumColors.accentCyan : AtriumColors.muted)

                // Active-state dot below the label (4pt cyan). Inactive tabs
                // reserve the same height so labels stay vertically aligned.
                Circle()
                    .fill(isActive ? AtriumColors.accentCyan : .clear)
                    .frame(width: 4, height: 4)
            }
            .padding(.vertical, AtriumSpacing.space4)
            .frame(maxWidth: .infinity)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Home-local type ramp

/// Atrium text styles specific to the Home surface. The shared
/// `AtriumTypography` ramp doesn't carry the exact Cormorant/Inter sizes the
/// Android Home styles use (22 wordmark, 30 display, 22 numeral, 18 row title,
/// 12 Inter-italic sub, 14 tab), so they're resolved here against the same
/// `AtriumFontFamily` primitives used by the DesignSystem.
private enum AtriumHomeText {
    static let displayTitle = style(.cormorant, 30, italic: true,  em: 0.011)
    static let avatar       = style(.cormorant, 14, italic: true,  em: 0.0)
    static let numeral      = style(.cormorant, 22, italic: true,  em: 0.0)
    static let rowTitle     = style(.cormorant, 18, italic: true,  em: 0.011)
    static let rowSub       = style(.inter,     12, italic: true,  em: 0.0)
    static let chevron      = style(.cormorant, 18, italic: true,  em: 0.0)
    static let tabLabel     = style(.cormorant, 14, italic: true,  em: 0.0)

    private static func style(_ family: AtriumFontFamily,
                              _ size: CGFloat,
                              italic: Bool,
                              em: CGFloat) -> AtriumTextStyle {
        AtriumTextStyle(font: resolve(family, size: size, italic: italic),
                        tracking: em * size)
    }

    /// Custom Atrium font when bundled, else the closest system face — the same
    /// fallback policy as `AtriumTypography.resolve`.
    private static func resolve(_ family: AtriumFontFamily,
                                size: CGFloat,
                                italic: Bool) -> Font {
        let name = family.postScriptName(weight: .medium, italic: italic)
        if UIFont(name: name, size: size) != nil {
            return Font.custom(name, size: size)
        }
        var font = Font.system(size: size, weight: .medium, design: family.systemDesign)
        if italic { font = font.italic() }
        return font
    }
}

/// The wordmark is a hair lighter in weight than the display title; pinned
/// separately so a future tweak to one doesn't drag the other.
private enum AtriumWordmark {
    static let title = AtriumTextStyle(
        font: {
            let name = AtriumFontFamily.cormorant.postScriptName(weight: .medium, italic: true)
            if UIFont(name: name, size: 22) != nil {
                return Font.custom(name, size: 22)
            }
            return Font.system(size: 22, weight: .medium, design: .serif).italic()
        }(),
        tracking: 0.018 * 22
    )
}
