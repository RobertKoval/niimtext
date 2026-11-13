"""Command line and Bluetooth LE session. The label text is never echoed, logged or written to disk."""
import argparse
import asyncio
import re
import sys

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError
from PIL import Image

from . import __version__
from .layout import DEFAULT_FONT, PrintFail, plan, render
from .protocol import DPMM, ERRORS, MODELS, Model, model_id, packet, row_packets, split_packets, u16

CHAR_UUID = "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f"  # Niimbot data characteristic (service e7810a71-...)
NAME = re.compile(r"([A-Z]+\d+[A-Z]*)-")  # advertised names look like D101-<serial>


class Printer:
    def __init__(self, client):
        self.client, self.buf, self.queue = client, bytearray(), asyncio.Queue()

    def _on_notify(self, _char, data: bytearray) -> None:
        self.buf += data
        for p in split_packets(self.buf):
            self.queue.put_nowait(p)

    async def start(self) -> None:
        await self.client.start_notify(CHAR_UUID, self._on_notify)

    async def write(self, raw: bytes) -> None:
        await self.client.write_gatt_char(CHAR_UUID, raw, response=False)
        await asyncio.sleep(0.01)

    async def ask(self, cmd: int, reply: int, data: bytes = b"\x01", timeout: float = 5) -> bytes:
        while not self.queue.empty():
            self.queue.get_nowait()
        await self.write(packet(cmd, data))
        async with asyncio.timeout(timeout):
            while True:
                rcmd, rdata = await self.queue.get()
                if rcmd == reply:
                    return rdata
                if rcmd == 0xDB:
                    raise PrintFail(f"printer error {rdata[0]}: {ERRORS.get(rdata[0], 'unknown')}")
                if rcmd == 0x00:
                    raise PrintFail(f"printer rejected command 0x{cmd:02x}")

    async def wait_printed(self, copies: int) -> None:
        async with asyncio.timeout(30 + 10 * copies):
            while True:
                s = await self.ask(0xA3, 0xB3)
                if len(s) == 10 and s[6]:
                    raise PrintFail(f"printer error {s[6]}: {ERRORS.get(s[6], 'unknown')}")
                page = int.from_bytes(s[:2], "big")
                print(f"status: page {page}/{copies}, print {s[2]}%, feed {s[3]}%", file=sys.stderr)
                if page >= copies:
                    return
                await asyncio.sleep(0.3)

    async def print_image(self, img: Image.Image, head_px: int, copies: int, density: int) -> None:
        cols, rows = img.size
        await self.ask(0x21, 0x31, bytes([density]))
        await self.ask(0x23, 0x33, b"\x01")  # label type: with gaps
        await self.ask(0x01, 0x02, u16(copies) + bytes(5))  # print start, single colour
        try:
            await self.ask(0x03, 0x04)  # page start
            await self.ask(0x13, 0x14, u16(rows) + u16(cols) + u16(copies))
            for raw in row_packets(img, head_px):
                await self.write(raw)
            await self.ask(0xE3, 0xE4)  # page end
            await self.wait_printed(copies)
        finally:
            await self.ask(0xF3, 0xF4)  # print end, also after a failure so the printer is not left mid-job


async def session(client, img: Image.Image, model: Model, copies: int, density: int) -> None:
    p = Printer(client)
    await p.start()
    await p.ask(0xC1, 0xC2)
    found = model_id(await p.ask(0x40, 0x48, b"\x08"))
    if found not in model.ids:
        raise PrintFail(f"printer reports model id {found}, not a {model.name} {model.ids}; refusing to print")
    await p.print_image(img, model.head_px, copies, density)


async def scan() -> None:
    for d, adv in (await BleakScanner.discover(timeout=8, return_adv=True)).values():
        if NAME.match(d.name or ""):
            print(f"{d.address}  {d.name}  rssi {adv.rssi}")


async def find(device: str | None, model: Model):
    family = model.name.split("_")[0]

    def match(d, _adv) -> bool:
        if device:
            return d.address.lower() == device.lower() or d.name == device
        m = NAME.match(d.name or "")
        return bool(m) and m.group(1) == family

    found = await BleakScanner.find_device_by_filter(match, timeout=10)
    if found is None:
        raise PrintFail(f"printer {device or family + '-*'} not found; is it on? try --scan")
    return found


async def run(img: Image.Image, model: Model, copies: int, density: int, device: str | None) -> None:
    async with BleakClient(await find(device, model)) as client:
        await session(client, img, model, copies, density)
    print("printed", file=sys.stderr)


def parse_size(size: str, model: Model) -> tuple[int, int]:
    try:
        w, h = (int(x) for x in size.lower().split("x"))
    except ValueError:
        raise PrintFail("--size must look like 30x15") from None
    across, along = (h, w) if model.rotate else (w, h)  # mm across the print head / along the feed
    limit = model.head_px // DPMM
    if not (0 < across <= limit and 0 < along <= 200):
        side = "H" if model.rotate else "W"
        raise PrintFail(f"bad --size for {model.name}: {side} must be 1..{limit} mm (across the head), "
                        "the other side 1..200 mm")
    return w, h


def read_stdin() -> str:
    if sys.stdin.isatty():
        raise PrintFail("pipe the text on stdin (typing it here would echo it)")
    try:
        text = sys.stdin.buffer.read().decode("utf-8")
    except UnicodeDecodeError:
        raise PrintFail("input is not UTF-8") from None
    text = text[:-1] if text.endswith("\n") else text
    if not text.strip():
        raise PrintFail("empty input")
    return text


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="niimtext", description="Print text from stdin on a Niimbot label printer "
                                 "over Bluetooth LE. The text is never echoed, logged or written to disk.")
    ap.add_argument("--model", default="D101", choices=MODELS, help="printer model (default D101; others untested)")
    ap.add_argument("--size", default="30x15", help="label WxH in mm as you read it (default 30x15)")
    ap.add_argument("--density", type=int, help="darkness: D101, N1 1..3 (default 2); B1, B21_C2B 1..5 (default 3)")
    ap.add_argument("--copies", type=int, default=1, help="1..100 (default 1)")
    ap.add_argument("--device", help="BLE name or address from --scan (default: first printer of --model)")
    ap.add_argument("--font", default=DEFAULT_FONT, help="TrueType/OpenType font file (default: JetBrains Mono)")
    ap.add_argument("--dry-run", action="store_true", help="check the text fits; print line count and font size only")
    ap.add_argument("--scan", action="store_true", help="list nearby Niimbot printers and exit")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return ap


def execute(args: argparse.Namespace) -> None:
    if args.scan:
        return asyncio.run(scan())
    model = MODELS[args.model]
    w, h = parse_size(args.size, model)
    density = model.default_density if args.density is None else args.density
    if density not in model.densities or not 1 <= args.copies <= 100:
        raise PrintFail(f"bad --density/--copies: density {model.densities.start}..{model.densities.stop - 1} "
                        f"for {model.name}, copies 1..100")
    text = read_stdin()
    if args.dry_run:
        lines, size = plan(text, w, h, args.font)
        return print(f"fits: {len(lines)} line(s) at {size} px font on {w}x{h} mm {model.name}", file=sys.stderr)
    asyncio.run(run(render(text, w, h, model, args.font), model, args.copies, density, args.device))


def main() -> int:
    args = parser().parse_args()
    try:
        execute(args)
        return 0
    except PrintFail as e:
        print(f"error: {e}", file=sys.stderr)
    except TimeoutError:
        print("error: printer did not answer in time", file=sys.stderr)
    except BleakError as e:
        print(f"error: bluetooth: {e}", file=sys.stderr)
    except Exception as e:  # type only: no traceback or message that could carry the text
        print(f"error: {type(e).__name__}", file=sys.stderr)
    return 1
