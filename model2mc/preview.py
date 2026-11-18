"""Preview images: the source model and the block build, side by side, from the same isometric angle.

Both are drawn with the same projection (screen x = x - z, screen y = (x + z) / 2 - y, in pixels of `s` per block),
so the two columns line up. The blocks are flat-coloured with each block's measured average colour and Minecraft's
own face shading (top 1.0, north/south 0.8, east/west 0.6), with a faint outline per face so the blocks read. The model
is point-splatted from its surface (texture colour, simple diffuse light) at twice the resolution and scaled down.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .colour import linear_to_srgb
from .mesh import Model

BG_TOP = np.array([206, 211, 217], np.float32)
BG_BOTTOM = np.array([168, 175, 184], np.float32)


def _zmerge(zbuf: np.ndarray, colbuf: np.ndarray, pix: np.ndarray, d: np.ndarray, c: np.ndarray):
    """Write values `c` at pixels `pix` where depth `d` is the nearest so far (bigger is nearer)."""
    if len(pix) == 0:
        return
    order = np.argsort(d, kind="stable")  # farthest first, so for a repeated pixel the nearest is written last
    p, dd = pix[order], d[order]
    best = np.full(len(zbuf), -1, np.int64)
    best[p] = order
    cand = np.nonzero(best >= 0)[0]
    src = best[cand]
    win = d[src] > zbuf[cand]
    cand, src = cand[win], src[win]
    zbuf[cand] = d[src]
    colbuf[cand] = c[src]


def _face_masks(s: int):
    """Pixel offsets (relative to a cube's projected min corner) for its top, +x and +z faces, and which of those
    pixels lie on the face's edge."""
    h = s // 2
    polys = {
        "top": [(0, -s), (s, -h), (0, 0), (-s, -h)],
        "east": [(s, h), (s, -h), (0, 0), (0, s)],
        "south": [(-s, h), (0, s), (0, 0), (-s, -h)],
    }
    ys, xs = np.mgrid[-s:s + 1, -s:s + 1]
    cx, cy = xs + 0.5, ys + 0.5
    out = {}
    for name, poly in polys.items():
        inside = np.ones(xs.shape, bool)
        sign = None
        for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
            e = (x1 - x0) * (cy - y0) - (y1 - y0) * (cx - x0)
            if sign is None:
                # orientation from the polygon's own winding
                area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1]))
                sign = 1 if area > 0 else -1
            inside &= e * sign > 0
        pad = np.pad(inside, 1)
        edge = inside & ~(pad[:-2, 1:-1] & pad[2:, 1:-1] & pad[1:-1, :-2] & pad[1:-1, 2:])
        sel = np.nonzero(inside)
        out[name] = (xs[sel], ys[sel], edge[sel])
    return out


def _canvas(shape, s):
    X, Y, Z = shape
    w = (X + Z) * s + 2 * s
    h = (X + Z) * s // 2 + Y * s + 2 * s
    return w, h, Z * s + s, Y * s + s  # width, height, origin x, origin y


def render_blocks(blocks: np.ndarray, top_rgb: np.ndarray, side_rgb: np.ndarray, s: int) -> Image.Image:
    """Isometric render of a block grid seen from +x +y +z. `top_rgb`/`side_rgb` (n_states, 3) uint8 per state."""
    X, Y, Z = blocks.shape
    W, H, ox, oy = _canvas(blocks.shape, s)
    zbuf = np.full(W * H, -1, np.int64)
    img = np.zeros((W * H, 3), np.float32)
    solid = blocks != 0
    pad = np.pad(solid, ((0, 1), (0, 1), (0, 1)))
    exposed = {
        "top": solid & ~pad[:-1, 1:, :-1],
        "east": solid & ~pad[1:, :-1, :-1],
        "south": solid & ~pad[:-1, :-1, 1:],
    }
    shade = {"top": 1.0, "east": 0.6, "south": 0.8}
    edge = 1.0 if s <= 2 else 0.93 if s <= 4 else 0.87  # faint outlines, none when a block is only a few pixels
    masks = _face_masks(s)
    for face, ex in exposed.items():
        x, y, z = np.nonzero(ex)
        if len(x) == 0:
            continue
        ax = (x - z) * s + ox
        ay = (x + z) * (s // 2) - y * s + oy
        depth = x + y + z
        rgb = (top_rgb if face == "top" else side_rgb)[blocks[x, y, z]].astype(np.float32) * shade[face]
        mx, my, medge = masks[face]
        px = (ax[:, None] + mx[None, :]).reshape(-1)
        py = (ay[:, None] + my[None, :]).reshape(-1)
        pix = py * W + px
        d = np.repeat(depth, len(mx))
        c = np.repeat(rgb, len(mx), axis=0) * np.tile(np.where(medge, edge, 1.0), len(x))[:, None]
        _zmerge(zbuf, img, pix, d, c)
    out = _background(W, H)
    covered = (zbuf >= 0).reshape(H, W)
    out[covered] = img.reshape(H, W, 3)[covered]
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def render_model(model: Model, shape, s: int, flip: bool = False, ss: int = 2) -> Image.Image:
    """Point-splat render of `model` (in voxel space) with the same projection as render_blocks.

    Points are depth-tested first, carrying only (part, triangle, barycentric); the texture is sampled once per
    visible pixel at the end."""
    X, Y, Z = shape
    W, H, ox, oy = _canvas(shape, s)
    Wb, Hb = W * ss, H * ss
    zbuf = np.full(Wb * Hb, -np.inf)
    ref = np.zeros((Wb * Hb, 5), np.float64)  # part, triangle, barycentric (3)
    density = 3.0 * (s * ss) ** 2  # points per square voxel unit: ~3 per supersampled pixel
    rng = np.random.default_rng(1)
    light = np.array([-0.35, 0.85, 0.4])
    light /= np.linalg.norm(light)
    view = np.array([1.0, 1.0, 1.0]) / np.sqrt(3)
    lits = []
    for pi, part in enumerate(model.parts):
        tris = part.tris.copy()
        if flip:  # a half turn about the vertical: the back of the model
            tris[..., 0] = X - tris[..., 0]
            tris[..., 2] = Z - tris[..., 2]
        n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
        n = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        n = np.where((n @ view)[:, None] < 0, -n, n)  # lit from both sides
        lits.append(0.42 + 0.58 * np.clip(n @ light, 0, 1))
        area = 0.5 * np.linalg.norm(np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]), axis=1)
        # random points, about `density` per supersampled pixel of surface (every triangle at least one)
        cnt = np.maximum(1, np.round(area * density).astype(np.int64))
        csum = np.cumsum(cnt)
        chunk = 3_000_000
        start = 0
        while start < len(tris):
            end = int(np.searchsorted(csum, csum[start] - cnt[start] + chunk, "right"))
            end = max(end, start + 1)
            t = np.repeat(np.arange(start, end), cnt[start:end])
            r = rng.random((len(t), 2))
            flipr = r.sum(1) > 1
            r[flipr] = 1 - r[flipr]
            b = np.column_stack([1 - r.sum(1), r])
            p = np.einsum("mk,mkj->mj", b, tris[t])
            px = np.floor(((p[:, 0] - p[:, 2]) * s + ox) * ss).astype(np.int64)
            py = np.floor(((p[:, 0] + p[:, 2]) * (s / 2) - p[:, 1] * s + oy) * ss).astype(np.int64)
            ok = (px >= 0) & (px < Wb) & (py >= 0) & (py < Hb)
            ref_rows = np.column_stack([np.full(ok.sum(), pi), t[ok], b[ok]])
            _zmerge(zbuf, ref, (py * Wb + px)[ok], p[ok].sum(1), ref_rows)
            start = end
    _fill_gaps(zbuf, ref, Wb, Hb)
    covered = np.isfinite(zbuf)
    colbuf = np.zeros((Wb * Hb, 3), np.float32)
    for pi, part in enumerate(model.parts):
        sel = np.nonzero(covered & (ref[:, 0] == pi))[0]
        if len(sel):
            t = ref[sel, 1].astype(np.int64)
            colbuf[sel] = part.colours(t, ref[sel, 2:])[:, :3] * lits[pi][t][:, None]
    srgb = linear_to_srgb(colbuf) * 255
    bg = _background(Wb, Hb).reshape(-1, 3)
    out = np.where(covered[:, None], srgb, bg).reshape(Hb, Wb, 3)
    img = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
    return img.resize((W, H), Image.LANCZOS)


def _fill_gaps(zbuf: np.ndarray, ref: np.ndarray, W: int, H: int, passes: int = 2, gap: float = 1.0):
    """Random splats leave pixels where a farther surface (or the background) shows through the nearer one. A pixel
    with at least five of its eight neighbours more than `gap` nearer takes the nearest neighbour's sample."""
    z = zbuf.reshape(H, W)
    r = ref.reshape(H, W, -1)
    offs = [(dy, dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx]
    for _ in range(passes):
        zp = np.pad(z, 1, constant_values=-np.inf)
        stack = np.stack([zp[1 + dy:H + 1 + dy, 1 + dx:W + 1 + dx] for dy, dx in offs])
        nearer = (stack > z[None] + gap).sum(0)
        fix = nearer >= 5
        if not fix.any():
            break
        k = stack.argmax(0)
        ys, xs = np.nonzero(fix)
        dy = np.array([o[0] for o in offs])[k[ys, xs]]
        dx = np.array([o[1] for o in offs])[k[ys, xs]]
        z[ys, xs] = z[ys + dy, xs + dx]
        r[ys, xs] = r[ys + dy, xs + dx]


def _background(W, H):
    t = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
    return np.broadcast_to(BG_TOP * (1 - t) + BG_BOTTOM * t, (H, W, 3)).copy()


def pick_scale(shape, target: int = 560) -> int:
    """Pixels per block: about `target` pixels across, but at least 4 (2 for very big builds) so blocks read."""
    X, Y, Z = shape
    span = max(X + Z, (X + Z) / 2 + Y)
    s = int(target / span)
    s -= s % 2
    floor = 4 if span <= 160 else 2
    return max(floor, min(s, 16))


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def preview(model: Model, blocks: np.ndarray, top_rgb: np.ndarray, side_rgb: np.ndarray, title: str,
            subtitle: str, target: int = 560) -> Image.Image:
    """2 x 2 sheet: model and blocks from the front-right (south-east) and from the back-left (north-west)."""
    s = pick_scale(blocks.shape, target)
    tiles = [
        render_model(model, blocks.shape, s),
        render_blocks(blocks, top_rgb, side_rgb, s),
        render_model(model, blocks.shape, s, flip=True),
        render_blocks(blocks[::-1, :, ::-1], top_rgb, side_rgb, s),
    ]
    tw, th = tiles[0].size
    gap, head, foot = 8, 34, 30
    sheet = Image.new("RGB", (2 * tw + 3 * gap, head + 2 * th + 3 * gap + foot), (40, 44, 52))
    d = ImageDraw.Draw(sheet)
    f = _font(18)
    d.text((gap, 8), "model", fill=(230, 233, 238), font=f)
    d.text((2 * gap + tw, 8), "blocks", fill=(230, 233, 238), font=f)
    for i, t in enumerate(tiles):
        sheet.paste(t, (gap + (i % 2) * (tw + gap), head + (i // 2) * (th + gap)))
    d.text((gap, head + 2 * th + 2 * gap + 4), f"{title}  ·  {subtitle}", fill=(200, 204, 212), font=_font(15))
    return sheet
