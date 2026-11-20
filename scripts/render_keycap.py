# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pillow"]
# ///
"""Render the glass keycap images used by the "keys" banner.

The banner cannot run a 3D scene inside a README, so this script traces one
keycap ahead of time: light refracts through a rounded glass shell, splits into
colour at the edges and reflects a small studio of soft lights. It writes an
unlit and a lit image, plus the numbers build_profile.py needs to place them.

Usage:
    uv run scripts/render_keycap.py            # final quality, about a minute
    uv run scripts/render_keycap.py --draft    # quick look at low quality
"""

import argparse
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

OUT = Path(__file__).resolve().parent / "keycap"

# Camera and key shape. build_profile.py reads these back from keycap.json.
SCALE = 124                    # pixels per key pitch at 1x
THETA, PHI = math.radians(18), math.radians(42)   # grid rotation, camera tilt
SIZE, TAPER, HEIGHT = 0.92, 0.72, 0.40   # a real keycap fills most of its pitch
ROUND, WALL, DISH = 0.055, 0.095, 0.032
FLOOR = -0.05


def _view():
    """Left, right, top, bottom of the image in key pitches: the key plus room for glow."""
    reach = SIZE / 2 * (math.cos(THETA) + math.sin(THETA))
    top = max(reach, reach * TAPER + HEIGHT / math.tan(PHI)) * math.sin(PHI)
    return -reach - 0.12, reach + 0.12, -top - 0.14, reach * math.sin(PHI) - FLOOR * math.cos(PHI) + 0.1


VIEW = _view()

GLASS = np.array([0x52, 0x27, 0xFF]) / 255    # tint of the glass body
LIGHT = np.array([0xFF, 0x7A, 0x27]) / 255    # the warm light and the backlight
ABSORB = 7.5 * (1 - GLASS)             # how fast each colour fades inside the glass
IOR = (1.46, 1.50, 1.55)                      # red, green, blue bend differently


# ---------------------------------------------------------------- shape

def _smax(a, b, k):
    h = np.clip(0.5 + 0.5 * (a - b) / k, 0, 1)
    return b + (a - b) * h + k * h * (1 - h)


def _tapered_box(p, half, z0, z1, r):
    """A box that narrows toward the top, with rounded edges."""
    x, y, z = p[:, 0], p[:, 1], p[:, 2]
    f = 1 - (1 - TAPER) * np.clip(z / HEIGHT, 0, 1)
    dx, dy = np.abs(x) / f - (half - r), np.abs(y) / f - (half - r)
    dz = np.abs(z - (z0 + z1) / 2) - ((z1 - z0) / 2 - r)
    outside = np.sqrt(np.maximum(dx, 0) ** 2 + np.maximum(dy, 0) ** 2 + np.maximum(dz, 0) ** 2)
    return outside + np.minimum(np.maximum(dx, np.maximum(dy, dz)), 0) - r


def sdf(p):
    """Distance to the keycap: a hollow shell with a shallow dish on top."""
    outer = _tapered_box(p, SIZE / 2, 0.0, HEIGHT, ROUND)
    radius = 1.1
    centre = np.array([0, 0, HEIGHT + radius - DISH])
    outer = _smax(outer, radius - np.linalg.norm(p - centre, axis=1), 0.03)
    inner = _tapered_box(p, SIZE / 2 - WALL, -1.0, HEIGHT - WALL - DISH, 0.035)
    return _smax(outer, -inner, 0.012) * 0.7


def normal(p):
    e = 1e-4
    n = np.zeros_like(p)
    for k in ((1, -1, -1), (-1, -1, 1), (-1, 1, -1), (1, 1, 1)):
        k = np.array(k, float)
        n += k * sdf(p + k * e)[:, None]
    return n / np.linalg.norm(n, axis=1, keepdims=True)


# ---------------------------------------------------------------- light

def _to_local(v):
    """Screen-aligned direction (x right, y away, z up) to the key's own axes."""
    cs, sn = math.cos(THETA), math.sin(THETA)
    return np.array([v[0] * cs + v[1] * sn, -v[0] * sn + v[1] * cs, v[2]])


def _lamp(d, toward, inner, outer, colour):
    toward = _to_local(np.array(toward, float) / np.linalg.norm(toward))
    c = d @ toward
    s = np.clip((c - outer) / (inner - outer), 0, 1)
    return (s * s * (3 - 2 * s))[:, None] * np.array(colour)


def sky(d):
    """A dark studio with a few soft lights."""
    up = np.clip(d[:, 2], 0, 1)[:, None]
    out = 0.02 * GLASS + 0.05 * up * np.array([0.5, 0.42, 1.0])
    out = out + _lamp(d, (-0.15, 0.92, 0.36), 0.975, 0.895, (3.4, 3.1, 4.4))    # big soft box behind
    out = out + _lamp(d, (-0.85, -0.25, 0.42), 0.97, 0.86, (3.2, 2.9, 4.6))   # cool strip, left
    out = out + _lamp(d, (0.80, 0.40, 0.22), 0.96, 0.78, 3.0 * LIGHT)         # warm rim, right
    out = out + _lamp(d, (0.15, -0.75, 0.62), 0.975, 0.90, (2.6, 2.4, 3.4))   # small light near the camera
    return out


def floor(pt, lit):
    r2 = pt[:, 0] ** 2 + pt[:, 1] ** 2
    out = np.tile(0.012 + 0.03 * GLASS, (len(pt), 1)) + 0.05 * np.exp(-r2 / 0.6)[:, None] * GLASS
    # a hint of the neighbouring keys' backlights, for the side faces to reflect
    out = out + 0.16 * np.exp(-(np.sqrt(r2) - 1.0) ** 2 / 0.08)[:, None] * LIGHT
    if lit:
        out = out + (2.6 * np.exp(-r2 / 0.30 ** 2) + 0.5 * np.exp(-r2 / 0.80 ** 2))[:, None] * LIGHT
    return out


def backdrop(o, d, lit):
    """What a ray in open air ends up seeing: the floor or the studio."""
    out = sky(d)
    down = d[:, 2] < -1e-3
    if down.any():
        t = (FLOOR - o[down, 2]) / d[down, 2]
        out[down] = floor(o[down] + d[down] * t[:, None], lit)
    return out


# ---------------------------------------------------------------- tracing

def march(p, d, sign):
    """Walk each ray to the next glass surface. Returns distance and whether it hit."""
    n = len(p)
    t, hit = np.zeros(n), np.zeros(n, bool)
    alive = np.arange(n)
    for _ in range(220):
        if not alive.size:
            break
        q = p[alive] + d[alive] * t[alive, None]
        dist = sdf(q) * sign[alive]
        h = dist < 1.5e-4
        gone = ~h & ((t[alive] > 7) | (q[:, 2] < FLOOR))
        hit[alive[h]] = True
        keep = ~(h | gone)
        t[alive[keep]] += np.maximum(dist[keep], 4e-4)
        alive = alive[keep]
    return t, hit


def trace(o, d, ior, lit, bounces=10):
    """Radiance along each ray, and whether the ray touched the key at all."""
    n = len(o)
    rad, thr = np.zeros((n, 3)), np.ones((n, 3))
    p, d, sign = o.copy(), d.copy(), np.ones(n)
    alive, touched = np.arange(n), np.zeros(n, bool)
    f0 = ((ior - 1) / (ior + 1)) ** 2
    eps = 1.5e-3
    for bounce in range(bounces):
        if not alive.size:
            break
        t, hit = march(p[alive], d[alive], sign[alive])
        if bounce == 0:
            touched[alive] = hit
        miss = alive[~hit]
        rad[miss] += thr[miss] * backdrop(p[miss], d[miss], lit)
        idx = alive[hit]
        ph = p[idx] + d[idx] * t[hit, None]
        dh, nh = d[idx], normal(ph)
        inside = sign[idx] < 0

        # entering the glass: some light reflects off the surface, the rest bends in
        e = idx[~inside]
        if e.size:
            dn, nn, pe = dh[~inside], nh[~inside], ph[~inside]
            cosi = np.clip(-(dn * nn).sum(1), 0, 1)
            fres = (f0 + (1 - f0) * (1 - cosi) ** 5)[:, None]
            rad[e] += thr[e] * fres * backdrop(pe, dn + 2 * cosi[:, None] * nn, lit)
            thr[e] *= 1 - fres
            eta = 1 / ior
            k = np.maximum(1 - eta ** 2 * (1 - cosi ** 2), 0)
            d[e] = eta * dn + (eta * cosi - np.sqrt(k))[:, None] * nn
            p[e] = pe - nn * eps
            sign[e] = -1

        # leaving the glass: it fades by colour with distance, then bends out
        # or, at a shallow angle, bounces back inside
        x = idx[inside]
        if x.size:
            dn, nn, px = dh[inside], nh[inside], ph[inside]
            thr[x] *= np.exp(-ABSORB * t[hit][inside, None])
            cosi = np.clip((dn * nn).sum(1), 0, 1)
            k = 1 - ior ** 2 * (1 - cosi ** 2)
            tir = k < 0
            d[x[tir]] = dn[tir] - 2 * cosi[tir, None] * nn[tir]
            p[x[tir]] = px[tir] - nn[tir] * eps
            out = ~tir
            fres = (f0 + (1 - f0) * (1 - cosi[out]) ** 5)[:, None]
            thr[x[out]] *= 1 - fres
            d[x[out]] = ior * dn[out] + (np.sqrt(k[out]) - ior * cosi[out])[:, None] * nn[out]
            p[x[out]] = px[out] + nn[out] * eps
            sign[x[out]] = 1
        d[idx] /= np.linalg.norm(d[idx], axis=1, keepdims=True)
        alive = idx
    rad[alive] += thr[alive] * 0.05 * GLASS
    return rad, touched


def render(lit, zoom, samples, disperse):
    """Returns premultiplied linear colour (h, w, 3) and coverage (h, w)."""
    w = round((VIEW[1] - VIEW[0]) * SCALE * zoom)
    h = round((VIEW[3] - VIEW[2]) * SCALE * zoom)
    sp, cp = math.sin(PHI), math.cos(PHI)
    offs = (np.arange(samples) + 0.5) / samples
    colour, cover = np.zeros((h, w, 3)), np.zeros((h, w))
    look = _to_local((0, cp, -sp))
    for sy in offs:
        for sx in offs:
            u = VIEW[0] + (np.arange(w) + sx) / (SCALE * zoom)
            v = VIEW[2] + (np.arange(h) + sy) / (SCALE * zoom)
            u, v = np.meshgrid(u, v)
            u, v = u.ravel(), v.ravel()
            # screen position to a point in the scene, then back along the view direction
            rot = np.stack([u, -v * sp, -v * cp], 1)
            cs, sn = math.cos(THETA), math.sin(THETA)
            o = np.stack([rot[:, 0] * cs + rot[:, 1] * sn, -rot[:, 0] * sn + rot[:, 1] * cs, rot[:, 2]], 1)
            o = o - look * 3.0
            d = np.tile(look, (len(o), 1))
            if disperse:
                rad = np.zeros((len(o), 3))
                for c in range(3):
                    r, touched = trace(o, d, IOR[c], lit)
                    rad[:, c] = r[:, c]
            else:
                rad, touched = trace(o, d, IOR[1], lit)
            colour += (rad * touched[:, None]).reshape(h, w, 3)
            cover += touched.reshape(h, w)
    return colour / samples ** 2, cover / samples ** 2


def _blur(a, radius):
    """Gaussian blur of a float image (h, w, channels)."""
    n = max(1, int(radius * 3))
    kernel = np.exp(-0.5 * (np.arange(-n, n + 1) / radius) ** 2)
    kernel /= kernel.sum()
    for axis in (0, 1):
        pad = [(n, n) if i == axis else (0, 0) for i in range(3)]
        padded = np.pad(a, pad)
        a = sum(w * np.take(padded, np.arange(k, k + a.shape[axis]), axis=axis) for k, w in enumerate(kernel))
    return a


def finish(colour, cover, zoom):
    """Add the glow around bright spots, then turn light into screen colours."""
    bright = np.maximum(colour - 0.9, 0)
    glow = 0.35 * _blur(bright, 2.2 * zoom) + 0.22 * _blur(bright, 7 * zoom)
    colour = colour + glow
    alpha = np.clip(np.maximum(cover, glow.max(2) * 1.4), 0, 1)
    straight = colour / np.maximum(alpha, 1e-3)[:, :, None]
    mapped = 1 - np.exp(-1.25 * straight)
    rgb = np.clip(mapped, 0, 1) ** (1 / 2.2)
    return Image.fromarray((np.dstack([rgb, alpha]) * 255).round().astype(np.uint8), "RGBA")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft", action="store_true", help="low quality, for a quick look")
    args = parser.parse_args()
    zoom, samples, disperse = (2, 1, False) if args.draft else (2, 3, True)
    OUT.mkdir(exist_ok=True)
    for name, lit in (("keycap", False), ("keycap-lit", True)):
        image = finish(*render(lit, zoom, samples, disperse), zoom)
        if args.draft:
            image.save(OUT / f"{name}-draft.png")
        else:
            image.save(OUT / f"{name}.webp", quality=92, method=6)
        print(name, image.size)
    if not args.draft:
        meta = dict(scale=SCALE, theta=math.degrees(THETA), phi=math.degrees(PHI), size=SIZE, height=HEIGHT,
                    x=VIEW[0] * SCALE, y=VIEW[2] * SCALE,
                    width=(VIEW[1] - VIEW[0]) * SCALE, height_px=(VIEW[3] - VIEW[2]) * SCALE)
        (OUT / "keycap.json").write_text(json.dumps(meta, indent=2) + "\n")


if __name__ == "__main__":
    main()
