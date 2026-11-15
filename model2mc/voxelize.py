"""Turning triangles into a grid of voxels: which cells are filled, which of them show, and their colours.

Grid axes are Minecraft's: x east, y up, z south. Voxel (x, y, z) covers [x, x+1) x [y, y+1) x [z, z+1) in voxel units.

1. The surface is sampled: every triangle gets a lattice of points no further apart than `spacing` voxels, each point
   coloured from the model (texture at its interpolated UV, etc.) and added, weighted by the area it stands for, into
   the voxel it falls in. That gives the surface cells and their average colours.
2. The inside is found by casting a ray along every column of the grid on all three axes and filling between pairs of
   crossings (even-odd). A cell is inside when at least two of the three axes say so; that vote keeps a small hole or a
   stray inner face from filling (or emptying) a whole column.
3. The shell is every filled cell with an empty face-neighbour (or `thickness` cells in from one): what you can see.
   Shell cells take the colour of the nearest sampled surface cell. In solid mode the cells under the shell are filled
   too, with a filler block.

By default a cell counts if its centre is inside the model, or if the surface passes through it and no inside cell is
next to it (so a part thinner than a block still shows). `loose` keeps every cell the surface touches, which makes
the build about half a block fatter all round.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from scipy import ndimage

from .mesh import Model

AIR, SHELL, INTERIOR = 0, 1, 2


@dataclass
class Voxels:
    kind: np.ndarray  # (X, Y, Z) uint8: AIR / SHELL / INTERIOR
    colour: np.ndarray  # (X, Y, Z, 3) float32 linear RGB (meaningful on SHELL cells)
    scale: float  # voxels per model unit
    origin: np.ndarray  # model-space point at voxel (0, 0, 0)
    surface_cells: int  # cells hit by surface samples
    model: Model | None = None  # the model moved into voxel space (for the preview)

    @property
    def shape(self):
        return self.kind.shape


@lru_cache(maxsize=256)
def _lattice(n: int) -> np.ndarray:
    """Barycentric centroids of the n*n sub-triangles of a triangle split n ways along each edge."""
    pts = []
    for i in range(n):
        for j in range(n - i):
            pts.append(((i + 1 / 3) / n, (j + 1 / 3) / n))
            if i + j < n - 1:
                pts.append(((i + 2 / 3) / n, (j + 2 / 3) / n))
    uv = np.array(pts, dtype=np.float64)
    out = np.column_stack([1 - uv.sum(1), uv[:, 0], uv[:, 1]])
    out.flags.writeable = False
    return out


def _sample_surface(model: Model, shape, spacing: float, max_points: int = 4_000_000):
    """Area-weighted colour per cell. Returns (weight (N,), rgb (N, 3)) over the flat grid.

    Samples are kept apart by which of the six directions their triangle faces, and a cell takes the average of its
    heaviest direction only: a block on a corner where two differently coloured faces meet takes one face's colour,
    not a muddy mix of both (a block can only be one colour)."""
    X, Y, Z = shape
    ncell = X * Y * Z
    keys, ws, cs = [], [], []
    for part in model.parts:
        tris = part.tris
        e = np.stack([tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 1], tris[:, 0] - tris[:, 2]], 1)
        longest = np.linalg.norm(e, axis=2).max(1)
        nrm = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
        area = 0.5 * np.linalg.norm(nrm, axis=1)
        ax = np.abs(nrm).argmax(1)
        bucket = ax * 2 + (nrm[np.arange(len(nrm)), ax] < 0)
        nsplit = np.maximum(1, np.ceil(longest / spacing)).astype(np.int64)
        for n in np.unique(nsplit):
            idx = np.nonzero(nsplit == n)[0]
            bary = _lattice(int(n))
            per = len(bary)
            step = max(1, max_points // per)
            for s in range(0, len(idx), step):
                ti = idx[s:s + step]
                b = np.tile(bary, (len(ti), 1))
                t = np.repeat(ti, per)
                pts = np.einsum("mk,mkj->mj", b, tris[t])
                cell = np.floor(pts).astype(np.int64)
                np.clip(cell, 0, np.array(shape) - 1, out=cell)
                key = ((cell[:, 0] * Y + cell[:, 1]) * Z + cell[:, 2]) * 6 + bucket[t]
                rgba = part.colours(t, b)
                w = (area[t] / per) * (rgba[:, 3] >= 0.5) + 1e-12  # alpha cutoff, tiny floor so cells register
                uk, inv = np.unique(key, return_inverse=True)
                keys.append(uk)
                ws.append(np.bincount(inv, w, len(uk)))
                cs.append(np.stack([np.bincount(inv, w * rgba[:, c], len(uk)) for c in range(3)], 1))
    key = np.concatenate(keys)
    uk, inv = np.unique(key, return_inverse=True)
    w = np.bincount(inv, np.concatenate(ws), len(uk))
    csum = np.concatenate(cs)
    c = np.stack([np.bincount(inv, csum[:, i], len(uk)) for i in range(3)], 1)
    cell = uk // 6
    order = np.lexsort((w, cell))  # by cell, heaviest direction last
    last = np.r_[cell[order][1:] != cell[order][:-1], True]
    pick = order[last]
    wsum = np.zeros(ncell, np.float64)
    rgb = np.zeros((ncell, 3), np.float64)
    wsum[cell[pick]] = w[pick]
    rgb[cell[pick]] = c[pick] / w[pick][:, None]
    return wsum, rgb


def _parity_axis(tris: np.ndarray, shape, axis: int) -> np.ndarray:
    """Even-odd inside test along `axis` for every column of cell centres. Returns a bool grid."""
    u_ax, v_ax = [a for a in range(3) if a != axis]
    dims = np.array(shape)
    U, V, A = dims[u_ax], dims[v_ax], dims[axis]
    # cell centres, nudged off the exact half-integers so a ray never runs exactly along a shared edge
    eu, ev = 0.5 + 1.2345e-4, 0.5 + 2.3456e-4
    p0, p1, p2 = tris[:, 0], tris[:, 1], tris[:, 2]
    tu = tris[:, :, u_ax]
    tv = tris[:, :, v_ax]
    iu0 = np.maximum(0, np.ceil(tu.min(1) - eu)).astype(np.int64)
    iu1 = np.minimum(U - 1, np.floor(tu.max(1) - eu)).astype(np.int64)
    iv0 = np.maximum(0, np.ceil(tv.min(1) - ev)).astype(np.int64)
    iv1 = np.minimum(V - 1, np.floor(tv.max(1) - ev)).astype(np.int64)
    cu = np.maximum(0, iu1 - iu0 + 1)
    cv = np.maximum(0, iv1 - iv0 + 1)
    cnt = cu * cv
    keep = cnt > 0
    tri_i = np.nonzero(keep)[0]
    cnt = cnt[keep]
    total = int(cnt.sum())
    if total == 0:
        return np.zeros(shape, bool)
    t = np.repeat(tri_i, cnt)
    local = np.arange(total) - np.repeat(np.cumsum(cnt) - cnt, cnt)
    cvr = cv[keep].repeat(cnt)
    ui = iu0[t] + local // cvr
    vi = iv0[t] + local % cvr
    pu, pv = ui + eu, vi + ev
    # edge functions (2D barycentric) in the u-v plane
    au, av = tu[t, 0], tv[t, 0]
    bu, bv = tu[t, 1], tv[t, 1]
    cu_, cv_ = tu[t, 2], tv[t, 2]
    w0 = (bu - pu) * (cv_ - pv) - (bv - pv) * (cu_ - pu)
    w1 = (cu_ - pu) * (av - pv) - (cv_ - pv) * (au - pu)
    w2 = (au - pu) * (bv - pv) - (av - pv) * (bu - pu)
    den = w0 + w1 + w2
    inside = (np.abs(den) > 1e-12) & (((w0 >= 0) & (w1 >= 0) & (w2 >= 0)) | ((w0 <= 0) & (w1 <= 0) & (w2 <= 0)))
    t, ui, vi = t[inside], ui[inside], vi[inside]
    w0, w1, w2, den = w0[inside] / den[inside], w1[inside] / den[inside], w2[inside] / den[inside], den[inside]
    hit = w0 * p0[t, axis] + w1 * p1[t, axis] + w2 * p2[t, axis]
    col = ui * V + vi
    order = np.lexsort((hit, col))
    col, hit = col[order], hit[order]
    # rank of each crossing within its column
    starts = np.r_[0, np.nonzero(np.diff(col))[0] + 1]
    counts = np.diff(np.r_[starts, len(col)])
    rank = np.arange(len(col)) - np.repeat(starts, counts)
    colcount = np.repeat(counts, counts)
    enter = (rank % 2 == 0) & (rank + 1 < colcount)
    ei = np.nonzero(enter)[0]
    a0 = np.clip(np.ceil(hit[ei] - 0.5), 0, A).astype(np.int64)
    a1 = np.clip(np.floor(hit[ei + 1] - 0.5) + 1, 0, A).astype(np.int64)  # exclusive
    ok = a1 > a0
    diff = np.zeros((U * V, A + 1), np.int32)
    np.add.at(diff, (col[ei][ok], a0[ok]), 1)
    np.add.at(diff, (col[ei][ok], a1[ok]), -1)
    filled = np.cumsum(diff[:, :A], axis=1) > 0
    filled = filled.reshape(U, V, A)
    # back to (x, y, z)
    perm = np.argsort([u_ax, v_ax, axis])
    return np.transpose(filled, perm)


def voxelize(model: Model, height: int | None = None, longest: int | None = None, mode: str = "shell",
             thickness: int = 1, spacing: float = 0.3, fill_inside: bool = True, loose: bool = False,
             cut=None) -> Voxels:
    """Voxelise `model` (already in Minecraft orientation) to `height` blocks tall (or `longest` blocks on its
    longest side). `mode` is "shell" (only what shows) or "solid" (filled)."""
    lo, hi = model.bounds()
    ext = np.maximum(hi - lo, 1e-9)
    if longest:
        scale = longest / ext.max()
    else:
        scale = (height or 64) / ext[1]
    shape = tuple(int(max(1, np.ceil(e * scale - 1e-6))) for e in ext)
    if max(shape) > 2048:
        raise ValueError(f"grid {shape} is too big; lower --height")
    vm = model.transformed(np.diag([scale, scale, scale, 1.0]) @ _translate(-lo))

    wsum, rgb = _sample_surface(vm, shape, spacing)
    surface = (wsum > 0).reshape(shape)
    colour = rgb.reshape(*shape, 3).astype(np.float32)

    solid = surface.copy()
    if fill_inside:
        all_tris = np.concatenate([p.tris for p in vm.parts])
        votes = sum(_parity_axis(all_tris, shape, a).astype(np.uint8) for a in range(3))
        inside = votes >= 2
        if loose:
            solid |= inside
        else:
            # a cell the surface only clips, with its centre outside, is dropped where an inside cell touches it
            # (even at a corner) (the surface is already covered there); kept where nothing is (a thin part: an ear, a blade)
            near_inside = ndimage.binary_dilation(inside, np.ones((3, 3, 3), bool))
            solid = inside | (surface & ~near_inside)

    if cut is not None:  # damage: remove cells before the shell is worked out, so a cut shows its inside
        removed = cut(shape)
        solid &= ~removed
        surface &= ~removed

    padded = np.pad(solid, 1)
    inner = ndimage.binary_erosion(padded, ndimage.generate_binary_structure(3, 1), iterations=thickness)[1:-1, 1:-1, 1:-1]
    shell = solid & ~inner
    kind = np.zeros(shape, np.uint8)
    kind[shell] = SHELL
    if mode == "solid":
        kind[inner] = INTERIOR
    elif mode != "shell":
        raise ValueError("mode must be shell or solid")

    # shell cells that no surface sample landed in take the nearest sampled cell's colour
    if surface.any():
        need = shell & ~surface
        if need.any():
            _, idx = ndimage.distance_transform_edt(~surface, return_indices=True)
            colour[need] = colour[idx[0][need], idx[1][need], idx[2][need]]
    return Voxels(kind, colour, float(scale), lo, int(surface.sum()), vm)


def _translate(v) -> np.ndarray:
    m = np.eye(4)
    m[:3, 3] = v
    return m
