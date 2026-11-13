"""Niimbot wire format and model table. Pure functions, no I/O.

Packet framing, command codes and the print sequence follow niimbluelib (MultiMote, MIT):
https://github.com/MultiMote/niimbluelib — its "B1" print task, which it uses for every model below.
"""
from dataclasses import dataclass

from PIL import Image

DPMM = 8  # 203 dpi


@dataclass(frozen=True)
class Model:
    name: str
    ids: tuple[int, ...]  # model ids the printer reports (info key 8)
    head_px: int
    rotate: bool  # niimbluelib printDirection "left": label is sent turned 90 deg clockwise
    densities: range
    default_density: int
    tested: bool


MODELS = {m.name: m for m in (
    Model("D101", (2560,), 192, True, range(1, 4), 2, True),
    Model("B1", (4096,), 384, False, range(1, 6), 3, False),
    Model("B21_C2B", (771, 775), 384, False, range(1, 6), 3, False),
    Model("N1", (3586,), 96, True, range(1, 4), 2, False),
)}

ERRORS = {1: "cover open", 2: "no paper", 3: "low battery", 6: "data error", 7: "overheat",
          8: "paper out", 9: "printer busy", 0x10: "wrong paper"}


def packet(cmd: int, data: bytes = b"\x01") -> bytes:
    checksum = cmd ^ len(data)
    for b in data:
        checksum ^= b
    raw = bytes([0x55, 0x55, cmd, len(data)]) + data + bytes([checksum, 0xAA, 0xAA])
    return b"\x03" + raw if cmd == 0xC1 else raw  # Connect is prefixed with 0x03


def split_packets(buf: bytearray) -> list[tuple[int, bytes]]:
    """Pop complete packets (cmd, data) off the front of buf; keeps any partial tail."""
    out = []
    while True:
        start = buf.find(b"\x55\x55")
        if start < 0 or len(buf) < start + 4:
            return out
        del buf[:start]
        end = 4 + buf[3] + 3
        if len(buf) < end:
            return out
        out.append((buf[2], bytes(buf[4:4 + buf[3]])))
        del buf[:end]


def u16(n: int) -> bytes:
    return n.to_bytes(2, "big")


def model_id(info: bytes) -> int:
    return info[0] << 8 if len(info) == 1 else int.from_bytes(info[:2], "big")


def bitmap_row(row: int, repeat: int, line: bytes, head_px: int) -> bytes:
    chunk = head_px // 8 // 3  # black-pixel counts per third of the head
    counts = [0, 0, 0]
    for i, b in enumerate(line):
        counts[i // chunk] += b.bit_count()
    return packet(0x85, u16(row) + bytes(counts) + bytes([repeat]) + line)


def row_packets(img: Image.Image, head_px: int) -> list[bytes]:
    """img: mode "1", rows = feed direction, cols = across the head, black pixel = 0."""
    cols, rows = img.size
    stride = (cols + 7) // 8
    bits = bytes(b ^ 0xFF for b in img.tobytes())  # mode "1" packs white = 1; the printer wants black = 1
    lines = [bits[r * stride:(r + 1) * stride] for r in range(rows)]
    out, row = [], 0
    while row < rows:
        repeat = 1
        while row + repeat < rows and repeat < 255 and lines[row + repeat] == lines[row]:
            repeat += 1
        line = lines[row]
        out.append(bitmap_row(row, repeat, line, head_px) if any(line) else packet(0x84, u16(row) + bytes([repeat])))
        row += repeat
    return out
