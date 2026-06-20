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

Requires Xcode 15.4+ (iOS 16 deployment target).

## CI

`.github/workflows/ios-ci.yml` runs XcodeGen + `xcodebuild build test` on a
`macos-14` runner for every change under `ios/**`. **This is the authoritative
build/test signal — the app cannot be built on Windows.**

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
