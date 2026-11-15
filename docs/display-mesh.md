# Display mesh format (`.m2md`, version 3)

`model2mc model.glb --height 50 --display-mesh giant.m2md [--display-tris 8000]` writes a decimated copy of the model
for a shader to ray-trace. It is small, flat and word-aligned, so it can be passed in one go as 32-bit words (for
example to a `render.set-data` call).

## Frame and scale

Positions are in **blocks**, in the same frame as the voxel output: origin at the corner of the voxel grid, `x` east,
`y` up, `z` south. The model's height in blocks is the voxel height (the mesh's `y` range is `0..H`). The up axis is
always +Y here; `--up`, `--yaw`, `--pitch`, `--roll` and `--mirror` are applied before export, so the mesh turns the
same way as the blocks. Place it at the same world offset as the schematic's grid corner and the two line up.

## Layout

Everything is little-endian, 32-bit words.

| Words | Content |
| --- | --- |
| 0 | magic, the bytes `M2MD` |
| 1 | version, `3`. Version 1 and 2 files still read: they have 4-word vertices with no normal. In version 2 the colour word's top byte is already the glow; in version 1 it is always 0xFF. |
| 2 | vertex count `V` (at most 65,536) |
| 3 | triangle count `T` |
| 4-6 | bounds min x, y, z (f32) |
| 7-9 | bounds max x, y, z (f32) |
| 10-12 | voxel grid size X, Y, Z (u32) |
| 13 | up axis, `1` = +Y |
| 14 | header size in words, `20`. Vertices start at this word. |
| 15 | words per vertex `S`, `5` |
| 16-19 | reserved, zero |
| 20 .. 20+SV | vertices, 5 words each: x, y, z as f32 bit patterns, then colour, then the normal |
| after that | indices: 3 per triangle, each a u16, two to a word (low half first). Padded with one u16 of 0 if `3T` is odd, so the section is `ceil(3T/2)` words. |

Colour word: `r | g << 8 | b << 16 | glow << 24`, sRGB 8-bit. Colour is sampled from the model's texture (and vertex
colours / material colour) at each vertex and averaged where vertices were merged. Interpolate it across a triangle
and convert to linear in the shader if you light in linear space.

`glow` marks green that glows, such as thin cracks the corner colours would average away. Each source triangle is
sampled at seven points (its corners, the middles of its edges and its middle). At each point take `G - max(R, B)` in
sRGB bytes; the triangle's value is the largest of the seven. A vertex gets 3 times the largest value of the source
triangles it stands for, clipped to 0-255. It is 0 where nothing is green.

Normal word: `nx | ny << 10 | nz << 20`, each 10 bits holding `round(n * 511 + 511)`; decode with `(q - 511) / 511` and
normalise. It is the surface normal of the source model about the vertex: the area-weighted normals of the source
faces the vertex stands for (its cell, or with edge collapse the source vertices within half its edge length). Shading
with it interpolated across a triangle shows the model's roundness and not the decimated facets. Where a thin part's
two sides cancel out it is the decimated mesh's own vertex normal.

Total size in words: `20 + 5V + ceil(3T/2)`. At the default 8,000 triangles that is roughly 4,200 vertices,
about 33,000 words, so three meshes fit well inside a 262,144-word (1 MiB) call. Triangles wind counter-clockwise seen
from outside, as in the source model.

## How it is decimated

Vertices at the same place are welded first. A model already under budget is exported as it is. No game data and no
network are involved.

With the optional `fast-simplification` package (`pip install -e '.[display]'`) the mesh is reduced by quadric edge
collapse: edges are collapsed one at a time, cheapest first, until the mesh has at most `--display-tris` triangles.
Three settings are tried, gentlest first, and the one with the fewest open edges wins, so a closed model normally stays
closed. Each vertex left takes the mean colour, the largest glow and the summed normal of the source vertices within
half its edge length. If no setting gets under the budget, the clustering below is used instead.

Without it the model is cut into a grid of cells, and each cell's vertices become one vertex placed where the cell's
face planes agree best (quadric error). The cell size is searched until the result has at most `--display-tris`
triangles (it aims for 97% or more of the budget). This needs nothing beyond numpy, but it can tear thin parts and
leave open edges, so install the extra for a mesh you will cut up or animate.

## Reading it

`model2mc.display.read()` is a short reference reader in Python.

```
V = word[2]; T = word[3]; H = word[14]; S = word[15] (4 before version 3)
vertex i : words H+S*i .. H+S*i+S-1
index k  : u16 number k of the section that starts at word H+S*V
```
