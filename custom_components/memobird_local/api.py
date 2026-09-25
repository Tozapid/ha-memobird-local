"""Client for the Memobird printer's local HTTP API (no cloud, no access key)."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import io
import itertools
import json
import time

import aiohttp
from PIL import Image, ImageOps

ENDPOINT = "/sys/printer"
PRINT_COMMAND = 3

# Print head width in dots.
PAPER_WIDTH = 384

# Sticker ids understood by the firmware for horizontal rules.
LINE_THICK = 41
LINE_THIN = 42
LINE_DASH = 43

# The firmware answers 500 to request bodies somewhere above ~26 KB, so a
# job is split into packages (same printID, pkgNo 1..pkgCount) below this.
MAX_PACKAGE_BYTES = 20_000
# Rows per image strip: 384 dots = 48 bytes a row, ~16 KB once base64'd.
IMAGE_STRIP_ROWS = 256
# Bytes of GBK text per text part.
TEXT_CHUNK_BYTES = 4_000

# The printer silently skips a job whose printID it has already seen,
# so every job gets a fresh, monotonically increasing id.
_print_ids = itertools.count(int(time.time()) % 1_000_000_000)


class MemobirdError(Exception):
    """Raised when the printer can't be reached or rejects a job."""


@dataclass
class PrinterStatus:
    """State reported by GET /sys/printer."""

    printer_state: int
    busy: bool


def prepare_image(data: bytes, *, dither: bool = True) -> Image.Image:
    """Turn any image into a 1-bit bitmap exactly one print head wide.

    Blocking (decoding and resampling): run it in an executor.
    """
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (OSError, ValueError) as err:
        raise MemobirdError(f"Can't read image: {err}") from err

    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        # Transparent areas should come out as paper, not black.
        img = img.convert("RGBA")
        background = Image.new("RGBA", img.size, "white")
        img = Image.alpha_composite(background, img)
    img = img.convert("L")

    if img.width > PAPER_WIDTH:
        height = max(1, round(img.height * PAPER_WIDTH / img.width))
        img = img.resize((PAPER_WIDTH, height), Image.Resampling.LANCZOS)
    if img.width < PAPER_WIDTH:
        canvas = Image.new("L", (PAPER_WIDTH, img.height), 255)
        canvas.paste(img, ((PAPER_WIDTH - img.width) // 2, 0))
        img = canvas

    if dither:
        return img.convert("1")
    return img.point(lambda v: 255 if v >= 128 else 0).convert("1", dither=Image.Dither.NONE)


@dataclass
class Document:
    """A print job made of text blocks, stickers and images."""

    parts: list[dict] = field(default_factory=list)

    def add_text(
        self,
        text: str,
        *,
        big: bool = False,
        bold: bool = False,
        underline: bool = False,
    ) -> Document:
        if not text.endswith("\n"):
            text += "\n"
        # The firmware only understands GBK; characters it can't encode
        # (emoji, mostly) become "?" instead of breaking the job.
        data = text.encode("gbk", errors="replace")
        for chunk in _split_lines(data, TEXT_CHUNK_BYTES):
            self.parts.append(
                {
                    "encodeType": 0,
                    "printType": 1,
                    "basetext": base64.b64encode(chunk).decode("ascii"),
                    "fontSize": 2 if big else 1,
                    "bold": int(bold),
                    "underline": int(underline),
                }
            )
        return self

    def add_line(self, kind: int = LINE_THIN) -> Document:
        self.parts.append({"encodeType": 0, "printType": 4, "iconID": kind})
        return self

    def add_image(self, img: Image.Image) -> Document:
        """Add a bitmap from prepare_image(), split into strips."""
        for top in range(0, img.height, IMAGE_STRIP_ROWS):
            strip = img.crop((0, top, img.width, min(top + IMAGE_STRIP_ROWS, img.height)))
            buf = io.BytesIO()
            # BMP stores rows bottom-up; the printer reads them top-down.
            ImageOps.flip(strip).save(buf, "BMP")
            self.parts.append(
                {
                    "encodeType": 0,
                    "printType": 5,
                    "basetext": base64.b64encode(buf.getvalue()).decode("ascii"),
                }
            )
        return self

    def packages(self) -> list[list[dict]]:
        """Group parts into request-sized packages, preserving order."""
        packages: list[list[dict]] = [[]]
        size = 0
        for part in self.parts:
            part_size = len(json.dumps(part, separators=(",", ":")))
            if packages[-1] and size + part_size > MAX_PACKAGE_BYTES:
                packages.append([])
                size = 0
            packages[-1].append(part)
            size += part_size
        return packages


def _payload(print_id: int, parts: list[dict], pkg_no: int, pkg_count: int) -> str:
    body = {
        "command": PRINT_COMMAND,
        "content": {"textList": parts},
        "encryptFlag": 0,
        "hasHead": 0,
        "hasSignature": 0,
        "hasTail": 0,
        "isFromDirectPrint": False,
        "msgType": 1,
        "pkgCount": pkg_count,
        "pkgNo": pkg_no,
        "printID": print_id,
        "priority": 0,
        "result": 0,
        "scripType": 3,
    }
    # Single-line JSON: the embedded parser is picky about layout.
    return json.dumps(body, ensure_ascii=True, separators=(",", ":"))


def _split_lines(data: bytes, limit: int) -> list[bytes]:
    """Split text at line breaks into chunks of at most `limit` bytes."""
    chunks: list[bytes] = []
    current = b""
    for line in data.splitlines(keepends=True):
        while len(line) > limit:
            # A single huge line: cut it on a GBK character boundary.
            cut = limit
            while cut > 0:
                try:
                    line[:cut].decode("gbk")
                    break
                except UnicodeDecodeError:
                    cut -= 1
            if current:
                chunks.append(current)
                current = b""
            chunks.append(line[:cut])
            line = line[cut:]
        if current and len(current) + len(line) > limit:
            chunks.append(current)
            current = b""
        current += line
    if current:
        chunks.append(current)
    return chunks


class MemobirdClient:
    """Talks to one printer on the LAN."""

    def __init__(
        self, session: aiohttp.ClientSession, host: str, timeout: float = 30
    ) -> None:
        self._session = session
        self._url = f"http://{host}{ENDPOINT}"
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def status(self) -> PrinterStatus:
        data = await self._request("GET")
        return PrinterStatus(
            printer_state=int(data.get("printerState", -1)),
            busy=bool(data.get("busy", 0)),
        )

    async def print_document(self, document: Document) -> int:
        if not document.parts:
            raise MemobirdError("Nothing to print")
        print_id = next(_print_ids)
        packages = document.packages()
        for pkg_no, parts in enumerate(packages, start=1):
            data = await self._request(
                "POST", _payload(print_id, parts, pkg_no, len(packages))
            )
            if data.get("result") != 1:
                raise MemobirdError(
                    f"Printer rejected job {print_id} package {pkg_no}/{len(packages)}: {data}"
                )
        return print_id

    async def _request(self, method: str, body: str | None = None) -> dict:
        try:
            async with self._session.request(
                method,
                self._url,
                data=body,
                headers={"Content-Type": "application/json"},
                timeout=self._timeout,
            ) as resp:
                resp.raise_for_status()
                # The firmware doesn't always send a JSON content type.
                return json.loads(await resp.text())
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise MemobirdError(f"Memobird at {self._url} failed: {err}") from err
