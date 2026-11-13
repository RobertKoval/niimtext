# Changelog

## 0.1.0 — unreleased

- First release: print text from stdin on a Niimbot D101 over Bluetooth LE (tested on macOS 14).
- B1, B21_C2B and N1 enabled from NiimBlue's model table, untested.
- Bundled JetBrains Mono font; glyph-by-glyph drawing so ligatures cannot alter the text.
- `--dry-run` reports line count and font size without rendering or printing.
- Refuses text it cannot print unambiguously, and printers whose model id does not match.
