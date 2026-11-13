"""Text -> label bitmap. Pure functions; nothing is written to disk."""
import unicodedata
from functools import cache
from importlib.resources import files
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .protocol import DPMM, Model

DEFAULT_FONT = files("niimtext") / "fonts" / "JetBrainsMono-Regular.ttf"  # 0/O and l/I/1 differ; Latin, Cyrillic, Greek
MIN_FONT_PX = 16  # 2 mm line; smaller thermal text is hard to read reliably
MARGIN_PX = 8  # 1 mm


class PrintFail(Exception):
    pass


@cache  # layout tries many sizes per wrap
def load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(path), size, layout_engine=ImageFont.Layout.BASIC)  # same with or without raqm
    except OSError:
        raise PrintFail("cannot load the font file given by --font") from None


def text_width(line: str, font: ImageFont.FreeTypeFont) -> float:
    return sum(font.getlength(ch) for ch in line)


def glyph(ch: str, font: ImageFont.FreeTypeFont) -> bytes:
    img = Image.new("L", (64, 64))
    ImageDraw.Draw(img).text((8, 8), ch, font=font, fill=255)
    return img.tobytes()


def check_printable(text: str, font_path: Path) -> None:
    font = load_font(font_path, 40)
    blank, missing = bytes(64 * 64), glyph("\U0010FFFD", font)  # how a glyph the font lacks renders
    if any(p != p.strip() for p in text.split("\n")):
        raise PrintFail("leading or trailing whitespace on a line would be invisible on the label")
    for i, ch in enumerate(text):
        if ch != "\n" and unicodedata.category(ch)[0] in "CZ" and ch != " ":
            raise PrintFail(f"control or non-plain space character at position {i + 1}")
        if unicodedata.category(ch).startswith("M"):
            raise PrintFail(f"combining mark at position {i + 1} would not render reliably")
        if not ch.isspace() and glyph(ch, font) in (blank, missing):
            raise PrintFail(f"character at position {i + 1} has no glyph in the font")


def wraps(text: str):
    """Candidate hard character wraps, widest lines first. Lines join back to the exact text."""
    paragraphs = text.split("\n")
    longest = max(map(len, paragraphs))
    for per_line in sorted({-(-longest // n) for n in range(1, longest + 1)}, reverse=True):  # balanced lines
        lines = [p[i:i + per_line] for p in paragraphs for i in range(0, max(len(p), 1), per_line)]
        if not any(line[:1].isspace() or line[-1:].isspace() for line in lines):  # edge space is invisible
            yield lines


def fit_size(lines: list[str], w: int, h: int, font_path: Path) -> int | None:
    for size in range(min(h // len(lines), 200), MIN_FONT_PX - 1, -1):
        font = load_font(font_path, size)
        asc, desc = font.getmetrics()
        if len(lines) * (asc + desc) <= h and max(text_width(line, font) for line in lines) <= w:
            return size
    return None


def layout(text: str, w: int, h: int, font_path: Path) -> tuple[list[str], int]:
    """Largest font size + hard character wrap that fits w x h px."""
    fits = [(size, lines) for lines in wraps(text) if (size := fit_size(lines, w, h, font_path))]
    if not fits:
        raise PrintFail("text does not fit on the label at a readable size; use a bigger --size")
    size, lines = max(fits, key=lambda f: f[0])  # first max wins: fewest lines at that size
    return lines, size


def label_px(width_mm: int, height_mm: int) -> tuple[int, int]:
    return width_mm * DPMM, height_mm * DPMM


def plan(text: str, width_mm: int, height_mm: int, font_path: Path) -> tuple[list[str], int]:
    """Validate the text and lay it out; renders nothing."""
    w, h = label_px(width_mm, height_mm)
    check_printable(text, font_path)
    return layout(text, w - 2 * MARGIN_PX, h - 2 * MARGIN_PX, font_path)


def draw_line(draw: ImageDraw.ImageDraw, x: float, y: float, line: str, font: ImageFont.FreeTypeFont) -> None:
    for ch in line:  # glyph by glyph: ligatures (e.g. "->" as one arrow) could misprint a password
        draw.text((x, y), ch, font=font, fill=0)
        x += font.getlength(ch)


def render(text: str, width_mm: int, height_mm: int, model: Model, font_path: Path) -> Image.Image:
    """Label drawn as read (width_mm wide), returned oriented for the print head."""
    lines, size = plan(text, width_mm, height_mm, font_path)
    font = load_font(font_path, size)
    asc, desc = font.getmetrics()
    w, h = label_px(width_mm, height_mm)
    img = Image.new("1", (w, h), 1)
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"  # no antialiasing on a thermal head
    y = (h - len(lines) * (asc + desc)) // 2
    for line in lines:
        draw_line(draw, (w - text_width(line, font)) / 2, y, line, font)
        y += asc + desc
    return img.transpose(Image.Transpose.ROTATE_270) if model.rotate else img  # ROTATE_270 = 90 deg clockwise
