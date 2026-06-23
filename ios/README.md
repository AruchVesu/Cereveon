# Cereveon — iOS client

SwiftUI port of the Android Cereveon app (`../android`). It speaks the same
backend contract (`../docs/API_CONTRACTS.md`) and reuses the on-device chess
engine (`SachmatuLenta`, ported verbatim from `../android/app/src/main/cpp`).

## Why XcodeGen (no `.xcodeproj` in git)

The Xcode project is **generated** from `project.yml` via
[XcodeGen](https://github.com/yonaskolb/XcodeGen); `*.xcodeproj/` is git-ignored.
That keeps the project definition a plain-text, diffable file editable on any OS
(the app itself only builds on macOS).

## Build (macOS)

```sh
brew install xcodegen
cd ios
xcodegen generate
open Cereveon.xcodeproj
# or headless:
xcodebuild -scheme Cereveon \
  -destination 'platform=iOS Simulator,name=iPhone 15' \
  clean build test
```

Requires Xcode 16+ (XcodeGen emits the Xcode-16 project format / objectVersion
77; iOS 16 deployment target). The **simulator** build needs no signing
(`CODE_SIGNING_ALLOWED=NO`).

### Before a real device / TestFlight build

The simulator/Appetize path above needs nothing extra. For a **device or
TestFlight** build, two things the simulator doesn't require:

1. **Signing** — in Xcode select the `Cereveon` target → Signing & Capabilities →
   pick a Team (a free Apple ID works for on-device development; a paid Apple
   Developer Program membership is required for TestFlight). XcodeGen leaves
   signing automatic; you may need to flip `CODE_SIGNING_ALLOWED` back on for the
   device destination.
2. **App icon** — `Cereveon/Assets.xcassets/AppIcon` ships a **generated
   placeholder** (the ♞ chess-knight glyph in Atrium cyan on the dark bg). The
   app builds + runs with it; replace `AppIcon.png` with final 1024×1024 artwork
   before an App Store submission.

Fonts (Cormorant / JetBrains Mono / Inter) are still optional — see
`Cereveon/Resources/Fonts/README.md`; the UI falls back to system faces until the
`.ttf` files are added.

## CI

`.github/workflows/ios-ci.yml` runs XcodeGen + `xcodebuild clean build test` on a
`macos-15` runner (Xcode 16.4) for every change under `ios/**`. **This is the
authoritative build/test signal — the app cannot be built on Windows.** Each
green run also uploads a `cereveon-ios-simulator-app` artifact (see below).

## Run it without a Mac (Appetize.io)

The app can't be built on Windows, but you can **run it interactively in a
browser** via [Appetize.io](https://appetize.io) — no Mac, no iPhone, no Apple
Developer account:

1. Open the latest green **iOS CI** run on the
   [Actions tab](../../actions/workflows/ios-ci.yml).
2. Download the **`cereveon-ios-simulator-app`** artifact. GitHub wraps artifacts
   in its own `.zip`, so **unzip it once** to get the inner
   `Cereveon-Simulator.zip` (a zipped iOS-Simulator build of `Cereveon.app`).
3. On [appetize.io](https://appetize.io) (free account) → **Upload app** → drop
   `Cereveon-Simulator.zip`, platform **iOS**.
4. **Tap to play** in the browser — sign in, start a game, and open the coach.
   The simulator reaches the live `cereveon.com` backend, so per-move coaching
   and the **streaming chat** work end-to-end.

Limits: it's a **simulator**, not a physical iPhone (high-fidelity, but do a real
TestFlight pass before any release); Appetize's free tier caps monthly minutes +
one concurrent session.

### Automated publish to a hosted link (optional)

With an Appetize API token configured, every push to `main` publishes the build
straight to a hosted Appetize link — no download/upload needed. One-time setup:

1. Create a free [Appetize.io](https://appetize.io) account → **Account → API
   token**, and add it as a repo secret:
   ```sh
   gh secret set APPETIZE_API_TOKEN      # paste the token when prompted
   ```
2. Push to `main` (or merge a PR). The **Publish to Appetize.io** step uploads the
   build and prints the app link + a `publicKey` in the run's **Summary**.
3. For a **stable link** that updates in place on every run (instead of a new app
   each time), copy that `publicKey` into a repo variable:
   ```sh
   gh variable set APPETIZE_PUBLIC_KEY --body "<publicKey-from-the-summary>"
   ```

The step is best-effort: it only runs on `main`, self-skips when the token isn't
set (the downloadable artifact above still works), and never reds the build if
Appetize is unreachable. The token is sent via HTTP basic auth and masked in logs.

## Layout

```
ios/
  project.yml                     XcodeGen definition (source of truth)
  Cereveon/
    App/                          @main entry + root view
    DesignSystem/                 Atrium tokens (colors, typography, spacing)
    Engine/                       SachmatuLenta C++ + Obj-C++ bridge + Swift provider
      cpp/                        portable engine (copied verbatim from android)
    Resources/Fonts/              fonts go here — see the README there
    Cereveon-Bridging-Header.h    exposes the Obj-C engine wrapper to Swift
    Info.plist
  CereveonTests/                  XCTest (engine perft / best-move)
```

## Invariants (inherited from Atrium and `docs/ARCHITECTURE.md`)

- Dark-only UI; no light mode.
- **No numeric evaluation** is shown — coarse band only.
- **No move arrows** — the focus ring is the only single-square emphasis.
- The coach chat panel is **non-modal** over a live, tappable board.
- The on-device engine plays the opponent only; all coaching/eval truth comes
  from the backend (ESV), never from the local engine.
