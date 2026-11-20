# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow", "fonttools[woff]"]
# ///
"""Build the profile README panels as SVG files.

GitHub strips CSS and scripts from READMEs, so every styled panel is an image.
Each SVG embeds its own fonts and bitmaps and animates with SMIL, which still
runs when GitHub shows the file through an <img> tag.

Usage:
    uv run scripts/build_profile.py                  # build assets/ and refresh README hashes
    uv run scripts/build_profile.py --preview DIR    # also write every banner variant to DIR

The "keys" banner uses the glass keycap images in scripts/keycap/. To change the
glass, edit and rerun scripts/render_keycap.py, then run this script again.
"""

import argparse
import base64
import hashlib
import io
import json
import math
import random
import re
from pathlib import Path
from xml.sax.saxutils import escape

from fontTools.subset import Options, Subsetter
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
FONTS = ROOT / "fonts"
ICONS = Path(__file__).resolve().parent / "icons"

# ---------------------------------------------------------------- content
# Edit this block to change what the panels say.

BANNER = "keys"  # threads | glyphs | gridscan | ripple | keys
NICK_STYLE = "highlight"  # whoami | chip | highlight | row | greeting | dict | entry | dict-highlight

PROFILE = {
    "handle": "stevefrdnt",
    "name": "Steven Ferdianto",
    "nick": "Steve",
    "nick_label": "or just Steve",  # the small note beside the line under "Steve"
    "pron": "\\stēv\\",  # Merriam-Webster style respelling
    "tagline": "Turning coffee into everyday apps.",
    "now": "building apps that make daily life easier",
    "rows": [
        ("ROLE", "Software Engineer"),
        ("FOCUS", "Web Apps and AI Apps"),
        ("STATUS", "Open Collab for a coffee"),
    ],
}

# image=None draws a placeholder screenshot. Point it at a real file later,
# e.g. ASSETS / "shot-project-one.png" (any size, it is cropped to 2:1).
# soon=True blurs the placeholder and stamps "Coming soon" over it.
PROJECTS = [
    {"slug": "project-one", "index": "02", "name": "Coming Soon", "domain": "",
     "blurb": "Something new is brewing.", "kind": "soon", "image": None, "soon": True},
    {"slug": "project-two", "index": "03", "name": "Coming Soon", "domain": "",
     "blurb": "Still in the coffee pot.", "kind": "soon", "image": None, "soon": True},
]

# Tech stack panel: (row label, [(name, icon file in scripts/icons, logo colour)]).
STACK = [
    ("Language", [("TypeScript", "typescript", "3178C6"), ("JavaScript", "javascript", "F7DF1E"),
                  ("Python", "python", "FFD43B")]),
    ("AI", [("Claude Code", "claude", "D97757"), ("OpenAI", "openai", "FFFFFF"),
            ("Vercel AI SDK", "vercel", "FFFFFF")]),
    ("Frontend", [("TanStack", "tanstack", "FF4154"), ("Next.js", "nextdotjs", "FFFFFF"),
                  ("React", "react", "61DAFB"), ("Tailwind", "tailwindcss", "06B6D4"), ("Figma", "figma", "F24E1E")]),
    ("Backend", [("FastAPI", "fastapi", "009688"), ("MySQL", "mysql", "6CB4EE"), ("Postgres", "postgresql", "7FA6F5"),
                 ("Laravel", "laravel", "FF2D20"), ("GraphQL", "graphql", "E10098")]),
]

CHIPS = [
    {"slug": "x", "icon": "x", "label": "@thestevefrdnt"},
    {"slug": "linkedin", "icon": "linkedin", "label": "in/stevefrdnt"},
]

# ---------------------------------------------------------------- theme
# "Ember": warm black with orange and amber. Change these to retheme everything.

W = 880  # close to GitHub's README column, so font-size 13 renders near 13px

BG = "#0C0A08"
SURFACE = "#14100C"
LINE = "#2A221A"
LINE_STRONG = "#5C3B1E"
TEXT = "#F3EEE7"
SECOND = "#A89E92"
MUTED = "#6A6056"
ACCENT = "#FF7A1A"
AMBER = "#FFB648"
DEEP = "#8A3410"

MONO_ADVANCE = 0.6  # JetBrains Mono advance per em


# ---------------------------------------------------------------- fonts

def _instance(file: str, weight: int) -> TTFont:
    return instantiateVariableFont(TTFont(FONTS / file), {"wght": weight})


def _subset_b64(font: TTFont, text: str) -> str:
    options = Options()
    options.flavor = "woff2"
    options.layout_features = []
    subsetter = Subsetter(options)
    subsetter.populate(text=text + " ")
    subsetter.subset(font)
    font.flavor = "woff2"
    buffer = io.BytesIO()
    font.save(buffer)
    return base64.b64encode(buffer.getvalue()).decode()


class Text:
    """Draws <text> and records every string, so font subsets hold exactly those glyphs."""

    def __init__(self) -> None:
        self.mono: list[str] = []
        self.mono_bold: list[str] = []
        self.display: list[str] = []

    def __call__(self, x: float, y: float, content: str, size: float = 13, cls: str = "",
                 anchor: str = "start", extra: str = "") -> str:
        classes = cls.split()
        if "d" in classes:
            self.display.append(content)
        elif "w" in classes:
            self.mono_bold.append(content)
        else:
            self.mono.append(content)
        c = f' class="{cls}"' if cls else ""
        a = f' text-anchor="{anchor}"' if anchor != "start" else ""
        return (f'<text x="{x:g}" y="{y:g}" font-size="{size:g}"{c}{a}{extra} '
                f'xml:space="preserve">{escape(content)}</text>')

    def faces(self) -> str:
        rules = []
        for weight, seen in ((400, self.mono), (600, self.mono_bold)):
            if seen:
                data = _subset_b64(_instance("JetBrainsMono.ttf", weight), "".join(seen))
                rules.append(f"@font-face{{font-family:M;font-weight:{weight};"
                             f"src:url(data:font/woff2;base64,{data}) format('woff2')}}")
        if self.display:
            data = _subset_b64(_instance("SpaceGrotesk.ttf", 500), "".join(self.display))
            rules.append(f"@font-face{{font-family:D;src:url(data:font/woff2;base64,{data}) format('woff2')}}")
        return "".join(rules)


def svg(width: float, height: float, title: str, body: list[str], t: Text, defs: str = "") -> str:
    style = (
        t.faces()
        + f"text{{font-family:M,'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;"
        f"fill:{SECOND};white-space:pre}}"
        f".d{{font-family:D,'Space Grotesk',system-ui,sans-serif;letter-spacing:-0.02em}}"
        f".p{{fill:{TEXT}}}.m{{fill:{MUTED}}}.a{{fill:{ACCENT}}}.b{{fill:{AMBER}}}.k{{fill:{BG}}}"
        f".w{{font-weight:600}}.l{{letter-spacing:0.14em}}"
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{width:g}" height="{height:g}" viewBox="0 0 {width:g} {height:g}" role="img">'
        f"<title>{escape(title)}</title><style>{style}</style>"
        f"{'<defs>' + defs + '</defs>' if defs else ''}{''.join(body)}</svg>\n"
    )


# ---------------------------------------------------------------- primitives

def rect(x, y, w, h, fill="none", stroke="", extra=""):
    if stroke:
        return (f'<rect x="{x + .5:g}" y="{y + .5:g}" width="{w - 1:g}" height="{h - 1:g}" '
                f'fill="{fill}" stroke="{stroke}"{extra}/>')
    return f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" fill="{fill}"{extra}/>'


def hline(x1, x2, y, color=LINE):
    return f'<line x1="{x1:g}" y1="{y + .5:g}" x2="{x2:g}" y2="{y + .5:g}" stroke="{color}"/>'


def vline(x, y1, y2, color=LINE):
    return f'<line x1="{x + .5:g}" y1="{y1:g}" x2="{x + .5:g}" y2="{y2:g}" stroke="{color}"/>'


def mono_width(text: str, size: float) -> float:
    return len(text) * size * MONO_ADVANCE


_DISPLAY = None


def display_width(text: str, size: float) -> float:
    """Width of text in the display font, from its advance widths (no kerning)."""
    global _DISPLAY
    if _DISPLAY is None:
        _DISPLAY = _instance("SpaceGrotesk.ttf", 500)
    cmap, hmtx = _DISPLAY.getBestCmap(), _DISPLAY["hmtx"]
    upem = _DISPLAY["head"].unitsPerEm
    advance = sum(hmtx[cmap[ord(c)]][0] for c in text) / upem
    return (advance - 0.02 * len(text)) * size  # .d sets letter-spacing -0.02em


def pulse_dot(cx, cy, color=ACCENT, r=3.5, dur=2.4):
    """A solid dot with a ring that expands and fades, like a live status light."""
    return (
        f'<circle cx="{cx:g}" cy="{cy:g}" r="{r:g}" fill="none" stroke="{color}">'
        f'<animate attributeName="r" values="{r:g};{r * 3:g}" dur="{dur}s" repeatCount="indefinite"/>'
        f'<animate attributeName="opacity" values="0.8;0" dur="{dur}s" repeatCount="indefinite"/></circle>'
        f'<circle cx="{cx:g}" cy="{cy:g}" r="{r:g}" fill="{color}"/>'
    )


# The status icon: a cup of hot coffee on a saucer with three wisps of steam.
# Only the steam moves. Each wisp fades in, rises and fades out, starting and ending
# invisible, so the icon loops without a jump.
CUP_W, CUP_H = 24, 30
# Ink bounds of the cup and saucer (strokes included, steam excluded), in icon units.
CUP_INK = (0.1, 15.1, 23.6, 29.9)  # left, top, right, bottom


def hot_coffee(x, y, scale=1.0, color=AMBER, dur=2.4):
    """(x, y) is the top-left of a CUP_W x CUP_H box. Steam uses the top 13 units."""
    stroke = f'stroke="{color}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"'
    cup = "M3 16 H18 V22 a6 6 0 0 1 -6 6 H9 a6 6 0 0 1 -6 -6 Z"
    wisps = "".join(
        f'<path d="M{wx} 13 q-1.7 -1.75 0 -3.5 t0 -3.5" fill="none" stroke="{color}" stroke-width="1.5" '
        f'stroke-linecap="round" opacity="0">'
        f'<animateTransform attributeName="transform" type="translate" values="0 2;0 -3" '
        f'dur="{dur}s" begin="-{i * dur / 3:.2f}s" repeatCount="indefinite"/>'
        f'<animate attributeName="opacity" values="0;0.95;0" keyTimes="0;0.4;1" '
        f'dur="{dur}s" begin="-{i * dur / 3:.2f}s" repeatCount="indefinite"/></path>'
        for i, wx in enumerate((6.5, 10.5, 14.5))
    )
    return (
        f'<g transform="translate({x:g} {y:g}) scale({scale:g})">{wisps}'
        f'<path d="{cup}" fill="{color}" fill-opacity="0.22" {stroke}/>'
        f'<path d="M18 18.2 h1.8 a2.9 2.9 0 0 1 0 5.8 H17.5" fill="none" {stroke}/>'
        f'<path d="M1 29 H20" {stroke}/>'
        f'</g>'
    )


def mix(a: str, b: str, t: float) -> str:
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(pa, pb))


def jpeg_data(img: Image.Image, quality: int = 82) -> str:
    buffer = io.BytesIO()
    img.convert("RGB").save(buffer, "JPEG", quality=quality, optimize=True, progressive=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


# ---------------------------------------------------------------- banners
# Each banner draws into a W x BH box and returns (body, defs).
# They port the idea of a React Bits background to SVG that runs without JS.

BH = 128


def banner_threads(t: Text, rng: random.Random):
    """After React Bits "Threads": a bundle of glowing strands drifting sideways and breathing."""
    body, defs = [], []
    defs.append('<filter id="glow" x="-10%" y="-50%" width="120%" height="200%">'
                '<feGaussianBlur stdDeviation="3"/></filter>')
    strands = []
    count = 18
    for i in range(count):
        f = i / (count - 1)
        period = W / rng.choice((1, 1, 2))
        amp = 22 + 16 * math.sin(f * math.pi) + rng.uniform(-4, 4)
        phase = f * 1.6 + rng.uniform(-0.2, 0.2)
        h2 = rng.uniform(0.2, 0.45)
        offset = (f - 0.5) * 26
        # Catmull-Rom through samples of a periodic curve, written as cubic Beziers.
        step = period / 16
        xs = [-period - step + k * step for k in range(int((W + 2 * period) / step) + 4)]

        def y(x):
            u = 2 * math.pi * x / period
            return offset + amp * (math.sin(u + phase) + h2 * math.sin(2 * u - phase * 1.7)) / (1 + h2)

        pts = [(x, y(x)) for x in xs]
        d = [f"M{pts[1][0]:.1f} {pts[1][1]:.1f}"]
        for k in range(1, len(pts) - 2):
            p0, p1, p2, p3 = pts[k - 1], pts[k], pts[k + 1], pts[k + 2]
            c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
            c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
            d.append(f"C{c1[0]:.1f} {c1[1]:.1f} {c2[0]:.1f} {c2[1]:.1f} {p2[0]:.1f} {p2[1]:.1f}")
        color = mix(AMBER, ACCENT, f) if f < 0.6 else mix(ACCENT, DEEP, (f - 0.6) / 0.4)
        drift = rng.uniform(9, 16)
        breathe = rng.uniform(5, 9)
        lo = rng.uniform(0.35, 0.6)
        width = 1.4 if i % 4 == 0 else 0.9
        opacity = 0.95 if i % 4 == 0 else 0.55
        strands.append(
            f'<g transform="translate(0 {BH / 2:g})"><g>'
            f'<animateTransform attributeName="transform" type="scale" values="1 1;1 {lo:.2f};1 1" '
            f'dur="{breathe:.1f}s" begin="-{rng.uniform(0, breathe):.1f}s" repeatCount="indefinite" '
            f'calcMode="spline" keySplines="0.45 0 0.55 1;0.45 0 0.55 1"/>'
            f'<path d="{"".join(d)}" fill="none" stroke="{color}" stroke-width="{width}" '
            f'stroke-opacity="{opacity}" stroke-linecap="round">'
            f'<animateTransform attributeName="transform" type="translate" from="0 0" to="{-period:g} 0" '
            f'dur="{drift:.1f}s" repeatCount="indefinite"/></path></g></g>'
        )
    body.append(rect(0, 0, W, BH, BG))
    body.append(f'<g filter="url(#glow)" opacity="0.7">{"".join(strands)}</g>')
    body.append(f'<g>{"".join(strands)}</g>')
    return body, defs


GLYPHS = "01<>/\\{}[]()=+*#%&$@!?;:~^|._-ABCDEFabcdef"


def banner_glyphs(t: Text, rng: random.Random):
    """After React Bits "Letter Glitch" and "Faulty Terminal": a glyph field that flickers,
    with the handle surfacing in the middle and a scanline rolling down."""
    body, defs = [], []
    size = 12
    cw, rh = size * MONO_ADVANCE, 16
    cols, rows = int(W / cw) + 1, BH // rh
    top = (BH - rows * rh) / 2 + 12
    body.append(rect(0, 0, W, BH, BG))
    for r in range(rows):
        line = "".join(rng.choice(GLYPHS) for _ in range(cols))
        body.append(t(0, top + r * rh, line, size, extra=f' fill="{MUTED}" fill-opacity="0.22"'))
    word = PROFILE["handle"].upper()
    mid_row = rows // 2
    start_col = (cols - len(word)) // 2
    word_cells = {(mid_row, start_col + i) for i in range(len(word))}
    cells = set()
    while len(cells) < 120:
        cell = (rng.randrange(rows), rng.randrange(cols))
        if cell not in word_cells:
            cells.add(cell)
    flicker = []
    for r, c in sorted(cells):
        x, y = c * cw, top + r * rh
        color = rng.choice((ACCENT, ACCENT, AMBER, TEXT))
        dur = rng.uniform(1.8, 4.5)
        flicker.append(
            f'<g opacity="0">{rect(x, y - 11, cw, rh, BG)}'
            + t(x, y, rng.choice(GLYPHS), size, extra=f' fill="{color}"')
            + f'<animate attributeName="opacity" values="0;1;0.6;0" keyTimes="0;0.08;0.3;1" '
            f'dur="{dur:.2f}s" begin="-{rng.uniform(0, dur):.2f}s" repeatCount="indefinite"/></g>'
        )
    body.extend(flicker)
    for i, ch in enumerate(word):
        x, y = (start_col + i) * cw, top + mid_row * rh
        delay = i * 0.12
        body.append(
            f'<g>{rect(x, y - 11, cw, rh, BG)}'
            + t(x, y, ch, size, "w", extra=f' fill="{AMBER}"')
            + f'<animate attributeName="opacity" values="1;1;0.2;1;1" keyTimes="0;0.7;0.74;0.78;1" '
            f'dur="5s" begin="{delay:.2f}s" repeatCount="indefinite"/></g>'
        )
    defs.append(f'<linearGradient id="scan" x1="0" y1="0" x2="0" y2="1">'
                f'<stop offset="0" stop-color="{ACCENT}" stop-opacity="0"/>'
                f'<stop offset="0.85" stop-color="{ACCENT}" stop-opacity="0.16"/>'
                f'<stop offset="1" stop-color="{AMBER}" stop-opacity="0.5"/></linearGradient>')
    body.append(f'<rect x="0" y="-40" width="{W}" height="40" fill="url(#scan)">'
                f'<animateTransform attributeName="transform" type="translate" from="0 0" to="0 {BH + 40}" '
                f'dur="4s" repeatCount="indefinite"/></rect>')
    # faint CRT rows
    defs.append(f'<pattern id="crt" width="4" height="3" patternUnits="userSpaceOnUse">'
                f'<rect width="4" height="1" fill="{BG}" fill-opacity="0.35"/></pattern>')
    body.append(rect(0, 0, W, BH, "url(#crt)"))
    return body, defs


def banner_gridscan(t: Text, rng: random.Random):
    """After React Bits "Grid Scan": a perspective floor rolling toward you,
    with a light band sweeping across it."""
    body, defs = [], []
    horizon = 40
    vx = W / 2
    body.append(rect(0, 0, W, BH, BG))
    defs.append(f'<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">'
                f'<stop offset="0" stop-color="{BG}"/><stop offset="1" stop-color="{DEEP}" stop-opacity="0.55"/>'
                f'</linearGradient>')
    body.append(rect(0, 0, W, horizon, "url(#sky)"))
    for _ in range(26):
        x, y = rng.uniform(0, W), rng.uniform(4, horizon - 8)
        dur = rng.uniform(2, 5)
        body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{rng.choice((0.7, 1, 1.2))}" fill="{TEXT}">'
                    f'<animate attributeName="opacity" values="0.1;0.9;0.1" dur="{dur:.1f}s" '
                    f'begin="-{rng.uniform(0, dur):.1f}s" repeatCount="indefinite"/></circle>')

    def grid(color: str, width: float) -> str:
        parts = []
        for k in range(-30, 31):
            xb = vx + k * 46
            parts.append(f'<line x1="{vx + k * 2.2:.1f}" y1="{horizon}" x2="{xb:.1f}" y2="{BH}" '
                         f'stroke="{color}" stroke-width="{width}"/>')
        ratio, n, dur = 0.66, 9, 1.6
        ys = [horizon + (BH - horizon) * ratio ** i for i in range(n + 2)]
        for i in range(n + 1):
            y0, y1 = ys[i + 1], ys[i]
            parts.append(
                f'<line x1="0" x2="{W}" y1="{y0:.1f}" y2="{y0:.1f}" stroke="{color}" stroke-width="{width}">'
                f'<animate attributeName="y1" values="{y0:.1f};{y1:.1f}" dur="{dur}s" repeatCount="indefinite"/>'
                f'<animate attributeName="y2" values="{y0:.1f};{y1:.1f}" dur="{dur}s" repeatCount="indefinite"/>'
                f'</line>'
            )
        return "".join(parts)

    defs.append(f'<linearGradient id="fade" x1="0" y1="0" x2="0" y2="1">'
                f'<stop offset="0" stop-color="#fff" stop-opacity="0"/>'
                f'<stop offset="0.35" stop-color="#fff" stop-opacity="0.6"/>'
                f'<stop offset="1" stop-color="#fff" stop-opacity="1"/></linearGradient>'
                f'<mask id="depth"><rect x="0" y="{horizon}" width="{W}" height="{BH - horizon}" fill="url(#fade)"/></mask>')
    defs.append(f'<linearGradient id="band" x1="0" y1="0" x2="1" y2="0">'
                f'<stop offset="0" stop-color="#fff" stop-opacity="0"/>'
                f'<stop offset="0.5" stop-color="#fff" stop-opacity="1"/>'
                f'<stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>'
                f'<mask id="sweep"><rect x="-260" y="0" width="260" height="{BH}" fill="url(#band)">'
                f'<animateTransform attributeName="transform" type="translate" from="0 0" to="{W + 260} 0" '
                f'dur="4.5s" repeatCount="indefinite"/></rect></mask>')
    body.append(f'<g mask="url(#depth)">{grid(LINE_STRONG, 1)}</g>')
    body.append(f'<g mask="url(#depth)"><g mask="url(#sweep)">{grid(AMBER, 1.3)}</g></g>')
    body.append(hline(0, W, horizon - 1, ACCENT))
    defs.append(f'<radialGradient id="hglow" cx="0.5" cy="0.5" r="0.5">'
                f'<stop offset="0" stop-color="{ACCENT}" stop-opacity="0.5"/>'
                f'<stop offset="1" stop-color="{ACCENT}" stop-opacity="0"/></radialGradient>')
    body.append(f'<ellipse cx="{vx}" cy="{horizon}" rx="{W * 0.45:g}" ry="16" fill="url(#hglow)"/>')
    return body, defs


def banner_ripple(t: Text, rng: random.Random):
    """After React Bits "Dot Grid" and "Ripple Grid": a field of dots with waves
    of light rolling out from two points."""
    body, defs = [], []
    gap = 14
    cols, rows = W // gap + 1, BH // gap + 1
    ox, oy = (W - (cols - 1) * gap) / 2, (BH - (rows - 1) * gap) / 2
    body.append(rect(0, 0, W, BH, BG))
    base = []
    for r in range(rows):
        for c in range(cols):
            base.append(f'<circle cx="{ox + c * gap:g}" cy="{oy + r * gap:g}" r="1.1"/>')
    body.append(f'<g fill="{MUTED}" fill-opacity="0.55">{"".join(base)}</g>')
    dur, speed = 4.0, 150.0
    for (sx, sy), color, shift in (((W * 0.28, BH * 0.5), ACCENT, 0.0), ((W * 0.74, BH * 0.4), AMBER, dur / 2)):
        buckets: dict[int, list[str]] = {}
        for r in range(rows):
            for c in range(cols):
                x, y = ox + c * gap, oy + r * gap
                d = math.hypot(x - sx, y - sy)
                if d > speed * dur * 0.9:
                    continue
                buckets.setdefault(int(d // 9), []).append(f'<circle cx="{x:g}" cy="{y:g}" r="2"/>')
        for b, circles in sorted(buckets.items()):
            delay = (b * 9 / speed + shift) % dur
            fade = max(0.15, 1 - b * 9 / (speed * dur))
            body.append(
                f'<g fill="{color}" opacity="0">{"".join(circles)}'
                f'<animate attributeName="opacity" values="0;{fade:.2f};0;0" keyTimes="0;0.06;0.22;1" '
                f'dur="{dur}s" begin="{delay - dur:.2f}s" repeatCount="indefinite"/></g>'
            )
    return body, defs


def _letters(chars):
    return [(c, 1) for c in chars]


# The standard US (ANSI) layout: (row position, [(legend, width in key units)]),
# far row first. A legend of None is a gap.
KEYBOARD = [
    (1.3, [("esc", 1), (None, 1)] + [(f"F{n}", 1) for n in (1, 2, 3, 4)] + [(None, 0.5)]
     + [(f"F{n}", 1) for n in (5, 6, 7, 8)] + [(None, 0.5)] + [(f"F{n}", 1) for n in (9, 10, 11, 12)]),
    (0, _letters("`1234567890-=") + [("backspace", 2)]),
    (-1, [("tab", 1.5)] + _letters("QWERTYUIOP[]") + [("\\", 1.5)]),
    (-2, [("caps", 1.75)] + _letters("ASDFGHJKL;'") + [("enter", 2.25)]),
    (-3, [("shift", 2.25)] + _letters("ZXCVBNM,./") + [("shift", 2.75)]),
    (-4, [("ctrl", 1.25), ("alt", 1.25), ("cmd", 1.25), ("", 6.25),
          ("cmd", 1.25), ("alt", 1.25), ("fn", 1.25), ("ctrl", 1.25)]),
]
# The point of the keyboard at the centre of the banner (between G and H). With the
# camera angle set in render_keycap.py, this spot keeps the view on the three
# letter rows: the number row's legends stay out of frame, and so do the wide
# keys, which have no rendered image.
KEY_VIEW = (7.0, -1.85)
KEYCAP = Path(__file__).resolve().parent / "keycap"   # written by render_keycap.py


def banner_keys(t: Text, rng: random.Random):
    """After React Bits "Glass Keys": a close-up of a glass keyboard that types in
    bursts and ripples. A README cannot run a 3D scene, so render_keycap.py traces
    one glass keycap ahead of time, unlit and lit. The camera has no perspective,
    so that one image fits every key, and SVG moves and fades the copies."""
    meta = json.loads((KEYCAP / "keycap.json").read_text())
    scale, loop = meta["scale"], 12.0            # pixels per key pitch, seconds per loop
    th, ph = math.radians(meta["theta"]), math.radians(meta["phi"])
    cs, sn, sp, cp = math.cos(th), math.sin(th), math.sin(ph), math.cos(ph)
    height = meta["height"]
    light, legend = "#FF7A27", "#DCD6FF"
    body, defs = [rect(0, 0, W, BH, BG)], []
    ox, oy = W / 2, BH / 2 + 24

    def proj(x, y, z=0.0):
        return scale * (x * cs - y * sn), -scale * ((x * sn + y * cs) * sp + z * cp)

    box = f'x="{meta["x"]:g}" y="{meta["y"]:g}" width="{meta["width"]:g}" height="{meta["height_px"]:g}"'
    for name, file in (("gkc", "keycap.webp"), ("gkx", "keycap-lit.webp")):
        data = base64.b64encode((KEYCAP / file).read_bytes()).decode()
        defs.append(f'<image id="{name}" {box} href="data:image/webp;base64,{data}"/>')
    defs.append(f'<radialGradient id="gkg"><stop offset="0" stop-color="{light}" stop-opacity="0.7"/>'
                f'<stop offset="0.5" stop-color="{light}" stop-opacity="0.26"/>'
                f'<stop offset="1" stop-color="{light}" stop-opacity="0"/></radialGradient>')
    defs.append('<filter id="gkf" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="1.2"/></filter>'
                '<filter id="gkn" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="2"/></filter>')
    k = scale / 100
    ground = f"{k * cs:.4f} {-k * sp * sn:.4f} {k * sn:.4f} {k * sp * cs:.4f}"
    defs.append(f'<g id="gkw" transform="matrix({ground} 0 0)"><circle r="100" fill="url(#gkg)"/></g>')
    lx, ly = proj(0, 0, height - 0.03)
    legend_at = f'matrix({ground} {lx:.1f} {ly:.1f})'

    keys = []
    for row_y, items in KEYBOARD:
        cursor = 0.0
        for label, width in items:
            kx, cursor = cursor + width / 2, cursor + width
            if label is None:
                continue
            x, y = proj(kx - KEY_VIEW[0], row_y - KEY_VIEW[1])
            x, y = x + ox, y + oy
            hx, hy = (width - 1 + meta["size"]) / 2, meta["size"] / 2
            if width == 1:
                in_view = -70 < x < W + 70 and -45 < y < BH + 85
            else:  # walk the key's base and top, since a long key's centre says little
                spots = [proj(kx - KEY_VIEW[0] + (i / 30 - 1) * hx * f, row_y - KEY_VIEW[1] + (j / 3 - 1) * hy * f, z)
                         for i in range(61) for j in range(7) for z, f in ((0, 1), (height, 0.72))]
                in_view = any(0 <= px + ox <= W and 0 <= py + oy <= BH for px, py in spots)
            if not in_view:
                continue
            if width != 1:
                print(f"keys banner: '{label}' is in view but has no image, move KEY_VIEW")
                continue
            keys.append(dict(wx=kx, wy=row_y, x=x, y=y, label=label, events=[],
                             seen=30 < x < W - 30 and 46 < y < BH + 26))
    keys.sort(key=lambda key: key["y"])  # far keys first, so near ones overlap them
    seen = [key for key in keys if key["seen"]]

    # idle typing in bursts, and two ripples that cross from opposite ends
    for start in (0.5, 5.7, 7.4):
        clock = start
        for key in rng.sample(seen, min(6, len(seen))):
            key["events"].append((clock, 1.0))
            clock += rng.uniform(0.14, 0.24)
    for start, source in ((2.7, min(seen, key=lambda key: key["x"])), (8.9, max(seen, key=lambda key: key["x"]))):
        for key in keys:
            dist = math.hypot(key["wx"] - source["wx"], key["wy"] - source["wy"])
            key["events"].append((start + dist / 7, max(0.25, 0.62 - dist * 0.03)))

    press_shape = [(0, 0), (0.06, 1), (0.16, 1), (0.36, 0)]
    glow_shape = [(0, 0), (0.07, 1), (0.26, 1), (0.95, 0)]
    travel = 0.32 * height * scale * cp

    def envelope(events, shape):
        times = sorted({0.0, loop} | {round(t0 + dt, 3) for t0, _ in events for dt, _ in shape})

        def value(when):
            best = 0.0
            for t0, strength in events:
                x = when - t0
                for (d0, v0), (d1, v1) in zip(shape, shape[1:]):
                    if d0 <= x <= d1:
                        best = max(best, strength * (v0 + (v1 - v0) * (x - d0) / (d1 - d0)))
            return best
        return ";".join(f"{x / loop:.4f}" for x in times), [value(x) for x in times]

    bands = {"far": [], "focus": [], "near": []}
    for key in keys:
        events = [(t0, st) for t0, st in key["events"] if t0 + 1 < loop]
        kt, glow = envelope(events, glow_shape)
        fade = (f'<animate attributeName="opacity" keyTimes="{kt}" values="{";".join(f"{v:.2f}" for v in glow)}" '
                f'dur="{loop:g}s" repeatCount="indefinite"/>')
        pt, press = envelope(events, press_shape)
        sink = (f'<animateTransform attributeName="transform" type="translate" keyTimes="{pt}" '
                f'values="{";".join(f"0 {v * travel:.1f}" for v in press)}" dur="{loop:g}s" repeatCount="indefinite"/>')
        label = key["label"]
        fs, dy = (30, 11) if len(label) == 1 else (17, 6)
        dim = (f'<g transform="{legend_at}">'
               + t(0, dy, label, fs, "d", anchor="middle", extra=f' style="fill:{legend};fill-opacity:.7"') + '</g>')
        hot = (f'<g transform="{legend_at}">'
               + t(0, dy, label, fs, "d", anchor="middle", extra=' style="fill:#FFF3E6"') + '</g>')
        band = "far" if key["y"] < 44 else "near" if key["y"] > BH + 16 else "focus"
        bands[band].append(
            f'<g transform="translate({key["x"]:.1f} {key["y"]:.1f})">'
            f'<use href="#gkw" opacity="0">{fade}</use>'
            f'<g>{sink}<use href="#gkc"/>{dim}'
            f'<g opacity="0">{fade}<use href="#gkx"/>{hot}</g></g></g>')
    # keys beyond and in front of the focus band are blurred, like a macro photo
    body.append(f'<g filter="url(#gkf)">{"".join(bands["far"])}</g>{"".join(bands["focus"])}'
                f'<g filter="url(#gkn)">{"".join(bands["near"])}</g>')

    defs.append(f'<linearGradient id="gkv" x1="0" y1="0" x2="0" y2="1">'
                f'<stop offset="0" stop-color="{BG}" stop-opacity="0.8"/><stop offset="0.3" stop-color="{BG}" stop-opacity="0"/>'
                f'<stop offset="0.82" stop-color="{BG}" stop-opacity="0"/><stop offset="1" stop-color="{BG}" stop-opacity="0.55"/>'
                f'</linearGradient>')
    body.append(rect(0, 0, W, BH, "url(#gkv)"))
    return body, defs


BANNERS = {
    "threads": banner_threads,
    "glyphs": banner_glyphs,
    "gridscan": banner_gridscan,
    "ripple": banner_ripple,
    "keys": banner_keys,
}


# ---------------------------------------------------------------- panels

def header(banner: str, nick_style: str = NICK_STYLE) -> str:
    t = Text()
    rng = random.Random(7)
    bar, top = 36, 36
    hero_top = top + BH
    H = hero_top + {"row": 250, "highlight": 246, "dict-highlight": 250, "dict": 250, "entry": 250}.get(nick_style, 236)
    body = [rect(0, 0, W, H, BG)]

    # title bar
    body.append(pulse_dot(20, bar / 2))
    body.append(t(34, 23, PROFILE["handle"], 13, "p w"))
    body.append(t(34 + mono_width(PROFILE["handle"], 13) + 12, 23, "~/README.md", 13, "m"))
    body.append(t(W - 16, 23, f"github.com/{PROFILE['handle']}", 12, "m", anchor="end"))
    body.append(hline(0, W, bar - 1))

    # banner
    b_body, b_defs = BANNERS[banner](t, rng)
    defs = [f'<clipPath id="bn"><rect x="0" y="0" width="{W}" height="{BH}"/></clipPath>',
            f'<linearGradient id="edge" x1="0" y1="0" x2="1" y2="0">'
            f'<stop offset="0" stop-color="{BG}"/><stop offset="0.06" stop-color="{BG}" stop-opacity="0"/>'
            f'<stop offset="0.94" stop-color="{BG}" stop-opacity="0"/><stop offset="1" stop-color="{BG}"/>'
            f'</linearGradient>'] + b_defs
    body.append(f'<g transform="translate(0 {top})" clip-path="url(#bn)">{"".join(b_body)}'
                f'{rect(0, 0, W, BH, "url(#edge)")}</g>')
    body.append(hline(0, W, hero_top - 1))

    # hero, left column
    split = 560
    x0 = 30
    name, nick = PROFILE["name"], PROFILE["nick"]
    prompt_y, name_y, tag_y, rule_y = hero_top + 38, hero_top + 98, hero_top + 132, hero_top + 156
    facts = [("NOW", PROFILE["now"]), ("HANDLE", f"@{PROFILE['handle']}")]
    if nick_style == "row":
        facts.insert(1, ("AKA", nick))
    if nick_style in ("dict", "entry", "dict-highlight"):
        name_y, tag_y, rule_y = name_y - 6, tag_y + 14, rule_y + 14
    body.append(t(x0, prompt_y, "$", 13, "a w"))
    body.append(t(x0 + 16, prompt_y, "whoami", 13, "m"))
    if nick_style == "whoami":
        # the prompt answers with the short name, the big name is the long form
        body.append(t(x0 + 16 + mono_width("whoami", 13) + 14, prompt_y, nick.lower(), 13, "b"))

    if nick_style in ("highlight", "dict-highlight"):
        # an accent rule under the "Steve" in "Steven" marks the short name
        sw = display_width(nick, 50)
        body.append(t(x0 - 3, name_y, name, 50, "d p"))
        if nick_style == "highlight":
            body.append(rect(x0, name_y + 9, sw - 4, 2, ACCENT))
            body.append(t(x0 + sw + 6, name_y + 14, PROFILE["nick_label"], 11, "m"))
            tag_y, rule_y = tag_y + 10, rule_y + 6
        else:
            # the respelling sits centred under "Steve", like a ruby annotation
            body.append(rect(x0, name_y + 9, sw - 4, 2, ACCENT))
            body.append(t(x0 + (sw - 4) / 2, name_y + 28, PROFILE["pron"], 13, "b", anchor="middle"))
    else:
        body.append(t(x0 - 3, name_y, name, 50, "d p"))
    if nick_style in ("dict", "entry"):
        # a dictionary-style line under the name: headword, respelling, definition
        pron_y = name_y + 25
        parts = ([(nick, 13, "p w"), (" " + PROFILE["pron"], 13, "b"), ("  noun, short for Steven", 13, "m")]
                 if nick_style == "entry" else
                 [(PROFILE["pron"], 13, "b"), ("  call me Steve", 13, "m")])
        x = x0
        for text, size, cls in parts:
            body.append(t(x, pron_y, text, size, cls))
            x += mono_width(text, size)
    if nick_style == "chip":
        # a small tag beside the name, centred on its cap height
        label = f"aka {nick}"
        cw, ch = mono_width(label, 12) + 22, 24
        cx0 = x0 - 3 + display_width(name, 50) + 14
        cy0 = name_y - 50 * 0.7 / 2 - ch / 2
        body.append(rect(cx0, cy0, cw, ch, SURFACE, LINE_STRONG))
        body.append(t(cx0 + cw / 2, cy0 + 16, label, 12, "b", anchor="middle"))

    if nick_style == "greeting":
        lead = f"Call me {nick}. "
        body.append(t(x0, tag_y, "Call me ", 14))
        body.append(t(x0 + mono_width("Call me ", 14), tag_y, nick, 14, "b"))
        body.append(t(x0 + mono_width(f"Call me {nick}", 14), tag_y, ".", 14))
        body.append(t(x0 + mono_width(lead, 14), tag_y, PROFILE["tagline"], 14))
    else:
        body.append(t(x0, tag_y, PROFILE["tagline"], 14))
    body.append(hline(x0, split - 30, rule_y))

    step = 24 if len(facts) > 2 else 26
    for i, (label, value) in enumerate(facts):
        fy = rule_y + 30 + i * step
        body.append(t(x0, fy, label, 10, "m l"))
        body.append(t(x0 + 78, fy, value, 13, "b" if label == "AKA" else "p"))
        if label == "NOW":
            cx = x0 + 78 + mono_width(value, 13) + 4
            body.append(f'<rect x="{cx:g}" y="{fy - 11:g}" width="8" height="14" fill="{ACCENT}">'
                        f'<animate attributeName="opacity" values="1;0" keyTimes="0;0.5" calcMode="discrete" '
                        f'dur="1.1s" repeatCount="indefinite"/></rect>')

    # hero, right column: a stacked list of facts
    body.append(rect(split, hero_top, W - split, H - hero_top, SURFACE))
    body.append(vline(split, hero_top, H))
    rows = PROFILE["rows"]
    rh = (H - hero_top) / len(rows)
    for i, (label, value) in enumerate(rows):
        y0 = hero_top + i * rh
        if i:
            body.append(hline(split, W, y0))
        label_y, value_y = y0 + round(rh * 0.36), y0 + round(rh * 0.72)
        body.append(t(split + 22, label_y, label, 10, "m l"))
        body.append(t(W - 22, label_y, f"{i + 1:02d}", 10, "m", anchor="end"))
        if label == "STATUS":
            # Size the cup so its ink is about 1.25x the cap height, which also brings
            # its stroke close to the font's stem weight. Centre the ink on the caps,
            # line its left edge up with the label, and leave a fixed gap to the text.
            size, scale, gap = 13, 0.8, 11
            cap = size * 0.73  # JetBrains Mono cap height
            left, top, right, bottom = CUP_INK
            ix = split + 22 - left * scale
            iy = value_y - cap / 2 - (top + bottom) / 2 * scale
            body.append(hot_coffee(ix, iy, scale))
            body.append(t(ix + right * scale + gap, value_y, value, size, "b"))
        else:
            body.append(t(split + 22, value_y, value, 13, "p"))

    body.append(rect(0, 0, W, H, stroke=LINE))
    title = (f"{PROFILE['name']} (call me {PROFILE['nick']}, @{PROFILE['handle']}): "
             f"{PROFILE['tagline']} Now {PROFILE['now']}.")
    return svg(W, H, title, body, t, "".join(defs))


def placeholder_shot(project: dict, seed: int) -> Image.Image:
    """A fake app screenshot in the theme colours, 2:1, drawn at 2x."""
    rng = random.Random(seed)
    w, h = 880, 440
    accent = (ACCENT, AMBER)[seed % 2]
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    for y in range(h):  # warm glow from the bottom right
        d.line([(0, y), (w, y)], fill=mix(BG, DEEP, 0.55 * (y / h) ** 2))
    ox, oy, ow, oh = 70, 54, 740, 420
    d.rounded_rectangle([ox, oy, ox + ow, oy + oh], 14, fill=SURFACE, outline=LINE_STRONG, width=2)
    d.line([(ox, oy + 36), (ox + ow, oy + 36)], fill=LINE, width=2)
    for i, c in enumerate((MUTED, MUTED, MUTED)):
        d.ellipse([ox + 18 + i * 20, oy + 13, ox + 28 + i * 20, oy + 23], fill=c)
    d.rounded_rectangle([ox + 260, oy + 10, ox + 480, oy + 26], 8, fill=BG)
    # sidebar
    d.line([(ox + 160, oy + 36), (ox + 160, oy + oh)], fill=LINE, width=2)
    for i in range(7):
        y = oy + 62 + i * 30
        d.rounded_rectangle([ox + 18, y, ox + 18 + rng.randint(70, 120), y + 10], 5,
                            fill=accent if i == 1 else LINE_STRONG)
    # headline
    font_path = FONTS / "SpaceGrotesk.ttf"
    try:
        font = ImageFont.truetype(str(font_path), 44)
        small = ImageFont.truetype(str(FONTS / "JetBrainsMono.ttf"), 16)
    except OSError:
        font = small = ImageFont.load_default()
    if not project.get("soon"):
        d.text((ox + 192, oy + 64), project["name"].replace("-", " ").title(), font=font, fill=TEXT)
        d.text((ox + 194, oy + 122), "placeholder screenshot", font=small, fill=SECOND)
    d.rounded_rectangle([ox + 194, oy + 158, ox + 314, oy + 190], 8, fill=accent)
    d.rounded_rectangle([ox + 326, oy + 158, ox + 446, oy + 190], 8, outline=LINE_STRONG, width=2)
    # cards
    for i in range(3):
        cx = ox + 194 + i * 178
        d.rounded_rectangle([cx, oy + 216, cx + 160, oy + 380], 10, fill=BG, outline=LINE, width=2)
        d.rounded_rectangle([cx + 14, oy + 230, cx + 146, oy + 300], 6, fill=mix(SURFACE, accent, 0.18 + 0.1 * i))
        d.rounded_rectangle([cx + 14, oy + 314, cx + 120, oy + 324], 5, fill=LINE_STRONG)
        d.rounded_rectangle([cx + 14, oy + 334, cx + 90, oy + 344], 5, fill=LINE)
    if project.get("soon"):
        img = coming_soon(img, project)
    return img


def coming_soon(img: Image.Image, project: dict) -> Image.Image:
    """Blur and darken a placeholder, then stamp "Coming soon" in the middle."""
    w, h = img.size
    img = img.filter(ImageFilter.GaussianBlur(9))
    img = Image.blend(img, Image.new("RGB", img.size, BG), 0.45)
    d = ImageDraw.Draw(img)
    try:
        big = ImageFont.truetype(str(FONTS / "SpaceGrotesk.ttf"), 64)
        small = ImageFont.truetype(str(FONTS / "JetBrainsMono.ttf"), 22)
    except OSError:
        big = small = ImageFont.load_default()
    title = "Coming Soon"
    sub = "in the works"
    tw = d.textlength(title, font=big)
    sw = d.textlength(sub, font=small)
    d.text(((w - tw) / 2, h / 2 - 78), title, font=big, fill=TEXT)
    d.text(((w - sw) / 2, h / 2 + 34), sub, font=small, fill=SECOND)
    # a short accent rule between the two lines, below the descender of the "g"
    d.rectangle([w / 2 - 28, h / 2 + 18, w / 2 + 28, h / 2 + 21], fill=ACCENT)
    return img


def load_shot(project: dict, seed: int) -> Image.Image:
    if project["image"] is None:
        return placeholder_shot(project, seed)
    img = Image.open(project["image"]).convert("RGB")
    w, h = img.size  # centre-crop to 2:1, then scale to 880 wide
    if w / h > 2:
        nw = int(h * 2)
        img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = int(w / 2)
        img = img.crop((0, 0, w, nh))  # keep the top of the page
    return img.resize((880, 440), Image.LANCZOS)


def tile(project: dict, position: int) -> str:
    """One project card. Tiles sit side by side at 50% each, so the left tile
    leaves out its right edge and the pair shares a single divider."""
    t = Text()
    TW, bar, img_h, cap = W / 2, 34, 220, 42
    TH = bar + img_h + cap
    body = [rect(0, 0, TW, TH, BG)]
    # bar
    body.append(rect(12, 9, 26, 16, ACCENT if position == 0 else AMBER))
    body.append(t(25, 21, project["index"], 10, "k w", anchor="middle"))
    body.append(t(48, 22, project["name"], 12, "p w"))
    if project["domain"]:
        body.append(t(TW - 14, 22, project["domain"], 11, "m", anchor="end"))
    body.append(hline(0, TW, bar - 1))
    # screenshot
    data = jpeg_data(load_shot(project, position))
    body.append(f'<image x="1" y="{bar}" width="{TW - 2:g}" height="{img_h}" '
                f'preserveAspectRatio="xMidYMid slice" href="{data}" xlink:href="{data}"/>')
    body.append(hline(0, TW, bar + img_h))
    # caption
    cy = bar + img_h + cap / 2 + 5
    body.append(t(14, cy, project["blurb"], 12, "p"))
    kind = project["kind"].upper()
    kw = mono_width(kind, 10) + 16
    body.append(rect(TW - 14 - kw, cy - 13, kw, 18, stroke=LINE_STRONG))
    body.append(t(TW - 14 - kw / 2, cy, kind, 10, "b l", anchor="middle"))
    # frame
    edges = [hline(0, TW, 0), hline(0, TW, TH - 1), vline(0, 0, TH)]
    if position == len(PROJECTS) - 1:
        edges.append(vline(TW - 1, 0, TH))
    body.extend(edges)
    title = f"{project['name']}: {project['blurb']}" + (f" ({project['domain']})" if project["domain"] else "")
    return svg(TW, TH, title, body, t)


def icon_path(name: str) -> str:
    raw = (ICONS / f"{name}.svg").read_text()
    return re.search(r'<path d="([^"]+)"', raw).group(1)


def chip(item: dict) -> str:
    t = Text()
    size = 12
    label = item["label"]
    w = 40 + mono_width(label, size) + 34
    h = 36
    body = [rect(0, 0, w, h, SURFACE, LINE)]
    body.append(f'<g transform="translate(14 11) scale(0.5833)" fill="{SECOND}">'
                f'<path d="{icon_path(item["icon"])}"/></g>')
    body.append(vline(38, 0, h))
    body.append(t(50, 22.5, label, size, "p"))
    body.append(t(w - 14, 22.5, "↗", size, "a", anchor="end"))
    return svg(w, h, label, body, t)


def stack_panel() -> str:
    """The tech stack as a panel in the same style as the header and cards:
    a title bar, a shaded label column, and one row of logo chips per group."""
    t = Text()
    bar, row_h, label_w = 34, 52, 168
    chip_h, size, gap = 28, 12, 8
    H = bar + row_h * len(STACK)
    body = [rect(0, 0, W, H, BG)]
    body.append(rect(12, 9, 26, 16, ACCENT))
    body.append(t(25, 21, "04", 10, "k w", anchor="middle"))
    body.append(t(48, 22, "stack", 12, "p w"))
    total = sum(len(items) for _, items in STACK)
    body.append(t(W - 14, 22, f"{len(STACK)} groups / {total} tools", 11, "m", anchor="end"))
    body.append(hline(0, W, bar - 1))
    body.append(rect(1, bar, label_w - 1, H - bar - 1, SURFACE))
    body.append(vline(label_w, bar, H))
    for r, (label, items) in enumerate(STACK):
        y0 = bar + r * row_h
        if r:
            body.append(hline(0, W, y0))
        mid = y0 + row_h / 2
        body.append(t(22, mid + 3.5, label.upper(), 10, "m l"))
        body.append(t(label_w - 16, mid + 3.5, f"{r + 1:02d}", 10, "m", anchor="end"))
        x, cy = label_w + 18, mid - chip_h / 2
        for name, icon, colour in items:
            w = 34 + mono_width(name, size) + 12
            body.append(rect(x, cy, w, chip_h, SURFACE, LINE))
            body.append(f'<g transform="translate({x + 11:g} {cy + 7:g}) scale({14 / 24:.4f})" fill="#{colour}">'
                        f'<path d="{icon_path(icon)}"/></g>')
            body.append(t(x + 34, cy + 18.5, name, size, "p"))
            x += w + gap
    body.append(rect(0, 0, W, H, stroke=LINE))
    title = "Tech stack. " + " ".join(f"{label}: {', '.join(n for n, _, _ in items)}." for label, items in STACK)
    return svg(W, H, title, body, t)


# ---------------------------------------------------------------- output

def write(path: Path, content: str) -> str:
    path.write_text(content)
    return hashlib.sha1(content.encode()).hexdigest()[:10]


def refresh_readme(hashes: dict[str, str]) -> None:
    readme = ROOT / "README.md"
    text = readme.read_text()
    for name, digest in hashes.items():
        text = re.sub(rf"(\./assets/{re.escape(name)})(\?v=[0-9a-f]+)?", rf"\1?v={digest}", text)
    readme.write_text(text)


# Wording options for the note under "Steve", written out by --preview.
NICK_LABELS = [
    "call me this part",
    "or just Steve",
    "Steve works fine",
    "friends call me this",
    "the short version",
    "this part is enough",
    "Steve for short",
    "\u2190 call me this",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", type=Path, help="also write every banner variant here")
    args = parser.parse_args()

    ASSETS.mkdir(exist_ok=True)
    hashes = {"header.svg": write(ASSETS / "header.svg", header(BANNER))}
    for i, project in enumerate(PROJECTS):
        name = f"tile-{project['slug']}.svg"
        hashes[name] = write(ASSETS / name, tile(project, i))
    hashes["stack.svg"] = write(ASSETS / "stack.svg", stack_panel())
    for item in CHIPS:
        name = f"chip-{item['slug']}.svg"
        hashes[name] = write(ASSETS / name, chip(item))
    refresh_readme(hashes)

    for name in hashes:
        print(f"{name:28} {(ASSETS / name).stat().st_size / 1024:7.1f} KB")

    if args.preview:
        args.preview.mkdir(parents=True, exist_ok=True)
        default = PROFILE["nick_label"]
        for i, label in enumerate(NICK_LABELS):
            PROFILE["nick_label"] = label
            path = args.preview / f"header-label-{i}.svg"
            path.write_text(header(BANNER, "highlight"))
            print(f"preview {path.name:26} {label}")
        PROFILE["nick_label"] = default
        for key in BANNERS:
            path = args.preview / f"header-{key}.svg"
            path.write_text(header(key))
            print(f"preview {path.name:20} {path.stat().st_size / 1024:7.1f} KB")


if __name__ == "__main__":
    main()
