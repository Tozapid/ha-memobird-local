"""Rasterize simple SVG line art (e.g. AI-drawn colouring pages) with Pillow.

Home Assistant has no SVG renderer (cairo isn't available), so this covers
the subset line drawings use: <g>, <path> (all commands, including arcs),
<circle>, <ellipse>, <rect>, <line>, <polyline>, <polygon>; fill, stroke,
stroke-width, fill-rule, opacity-free colours; transform (matrix, translate,
scale, rotate, skewX, skewY); presentation attributes and style="...".
Text, gradients, filters, masks, clipping and <use> are ignored.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

from PIL import Image, ImageChops, ImageDraw

# Draw this many times larger, then downsample: smooth curves and even lines.
SUPERSAMPLE = 3
# Tall drawings are cropped to this multiple of the width.
MAX_ASPECT = 2.0
CURVE_STEPS = 24

Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1, 0, 0, 1, 0, 0)

_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_NAMED = {
    "black": 0, "white": 255, "none": None, "transparent": None,
    "gray": 128, "grey": 128, "silver": 192, "red": 76, "green": 75,
    "blue": 29, "yellow": 226, "orange": 173, "pink": 212, "brown": 90,
    "purple": 52, "navy": 15, "lightgray": 211, "lightgrey": 211,
    "darkgray": 169, "darkgrey": 169,
}
_INHERITED = ("fill", "stroke", "stroke-width", "fill-rule", "stroke-linecap",
              "stroke-linejoin", "display", "visibility")


class SvgError(ValueError):
    """The SVG can't be parsed."""


def render_svg(svg: str, width: int = 384) -> Image.Image:
    """Render SVG markup to a grayscale image `width` pixels wide."""
    if re.search(r"<!(DOCTYPE|ENTITY)", svg, re.IGNORECASE):
        raise SvgError("SVG with DOCTYPE/ENTITY declarations is not supported")
    # Models sometimes wrap the SVG in prose or a code fence.
    if match := re.search(r"<svg\b.*</svg>", svg, re.DOTALL | re.IGNORECASE):
        svg = match[0]
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as err:
        raise SvgError(f"Invalid SVG: {err}") from err
    if _tag(root) != "svg":
        raise SvgError("Root element is not <svg>")

    vx, vy, vw, vh = _viewbox(root)
    scale = width * SUPERSAMPLE / vw
    height = min(round(vh * width / vw), round(width * MAX_ASPECT))
    canvas = Image.new("L", (width * SUPERSAMPLE, height * SUPERSAMPLE), 255)

    base = (scale, 0, 0, scale, -vx * scale, -vy * scale)
    style = {"fill": "black", "stroke": "none", "stroke-width": "1"}
    _draw_children(canvas, root, base, style)
    return canvas.resize((width, height), Image.Resampling.LANCZOS)


def _tag(el: ET.Element) -> str:
    return el.tag.rsplit("}", 1)[-1]


def _viewbox(root: ET.Element) -> tuple[float, float, float, float]:
    if vb := root.get("viewBox"):
        nums = [float(n) for n in _NUMBER.findall(vb)]
        if len(nums) == 4 and nums[2] > 0 and nums[3] > 0:
            return nums[0], nums[1], nums[2], nums[3]
    w = _length(root.get("width"), 300)
    h = _length(root.get("height"), 150)
    return 0, 0, w or 300, h or 150


def _length(value: str | None, default: float) -> float:
    if not value:
        return default
    match = _NUMBER.match(value.strip())
    return float(match[0]) if match else default


# Styles


def _style_of(el: ET.Element, inherited: dict) -> dict:
    style = {k: v for k, v in inherited.items() if k in _INHERITED}
    for key in _INHERITED:
        if (value := el.get(key)) is not None:
            style[key] = value
    for decl in (el.get("style") or "").split(";"):
        if ":" in decl:
            key, value = decl.split(":", 1)
            style[key.strip()] = value.strip()
    return style


def _gray(color: str | None) -> int | None:
    """Colour -> luminance 0..255, or None for no paint."""
    if color is None:
        return None
    color = color.strip().lower()
    if color in _NAMED:
        return _NAMED[color]
    if color.startswith("#"):
        hexa = color[1:]
        if len(hexa) in (3, 4):
            hexa = "".join(c * 2 for c in hexa[:3])
        if len(hexa) >= 6:
            try:
                r, g, b = (int(hexa[i:i + 2], 16) for i in (0, 2, 4))
            except ValueError:
                return 0
            return round(0.299 * r + 0.587 * g + 0.114 * b)
    if color.startswith("rgb"):
        nums = [float(n) for n in _NUMBER.findall(color)[:3]]
        if len(nums) == 3:
            if "%" in color:
                nums = [n * 2.55 for n in nums]
            return round(0.299 * nums[0] + 0.587 * nums[1] + 0.114 * nums[2])
    if color.startswith("url("):
        return None  # gradients/patterns aren't supported
    return 0  # unknown colour names: draw rather than lose the line


# Transforms


def _mul(a: Matrix, b: Matrix) -> Matrix:
    return (
        a[0] * b[0] + a[2] * b[1],
        a[1] * b[0] + a[3] * b[1],
        a[0] * b[2] + a[2] * b[3],
        a[1] * b[2] + a[3] * b[3],
        a[0] * b[4] + a[2] * b[5] + a[4],
        a[1] * b[4] + a[3] * b[5] + a[5],
    )


def _parse_transform(text: str | None) -> Matrix:
    m = IDENTITY
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", text or ""):
        v = [float(n) for n in _NUMBER.findall(args)]
        t: Matrix = IDENTITY
        if name == "matrix" and len(v) == 6:
            t = tuple(v)  # type: ignore[assignment]
        elif name == "translate" and v:
            t = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0)
        elif name == "scale" and v:
            t = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "rotate" and v:
            a = math.radians(v[0])
            t = (math.cos(a), math.sin(a), -math.sin(a), math.cos(a), 0, 0)
            if len(v) == 3:
                t = _mul(_mul((1, 0, 0, 1, v[1], v[2]), t), (1, 0, 0, 1, -v[1], -v[2]))
        elif name == "skewX" and v:
            t = (1, 0, math.tan(math.radians(v[0])), 1, 0, 0)
        elif name == "skewY" and v:
            t = (1, math.tan(math.radians(v[0])), 0, 1, 0, 0)
        m = _mul(m, t)
    return m


def _apply(m: Matrix, pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return [(m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]) for x, y in pts]


def _scale_of(m: Matrix) -> float:
    return math.sqrt(abs(m[0] * m[3] - m[1] * m[2])) or 1.0


# Geometry: every shape becomes a list of subpaths (point lists, closed flag)


Subpath = tuple[list[tuple[float, float]], bool]


def _shape(el: ET.Element) -> list[Subpath]:
    tag = _tag(el)
    f = lambda name, d=0.0: _length(el.get(name), d)  # noqa: E731
    if tag == "path":
        return _path(el.get("d") or "")
    if tag == "circle":
        return [(_ellipse_pts(f("cx"), f("cy"), f("r"), f("r")), True)]
    if tag == "ellipse":
        return [(_ellipse_pts(f("cx"), f("cy"), f("rx"), f("ry")), True)]
    if tag == "rect":
        return [(_rect_pts(f("x"), f("y"), f("width"), f("height"), el.get("rx"), el.get("ry")), True)]
    if tag == "line":
        return [([(f("x1"), f("y1")), (f("x2"), f("y2"))], False)]
    if tag in ("polyline", "polygon"):
        nums = [float(n) for n in _NUMBER.findall(el.get("points") or "")]
        pts = list(zip(nums[0::2], nums[1::2]))
        return [(pts, tag == "polygon")] if len(pts) > 1 else []
    return []


def _ellipse_pts(cx: float, cy: float, rx: float, ry: float) -> list[tuple[float, float]]:
    if rx <= 0 or ry <= 0:
        return []
    n = max(24, min(180, int(max(rx, ry))))
    return [(cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n)) for i in range(n)]


def _rect_pts(x, y, w, h, rx_s, ry_s) -> list[tuple[float, float]]:
    if w <= 0 or h <= 0:
        return []
    rx = _length(rx_s, -1)
    ry = _length(ry_s, -1)
    if rx < 0:
        rx = max(ry, 0)
    if ry < 0:
        ry = rx
    rx, ry = min(rx, w / 2), min(ry, h / 2)
    if rx <= 0 or ry <= 0:
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    pts: list[tuple[float, float]] = []
    for cx, cy, start in ((x + w - rx, y + ry, -90), (x + w - rx, y + h - ry, 0),
                          (x + rx, y + h - ry, 90), (x + rx, y + ry, 180)):
        for i in range(9):
            a = math.radians(start + i * 90 / 8)
            pts.append((cx + rx * math.cos(a), cy + ry * math.sin(a)))
    return pts


def _path(d: str) -> list[Subpath]:
    tokens = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|" + _NUMBER.pattern, d)
    subpaths: list[Subpath] = []
    pts: list[tuple[float, float]] = []
    cx = cy = sx = sy = 0.0
    last_ctrl: tuple[float, float] | None = None
    last_cmd = ""
    i = 0
    cmd = ""

    def num() -> float:
        nonlocal i
        value = float(tokens[i])
        i += 1
        return value

    def flush(closed: bool) -> None:
        nonlocal pts
        if len(pts) > 1:
            subpaths.append((pts, closed))
        pts = []

    while i < len(tokens):
        tok = tokens[i]
        if tok.isalpha():
            cmd = tok
            i += 1
            if cmd in "Zz":
                flush(True)
                cx, cy = sx, sy
                last_cmd, last_ctrl = cmd, None
                continue
        elif not cmd:
            break  # numbers before any command
        rel = cmd.islower()
        c = cmd.upper()
        ox, oy = (cx, cy) if rel else (0.0, 0.0)
        try:
            if c == "M":
                flush(False)
                cx, cy = ox + num(), oy + num()
                sx, sy = cx, cy
                pts = [(cx, cy)]
                cmd = "l" if rel else "L"  # following pairs are lineto
            elif c == "L":
                cx, cy = ox + num(), oy + num()
                pts.append((cx, cy))
            elif c == "H":
                cx = ox + num()
                pts.append((cx, cy))
            elif c == "V":
                cy = (cy if rel else 0.0) + num()
                pts.append((cx, cy))
            elif c in "CS":
                if c == "C":
                    x1, y1 = ox + num(), oy + num()
                elif last_ctrl and last_cmd.upper() in "CS":
                    x1, y1 = 2 * cx - last_ctrl[0], 2 * cy - last_ctrl[1]
                else:
                    x1, y1 = cx, cy
                x2, y2 = ox + num(), oy + num()
                x, y = ox + num(), oy + num()
                pts.extend(_cubic((cx, cy), (x1, y1), (x2, y2), (x, y)))
                last_ctrl = (x2, y2)
                cx, cy = x, y
            elif c in "QT":
                if c == "Q":
                    x1, y1 = ox + num(), oy + num()
                elif last_ctrl and last_cmd.upper() in "QT":
                    x1, y1 = 2 * cx - last_ctrl[0], 2 * cy - last_ctrl[1]
                else:
                    x1, y1 = cx, cy
                x, y = ox + num(), oy + num()
                pts.extend(_quad((cx, cy), (x1, y1), (x, y)))
                last_ctrl = (x1, y1)
                cx, cy = x, y
            elif c == "A":
                rx, ry, rot, large, sweep = num(), num(), num(), num(), num()
                x, y = ox + num(), oy + num()
                pts.extend(_arc(cx, cy, rx, ry, rot, bool(large), bool(sweep), x, y))
                cx, cy = x, y
            else:
                i += 1
                continue
        except (IndexError, ValueError):
            break  # truncated path: keep what we have
        if not pts:
            pts = [(cx, cy)]
        if c not in "CSQT":
            last_ctrl = None
        last_cmd = cmd
    flush(False)
    return subpaths


def _cubic(p0, p1, p2, p3) -> list[tuple[float, float]]:
    out = []
    for k in range(1, CURVE_STEPS + 1):
        t = k / CURVE_STEPS
        u = 1 - t
        out.append((
            u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
            u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1],
        ))
    return out


def _quad(p0, p1, p2) -> list[tuple[float, float]]:
    out = []
    for k in range(1, CURVE_STEPS + 1):
        t = k / CURVE_STEPS
        u = 1 - t
        out.append((
            u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
            u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
        ))
    return out


def _arc(x1, y1, rx, ry, rot, large, sweep, x2, y2) -> list[tuple[float, float]]:
    """Endpoint arc -> points (SVG spec, appendix F.6)."""
    if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
        return [(x2, y2)]
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(rot)
    cos, sin = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cos * dx + sin * dy, -sin * dx + cos * dy
    lam = x1p**2 / rx**2 + y1p**2 / ry**2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx**2 * ry**2 - rx**2 * y1p**2 - ry**2 * x1p**2
    den = rx**2 * y1p**2 + ry**2 * x1p**2
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        coef = -coef
    cxp, cyp = coef * rx * y1p / ry, -coef * ry * x1p / rx
    cx = cos * cxp - sin * cyp + (x1 + x2) / 2
    cy = sin * cxp + cos * cyp + (y1 + y2) / 2

    def angle(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t1 = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = angle((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    steps = max(4, int(abs(dt) / (2 * math.pi) * 48))
    out = []
    for k in range(1, steps + 1):
        t = t1 + dt * k / steps
        ex, ey = rx * math.cos(t), ry * math.sin(t)
        out.append((cos * ex - sin * ey + cx, sin * ex + cos * ey + cy))
    return out


# Painting


def _draw_children(canvas: Image.Image, parent: ET.Element, m: Matrix, style: dict) -> None:
    for el in parent:
        tag = _tag(el)
        if tag in ("defs", "title", "desc", "metadata", "style", "text",
                   "clipPath", "mask", "linearGradient", "radialGradient",
                   "pattern", "symbol", "filter"):
            continue
        el_style = _style_of(el, style)
        if el_style.get("display") == "none" or el_style.get("visibility") == "hidden":
            continue
        el_m = _mul(m, _parse_transform(el.get("transform")))
        if tag in ("g", "svg", "a"):
            _draw_children(canvas, el, el_m, el_style)
        else:
            _draw_shape(canvas, _shape(el), el_m, el_style)


def _draw_shape(canvas: Image.Image, subpaths: list[Subpath], m: Matrix, style: dict) -> None:
    if not subpaths:
        return
    subpaths = [(_apply(m, pts), closed) for pts, closed in subpaths]

    fill = _gray(style.get("fill", "black"))
    if fill is not None:
        mask = Image.new("1", canvas.size, 0)
        evenodd = style.get("fill-rule") == "evenodd"
        for pts, _closed in subpaths:
            if len(pts) < 3:
                continue
            sub = Image.new("1", canvas.size, 0)
            ImageDraw.Draw(sub).polygon(pts, fill=1)
            mask = ImageChops.logical_xor(mask, sub) if evenodd else ImageChops.logical_or(mask, sub)
        canvas.paste(fill, mask=mask)

    stroke = _gray(style.get("stroke"))
    if stroke is None:
        return
    width = _length(style.get("stroke-width"), 1) * _scale_of(m)
    if width <= 0:
        return
    w = max(1, round(width))
    draw = ImageDraw.Draw(canvas)
    round_join = style.get("stroke-linejoin", "round") != "miter"
    for pts, closed in subpaths:
        line = pts + [pts[0]] if closed else pts
        draw.line(line, fill=stroke, width=w, joint="curve" if round_join else None)
        if w > 2:
            # Round caps/joins so thick outlines have no notches.
            r = width / 2
            for x, y in line if round_join else (line[0], line[-1]):
                draw.ellipse([x - r, y - r, x + r, y + r], fill=stroke)
