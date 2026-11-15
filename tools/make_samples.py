#!/usr/bin/env python3
"""Build synthetic test models into samples/synthetic/ (they are small and made in code, so they are not committed).

  cube.glb        a textured cube, a different colour per face (checks UV orientation and the axes)
  cube.obj        the same cube as OBJ + MTL + PNG
  sphere.glb      a UV sphere with a gradient texture
  rainbow.glb     a vertex-coloured torus
  part.stl        an STL (no colour)
  blob300k.glb    a lumpy, textured ~330k-triangle blob, about what an AI model generator exports (for timing)
"""
import sys
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image

OUT = Path(__file__).resolve().parent.parent / "samples" / "synthetic"

# face -> colour; the cube's texture is a 3 x 2 atlas, each cell one face, with a white mark in its top-left corner
FACE_COLOURS = {
    "+x": (200, 40, 40),    # east: red
    "-x": (40, 160, 60),    # west: green
    "+y": (240, 210, 40),   # up: yellow
    "-y": (60, 60, 60),     # down: dark grey
    "+z": (40, 80, 200),    # south (the front): blue
    "-z": (230, 120, 30),   # north: orange
}
CELLS = ["+x", "-x", "+y", "-y", "+z", "-z"]


def cube_mesh() -> trimesh.Trimesh:
    tex = Image.new("RGB", (384, 256))
    px = tex.load()
    for i, face in enumerate(CELLS):
        cx, cy = (i % 3) * 128, (i // 3) * 128
        for y in range(128):
            for x in range(128):
                mark = x < 24 and y < 24
                px[cx + x, cy + y] = (255, 255, 255) if mark else FACE_COLOURS[face]
    verts, uvs, faces = [], [], []
    axes = {"+x": (0, 1), "-x": (0, -1), "+y": (1, 1), "-y": (1, -1), "+z": (2, 1), "-z": (2, -1)}
    for i, face in enumerate(CELLS):
        a, sgn = axes[face]
        u_ax, v_ax = [k for k in range(3) if k != a]
        corners = []
        for du, dv in [(0, 0), (1, 0), (1, 1), (0, 1)]:
            p = np.zeros(3)
            p[a] = 1.0 if sgn > 0 else 0.0
            p[u_ax], p[v_ax] = du, dv
            corners.append(p)
        cx, cy = (i % 3) / 3, (i // 3) / 2
        # texture v runs up from the image's bottom (OBJ convention, which trimesh uses for every format)
        cell_uv = [(cx + 0.02 + du * 0.293, 1 - (cy + 0.02 + (1 - dv) * 0.46)) for du, dv in
                   [(0, 0), (1, 0), (1, 1), (0, 1)]]
        base = len(verts)
        verts += corners
        uvs += cell_uv
        quad = [[0, 1, 2], [0, 2, 3]]
        n = np.cross(corners[1] - corners[0], corners[2] - corners[0])
        if np.dot(n, np.eye(3)[a] * sgn) < 0:
            quad = [[0, 2, 1], [0, 3, 2]]
        faces += [[base + q for q in t] for t in quad]
    mat = trimesh.visual.material.PBRMaterial(baseColorTexture=tex)
    vis = trimesh.visual.TextureVisuals(uv=np.array(uvs), material=mat)
    return trimesh.Trimesh(np.array(verts) - 0.5, np.array(faces), visual=vis, process=False)


def sphere_mesh() -> trimesh.Trimesh:
    rows, cols = 48, 96
    th = np.linspace(0, np.pi, rows + 1)
    ph = np.linspace(0, 2 * np.pi, cols + 1)
    T, P = np.meshgrid(th, ph, indexing="ij")
    verts = np.stack([np.sin(T) * np.cos(P), np.cos(T), np.sin(T) * np.sin(P)], -1).reshape(-1, 3)
    uv = np.stack([P / (2 * np.pi), 1 - T / np.pi], -1).reshape(-1, 2)
    f = []
    for r in range(rows):
        for c in range(cols):
            a, b = r * (cols + 1) + c, r * (cols + 1) + c + 1
            d, e = a + cols + 1, b + cols + 1
            f += [[a, b, e], [a, e, d]]
    # gradient: hue along u, dark to light along v
    u = np.linspace(0, 1, 512)[None, :]
    v = np.linspace(1, 0, 256)[:, None]
    hue = np.stack([0.5 + 0.5 * np.cos(2 * np.pi * (u + k / 3)) for k in range(3)], -1)
    img = (hue * (0.25 + 0.75 * v[..., None]) * 255).astype(np.uint8)
    mat = trimesh.visual.material.PBRMaterial(baseColorTexture=Image.fromarray(img))
    m = trimesh.Trimesh(verts, np.array(f), visual=trimesh.visual.TextureVisuals(uv=uv, material=mat), process=False)
    m.fix_normals()
    return m


def rainbow_torus() -> trimesh.Trimesh:
    m = trimesh.creation.torus(major_radius=1.0, minor_radius=0.4, major_sections=96, minor_sections=48)
    ang = (np.arctan2(m.vertices[:, 2], m.vertices[:, 0]) / (2 * np.pi)) % 1.0
    rgb = np.stack([0.5 + 0.5 * np.cos(2 * np.pi * (ang + k / 3)) for k in range(3)], -1)
    m.visual = trimesh.visual.ColorVisuals(m, vertex_colors=(np.column_stack([rgb, np.ones(len(rgb))]) * 255).astype(np.uint8))
    return m


def blob() -> trimesh.Trimesh:
    m = trimesh.creation.icosphere(subdivisions=7)  # 327,680 triangles
    rng = np.random.default_rng(3)
    v = m.vertices
    bump = np.zeros(len(v))
    for _ in range(12):
        d = rng.normal(size=3)
        d /= np.linalg.norm(d)
        bump += rng.uniform(0.05, 0.18) * np.exp(-((v @ d - 1) ** 2) / rng.uniform(0.05, 0.3))
    m.vertices = v * (1 + bump)[:, None] * np.array([1.0, 1.4, 0.9])
    uv = np.column_stack([(np.arctan2(v[:, 2], v[:, 0]) / (2 * np.pi)) % 1.0, 0.5 + np.arcsin(np.clip(v[:, 1], -1, 1)) / np.pi])
    noise = rng.random((64, 64))
    img = Image.fromarray((noise * 255).astype(np.uint8)).resize((2048, 2048), Image.BICUBIC)
    n = np.asarray(img, np.float32)[..., None] / 255
    base = np.array([150, 90, 60], np.float32) * (1 - n) + np.array([90, 140, 80], np.float32) * n
    tex = Image.fromarray(base.astype(np.uint8))
    m.visual = trimesh.visual.TextureVisuals(uv=uv, material=trimesh.visual.material.PBRMaterial(baseColorTexture=tex))
    return m


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cube = cube_mesh()
    cube.export(OUT / "cube.glb")
    obj, files = trimesh.exchange.obj.export_obj(cube, include_texture=True, return_texture=True, mtl_name="cube.mtl")
    (OUT / "cube.obj").write_text(obj)
    for name, data in files.items():
        (OUT / name).write_bytes(data)
    sphere_mesh().export(OUT / "sphere.glb")
    rainbow_torus().export(OUT / "rainbow.glb")
    trimesh.creation.capsule(height=2.0, radius=0.6).export(OUT / "part.stl")
    if "--no-blob" not in sys.argv:
        blob().export(OUT / "blob300k.glb")
    print("wrote", ", ".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    main()
