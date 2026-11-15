"""Display mesh: a decimated, colour-baked copy of the model for a game shader to ray-trace.

The mesh is in the same frame as the voxel grid (blocks, origin at the grid's corner, x east, y up, z south), so
its height in blocks is the voxel height. See docs/display-mesh.md for the file layout.

Decimation is quadric edge collapse when the optional `fast-simplification` package is installed
(the `[display]` extra): edges are collapsed one at a time, cheapest first, so the surface stays closed and
keeps its shape, and the colour, glow and normal of each vertex left are gathered from the source surface about it.
Without the package it is vertex clustering with quadric-error vertex placement: the model is cut into a grid of
cells, every vertex in a cell becomes one vertex, and the cell size is searched until the triangle count is just
under the budget. Clustering tears thin parts (open edges, flaps); prefer the edge collapse for a mesh that is cut up
or animated.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .colour import linear_to_srgb
from .mesh import Model

MAGIC = b"M2MD"
VERSION = 3
VERTEX_WORDS = 5
GLOW_SAMPLES = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [.5, .5, 0], [0, .5, .5], [.5, 0, .5], [1 / 3, 1 / 3, 1 / 3]])
HEADER_WORDS = 20
UP_Y = 1


@dataclass
class DisplayMesh:
    verts: np.ndarray  # (v, 3) float32, blocks
    rgb: np.ndarray  # (v, 3) uint8, sRGB
    tris: np.ndarray  # (t, 3) uint32 vertex indices
    grid: tuple[int, int, int]  # voxel grid size the mesh matches
    glow: np.ndarray | None = None  # (v,) uint8: how much greener than stone the greenest sample near the vertex was
    normals: np.ndarray | None = None  # (v, 3) float32 unit normals: the source's surface normal about the vertex

    @property
    def bounds(self) -> np.ndarray:
        return np.stack([self.verts.min(0), self.verts.max(0)])


def _corner_data(model: Model):
    """Flat corner arrays: positions (n,3), linear colour (n,3) sampled at each corner, triangle count per part."""
    pos, col, glow = [], [], []
    for p in model.parts:
        n = len(p.tris)
        tri_idx = np.repeat(np.arange(n), 3)
        bary = np.tile(np.eye(3), (n, 1))
        c = p.colours(tri_idx, bary)[:, :3]
        pos.append(p.tris.reshape(-1, 3))
        col.append(c)
        # glow: the greenest of seven samples over each triangle (corners, edge middles, middle), so a thin crack the
        # corners miss still counts; every corner of the triangle carries it
        gt = np.zeros(n, np.float32)
        for b in GLOW_SAMPLES:
            s7 = linear_to_srgb(np.clip(p.colours(np.arange(n), np.tile(b, (n, 1)))[:, :3], 0, 1)) * 255
            gt = np.maximum(gt, s7[:, 1] - np.maximum(s7[:, 0], s7[:, 2]))
        glow.append(np.repeat(np.clip(gt * 3.0, 0, 255), 3))
    return np.concatenate(pos), np.concatenate(col), np.concatenate(glow)


def _weld(pos: np.ndarray, col: np.ndarray, glow: np.ndarray):
    """Merge corners at the same place; the colour is the mean of the corners merged. Returns verts, colours,
    and per-corner vertex index."""
    q = np.round(pos * 1e4).astype(np.int64)
    _, first, inv = np.unique(q, axis=0, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    n = len(first)
    cnt = np.bincount(inv, minlength=n).astype(np.float64)
    c = np.stack([np.bincount(inv, col[:, k], n) for k in range(3)], 1) / cnt[:, None]
    g = np.zeros(n, np.float32)
    np.maximum.at(g, inv, glow)
    return pos[first], c, g, inv


def _cluster(verts, colour, glow, faces, cell, lo):
    """One clustering pass at cell size `cell`. Returns (new verts, colours, faces)."""
    g = np.floor((verts - lo) / cell).astype(np.int64)
    dims = g.max(0) + 1
    key = (g[:, 0] * dims[1] + g[:, 1]) * dims[2] + g[:, 2]
    uniq, cl = np.unique(key, return_inverse=True)
    cl = cl.reshape(-1)
    m = len(uniq)

    # quadric per cluster from the planes of the triangles at its corners
    a, b, c = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    nrm = np.cross(b - a, c - a)
    area2 = np.linalg.norm(nrm, axis=1)
    ok = area2 > 1e-12
    nrm[ok] /= area2[ok, None]
    nrm[~ok] = 0
    d = -(nrm * a).sum(1)
    plane = np.concatenate([nrm, d[:, None]], 1)  # (f, 4)
    q = plane[:, :, None] * plane[:, None, :] * area2[:, None, None]  # (f, 4, 4), area weighted
    Q = np.zeros((m, 4, 4))
    for k in range(3):
        np.add.at(Q, cl[faces[:, k]], q)

    cnt = np.bincount(cl, minlength=m).astype(np.float64)
    mean = np.stack([np.bincount(cl, verts[:, k], m) for k in range(3)], 1) / cnt[:, None]
    rgb = np.stack([np.bincount(cl, colour[:, k], m) for k in range(3)], 1) / cnt[:, None]

    gl = np.zeros(m, np.float32)
    np.maximum.at(gl, cl, glow)

    # the source surface's normal about each cluster: its faces' normals, area weighted (every face counts once a cluster)
    fc = cl[faces]
    wn = nrm * area2[:, None]
    nsum = np.zeros((m, 3))
    asum = np.zeros(m)
    for k in range(3):
        first = np.ones(len(faces), bool)
        for j in range(k):
            first &= fc[:, k] != fc[:, j]
        np.add.at(nsum, fc[first, k], wn[first])
        np.add.at(asum, fc[first, k], area2[first])

    # minimise v'Qv, pulled gently toward the cluster mean so flat clusters stay put
    A = Q[:, :3, :3].copy()
    scale = np.trace(A, axis1=1, axis2=2) / 3 + 1e-12
    reg = 1e-2 * scale
    A += reg[:, None, None] * np.eye(3)
    rhs = -Q[:, :3, 3] + reg[:, None] * mean
    pos = np.linalg.solve(A, rhs[:, :, None])[:, :, 0]
    # keep the result inside its own cell (grown a little), so a bad solve cannot spike
    cell_lo = lo + np.stack([(uniq // (dims[2] * dims[1])), (uniq // dims[2]) % dims[1], uniq % dims[2]], 1) * cell
    pos = np.clip(pos, cell_lo - 0.25 * cell, cell_lo + 1.25 * cell)

    nf = cl[faces]
    keep = (nf[:, 0] != nf[:, 1]) & (nf[:, 1] != nf[:, 2]) & (nf[:, 0] != nf[:, 2])
    nf = nf[keep]
    # drop duplicate triangles (same three clusters, either winding is kept once, first one wins)
    s = np.sort(nf, axis=1)
    _, idx = np.unique(s, axis=0, return_index=True)
    nf = nf[np.sort(idx)]
    return pos, rgb, nf, gl, _normals(pos, nf, nsum, asum)


def _normals(pos, faces, nsum=None, asum=None):
    """Unit vertex normals: the source's summed normal where it is a clear direction (not a thin part's two sides
    cancelling), else the area-weighted normal of the mesh's own faces at the vertex."""
    a, b, c = pos[faces[:, 0]], pos[faces[:, 1]], pos[faces[:, 2]]
    fn = np.cross(b - a, c - a)
    own = np.zeros((len(pos), 3))
    for k in range(3):
        np.add.at(own, faces[:, k], fn)
    ln = np.linalg.norm(own, axis=1, keepdims=True)
    own = np.where(ln > 1e-12, own / np.maximum(ln, 1e-12), np.array([0.0, 1.0, 0.0]))
    if nsum is None:
        return own
    ls = np.linalg.norm(nsum, axis=1)
    clear = (ls > 0.35 * np.maximum(asum, 1e-12)) & ((nsum * own).sum(1) > 0)
    return np.where(clear[:, None], nsum / np.maximum(ls, 1e-12)[:, None], own)


def bad_edges(faces: np.ndarray) -> int:
    """How many edges of the mesh are not shared by exactly two triangles (0 for a closed, clean surface)."""
    e = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    _, cnt = np.unique(e, axis=0, return_counts=True)
    return int((cnt != 2).sum())


def _collapse(verts, colour, glow, faces, budget):
    """Quadric edge collapse to `budget` triangles, or None without the `fast-simplification` package. The gentlest
    setting that reaches the budget with the fewest bad edges wins. Each vertex left takes the mean colour, the
    brightest glow and the summed normal of the source vertices within half its edges' length of it."""
    try:
        import fast_simplification
        from scipy.spatial import cKDTree
    except ImportError:
        return None
    best = None
    for agg in (3, 5, 7):
        v, f = fast_simplification.simplify(verts.astype(np.float32), faces.astype(np.int32), target_count=budget, agg=agg)
        v, f = np.asarray(v, np.float64), np.asarray(f, np.int64)
        f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])]
        if len(f) == 0 or len(f) > budget:
            continue
        bad = bad_edges(f)
        if best is None or bad < best[0]:
            best = (bad, v, f)
        if bad == 0:
            break
    if best is None:
        return None
    _, v, f = best
    # each new vertex's reach: half the mean length of its edges
    e = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    ln = np.linalg.norm(v[e[:, 0]] - v[e[:, 1]], axis=1)
    reach = (np.bincount(e.reshape(-1), np.repeat(ln, 2), len(v)) / np.maximum(np.bincount(e.reshape(-1), minlength=len(v)), 1)) * 0.5
    tree = cKDTree(verts)
    near = tree.query_ball_point(v, np.maximum(reach, 1e-9))
    nearest = tree.query(v)[1]
    cnt = np.array([len(x) for x in near])
    owner = np.repeat(np.arange(len(v)), np.maximum(cnt, 1))
    src = np.concatenate([np.asarray(x, np.int64) if len(x) else np.array([nearest[i]], np.int64) for i, x in enumerate(near)])
    m = len(v)
    k = np.bincount(owner, minlength=m).astype(np.float64)
    rgb = np.stack([np.bincount(owner, colour[src, c], m) for c in range(3)], 1) / k[:, None]
    gl = np.zeros(m, np.float32)
    np.maximum.at(gl, owner, glow[src])
    # the source surface's normal about each vertex: its source vertices' area-weighted normals
    a, b, c = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    fn = np.cross(b - a, c - a)
    fa = np.linalg.norm(fn, axis=1)
    vn = np.zeros((len(verts), 3))
    va = np.zeros(len(verts))
    for j in range(3):
        np.add.at(vn, faces[:, j], fn)
        np.add.at(va, faces[:, j], fa)
    nsum = np.stack([np.bincount(owner, vn[src, c], m) for c in range(3)], 1)
    asum = np.bincount(owner, va[src], m)
    return v, rgb, f, gl, _normals(v, f, nsum, asum)


def build(model: Model, grid: tuple[int, int, int], budget: int = 8000) -> DisplayMesh:
    """Decimate `model` (already in voxel space, in blocks) to at most `budget` triangles."""
    pos, col, glow = _corner_data(model)
    verts, colour, glow, inv = _weld(pos, col, glow)
    faces = inv.reshape(-1, 3)
    faces = faces[(faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])]
    lo, hi = verts.min(0), verts.max(0)
    collapsed = _collapse(verts, colour, glow, faces, budget) if len(faces) > budget else None
    if len(faces) <= budget:
        out_v, out_c, out_f, out_g, out_n = verts, colour, faces, glow, _normals(verts, faces)
    elif collapsed is not None:
        out_v, out_c, out_f, out_g, out_n = collapsed
    else:
        # largest cell count whose result fits: search the cell size
        top = float((hi - lo).max())
        a, b = top / 4096, top  # a: fine (too many tris), b: coarse (few)
        best = None
        for _ in range(24):
            mid = (a * b) ** 0.5
            r = _cluster(verts, colour, glow, faces, mid, lo)
            if len(r[2]) <= budget:
                best, b = r, mid
            else:
                a = mid
            if best is not None and len(best[2]) >= 0.97 * budget:
                break
        if best is None:
            best = _cluster(verts, colour, glow, faces, b, lo)
        out_v, out_c, out_f, out_g, out_n = best
    # drop vertices nothing uses
    used = np.zeros(len(out_v), bool)
    used[out_f.reshape(-1)] = True
    remap = np.cumsum(used) - 1
    out_v, out_c, out_f, out_g, out_n = out_v[used], out_c[used], remap[out_f], out_g[used], out_n[used]
    if len(out_v) > 65536:
        raise ValueError("display mesh has more than 65,536 vertices; lower --display-tris")
    srgb = (linear_to_srgb(np.clip(out_c, 0, 1)) * 255).round().astype(np.uint8)
    return DisplayMesh(out_v.astype(np.float32), srgb, out_f.astype(np.uint32), tuple(int(x) for x in grid),
                       np.clip(out_g, 0, 255).round().astype(np.uint8), out_n.astype(np.float32))


def to_bytes(m: DisplayMesh) -> bytes:
    v, t = len(m.verts), len(m.tris)
    lo, hi = m.bounds
    header = struct.pack("<4sIII3f3f3IIII", MAGIC, VERSION, v, t, *lo, *hi, *m.grid, UP_Y, HEADER_WORDS, VERTEX_WORDS)
    header += b"\0" * (HEADER_WORDS * 4 - len(header))
    vert = np.zeros((v, VERTEX_WORDS), np.uint32)
    n = m.normals if m.normals is not None else np.tile(np.array([0.0, 1.0, 0.0], np.float32), (v, 1))
    q = np.rint(np.clip(n, -1, 1) * 511.0 + 511.0).astype(np.uint32)
    vert[:, 4] = q[:, 0] | q[:, 1] << 10 | q[:, 2] << 20
    vert[:, :3] = m.verts.view(np.uint32)
    vert[:, 3] = (m.rgb[:, 0].astype(np.uint32) | m.rgb[:, 1].astype(np.uint32) << 8
                  | m.rgb[:, 2].astype(np.uint32) << 16 | (m.glow if m.glow is not None else np.zeros(v, np.uint8)).astype(np.uint32) << 24)
    idx = m.tris.astype("<u2").reshape(-1)
    if len(idx) % 2:
        idx = np.append(idx, np.uint16(0))
    return header + vert.astype("<u4").tobytes() + idx.tobytes()


def write(m: DisplayMesh, path: str | Path) -> int:
    data = to_bytes(m)
    Path(path).write_bytes(data)
    return len(data)


def read(path: str | Path) -> DisplayMesh:
    """Read a display mesh back (used by the tests, and a reference for readers in other languages)."""
    b = Path(path).read_bytes()
    magic, ver, v, t, *rest = struct.unpack_from("<4sIII3f3f3IIII", b)
    if magic != MAGIC or ver not in (1, 2, VERSION):
        raise ValueError("not a model2mc display mesh")
    grid = tuple(rest[6:9])
    hw = rest[10]
    vw = rest[11] if ver >= 3 else 4
    words = np.frombuffer(b, "<u4", offset=hw * 4, count=v * vw).reshape(v, vw)
    verts = words[:, :3].copy().view(np.float32)
    rgb = np.stack([(words[:, 3] >> s) & 255 for s in (0, 8, 16)], 1).astype(np.uint8)
    off = (hw + v * vw) * 4
    tris = np.frombuffer(b, "<u2", offset=off, count=t * 3).reshape(t, 3).astype(np.uint32)
    glow = (words[:, 3] >> 24).astype(np.uint8) if ver >= 2 else None
    normals = None
    if ver >= 3:
        normals = (np.stack([(words[:, 4] >> s) & 1023 for s in (0, 10, 20)], 1).astype(np.float32) - 511.0) / 511.0
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-6)
    return DisplayMesh(verts, rgb, tris, grid, glow, normals)
