"""Render Markdown or HTML-formatted text into a bitmap for the printer.

The firmware can only style a whole text block, so rich text is laid out
and drawn here with bundled DejaVu fonts, then printed as an image.

Supported HTML: <b> <strong> <i> <em> <u> <ins> <s> <del> <strike> <code>
<tt> <big> <small> <h1>-<h3> <p> <div> <center> <br> <hr> <ul> <ol> <li>.
Supported Markdown: **bold** __bold__ *italic* _italic_ ++underline++
~~strike~~ `code`, # headings, - / * / + bullets, 1. numbered lists,
--- rules and backslash escapes. As in Telegram, line breaks are kept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from html import escape
from html.parser import HTMLParser
from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageFont

FORMAT_PLAIN = "plain"
FORMAT_MARKDOWN = "markdown"
FORMAT_HTML = "html"
FORMATS = [FORMAT_PLAIN, FORMAT_MARKDOWN, FORMAT_HTML]

FONT_DIR = Path(__file__).parent / "fonts"
BASE_SIZE = 24
SIZES = {"h1": 40, "h2": 34, "h3": 28, "big": 34, "small": 20}
LINE_SPACING = 1.25
BLOCK_GAP = 8
MARGIN = 4
BULLET = "• "
# Anti-aliased glyph edges darker than this become black dots.
INK_THRESHOLD = 160
# DejaVu has no emoji: drop them (and their joiners/variation selectors)
# rather than printing empty boxes.
_NO_GLYPH = re.compile("[\U00010000-\U0010FFFF\u200d\ufe0e\ufe0f]")


@dataclass
class Run:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    mono: bool = False
    size: int = BASE_SIZE


@dataclass
class Block:
    runs: list[Run] = field(default_factory=list)
    bullet: str | None = None
    indent: int = 0
    center: bool = False
    rule: bool = False
    gap_before: int = 0


@lru_cache(maxsize=64)
def _font(bold: bool, italic: bool, mono: bool, size: int) -> ImageFont.FreeTypeFont:
    if mono:
        name = "DejaVuSansMono-Bold.ttf" if bold else "DejaVuSansMono.ttf"
    else:
        name = {
            (False, False): "DejaVuSans.ttf",
            (True, False): "DejaVuSans-Bold.ttf",
            (False, True): "DejaVuSans-Oblique.ttf",
            (True, True): "DejaVuSans-BoldOblique.ttf",
        }[(bold, italic)]
    return ImageFont.truetype(str(FONT_DIR / name), size)


def _run_font(run: Run) -> ImageFont.FreeTypeFont:
    return _font(run.bold, run.italic, run.mono, run.size)


class _Parser(HTMLParser):
    """Turns the supported HTML subset into blocks of styled runs."""

    INLINE = {
        "b": "bold", "strong": "bold",
        "i": "italic", "em": "italic",
        "u": "underline", "ins": "underline",
        "s": "strike", "del": "strike", "strike": "strike",
        "code": "mono", "tt": "mono",
    }
    HEADINGS = {"h1", "h2", "h3"}
    BLOCKS = {"p", "div", "center", *HEADINGS}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self._styles: list[str] = []
        self._sizes: list[int] = []
        self._lists: list[list] = []  # [ordered, counter]
        self._center = 0
        self._block = Block()
        self._gap = 0
        # A newline right after a block tag is layout, not an empty line.
        self._after_block = False

    # Blocks

    def _flush(self, gap: int = 0) -> None:
        block = self._block
        if block.runs or block.bullet:
            block.gap_before = max(block.gap_before, self._gap)
            self.blocks.append(block)
            self._gap = 0
        self._block = Block(center=self._center > 0, indent=self._indent())
        self._gap = max(self._gap, gap)

    def _newline(self) -> None:
        if self._block.runs or self._block.bullet:
            self._flush()
        else:
            # An empty line keeps its height, as in a chat message.
            self._gap += int(BASE_SIZE * LINE_SPACING)

    def _indent(self) -> int:
        return max(0, len(self._lists) - 1) * BASE_SIZE

    # Tags

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.INLINE:
            self._styles.append(self.INLINE[tag])
        elif tag in ("big", "small"):
            self._sizes.append(SIZES[tag])
        elif tag == "br":
            self._newline()
            self._after_block = True
        elif tag == "hr":
            self._flush(BLOCK_GAP)
            self.blocks.append(Block(rule=True, gap_before=self._gap))
            self._gap = BLOCK_GAP
            self._after_block = True
        elif tag in ("ul", "ol"):
            self._flush(BLOCK_GAP if not self._lists else 0)
            self._lists.append([tag == "ol", 0])
            self._after_block = True
        elif tag == "li":
            self._flush()
            if self._lists:
                self._lists[-1][1] += 1
                ordered, number = self._lists[-1]
                self._block.bullet = f"{number}. " if ordered else BULLET
            else:
                self._block.bullet = BULLET
            self._block.indent = self._indent()
        elif tag in self.BLOCKS:
            if tag == "center":
                self._center += 1
            self._flush(BLOCK_GAP)
            if tag in self.HEADINGS:
                self._sizes.append(SIZES[tag])
                self._styles.append("bold")

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag not in self.INLINE and tag not in ("big", "small"):
            self._after_block = True
        if tag in self.INLINE:
            _remove_last(self._styles, self.INLINE[tag])
        elif tag in ("big", "small"):
            _remove_last(self._sizes, SIZES[tag])
        elif tag in ("ul", "ol"):
            self._flush(BLOCK_GAP)
            if self._lists:
                self._lists.pop()
            self._block.indent = self._indent()
        elif tag == "li":
            self._flush()
        elif tag in self.BLOCKS:
            if tag in self.HEADINGS:
                _remove_last(self._sizes, SIZES[tag])
                _remove_last(self._styles, "bold")
            self._flush(BLOCK_GAP)
            if tag == "center":
                self._center = max(0, self._center - 1)
                self._block.center = self._center > 0

    def handle_data(self, data: str) -> None:
        if self._after_block and data.startswith("\n"):
            data = data[1:]
        self._after_block = False
        data = _NO_GLYPH.sub("", data)
        # Keep line breaks (Telegram-style), collapse other whitespace.
        for i, line in enumerate(data.split("\n")):
            if i:
                self._newline()
            line = re.sub(r"[ \t\r\f\v]+", " ", line)
            if not self._block.runs:
                line = line.lstrip()
            if line:
                self._block.runs.append(
                    Run(
                        line,
                        bold="bold" in self._styles,
                        italic="italic" in self._styles,
                        underline="underline" in self._styles,
                        strike="strike" in self._styles,
                        mono="mono" in self._styles,
                        size=self._sizes[-1] if self._sizes else BASE_SIZE,
                    )
                )

    def close(self) -> list[Block]:
        super().close()
        self._flush()
        return self.blocks


def _remove_last(stack: list, value) -> None:
    for i in range(len(stack) - 1, -1, -1):
        if stack[i] == value:
            del stack[i]
            return


_ESCAPABLE = r"\\`*_~+#\-\[\]()!.>|{}"
_INLINE_RULES = [
    (re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*"), r"<b>\1</b>"),
    (re.compile(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)"), r"<b>\1</b>"),
    (re.compile(r"\+\+(?=\S)(.+?)(?<=\S)\+\+"), r"<u>\1</u>"),
    (re.compile(r"~~(?=\S)(.+?)(?<=\S)~~"), r"<s>\1</s>"),
    (re.compile(r"(?<![*\w])\*(?=\S)(.+?)(?<=\S)\*(?![*\w])"), r"<i>\1</i>"),
    (re.compile(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)"), r"<i>\1</i>"),
]


def markdown_to_html(text: str) -> str:
    """Convert the supported Markdown subset to the supported HTML subset."""
    stash: list[str] = []

    def keep(html: str) -> str:
        stash.append(html)
        return f"\x00{len(stash) - 1}\x00"

    def inline(line: str) -> str:
        line = re.sub(rf"\\([{_ESCAPABLE}])", lambda m: keep(escape(m[1])), line)
        line = re.sub(r"`([^`]+)`", lambda m: keep(f"<code>{escape(m[1])}</code>"), line)
        line = escape(line, quote=False)
        for pattern, repl in _INLINE_RULES:
            line = pattern.sub(repl, line)
        return line

    out: list[str] = []
    list_tag: str | None = None
    for raw in text.split("\n"):
        line = raw.rstrip()
        item = re.match(r"^\s*(?:([-*+])|(\d+)[.)])\s+(.*)$", line)
        if item and not re.fullmatch(r"\s*([-*_])(\s*\1){2,}\s*", line):
            tag = "ol" if item[2] else "ul"
            if list_tag != tag:
                if list_tag:
                    out.append(f"</{list_tag}>")
                out.append(f"<{tag}>")
                list_tag = tag
            out.append(f"<li>{inline(item[3])}</li>")
            continue
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None
        if heading := re.match(r"^(#{1,3})\s+(.*)$", line):
            level = len(heading[1])
            out.append(f"<h{level}>{inline(heading[2])}</h{level}>")
        elif re.fullmatch(r"\s*([-*_])(\s*\1){2,}\s*", line):
            out.append("<hr>")
        else:
            out.append(inline(line) + "<br>")
    if list_tag:
        out.append(f"</{list_tag}>")

    html = "".join(out)
    return re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m[1])], html)


def parse(text: str, fmt: str) -> list[Block]:
    if fmt == FORMAT_MARKDOWN:
        text = markdown_to_html(text)
    elif fmt != FORMAT_HTML:
        text = escape(text, quote=False)
    parser = _Parser()
    parser.feed(text)
    blocks = parser.close()
    # Drop trailing empty lines so the paper isn't wasted.
    while blocks and not blocks[-1].runs and not blocks[-1].rule and not blocks[-1].bullet:
        blocks.pop()
    return blocks


@dataclass
class _Piece:
    text: str
    run: Run
    width: float


def _wrap(block: Block, width: int) -> list[list[_Piece]]:
    """Break a block's runs into lines that fit `width` pixels."""
    lines: list[list[_Piece]] = [[]]
    used = 0.0
    for run in block.runs:
        font = _run_font(run)
        for token in re.findall(r"\S+|\s+", run.text):
            is_space = token.isspace()
            if is_space and not lines[-1]:
                continue
            w = font.getlength(token)
            if used + w <= width or is_space:
                lines[-1].append(_Piece(token, run, w))
                used += w
                continue
            if lines[-1]:
                lines.append([])
                used = 0.0
            # A word longer than the line: split it by characters.
            while font.getlength(token) > width:
                cut = len(token)
                while cut > 1 and font.getlength(token[:cut]) > width:
                    cut -= 1
                lines[-1].append(_Piece(token[:cut], run, font.getlength(token[:cut])))
                lines.append([])
                token = token[cut:]
            w = font.getlength(token)
            lines[-1].append(_Piece(token, run, w))
            used = w
    for line in lines:
        while line and line[-1].text.isspace():
            line.pop()
    return lines


def render(text: str, fmt: str, width: int = 384) -> Image.Image | None:
    """Lay out formatted text; returns a 1-bit image, or None if empty."""
    blocks = parse(text, fmt)
    if not blocks:
        return None

    # (baseline, drawing op) pairs, collected during layout.
    ops: list[tuple[int, object]] = []
    y = 0
    for block in blocks:
        y += block.gap_before
        if block.rule:
            ops.append((y, ("rule",)))
            y += 6
            continue
        bullet_width = 0.0
        if block.bullet:
            bullet_font = _font(False, False, False, BASE_SIZE)
            bullet_width = bullet_font.getlength(block.bullet)
        left = MARGIN + block.indent
        text_left = left + bullet_width
        lines = _wrap(block, width - MARGIN - int(text_left)) if block.runs else [[]]
        for i, line in enumerate(lines):
            size = max((p.run.size for p in line), default=BASE_SIZE)
            ascent = max(
                (_run_font(p.run).getmetrics()[0] for p in line),
                default=_font(False, False, False, size).getmetrics()[0],
            )
            line_height = int(size * LINE_SPACING)
            baseline = y + ascent + (line_height - size) // 2
            line_width = sum(p.width for p in line)
            x = text_left
            if block.center:
                x = (width - line_width) / 2
            if i == 0 and block.bullet:
                ops.append((baseline, ("bullet", left, block.bullet)))
            ops.append((baseline, ("line", x, line)))
            y += line_height
    canvas_height = y + MARGIN

    img = Image.new("L", (width, canvas_height), 255)
    draw = ImageDraw.Draw(img)
    for pos, op in ops:
        if op[0] == "rule":
            draw.rectangle([MARGIN, pos + 2, width - MARGIN, pos + 3], fill=0)
        elif op[0] == "bullet":
            font = _font(False, False, False, BASE_SIZE)
            draw.text((op[1], pos), op[2], font=font, fill=0, anchor="ls")
        else:
            x = op[1]
            for piece in op[2]:
                font = _run_font(piece.run)
                draw.text((x, pos), piece.text, font=font, fill=0, anchor="ls")
                thickness = max(1, piece.run.size // 14)
                if piece.run.underline:
                    uy = pos + max(2, piece.run.size // 8)
                    draw.rectangle([x, uy, x + piece.width, uy + thickness - 1], fill=0)
                if piece.run.strike:
                    sy = pos - int(piece.run.size * 0.3)
                    draw.rectangle([x, sy, x + piece.width, sy + thickness - 1], fill=0)
                x += piece.width

    return img.point(lambda v: 0 if v < INK_THRESHOLD else 255).convert(
        "1", dither=Image.Dither.NONE
    )
