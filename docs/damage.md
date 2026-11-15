# Seeded damage

`--damage-seed N` and `--damage SPEC` remove a few ragged chunks from the model before the blocks are worked out, so a
model converts already broken. The function depends only on the seed, the spec and the size of the voxel grid, and
uses nothing but integer hashing and a few float operations, so a shader can run the same cuts on the display mesh.
The Python is `model2mc/damage.py`; this page is the definition.

```
model2mc giant.glb --height 50 --damage-seed 7 --damage cuts=4,size=0.22,rough=0.6,ymin=0.3,ymax=0.95
```

The cut happens in solid space, before the shell is taken, so in `shell` mode a cut shows the hollow inside as a
shell too. Exposed cut faces take the colour of the nearest surface block (the game paints the glowing edge itself).

## Spec

`cuts` (default 3, 0 to 64), `size` (default 0.2, mean radius as a fraction of the grid height), `rough` (default 0.5,
0 to 1), `ymin` and `ymax` (default 0 and 1, the band of the height where chunk centres go). `--damage` with no seed
uses seed 0.

## Definition

Let the grid be `X, Y, Z` blocks (the `size` in the JSON dump; the display mesh header carries it too). All
arithmetic in u32 wraps.

**hash3(seed, ix, iy, iz)** (u32): take each integer as its low 32 bits (two's complement),
```
x = seed ^ (ix * 0x8DA6B343) ^ (iy * 0xD8163841) ^ (iz * 0xCB1AB31F)
x ^= x >> 16;  x *= 0x7FEB352D;  x ^= x >> 15;  x *= 0x846CA68B;  x ^= x >> 16
```
**unit(...)** = `hash3(...) / 2^32`, in [0, 1).

**Chunks.** For `i` in `0..cuts-1`, with `r[k] = unit(seed, i, k, 7001)` for k = 0..3:
```
centre = ( (0.1 + 0.8 r0) X,  (ymin + (ymax-ymin) r1) Y,  (0.1 + 0.8 r2) Z )
radius = size * Y * (0.6 + 0.8 r3)
```
**Value noise** `vn(s, p)` at a point `p`: `f = floor(p)`, `t = p - f`, `t = t*t*(3 - 2t)` per axis, then the trilinear
blend of `unit(s, f.x+dx, f.y+dy, f.z+dz)` over the eight corners (dx, dy, dz in 0/1) using weights from `t`; result
`2*blend - 1`, in [-1, 1].

**Cut test** for a block with integer coordinates `(bx, by, bz)`, tested at its centre `g = (bx+0.5, by+0.5, bz+0.5)`.
For each chunk `i`, with `grain = max(Y/16, 1)`, `p = g / grain`, and `s = seed*31 + i` (taken mod 2^32):
```
n = 0.65 * vn(s, p + 17) + 0.35 * vn(s + 1000, 2p + 53)
removed if  |g - centre| < radius * (1 + rough * 0.75 * n)
```
A block is removed if any chunk removes it. Chunks reach at most `radius * (1 + 0.75 rough)` from the centre, so only
that box needs testing.

Python does this in float64; a shader in f32 can differ for a block whose centre sits within a rounding error of the
edge. That is a few blocks at most and not visible. To check a port, these values come from the tests:
`hash3(1,0,0,0) = 1753845952`, `hash3(12345,1,-2,3) = 2801712057`, and the default spec with seed 5 on a 30x60x30 grid
removes 12,113 blocks.

For the shader to cut the display mesh the same way, work in block coordinates (the mesh is already in them) and use
the grid size from the header. The mesh is not cut by the converter; only the voxels are.
