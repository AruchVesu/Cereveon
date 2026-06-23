# Atrium fonts

The Atrium typography uses three OFL (SIL Open Font License) families. They are
**bundled** here and registered via `Info.plist` `UIAppFonts`;
`AtriumTypography.resolve(...)` looks each face up by its PostScript name and
falls back to the closest system face only if one fails to load.

## Bundled faces

The PostScript name of each file equals its filename stem — that is the lookup
key in `AtriumTypography.postScriptName(...)` and the string in `UIAppFonts`.

| File (= PostScript name) | Family · weight · style |
|--------------------------|-------------------------|
| `CormorantGaramond-Regular.ttf`      | Cormorant Garamond · 400 |
| `CormorantGaramond-Medium.ttf`       | Cormorant Garamond · 500 |
| `CormorantGaramond-Italic.ttf`       | Cormorant Garamond · 400 italic |
| `CormorantGaramond-MediumItalic.ttf` | Cormorant Garamond · 500 italic |
| `JetBrainsMono-Regular.ttf`          | JetBrains Mono · 400 |
| `JetBrainsMono-Medium.ttf`           | JetBrains Mono · 500 |
| `Inter-Regular.ttf`                  | Inter · 400 |
| `Inter-Medium.ttf`                   | Inter · 500 |

`BundledFontsTests` asserts every face is in the app bundle and resolves by its
PostScript name, and that the typography lookup agrees with those names — so a
rename or a dropped `UIAppFonts` entry fails CI rather than silently degrading.

## Provenance

All three families are OFL — redistributable with the license shipped alongside
(`OFL-CormorantGaramond.txt`, `OFL-Inter.txt`, `OFL-JetBrainsMono.txt`; kept in
the repo, excluded from the app bundle in `project.yml`).

- **JetBrains Mono** — static `JetBrainsMono-{Regular,Medium}.ttf` taken verbatim
  from [JetBrains/JetBrainsMono](https://github.com/JetBrains/JetBrainsMono)
  (`fonts/ttf/`).
- **Cormorant Garamond** and **Inter** — upstream (google/fonts) now ships these
  as *variable* fonts only, which `UIFont(name:)` can't address per weight. The
  static faces here were instantiated from the variable sources with
  `fonttools varLib.instancer` (Cormorant pinned at `wght` 400/500; Inter pinned
  at `opsz` 14, `wght` 400/500), with the name table set so each file's
  PostScript name matches its stem.

To regenerate, pin the same axes from the current variable sources and re-set the
PostScript / family names to the stems above.

## Latin subset

The bundled faces are subset to Latin plus the punctuation/symbol ranges the UI
actually uses — Basic Latin, Latin-1 Supplement, Latin Extended-A/B, General
Punctuation, basic arrows, and every non-ASCII codepoint found in the iOS Swift
source — via `fonttools` `Subsetter` with `name_IDs=['*']` so the PostScript name
survives. This drops non-Latin coverage the app never renders (Cyrillic, Greek,
Vietnamese, …) under the invariant `dropped_used == 0` (no codepoint the source
uses was removed).

Cormorant Garamond additionally keeps only the OpenType features SwiftUI applies
by default (`layout_features=['ccmp','calt','liga','locl','kern']`) — no source
enables any optional feature, so its small caps, stylistic sets (`ss##`), char
variants (`cv##`), oldstyle figures and swashes (~1000 alternate glyphs per face)
are pruned with no visual change. JetBrains Mono and Inter keep all features
(`['*']`); they carry no such alternate bloat. Net **~3.6 MB → ~0.9 MB**.

The chess pieces on the board are Unicode glyphs (U+265A–F) drawn by system font
substitution, **not** these text fonts, so subsetting does not affect the board.
`BundledFontsTests.testSubsetFacesResolveAndCoverPrintableAscii` guards the floor
(each face resolves to itself and still covers printable ASCII).
