# Atrium fonts

The Atrium typography uses three OFL (SIL Open Font License) families. They are
**not yet bundled** — `AtriumTypography` falls back to system faces until the
`.ttf` files are added here.

## Files to add

Drop these exact filenames into this folder. The PostScript name of each font
must match the lookup name in `AtriumTypography.postScriptName(...)`.

| File | Family · weight · style | Source |
|------|------------------------|--------|
| `CormorantGaramond-Regular.ttf`      | Cormorant Garamond · 400 | https://fonts.google.com/specimen/Cormorant+Garamond |
| `CormorantGaramond-Medium.ttf`       | Cormorant Garamond · 500 | ″ |
| `CormorantGaramond-Italic.ttf`       | Cormorant Garamond · 400 italic | ″ |
| `CormorantGaramond-MediumItalic.ttf` | Cormorant Garamond · 500 italic | ″ |
| `JetBrainsMono-Regular.ttf`          | JetBrains Mono · 400 | https://fonts.google.com/specimen/JetBrains+Mono |
| `JetBrainsMono-Medium.ttf`           | JetBrains Mono · 500 | ″ |
| `Inter-Regular.ttf`                  | Inter · 400 | https://fonts.google.com/specimen/Inter |
| `Inter-Medium.ttf`                   | Inter · 500 | ″ |

All three are OFL — redistributable; commit each family's `OFL.txt` alongside.

## After adding the files

1. They bundle automatically (the `Cereveon` source group includes `Resources/`).
2. Register them by adding this to `ios/Cereveon/Info.plist`:

```xml
<key>UIAppFonts</key>
<array>
    <string>CormorantGaramond-Regular.ttf</string>
    <string>CormorantGaramond-Medium.ttf</string>
    <string>CormorantGaramond-Italic.ttf</string>
    <string>CormorantGaramond-MediumItalic.ttf</string>
    <string>JetBrainsMono-Regular.ttf</string>
    <string>JetBrainsMono-Medium.ttf</string>
    <string>Inter-Regular.ttf</string>
    <string>Inter-Medium.ttf</string>
</array>
```

3. If a font's PostScript name differs from its filename, update the lookup in
   `AtriumTypography`, not just the filename.
