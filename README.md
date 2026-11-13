# niimtext

Print a line of text on a Niimbot label printer over Bluetooth LE, straight from the command line, without the vendor app.

The text comes from stdin and stays in memory: it is never echoed, logged, sent anywhere but the printer, or written to disk (not even as a preview image).

## Why

The Niimbot app works through a cloud account. That is fine for shipping labels and not fine for printing a Wi-Fi key, a recovery code or a password for a sticker on a server. `niimtext` talks to the printer directly, so the secret goes from your password manager to the label and nowhere else.

## Install

Python 3.11+ is needed. From a clone of this repository:

```sh
uv tool install .      # or: pipx install .
```

This puts a `niimtext` command on your `PATH`. It depends on [bleak](https://github.com/hbldh/bleak) (Bluetooth LE) and [Pillow](https://python-pillow.org) (rasterising).

On macOS, the terminal app needs Bluetooth permission the first time (System Settings → Privacy & Security → Bluetooth).

## Usage

Turn the printer on, then pipe the text in:

```sh
# macOS Keychain
security find-generic-password -s my-service -a my-account -w | niimtext --size 30x15

# Linux, pass(1)
pass show wifi/home | niimtext

# anything else
printf 'hello' | niimtext --size 40x12 --copies 2
```

`niimtext` refuses to read from a terminal, because typing the text there would echo it.

Check that a text fits before printing; this reports only the number of lines and the font size, never the text:

```sh
pass show wifi/home | niimtext --dry-run
```

Find printers nearby:

```sh
niimtext --scan
```

macOS shows an opaque Bluetooth UUID instead of a MAC address; either it or the printer's name works with `--device`.

### Options

| Option | Default | Meaning |
|---|---|---|
| `--size WxH` | `30x15` | Label size in mm as you read it. The side across the print head is limited by the model (D101: H ≤ 24 mm). |
| `--model` | `D101` | Printer model, see below. The printer's reported model id must match, otherwise nothing is printed. |
| `--density N` | per model | Print darkness (D101, N1: 1–3, default 2; B1, B21_C2B: 1–5, default 3). |
| `--copies N` | `1` | 1–100. |
| `--device NAME` | first printer of `--model` | BLE name or address from `--scan`. |
| `--font FILE` | bundled JetBrains Mono | Any TrueType/OpenType font. |
| `--dry-run` | | Validate and lay out only; no Bluetooth, no printing. |
| `--scan` | | List nearby Niimbot printers. |

### Layout and refusals

The text is drawn as large as fits the label, centred, in a monospace font where `0`/`O` and `l`/`I`/`1` are clearly different. Long text is wrapped by character (a password has no word boundaries), lines are balanced, and a wrapped label always joins back to exactly the input. Newlines in the input start new lines; a single trailing newline is dropped.

`niimtext` fails instead of printing something that could be misread:

- text that only fits below a 2 mm (16 px) line height;
- a character the font has no glyph for (e.g. emoji);
- control characters (including tab), non-breaking and other special spaces, combining marks;
- leading or trailing spaces on a line, and wraps that would put a space at a line edge (invisible on paper);
- a printer whose reported model differs from `--model`;
- input that is not UTF-8.

Characters are drawn one glyph at a time, so font ligatures (`->` as an arrow, `!=` as `≠`) cannot change what is printed.

## Supported printers

| Model | Head | Status |
|---|---|---|
| D101 | 192 px (24 mm), 203 dpi | **Tested** on macOS 14 |
| B1 | 384 px (48 mm), 203 dpi | Untested |
| B21_C2B | 384 px (48 mm), 203 dpi | Untested |
| N1 | 96 px (12 mm), 203 dpi | Untested |

The untested models use the same print sequence as the D101 in [NiimBlue](https://github.com/MultiMote/niimblue) and their parameters are taken from its model table, so they are expected to work, but nobody has printed on them with `niimtext` yet. If you have one, a report (works or not) is very welcome. With them, `--device` may be needed if the default name match does not find the printer.

The Bluetooth data characteristic is the one the D101 uses; NiimBlue finds it by its properties instead, so a model exposing a different one would fail to connect rather than misprint.

Not supported: D11, D110, original B21 and other models that need a different print sequence in NiimBlue, and 300 dpi models.

Platforms: tested on macOS 14 only. Linux should work through bleak's BlueZ backend but has not been tried. Windows is untested.

## Limits

- Text only: one font size per label, no images, barcodes or QR codes.
- Labels with gaps only (no continuous or black-mark paper).
- Only the D101 has printed real labels so far.
- Wrapping only splits text into equal-length lines; a text where every such split puts a space at a line edge (e.g. `a b c d e f …`) is refused on small labels. Use a bigger `--size`.
- Real labels were printed with an earlier build that used the macOS Menlo font; the bundled JetBrains Mono has not been on paper yet.

## Development

```sh
uv sync
uv run pytest
uv run ruff check .
```

The tests need no printer: they cover packet framing and checksums, the bitmap encoding, the print sequence against a fake printer, layout and wrapping, and every refusal.

## Credits

The protocol is reimplemented from [niimbluelib](https://github.com/MultiMote/niimbluelib) by MultiMote (MIT), the library behind [NiimBlue](https://github.com/MultiMote/niimblue). [niimprint](https://github.com/kjy00302/niimprint) by kjy00302 (MIT, also maintained as a fork by AndBondStyle) was an earlier open implementation of the protocol; no code is taken from it. The bundled font is [JetBrains Mono](https://github.com/JetBrains/JetBrainsMono) (SIL OFL 1.1). See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Niimbot is a trademark of its owner; this project is not affiliated with it.

## Licence

MIT, see [LICENSE](LICENSE). The bundled font stays under the SIL Open Font License 1.1.
