# Engine

On-device chess engine for the iOS app.

## Provenance — do not diverge

`cpp/SachmatuLenta.{h,cpp}` are copied **verbatim** from
`../../../android/app/src/main/cpp/`. Edit them in the Android tree and re-sync;
keeping the two byte-identical is what lets the perft tests guarantee the iOS
build reproduces the Android engine. The Android JNI shim
(`native_chess_engine.cpp`) is **not** copied — it is replaced by the
Objective-C++ wrapper `CereveonEngine.mm`.

## Bridge

- `CereveonEngine.h` — pure Obj-C interface (Swift-importable via the app
  bridging header).
- `CereveonEngine.mm` — Obj-C++; drives `SachmatuLenta`. Mirrors
  `native_chess_engine.cpp`: it computes **Black's** move only (the AI side) and
  ignores the FEN side-to-move field.
- `EngineProvider.swift` — Swift `EngineProvider` protocol + `NativeEngineProvider`.

## Deferred to Phase 2 (board work)

`EngineProvider.kt` on Android runs a coordinate-reconciliation layer
(`JniMoveBridge`, an 8-symmetry search) to map the engine's raw, Black-relative
`(row, col)` move onto the on-screen board square. That mapping is **not** ported
yet — `AIMove` here carries the engine's raw coordinates. Port `JniMoveBridge`
when wiring the engine to the live board.
