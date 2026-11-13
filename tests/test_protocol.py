import pytest
from PIL import Image

from niimtext.protocol import MODELS, model_id, packet, row_packets, split_packets


def test_packet_framing_matches_niimbluelib_vector():
    assert packet(0x4A, b"\x04") == bytes.fromhex("55554a01044faaaa")  # from niimbluelib's parser docs


def test_connect_packet_has_0x03_prefix():
    assert packet(0xC1) == bytes.fromhex("035555c10101c1aaaa")


def test_checksum_is_xor_of_cmd_len_and_data():
    raw = packet(0x13, bytes([0x00, 0xF0, 0x00, 0x78, 0x00, 0x01]))
    assert raw[-3] == 0x13 ^ 6 ^ 0xF0 ^ 0x78 ^ 0x01


def test_split_packets_keeps_partial_tail():
    buf = bytearray.fromhex("55554a01044faaaa5555f6")
    assert split_packets(buf) == [(0x4A, b"\x04")]
    assert buf == bytearray.fromhex("5555f6")


def test_split_packets_reassembles_across_notifications():
    whole = packet(0xB3, bytes(range(10)))
    buf, out = bytearray(), []
    for i in range(0, len(whole), 4):  # BLE notifications can cut a packet anywhere
        buf += whole[i:i + 4]
        out += split_packets(buf)
    assert out == [(0xB3, bytes(range(10)))] and not buf


def test_split_packets_skips_garbage_before_header():
    buf = bytearray(b"\x00\x01") + packet(0x31, b"\x01")
    assert split_packets(buf) == [(0x31, b"\x01")]


@pytest.mark.parametrize(("info", "expected"), [(b"\x0a", 2560), (b"\x0a\x00", 2560), (b"\x10\x00\x07", 4096)])
def test_model_id_one_or_two_bytes(info, expected):
    assert model_id(info) == expected


def decode(packets: list[bytes]) -> list[tuple[int, int, bytes]]:
    """(row, repeat, bitmap or b'' for blank) per packet."""
    out = []
    for p in packets:
        cmd, data = p[2], p[4:-3]
        row = int.from_bytes(data[:2], "big")
        out.append((row, data[2], b"") if cmd == 0x84 else (row, data[5], data[6:]))
    return out


@pytest.mark.parametrize("model", MODELS.values(), ids=MODELS)
def test_row_packets_cover_every_row_once_and_keep_pixels(model):
    cols, rows = model.head_px, 300  # 300 rows forces a repeat run longer than the 255 cap
    img = Image.new("1", (cols, rows), 1)
    for y in range(40, 50):
        for x in range(0, cols, 3):
            img.putpixel((x, y), 0)
    stride = cols // 8
    rebuilt = bytearray()
    next_row = 0
    for row, repeat, bitmap in decode(row_packets(img, model.head_px)):
        assert row == next_row and 1 <= repeat <= 255
        rebuilt += (bitmap or bytes(stride)) * repeat
        next_row += repeat
    assert next_row == rows
    assert bytes(rebuilt) == bytes(b ^ 0xFF for b in img.tobytes())  # black = 1 on the wire


@pytest.mark.parametrize("model", MODELS.values(), ids=MODELS)
def test_bitmap_row_counts_black_pixels_per_head_third(model):
    third = model.head_px // 3
    img = Image.new("1", (model.head_px, 1), 1)
    for x in (0, third - 1, third, 2 * third + 5, 2 * third + 6):  # 2, 1 and 2 per third
        img.putpixel((x, 0), 0)
    (p,) = row_packets(img, model.head_px)
    assert p[2] == 0x85 and list(p[6:9]) == [2, 1, 2]
