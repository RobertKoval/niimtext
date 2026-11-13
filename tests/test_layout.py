import pytest
from PIL import Image, ImageDraw

from niimtext.layout import DEFAULT_FONT, MARGIN_PX, MIN_FONT_PX, PrintFail, draw_line, layout, load_font, plan, render
from niimtext.protocol import MODELS

D101, B1 = MODELS["D101"], MODELS["B1"]
W, H = 30 * 8 - 2 * MARGIN_PX, 15 * 8 - 2 * MARGIN_PX  # 30x15 mm label inside the margins


def fits(text: str, size: str = "30x15") -> bool:
    w, h = (int(x) for x in size.split("x"))
    try:
        plan(text, w, h, DEFAULT_FONT)
        return True
    except PrintFail:
        return False


@pytest.mark.parametrize("text", [
    "ab  cd 0O lI1 £ пароль x" * 2,
    "x" * 60,
    "correct horse battery staple",
    "first\nsecond line\nthird",
    "short",
])
def test_wrap_joins_back_to_the_exact_text(text):
    lines, _ = layout(text, W, H, DEFAULT_FONT)
    assert "".join(lines) == text.replace("\n", ""), "wrap changed the text"
    assert len(lines) >= text.count("\n") + 1
    assert all(line == line.strip() for line in lines), "space at a line edge would be invisible"


def test_font_is_as_large_as_fits():
    _, one_line = layout("ab", W, H, DEFAULT_FONT)
    _, many = layout("ab" * 12, W, H, DEFAULT_FONT)
    assert one_line > many >= MIN_FONT_PX


@pytest.mark.parametrize("bad", ["a\U0001F600b", "a\tb", "a\u00a0b", "a\u200bb", "e\u0301", " pw", "pw ", "a\n b"])
def test_refuses_text_that_would_print_ambiguously(bad):
    assert not fits(bad)


def test_refuses_text_too_long_for_a_readable_font():
    assert not fits("x" * 400)


def test_fit_boundary_one_more_character_is_refused():
    lo, hi = 1, 400  # fits("x" * lo) and not fits("x" * hi)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if fits("x" * mid) else (lo, mid)
    longest = lo
    assert fits("x" * longest) and not fits("x" * (longest + 1))


def test_refuses_unreadable_font_file(tmp_path):
    bad = tmp_path / "not-a-font.ttf"
    bad.write_bytes(b"nope")
    with pytest.raises(PrintFail, match="--font"):
        plan("abc", 30, 15, bad)


def ink(img: Image.Image) -> list[tuple[int, int]]:
    cols, rows = img.size
    return [(x, y) for y in range(rows) for x in range(cols) if img.getpixel((x, y)) == 0]


def test_d101_label_is_turned_clockwise_for_the_head():
    img = render("A\n.", 30, 15, D101, DEFAULT_FONT)
    cols, rows = img.size
    assert (cols, rows) == (120, 240)  # cols across the head = label height
    dots = ink(img)
    high = sum(x >= cols // 2 for x, _ in dots)
    assert high > 3 * (len(dots) - high) > 0, "label top line (big A) must land in the high columns"


def test_b1_label_is_sent_upright():
    img = render("A\n.", 40, 20, B1, DEFAULT_FONT)
    cols, rows = img.size
    assert (cols, rows) == (320, 160)
    dots = ink(img)
    top = sum(y < rows // 2 for _, y in dots)
    assert top > 3 * (len(dots) - top) > 0, "label top line (big A) must land in the first rows"


@pytest.mark.parametrize("text", ["W", "WWWWWWWWWWWWWWWWWWWW", "ab\ncd\nef"])
def test_ink_stays_inside_the_margin(text):
    img = render(text, 30, 15, D101, DEFAULT_FONT)
    cols, rows = img.size
    assert all(MARGIN_PX <= x < cols - MARGIN_PX and MARGIN_PX <= y < rows - MARGIN_PX for x, y in ink(img))


@pytest.mark.parametrize("pair", ["->", "!=", "=>", "<=", ":=", "/*"])
def test_ligature_pairs_print_as_separate_characters(pair):
    font = load_font(DEFAULT_FONT, 40)

    def drawn(parts):
        img = Image.new("1", (200, 60), 1)
        draw = ImageDraw.Draw(img)
        x = 0.0
        for part in parts:
            draw_line(draw, x, 0, part, font)
            x += sum(font.getlength(ch) for ch in part)
        return img.tobytes()

    assert drawn([pair]) == drawn(list(pair))
