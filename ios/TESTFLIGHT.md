# Running Cereveon on a real iPhone (device / TestFlight pass)

The Appetize web simulator can't reliably deliver keyboard input or exercise the
app's TLS pinning, so the **authoritative** check is a real device. This is a
short runbook for whoever has a Mac. The app and backend are already verified
(the backend returns tokens; fonts, fixes, and a standard text binding are all
compiled in) — this pass is to **see it render** and smoke-test on real hardware.

There's nothing to stand up locally: the app talks to the live `cereveon.com`
backend over the network.

## Prerequisites

- macOS with **Xcode 16+** (the project is Xcode-16 format; iOS 16 deployment target).
- An **Apple ID**.
  - **Free** Apple ID → run on *your own* iPhone (Path A). Good enough for the visual/font pass.
  - **Paid** Apple Developer Program ($99/yr) → TestFlight, to share with other testers (Path B).
- [XcodeGen](https://github.com/yonaskolb/XcodeGen): `brew install xcodegen`.

## 1. Generate and open the project

The `.xcodeproj` is git-ignored and generated from `project.yml`:

```sh
cd ios
xcodegen generate
open Cereveon.xcodeproj
```

## 2. Signing (the only device-specific setup)

The project ships `CODE_SIGNING_ALLOWED = NO` (simulator needs no signing). For a
device build:

1. Select the **Cereveon** target → **Signing & Capabilities**.
2. Check **Automatically manage signing** and pick your **Team** (your Apple ID).
3. If Xcode complains that signing is disabled, set **CODE_SIGNING_ALLOWED = YES**
   for the *Debug* configuration (target build settings), or just let Xcode's
   "automatically manage" fix it for the device destination.
4. Bundle identifier is `ai.chesscoach.app`. With a free Apple ID you may need to
   change it to something unique to you (e.g. `com.<yourname>.cereveon`) — that's
   fine; it doesn't affect anything but provisioning.

## Path A — run on your own iPhone (fastest, free)

1. Plug in your iPhone (or pair wirelessly) and pick it as the run destination.
2. **Product → Run** (⌘R).
3. First launch: the cert is untrusted. On the phone go **Settings → General →
   VPN & Device Management → <your developer cert> → Trust**, then reopen the app.

That's it — you're on the live backend immediately.

## Path B — TestFlight (share with testers, paid account)

1. In [App Store Connect](https://appstoreconnect.apple.com), create an app
   record (bundle id `ai.chesscoach.app`, or your unique one).
2. In Xcode: select **Any iOS Device (arm64)** as the destination →
   **Product → Archive**.
3. In the Organizer: **Distribute App → TestFlight (App Store Connect) → Upload**.
4. Back in App Store Connect → **TestFlight** tab → add the build to **Internal
   Testing** and invite testers by email. Internal testing needs no review;
   external testing needs a quick Beta App Review.

## Smoke checklist (what to verify on device)

- **Auth:** Create account with a valid email + an **8+ character** password
  (the backend rejects shorter with a 400). You should land in onboarding → Home.
- **Fonts (the main goal):** the **"Welcome" / greeting** titles are an italic
  serif (**Cormorant Garamond**); the **kickers** (`CEREVEON · ENTER`, `DAY N`,
  `LIBRARY`) are spaced monospace (**JetBrains Mono**); small inline text is
  **Inter**. No tofu (□) on `→ › — ·` or accented coach text. If everything looks
  like plain San Francisco, fonts didn't register — tell the team.
- **Play:** start a game, make moves, confirm the engine replies and the eval
  band updates.
- **Coach:** open the chat panel over the live board; messages stream.
- **TLS pinning:** that the app reaches the backend at all confirms pinning
  accepts the live chain (it pins LE **YE1** + ISRG **X1/X2**).

## Notes

- **App icon** is a generated placeholder (the framed ♞ knight). Fine for device
  / TestFlight; replace `Assets.xcassets/AppIcon` with final 1024² art before any
  App Store submission.
- Everything else (signing aside) matches `README.md` → *Before a real device /
  TestFlight build*.
