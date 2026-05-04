# Handoff: Cereveon — Coaching UI (Atrium language)

## Overview

Cereveon is an AI Chess Coach (Android). This handoff covers the **in-game coaching surface** plus the broader Atrium product flow: onboarding, post-move analysis, coach chat, game-end summary, home/library, lessons, opening repertoire, profile, settings, and paywall.

The core product invariant from the architecture spec is honored throughout the UI: **the coach explains, it does not suggest moves.** No arrows, no "best move" callouts, no centipawn numbers. Engine signal is shown as coarse bands (`losing · worse · equal · better · winning`) only.

## About the Design Files

The files in this bundle are **design references created in HTML** — React + Babel prototypes showing intended look and behavior, not production code to copy directly. Your task is to **recreate these designs in the Cereveon Android codebase** (Jetpack Compose, per the repo's `android/` folder) using its established patterns. The HTML uses inline-styled React components for fast iteration; on Android you'll lift the tokens, layouts, and copy and re-express them as Composables.

## Fidelity

**High-fidelity (hifi).** Final colors, typography, spacing, and copy are all set. Recreate pixel-perfectly using Compose Material 3 with custom theming.

## Design system — "Cereveon · Atrium"

Dark cyberpunk with a scholarly undertone. Restraint is the rule: neon only for *signal* (eval, player turn, focus). Body voice is Cormorant Garamond italic — book-like, calm, unhurried.

### Color tokens (exact)

| Token            | Hex          | Use |
|------------------|--------------|-----|
| `bg.base`        | `#0a0a10`    | App background base |
| `bg.gradient`    | `radial-gradient(ellipse at 50% 100%, #16141f 0%, #0a0a10 70%)` | Default screen background |
| `bg.surface`     | `#0d0e14`    | Board mat, raised cards |
| `ink`            | `#f4efe1`    | Primary text (warm paper) |
| `muted`          | `#9aa0b4`    | Secondary text |
| `dim`            | `#6b7080`    | Tertiary / labels |
| `hairline`       | `rgba(255,255,255,0.08)` | Dividers, borders |
| `accent.cyan`    | `#4fd9e5`    | Player, signal, focus |
| `accent.amber`   | `#ffc069`    | Opponent, warnings, black piece rim |

### Typography (exact)

| Role | Family | Size / Style |
|------|--------|--------------|
| Display title | Cormorant Garamond Italic 500 | 28–44px, line-height 1.0–1.1, letter-spacing 0.3 |
| Body / coach prose | Cormorant Garamond 400 | 16–18px, line-height 1.4–1.45, `text-wrap: pretty` |
| UI label / kicker | JetBrains Mono 500 | 8–11px, letter-spacing 2.0–2.4, UPPERCASE |
| Numerics (clocks, eval) | JetBrains Mono 500 | 11–22px, letter-spacing 0.5 |
| Inline UI text | Inter 400/500 | 11–14px |

Drop-cap pattern: first letter at `44px italic 500`, color `accent.cyan`, with `text-shadow: 0 0 12px rgba(79,217,229,0.4)`. Float left, padding-right 6, padding-top 4.

### Layout primitives

- **Phone canvas**: 412 × 892 dp (Pixel-class), dark frame.
- **Horizontal padding**: 20–24dp on text content; 14–16dp on cards.
- **Section rhythm**: ChapterHeader → ornament rule (`✦`) → content → optional hairline divider.
- **Cards**: 1px hairline border, optional 4 corner ticks (12px, accent color, top-left and bottom-right) for "official document" feel.
- **Buttons**: 44dp height, 1px border, italic Cormorant label at 16px. Primary uses `accent.cyan`. Square-ish 2dp radius — never pill.
- **Eval band**: horizontal track 4–6px, 5 tick marks at the band positions, glowing dot at current band. NO numeric eval anywhere.

### Motion

- `cv-pulse` 1.8–2.2s ease-in-out, opacity 1 ↔ 0.45 — for live indicators and the focus ring on the board.
- All width transitions: 400ms cubic-bezier(.3,.9,.3,1).
- Coach typing dots: staggered 0.15s offset, same pulse keyframe.

## Screens

Each screen lives at 412×892. Sections list components top → bottom.

### 1. In-game coaching · Variation B "Atrium" (the chosen direction)

**Purpose**: Active game. Coach offers commentary on the live position.

Top → bottom:
1. **Chapter header** — Mono kicker `CHAPTER IX · MOVE 14`, italic Cormorant title `The Pin`, ornament rule.
2. **Opponent rail** — amber dot + name + ELO (left), mono clock `14:03` amber (right).
3. **Board** — 332×332 inset on `#0d0e14` mat. Last-move tint (cyan from→to). Focus ring on the piece being discussed (pulsing dashed amber, NOT an arrow).
4. **Player rail** — cyan dot, "You · 1720 · white", clock `14:21` cyan.
5. **Eval band** (full).
6. **Coach paragraph** — drop cap "N", italic Cormorant prose: *"Notice how your pieces are working together here — the bishop on g5 quietly constrains Black's knight, and the central pawn holds the space you've been building toward."*
7. **Footer** — Hairline rule, `THEME · PIN` mono left, `BAND · BETTER` mono right (cyan).
8. **Action bar** — primary "Ask the coach" (italic Cormorant) + 44px square `?` button.

### 2. Onboarding · Skill calibration

**Purpose**: Step 2 of 3. Gather rating estimate + confidence so the adaptation layer can dispatch the first opponent.

- Centered header: mono `CEREVEON · STEP 2 OF 3` (cyan), italic title "How do you play?", italic-muted subtitle, ornament rule.
- Rating slider: cyan glowing dot on rule with `1720` italic 44px above. Tick labels `800 · 1400 · 2000 · 2600+`.
- Confidence radio group: 3 options (Sure of it / Guessing / Rusty), each a row with custom radio dot and italic title + Inter sub.
- First-opponent preview row (amber dot, "~1680 · adaptive").
- Footer: Back + primary Continue.

### 3. Post-move analysis

**Purpose**: After a move, present the coach's reading.

- Chapter header `ANALYSIS · MOVE 14` / "Bg5 · The Pin".
- Hero: 120×120 board thumbnail (left) + move notation block (right): `YOUR MOVE` kicker, `Bg5` mono 28px, `14.8s · library line` mono.
- Eval row: between `POSITION` (left) and `BETTER · HOLDING` (right cyan), compact eval band.
- Drop-cap prose explaining the move. Use `<em>` on the operative word ("constrains"), styled in cyan italic.
- Themes studied: 4 pill chips (Cormorant italic), active ones cyan-bordered.
- Footer: Continue (primary) + Ask why (`?` icon).

### 4. Coach chat

**Purpose**: Multi-turn dialogue. Coach replies are the design centerpiece.

- Chapter header `DIALOGUE` / "With the coach".
- Context chip centered: cyan dot + mono `DISCUSSING · MOVE 14 · BG5`.
- Message list (scrollable):
  - **Coach messages** — left aligned. Mono `COACH` kicker (cyan). Cormorant italic body 16px with a 1px gradient gutter on the left edge (cyan top, fading down) glowing.
  - **User messages** — right aligned. Mono `YOU` kicker (dim). Inter italic 14px muted. No bubble — typography alone separates voices.
- Typing indicator: 3 cyan dots, staggered pulse.
- Composer: hairline-bordered row with placeholder "Ask the coach…" italic, and a 32×32 `→` button (cyan).

### 5. Game-end summary

**Purpose**: Reflect on the just-finished game.

- Chapter header `GAME 047 · CONCLUDED` / "A quiet victory".
- Hero result card with corner ticks: `RESULT` kicker, "Won · 1–0" italic 44px (with cyan halo), mono `38 MOVES · 27:41 · OPPONENT RESIGNED` (cyan).
- 3-cell metric strip (1px hairline grid): Rating `+7` cyan / Accuracy `86%` ink / Theme `Patient` ink. Each has a small mono sub-label.
- Coach reflection paragraph (drop cap "Y").
- Footer: Review game (primary) + New.

### 6. Home / Library

**Purpose**: First screen on app open. Resume + study paths.

- Wordmark "Cereveon" italic + 30dp avatar circle (cyan rim with initials).
- Mono kicker `TUESDAY · DAY 047`, italic title "Continue your study.", ornament rule.
- Resume card — last game thumbnail or position teaser.
- Path cards (3): "Play", "Lessons", "Openings" — each italic Cormorant title, mono sub-kicker, hairline border, optional cyan accent on hover/active.

### 7. Lessons (curriculum)

**Purpose**: Structured study program.

- Chapter header `CURRICULUM` / "Patterns of restraint".
- Progress meter — same eval-band primitive but neutral, position = chapters complete.
- Lesson list rows: index numeral (Cormorant italic 22px, dim if locked / cyan if current), title italic, sub line mono `5 STUDIES · 22 MIN`. Locked rows dim; completed have a cyan tick.

### 8. Opening repertoire

**Purpose**: Saved openings, win rates, study links.

- Header "Repertoire" + W/B toggle (italic radio).
- Cards per opening: italic title (e.g., "Italian Game"), short tagline italic muted, win-rate mini bar (cyan player, amber opponent), mono `12 GAMES · LAST PLAYED 3D AGO`.

### 9. Profile / stats

**Purpose**: Personal trends.

- Italic display rating (44px) with delta (`+7` cyan).
- 3-up metric grid (same primitive as game-end summary).
- A "patterns" section — italic chips of recurring themes ("pin", "central space", "patient endgames").
- Recent games list (compact rows, mono date + result + rating delta).

### 10. Settings

**Purpose**: Preferences.

- Chapter header `SETTINGS`.
- Sections divided by hairline rules: Coach voice (radio: formal/conversational/terse), Board style (radio: flat/engraved/wireframe — already wired in code as a tweak), Sound, Notifications, Account.
- Each row: italic title left, value or chevron right, all 44dp tall.

### 11. Paywall

**Purpose**: Cereveon Plus upsell.

- Hero: italic "Cereveon Plus" 44px with cyan halo, mono kicker.
- 3-feature list: each row = small cyan tick + italic feature title + Inter sub.
- Primary CTA full-width "Begin · 30 days free" italic, mono price sub.
- Restore + Terms links muted bottom.

## Interactions & Behavior

- **Board piece focus ring**: pulses 1.8s. Set by the model layer when the coach is referencing a specific square; cleared when the next move is made or the user taps elsewhere.
- **Eval band updates**: animate width 400ms when band changes. Never show centipawn numbers — only the 5-band step.
- **Last-move highlight**: cyan tint on `from` (light) and `to` (filled, with 1.5px inner border).
- **Coach commentary feed**: prose appears with a 200ms fade + 8px upward translate. Typing dots show during LLM streaming; replace with body when done.
- **Chat composer**: pressing send disables → shows typing dots → coach reply streams in.
- **Onboarding slider**: live updates the rating numeral and the "first opponent" preview band as the user drags.

## State Management

Use the Cereveon backend contract from `ARCHITECTURE.md`:
- `position: FEN` (single source of truth).
- `engine.signal: { band: 'losing'|'worse'|'equal'|'better'|'winning'|'mate', side: 'white'|'black' }` — derived server-side from Stockfish.
- `coach.commentary: { text: string, themes: string[], focusSquare?: 'a1'..'h8' }` — from the LLM via the SECA-validated pipeline.
- `clock: { white: ms, black: ms, turn: 'white'|'black' }`.
- `move.history: SAN[]`.

UI never reads centipawns or PV lines — they are filtered out before reaching the client per the spec.

## Design Tokens (Compose translation hint)

```kotlin
object CereveonColors {
    val BgBase = Color(0xFF0A0A10)
    val BgSurface = Color(0xFF0D0E14)
    val Ink = Color(0xFFF4EFE1)
    val Muted = Color(0xFF9AA0B4)
    val Dim = Color(0xFF6B7080)
    val Hairline = Color(0xFFFFFFFF).copy(alpha = 0.08f)
    val AccentCyan = Color(0xFF4FD9E5)
    val AccentAmber = Color(0xFFFFC069)
}
```

Spacing scale: 4 / 8 / 12 / 16 / 20 / 24 / 32 / 44 dp.

## Assets

- **Fonts**: Cormorant Garamond (Google Fonts), JetBrains Mono (Google Fonts), Inter (Google Fonts). Bundle the WOFF2/TTF subsets for the weights used: Cormorant 400/500 + italics, JetBrains 400/500, Inter 400/500.
- **Chess pieces**: Unicode `♔♕♖♗♘♙♚♛♜♝♞♟` rendered as text. White pieces use ivory fill `#f4efe1` with a soft cyan rim (text-shadow). Black pieces use a warm obsidian fill `#1a1108` with a 4-direction amber outline + halo so they read on both square tones. On Android, replicate this via a custom piece text view that draws the glyph + shadow stack, or pre-render to a vector asset per piece. **Do not** introduce a different piece set.
- **Icons**: minimal — `←`, `?`, `→`, `⋯`, `✦` (ornament). All as text glyphs from Cormorant or system; no bespoke iconography needed.

## Files in this bundle

| File | Role |
|------|------|
| `Cereveon Coaching.html` | Entry point — opens the design canvas with all 11 screens |
| `app.jsx` | Canvas wiring + tweaks panel |
| `coach-common.jsx` | Shared atoms — `CoachBoard`, `EvalBand`, `HairlineRule`, `CapturedRow`, `CoachMark`, sample `CV_POSITION` |
| `coach-variant-atrium.jsx` | The chosen in-game coaching variant (the canonical Atrium screen) |
| `coach-variant-obsidian.jsx` | Variant A — reference, not the chosen direction |
| `coach-variant-hud.jsx` | Variant C — reference, not the chosen direction |
| `atrium-screens.jsx` | Onboarding, post-move analysis, coach chat, game-end summary |
| `atrium-screens-2.jsx` | Home, lessons, repertoire, profile, settings, paywall |
| `android-frame.jsx` | Device frame (status bar + nav pill) — for visual context only |
| `design-canvas.jsx` | Pan/zoom presentation shell — not part of the product |
| `tweaks-panel.jsx` | Live tweak shell — not part of the product |

To preview the design locally, open `Cereveon Coaching.html` in a browser.

## Implementation order (suggested)

1. Set up the Compose theme with the color tokens and font families above.
2. Build the shared atoms first — `CoachBoard` (with focus ring + last-move tint), `EvalBand`, `ChapterHeader`, `DropCapText`, `AtriumButton`. These cover ~70% of every screen.
3. Implement Variation B (Atrium in-game) end-to-end against a stubbed game state. This validates the system.
4. Layer in onboarding → analysis → chat → summary. These reuse the same atoms.
5. Home / lessons / repertoire / profile / settings / paywall.
6. Hook to backend contracts. Verify the no-numeric-eval and no-move-suggestion invariants hold in the rendered UI (spot-check with the SECA validator's output).

## Out of scope for this handoff

- The Stockfish pool, opponent engine, RAG store, SECA validator — these are backend concerns already specified in `ARCHITECTURE.md` and `pipeline.md`.
- Achievements, leaderboards, social — not designed.
- Light mode — Cereveon Atrium is dark-only by design intent.
