import asyncio
import io
import sys

import pytest
from PIL import Image

from niimtext import cli
from niimtext.layout import PrintFail
from niimtext.protocol import MODELS, packet, split_packets

SECRET = "Sentinel-7f3q"


class Stdin:
    def __init__(self, data: bytes, tty: bool = False):
        self.buffer, self.tty = io.BytesIO(data), tty

    def isatty(self) -> bool:
        return self.tty


def run_main(monkeypatch, capsys, *args, stdin: bytes = SECRET.encode(), tty: bool = False):
    monkeypatch.setattr(sys, "argv", ["niimtext", *args])
    monkeypatch.setattr(sys, "stdin", Stdin(stdin, tty))
    code = cli.main()
    out, err = capsys.readouterr()
    assert SECRET not in out + err, "the label text leaked to the terminal"
    return code, err


@pytest.fixture
def no_bluetooth(monkeypatch):
    async def forbidden(*_a, **_k):
        raise AssertionError("Bluetooth touched")
    monkeypatch.setattr(cli, "run", forbidden)
    monkeypatch.setattr(cli, "scan", forbidden)


def test_dry_run_reports_layout_without_the_text(monkeypatch, capsys, no_bluetooth):
    code, err = run_main(monkeypatch, capsys, "--dry-run")
    assert code == 0 and "2 line(s) at 39 px font" in err


@pytest.mark.parametrize(("args", "stdin", "tty", "message"), [
    ((), SECRET.encode(), True, "pipe the text"),
    ((), b"", False, "empty input"),
    ((), b" \n", False, "empty input"),
    ((), b"\xff" + SECRET.encode(), False, "not UTF-8"),
    ((), b"a\tb", False, "control"),
    (("--size", "30"), SECRET.encode(), False, "--size must look like"),
    (("--font", "/nonexistent.ttf"), SECRET.encode(), False, "--font"),
])
def test_refusals_never_echo_the_text(monkeypatch, capsys, no_bluetooth, args, stdin, tty, message):
    code, err = run_main(monkeypatch, capsys, "--dry-run", *args, stdin=stdin, tty=tty)
    assert code == 1 and message in err


@pytest.mark.parametrize(("model", "size", "ok"), [
    ("D101", "30x23", True), ("D101", "30x24", True), ("D101", "30x25", False),  # H across a 24 mm head
    ("D101", "200x15", True), ("D101", "201x15", False), ("D101", "30x0", False),
    ("B1", "48x20", True), ("B1", "49x20", False),  # W across a 48 mm head
    ("N1", "30x12", True), ("N1", "30x13", False),
])
def test_size_limit_is_the_print_head(monkeypatch, capsys, no_bluetooth, model, size, ok):
    code, err = run_main(monkeypatch, capsys, "--dry-run", "--model", model, "--size", size, stdin=b"ab")
    assert (code == 0) == ok, err


@pytest.mark.parametrize(("args", "ok"), [
    (("--copies", "0"), False), (("--copies", "1"), True), (("--copies", "100"), True), (("--copies", "101"), False),
    (("--density", "0"), False), (("--density", "1"), True), (("--density", "3"), True), (("--density", "4"), False),
    (("--model", "B1", "--density", "5"), True), (("--model", "B1", "--density", "6"), False),
])
def test_copies_and_density_bounds(monkeypatch, capsys, no_bluetooth, args, ok):
    code, err = run_main(monkeypatch, capsys, "--dry-run", *args, stdin=b"ab")
    assert (code == 0) == ok, err


def test_unexpected_error_prints_only_its_type(monkeypatch, capsys, no_bluetooth):
    def boom(text, *_a):
        raise ValueError(text)  # a message that carries the text must not reach the terminal
    monkeypatch.setattr(cli, "plan", boom)
    code, err = run_main(monkeypatch, capsys, "--dry-run")
    assert code == 1 and "ValueError" in err


class FakePrinter:
    """A BLE client that answers like a Niimbot printer and records every packet written to it."""

    def __init__(self, model_id: int, fail_status: int = 0):
        self.model_id, self.fail_status = model_id, fail_status
        self.sent: list[tuple[int, bytes]] = []
        self.copies = 0

    async def start_notify(self, _uuid, callback):
        self.notify = callback

    async def write_gatt_char(self, _uuid, raw: bytes, response: bool):
        buf = bytearray(raw.removeprefix(b"\x03"))
        (cmd, data), = split_packets(buf)
        self.sent.append((cmd, data))
        reply = self.reply(cmd, data)
        if reply:
            self.notify(None, bytearray(packet(*reply)))

    def reply(self, cmd: int, data: bytes):
        if cmd == 0x40:
            return 0x48, self.model_id.to_bytes(2, "big")
        if cmd == 0x01:
            self.copies = int.from_bytes(data[:2], "big")
        if cmd == 0xA3:
            return 0xB3, self.copies.to_bytes(2, "big") + bytes([100, 100, 0, 0, self.fail_status, 0, 0, 0])
        answers = {0xC1: 0xC2, 0x21: 0x31, 0x23: 0x33, 0x01: 0x02, 0x03: 0x04, 0x13: 0x14, 0xE3: 0xE4, 0xF3: 0xF4}
        return (answers[cmd], b"\x01") if cmd in answers else None


def label(cols: int, rows: int) -> Image.Image:
    img = Image.new("1", (cols, rows), 1)
    img.paste(0, (cols // 4, rows // 4, cols // 2, rows // 2))
    return img


def test_print_sequence_matches_niimbluelib_b1_task():
    fake, model = FakePrinter(2560), MODELS["D101"]
    asyncio.run(cli.session(fake, label(120, 240), model, copies=2, density=3))
    commands = [cmd for cmd, _ in fake.sent]
    assert commands[:8] == [0xC1, 0x40, 0x21, 0x23, 0x01, 0x03, 0x13, 0x84]
    assert commands[-3:] == [0xE3, 0xA3, 0xF3]
    sent = dict(fake.sent)
    assert sent[0x21] == b"\x03"  # density
    assert sent[0x01] == bytes.fromhex("00020000000000")  # 2 copies, single colour
    assert sent[0x13] == bytes.fromhex("00f000780002")  # rows 240, cols 120, copies 2
    rows = [d for c, d in fake.sent if c in (0x84, 0x85)]
    assert sum(d[2] if len(d) == 3 else d[5] for d in rows) == 240


def test_wrong_model_is_refused_before_printing():
    fake = FakePrinter(4096)  # a B1 answering when D101 was asked for
    with pytest.raises(PrintFail, match="refusing"):
        asyncio.run(cli.session(fake, label(120, 240), MODELS["D101"], copies=1, density=2))
    assert [cmd for cmd, _ in fake.sent] == [0xC1, 0x40]


def test_printer_error_still_ends_the_print_job():
    fake = FakePrinter(2560, fail_status=2)  # no paper
    with pytest.raises(PrintFail, match="no paper"):
        asyncio.run(cli.session(fake, label(120, 240), MODELS["D101"], copies=1, density=2))
    assert fake.sent[-1][0] == 0xF3
