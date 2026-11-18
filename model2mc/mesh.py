"""Loading a model into flat triangle arrays with a colour for any point on any triangle.

Everything the voxeliser needs from a model is here: one array of triangles (world space, after every node's
transform) and, for each triangle, a way to colour a point on it from its barycentric coordinates: a base-colour
texture sampled at the interpolated UV, interpolated vertex colours, or the material's flat colour.

GLB / glTF is the main input. OBJ (with its MTL and texture) works through the same path. STL has no colour, so every
triangle gets one flat colour.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image

from .colour import srgb_to_linear

SUPPORTED = {".glb", ".gltf", ".obj", ".stl"}


@dataclass
class Part:
    """One mesh of the model with its own colour source."""

    tris: np.ndarray  # (n, 3, 3) float64, world space
    uv: np.ndarray | None = None  # (n, 3, 2), OBJ convention (v up)
    texture: np.ndarray | None = None  # (h, w, 4) float32, linear RGB + alpha
    vcol: np.ndarray | None = None  # (n, 3, 4) float32, linear RGB + alpha
    factor: np.ndarray = field(default_factory=lambda: np.ones(4, np.float32))  # linear RGBA

    def colours(self, tri_idx: np.ndarray, bary: np.ndarray) -> np.ndarray:
        """Linear RGBA at barycentric points `bary` (m, 3) on this part's triangles `tri_idx` (m,)."""
        out = np.broadcast_to(self.factor, (len(tri_idx), 4)).astype(np.float32)
        if self.texture is not None and self.uv is not None:
            uv = np.einsum("mk,mkj->mj", bary, self.uv[tri_idx])
            out = out * sample_texture(self.texture, uv)
        if self.vcol is not None:
            out = out * np.einsum("mk,mkj->mj", bary, self.vcol[tri_idx]).astype(np.float32)
        return out


@dataclass
class Model:
    parts: list[Part]
    name: str

    @property
    def triangle_count(self) -> int:
        return sum(len(p.tris) for p in self.parts)

    def bounds(self) -> np.ndarray:
        pts = np.concatenate([p.tris.reshape(-1, 3) for p in self.parts])
        return np.stack([pts.min(0), pts.max(0)])

    def transformed(self, matrix: np.ndarray) -> "Model":
        """A copy with every triangle moved by the 4x4 `matrix`."""
        parts = []
        for p in self.parts:
            pts = p.tris.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]
            parts.append(Part(pts.reshape(-1, 3, 3), p.uv, p.texture, p.vcol, p.factor))
        return Model(parts, self.name)


def sample_texture(tex: np.ndarray, uv: np.ndarray) -> np.ndarray:
    """Bilinear sample of `tex` (h, w, c) at `uv` (m, 2), wrapping (glTF's default REPEAT)."""
    h, w = tex.shape[:2]
    x = (uv[:, 0] % 1.0) * w - 0.5
    y = (1.0 - (uv[:, 1] % 1.0)) * h - 0.5
    x0 = np.floor(x).astype(np.int64)
    y0 = np.floor(y).astype(np.int64)
    fx = (x - x0)[:, None].astype(np.float32)
    fy = (y - y0)[:, None].astype(np.float32)
    x0w, x1w = x0 % w, (x0 + 1) % w
    y0w, y1w = y0 % h, (y0 + 1) % h
    top = tex[y0w, x0w] * (1 - fx) + tex[y0w, x1w] * fx
    bot = tex[y1w, x0w] * (1 - fx) + tex[y1w, x1w] * fx
    return top * (1 - fy) + bot * fy


def _image_to_linear(img: Image.Image) -> np.ndarray:
    arr = np.asarray(img.convert("RGBA"), dtype=np.float32) / 255.0
    arr[..., :3] = srgb_to_linear(arr[..., :3])
    return arr


def _rgba(c) -> np.ndarray:
    """A colour from trimesh (0-255 ints or 0-1 floats, sRGB) as linear RGBA."""
    a = np.asarray(c, dtype=np.float32).reshape(-1)
    if a.size == 3:
        a = np.append(a, 255.0 if a.max() > 1.0 else 1.0)
    if a.max() > 1.0 or a.dtype.kind in "iu":
        a = a / 255.0
    a[:3] = srgb_to_linear(a[:3])
    return a.astype(np.float32)


def _part_from_mesh(mesh: trimesh.Trimesh, transform: np.ndarray) -> Part:
    verts = mesh.vertices @ transform[:3, :3].T + transform[:3, 3]
    faces = np.asarray(mesh.faces)
    part = Part(tris=verts[faces])
    vis = mesh.visual
    if isinstance(vis, trimesh.visual.texture.TextureVisuals):
        mat = vis.material
        img = None
        if isinstance(mat, trimesh.visual.material.PBRMaterial):
            img = mat.baseColorTexture
            if mat.baseColorFactor is not None:
                part.factor = _rgba(mat.baseColorFactor)
        elif isinstance(mat, trimesh.visual.material.SimpleMaterial):
            img = mat.image
            if mat.diffuse is not None and img is None:
                part.factor = _rgba(mat.diffuse)
        if img is not None and vis.uv is not None and len(vis.uv) == len(mesh.vertices):
            part.texture = _image_to_linear(img)
            part.uv = np.asarray(vis.uv, dtype=np.float64)[faces][..., :2]
        # glTF COLOR_0 alongside a texture multiplies it
        col = mesh.vertex_attributes.get("color") if hasattr(mesh, "vertex_attributes") else None
        if col is not None and len(col) == len(mesh.vertices):
            vc = np.asarray(col, dtype=np.float32)
            vc = vc / 255.0 if vc.max() > 1.0 else vc
            if vc.shape[1] == 3:
                vc = np.concatenate([vc, np.ones((len(vc), 1), np.float32)], 1)
            vc[:, :3] = srgb_to_linear(vc[:, :3])
            part.vcol = vc[faces]
    elif isinstance(vis, trimesh.visual.color.ColorVisuals) and vis.defined:
        if vis.kind == "vertex":
            vc = np.asarray(vis.vertex_colors, dtype=np.float32) / 255.0
            vc[:, :3] = srgb_to_linear(vc[:, :3])
            part.vcol = vc[faces]
        elif vis.kind == "face":
            fc = np.asarray(vis.face_colors, dtype=np.float32) / 255.0
            fc[:, :3] = srgb_to_linear(fc[:, :3])
            part.vcol = np.repeat(fc[:, None, :], 3, axis=1)
    return part


def load(path: str | Path, default_colour=(200, 200, 200)) -> Model:
    """Load a GLB/glTF/OBJ/STL file. Raises ValueError for formats we don't read."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError(f"{path.name}: unsupported format {ext} (use GLB, glTF, OBJ or STL)")
    if not path.is_file():
        raise ValueError(f"{path}: no such file")
    scene = trimesh.load(str(path), force="scene", process=False)
    parts: list[Part] = []
    for node in scene.graph.nodes_geometry:
        transform, geom_name = scene.graph[node]
        geom = scene.geometry[geom_name]
        if not isinstance(geom, trimesh.Trimesh) or len(geom.faces) == 0:
            continue
        part = _part_from_mesh(geom, np.asarray(transform, dtype=np.float64))
        if part.texture is None and part.vcol is None and np.allclose(part.factor, 1.0):
            # nothing to colour it by (STL, or an untextured white material): one flat colour
            if ext == ".stl" or not isinstance(geom.visual, trimesh.visual.texture.TextureVisuals):
                part.factor = _rgba(default_colour)
        parts.append(part)
    if not parts:
        raise ValueError(f"{path.name}: no triangles found")
    return Model(parts, path.stem)


def orientation_matrix(up: str = "y", yaw: float = 0.0, pitch: float = 0.0, roll: float = 0.0,
                       mirror: bool = False) -> np.ndarray:
    """A 4x4 that takes the model's axes to Minecraft's (Y up, the model's front +Z = south).

    `up` names the model's up axis (glTF is +Y; many OBJ/STL exports are +Z). `yaw` turns it about the vertical
    afterwards, `pitch` about east-west (X) and `roll` about north-south (Z), in degrees.
    """
    m = np.eye(4)
    up = up.lower()
    sign = -1.0 if up.startswith("-") else 1.0
    axis = up.lstrip("+-")
    if axis == "z":  # Z-up, Y-forward (Blender style) -> Y-up, Z-forward... front stays front
        m[:3, :3] = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float)
    elif axis == "x":
        m[:3, :3] = np.array([[0, 1, 0], [-1, 0, 0], [0, 0, 1]], float)
    elif axis != "y":
        raise ValueError(f"--up must be x, y or z (optionally with -), not {up!r}")
    if sign < 0:
        m[:3, :3] = np.diag([1.0, -1.0, -1.0]) @ m[:3, :3]

    def rot(axis_i: int, deg: float) -> np.ndarray:
        r = np.eye(4)
        c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
        i, j = [(1, 2), (2, 0), (0, 1)][axis_i]
        r[i, i], r[i, j], r[j, i], r[j, j] = c, -s, s, c
        return r

    m = rot(2, roll) @ rot(0, pitch) @ rot(1, yaw) @ m
    if mirror:
        m = np.diag([-1.0, 1, 1, 1]) @ m
    return m
