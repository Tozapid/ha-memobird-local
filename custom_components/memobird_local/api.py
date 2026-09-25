"""Client for the Memobird printer's local HTTP API (no cloud, no access key)."""

from __future__ import annotations

import base64
import itertools
import json
import time
from dataclasses import dataclass, field

import aiohttp

ENDPOINT = "/sys/printer"
PRINT_COMMAND = 3

# Sticker ids understood by the firmware for horizontal rules.
LINE_THICK = 41
LINE_THIN = 42
LINE_DASH = 43

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


@dataclass
class Document:
    """A print job made of text blocks and stickers."""

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
        self.parts.append(
            {
                "encodeType": 0,
                "printType": 1,
                "basetext": base64.b64encode(data).decode("ascii"),
                "fontSize": 2 if big else 1,
                "bold": int(bold),
                "underline": int(underline),
            }
        )
        return self

    def add_line(self, kind: int = LINE_THIN) -> Document:
        self.parts.append({"encodeType": 0, "printType": 4, "iconID": kind})
        return self

    def payload(self, print_id: int) -> str:
        body = {
            "command": PRINT_COMMAND,
            "content": {"textList": self.parts},
            "encryptFlag": 0,
            "hasHead": 0,
            "hasSignature": 0,
            "hasTail": 0,
            "isFromDirectPrint": False,
            "msgType": 1,
            "pkgCount": 1,
            "pkgNo": 1,
            "printID": print_id,
            "priority": 0,
            "result": 0,
            "scripType": 3,
        }
        # Single-line JSON: the embedded parser is picky about layout.
        return json.dumps(body, ensure_ascii=True, separators=(",", ":"))


class MemobirdClient:
    """Talks to one printer on the LAN."""

    def __init__(
        self, session: aiohttp.ClientSession, host: str, timeout: float = 10
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
        data = await self._request("POST", document.payload(print_id))
        if data.get("result") != 1:
            raise MemobirdError(f"Printer rejected job {print_id}: {data}")
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
