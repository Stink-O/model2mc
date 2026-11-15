"""Seeded damage: remove ragged spherical chunks from a voxel model.

Everything here is integer hashing plus a few float operations, so another program (a shader, a Rust mod) can run
the same function and cut the same chunks. The exact definition is in docs/damage.md; keep the two in step.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

U32 = np.uint32
MASK = 0xFFFFFFFF


@dataclass(frozen=True)
class Spec:
    cuts: int = 3  # number of chunks
    size: float = 0.2  # mean chunk radius, as a fraction of the model's height
    rough: float = 0.5  # how ragged the edge is, 0 (smooth balls) to 1
    ymin: float = 0.0  # chunk centres are placed between these fractions of the height
    ymax: float = 1.0

    @staticmethod
    def parse(text: str | None) -> "Spec":
        """`cuts=4,size=0.25,rough=0.6,ymin=0.4,ymax=0.9` (every key optional)."""
        kw = {}
        for part in (text or "").split(","):
            part = part.strip()
            if not part:
                continue
            k, _, v = part.partition("=")
            k = k.strip()
            if k not in Spec.__dataclass_fields__ or not v:
                raise ValueError(f"--damage: unknown or empty setting {part!r} "
                                 f"(use cuts, size, rough, ymin, ymax)")
            try:
                kw[k] = int(v) if k == "cuts" else float(v)
            except ValueError:
                raise ValueError(f"--damage: {k} needs a number, got {v!r}")
        s = Spec(**kw)
        if not 0 <= s.cuts <= 64:
            raise ValueError("--damage: cuts must be 0 to 64")
        if not 0 < s.size <= 1 or not 0 <= s.rough <= 1 or not 0 <= s.ymin <= s.ymax <= 1:
            raise ValueError("--damage: size is 0-1 (above 0), rough 0-1, and 0 <= ymin <= ymax <= 1")
        return s


def hash3(seed, ix, iy, iz):
    """32-bit hash of a seed and three integers (arrays or scalars) -> uint32. Wang/lowbias32 finaliser."""
    with np.errstate(over="ignore"):
        x = (np.asarray(seed, np.int64) & MASK).astype(U32)
        x = x ^ (np.asarray(ix, np.int64) & MASK).astype(U32) * U32(0x8DA6B343)
        x = x ^ (np.asarray(iy, np.int64) & MASK).astype(U32) * U32(0xD8163841)
        x = x ^ (np.asarray(iz, np.int64) & MASK).astype(U32) * U32(0xCB1AB31F)
        x = x ^ (x >> U32(16))
        x = x * U32(0x7FEB352D)
        x = x ^ (x >> U32(15))
        x = x * U32(0x846CA68B)
        x = x ^ (x >> U32(16))
    return x


def unit(seed, ix, iy, iz):
    """hash3 as a float in [0, 1)."""
    return hash3(seed, ix, iy, iz).astype(np.float64) / 4294967296.0


def value_noise(seed, p):
    """3D value noise at points p (n, 3), in [-1, 1]: smoothstep-blended hashes at the integer lattice."""
    f = np.floor(p)
    t = p - f
    t = t * t * (3 - 2 * t)
    i = f.astype(np.int64)
    out = 0.0
    for dx in (0, 1):
        wx = t[:, 0] if dx else 1 - t[:, 0]
        for dy in (0, 1):
            wy = t[:, 1] if dy else 1 - t[:, 1]
            for dz in (0, 1):
                wz = t[:, 2] if dz else 1 - t[:, 2]
                out = out + wx * wy * wz * unit(seed, i[:, 0] + dx, i[:, 1] + dy, i[:, 2] + dz)
    return out * 2 - 1


def regions(shape, seed: int, spec: Spec):
    """The chunks as a list of (centre (3,), radius), in blocks, for a grid of `shape` = (X, Y, Z)."""
    X, Y, Z = shape
    out = []
    for i in range(spec.cuts):
        r = [float(unit(seed, i, k, 7001)) for k in range(5)]
        centre = np.array([(0.1 + 0.8 * r[0]) * X, (spec.ymin + (spec.ymax - spec.ymin) * r[1]) * Y,
                           (0.1 + 0.8 * r[2]) * Z])
        radius = spec.size * Y * (0.6 + 0.8 * r[3])
        out.append((centre, radius))
    return out


def cut_mask(shape, seed: int, spec: Spec) -> np.ndarray:
    """Boolean (X, Y, Z) array: True where a block is removed. Cells are tested at their centres."""
    X, Y, Z = shape
    mask = np.zeros(shape, bool)
    if spec.cuts == 0:
        return mask
    grain = max(Y / 16.0, 1.0)  # size of the lattice cells of the edge noise, in blocks
    for i, (c, r) in enumerate(regions(shape, seed, spec)):
        reach = r * (1 + spec.rough * 0.75)
        lo = np.maximum(np.floor(c - reach).astype(int), 0)
        hi = np.minimum(np.ceil(c + reach).astype(int), [X, Y, Z])
        if (hi <= lo).any():
            continue
        g = np.stack(np.meshgrid(*[np.arange(l, h) for l, h in zip(lo, hi)], indexing="ij"), -1).reshape(-1, 3) + 0.5
        p = g / grain
        n = 0.65 * value_noise(seed * 31 + i, p + 17.0) + 0.35 * value_noise(seed * 31 + i + 1000, p * 2.0 + 53.0)
        d = np.linalg.norm(g - c, axis=1)
        hit = d < r * (1 + spec.rough * 0.75 * n)
        sub = mask[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]]
        sub |= hit.reshape(sub.shape)
    return mask
