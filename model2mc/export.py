"""Writing a block grid out: Sponge schematics (.schem), vanilla structure files (.nbt) and a plain voxel dump (.json).

A block grid is `blocks` (X, Y, Z) uint16 indices into `states`, a list of block state strings whose index 0 is
"minecraft:air". Axes are Minecraft's: x east, y up, z south.
"""
from __future__ import annotations

import base64
import gzip
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import __version__, nbt

AIR = "minecraft:air"


@dataclass
class BlockGrid:
    blocks: np.ndarray  # (X, Y, Z) uint16
    states: list[str]  # index 0 is air
    rgb: list[tuple[int, int, int] | None] = field(default_factory=list)  # per state, None for air
    colours: np.ndarray | None = None  # optional (X, Y, Z, 3) uint8 sampled colours

    @property
    def shape(self):
        return self.blocks.shape

    def count(self) -> int:
        return int((self.blocks != 0).sum())

    def compact(self) -> "BlockGrid":
        """Drop palette entries no block uses (air stays at 0)."""
        used = np.unique(self.blocks)
        used = np.union1d([0], used)
        remap = np.zeros(len(self.states), np.uint16)
        remap[used] = np.arange(len(used), dtype=np.uint16)
        rgb = [self.rgb[i] for i in used] if self.rgb else []
        return BlockGrid(remap[self.blocks], [self.states[i] for i in used], rgb, self.colours)


def _yzx(blocks: np.ndarray) -> np.ndarray:
    """Flatten in schematic order: index = x + z * Width + y * Width * Length."""
    return np.ascontiguousarray(blocks.transpose(1, 2, 0)).reshape(-1)


def varints(values: np.ndarray) -> bytes:
    """Unsigned LEB128 varints, as Sponge BlockData uses."""
    v = values.astype(np.uint32)
    if v.size == 0:
        return b""
    nbytes = np.ones(v.shape, np.int64)
    for k in (7, 14, 21, 28):
        nbytes += v >= (1 << k)
    out = np.empty(int(nbytes.sum()), np.uint8)
    pos = np.cumsum(nbytes) - nbytes
    rest = v.copy()
    for k in range(int(nbytes.max())):
        sel = nbytes > k
        more = nbytes[sel] > k + 1
        out[pos[sel] + k] = (rest[sel] & 0x7F) | (more.astype(np.uint32) << 7)
        rest[sel] >>= 7
    return out.tobytes()


def read_varints(data: bytes, count: int) -> np.ndarray:
    out = np.empty(count, np.int64)
    i = j = 0
    while j < count:
        v = shift = 0
        while True:
            b = data[i]
            i += 1
            v |= (b & 0x7F) << shift
            if not b & 0x80:
                break
            shift += 7
        out[j] = v
        j += 1
    return out


def write_schem(grid: BlockGrid, path, data_version: int, version: int = 3, name: str = "model2mc",
                author: str = "model2mc"):
    """Sponge Schematic v2 or v3. Pasting with WorldEdit puts the model's base centre at your feet."""
    X, Y, Z = grid.shape
    if max(X, Y, Z) > 65535:
        raise ValueError("schematic dimensions are limited to 65535")
    palette = {s: nbt.Int(i) for i, s in enumerate(grid.states)}
    data = nbt.ByteArray(varints(_yzx(grid.blocks)))
    centre = [-(X // 2), 0, -(Z // 2)]
    meta = {"Name": name, "Author": author, "Date": nbt.Long(int(time.time() * 1000)),
            "Generator": f"model2mc {__version__}"}
    dims = {"Width": nbt.Short(X), "Height": nbt.Short(Y), "Length": nbt.Short(Z)}
    if version == 3:
        body = {"Version": nbt.Int(3), "DataVersion": nbt.Int(data_version), "Metadata": meta, **dims,
                "Offset": nbt.IntArray(centre),
                "Blocks": {"Palette": palette, "Data": data, "BlockEntities": []}}
        nbt.write(path, {"Schematic": body}, "")
    elif version == 2:
        meta.update({"WEOffsetX": nbt.Int(centre[0]), "WEOffsetY": nbt.Int(0), "WEOffsetZ": nbt.Int(centre[2])})
        body = {"Version": nbt.Int(2), "DataVersion": nbt.Int(data_version), "Metadata": meta, **dims,
                "Offset": nbt.IntArray([0, 0, 0]), "PaletteMax": nbt.Int(len(palette)), "Palette": palette,
                "BlockData": data, "BlockEntities": []}
        nbt.write(path, body, "Schematic")
    else:
        raise ValueError("schematic version must be 2 or 3")


def read_schem(path) -> BlockGrid:
    """Read a v2 or v3 schematic written by any tool (used to check our own output)."""
    name, root = nbt.read(path)
    s = root["Schematic"] if "Schematic" in root and isinstance(root["Schematic"], dict) else root
    X, Y, Z = s["Width"].v & 0xFFFF, s["Height"].v & 0xFFFF, s["Length"].v & 0xFFFF
    if s["Version"].v >= 3:
        pal, data = s["Blocks"]["Palette"], s["Blocks"]["Data"]
    else:
        pal, data = s["Palette"], s["BlockData"]
    states = [None] * len(pal)
    for k, v in pal.items():
        states[v.v] = k
    flat = read_varints(bytes(data), X * Y * Z)
    blocks = flat.reshape(Y, Z, X).transpose(2, 0, 1).astype(np.uint16)
    return BlockGrid(blocks, states)


def _split_state(state: str) -> dict:
    m = re.fullmatch(r"([a-z0-9_.:/-]+)(?:\[(.*)\])?", state)
    if not m:
        raise ValueError(f"bad block state {state!r}")
    out = {"Name": m.group(1)}
    if m.group(2):
        out["Properties"] = dict(kv.split("=", 1) for kv in m.group(2).split(","))
    return out


@dataclass
class Tile:
    name: str                       # structure id path, e.g. "giant_x0_y1_z0" (or the stem for a single tile)
    offset: tuple[int, int, int]    # where its corner goes, relative to the model's base centre
    size: tuple[int, int, int]
    data: bytes                     # gzipped structure NBT


def safe_name(stem: str) -> str:
    """A name Minecraft accepts in a resource location: lower case a-z 0-9 _ . -"""
    return re.sub(r"[^a-z0-9_.-]", "_", stem.lower()) or "model"


def structure_tiles(grid: BlockGrid, stem: str, data_version: int, tile: int = 48,
                    include_air: bool = False) -> list[Tile]:
    """Cut the grid into structure files of at most tile^3 (what a structure block can save and load)."""
    X, Y, Z = grid.shape
    nx, ny, nz = (-(-X // tile), -(-Y // tile), -(-Z // tile))
    stem = safe_name(stem)
    palette = [_split_state(s) for s in grid.states]
    out = []
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                sub = grid.blocks[i * tile:(i + 1) * tile, j * tile:(j + 1) * tile, k * tile:(k + 1) * tile]
                sx, sy, sz = sub.shape
                pos = np.argwhere(sub != 0) if not include_air else np.argwhere(np.ones_like(sub, bool))
                if len(pos) == 0:
                    continue
                st = sub[pos[:, 0], pos[:, 1], pos[:, 2]]
                root = {
                    "DataVersion": nbt.Int(data_version),
                    "size": [nbt.Int(sx), nbt.Int(sy), nbt.Int(sz)],
                    "palette": palette,
                    "blocks": [{"pos": [nbt.Int(int(a)), nbt.Int(int(b)), nbt.Int(int(c))], "state": nbt.Int(int(s))}
                               for (a, b, c), s in zip(pos, st)],
                    "entities": [],
                }
                single = nx * ny * nz == 1
                name = stem if single else f"{stem}_x{i}_y{j}_z{k}"
                data = gzip.compress(nbt.dumps(root, ""), compresslevel=6, mtime=0)
                out.append(Tile(name, (i * tile - X // 2, j * tile, k * tile - Z // 2), (sx, sy, sz), data))
    return out


def write_structures(grid: BlockGrid, path, data_version: int, tile: int = 48, include_air: bool = False) -> list[Path]:
    """Vanilla structure files. Structure blocks save and load at most 48x48x48, so a bigger build is cut into
    tiles named NAME_x{i}_y{j}_z{k}.nbt, with NAME_place.txt listing a /place command for each."""
    path = Path(path)
    X, Y, Z = grid.shape
    stem = safe_name(path.stem)
    # tiles and commands from an earlier run with the same name would be mixed up with these ones
    tile_re = re.compile(re.escape(stem) + r"_x\d+_y\d+_z\d+\.nbt")
    for old in path.parent.glob(f"{stem}_*"):
        if tile_re.fullmatch(old.name) or old.name == f"{stem}_place.txt":
            old.unlink()
    if path.is_file():
        path.unlink()
    tiles = structure_tiles(grid, stem, data_version, tile, include_air)
    written, cmds = [], []
    for t in tiles:
        out = path if t.name == stem else path.with_name(f"{t.name}.nbt")
        out.write_bytes(t.data)
        written.append(out)
        dx, dy, dz = t.offset
        cmds.append(f"place template minecraft:{t.name} ~{dx} ~{dy} ~{dz}")
    if len(written) > 1 or max(X, Y, Z) > tile:
        note = path.with_name(f"{stem}_place.txt")
        note.write_text(
            "# Copy the .nbt files into <world>/generated/minecraft/structures/, then run these from where the\n"
            "# model's base centre should go (or load each tile with a structure block at the offsets shown).\n"
            + "\n".join(cmds) + "\n")
        written.append(note)
    return written


def write_voxels(grid: BlockGrid, path, meta: dict | None = None):
    """A plain dump for other tools (and the Orbital Laser mod): size, palette, and one index per cell.

    blocks is base64 of little-endian u8 (palette <= 256) or u16 indices in schematic order,
    index = x + z * size[0] + y * size[0] * size[2]. colors, if present, is base64 of RGB bytes in the same order
    (the model's sampled colour before matching; 0,0,0 for air).
    """
    X, Y, Z = grid.shape
    flat = _yzx(grid.blocks)
    enc = "u8" if len(grid.states) <= 256 else "u16le"
    raw = flat.astype(np.uint8 if enc == "u8" else "<u2").tobytes()
    doc = {
        "format": "model2mc-voxels",
        "version": 1,
        "generator": f"model2mc {__version__}",
        "size": [X, Y, Z],
        "axes": "x east, y up, z south",
        "order": "x + z*size[0] + y*size[0]*size[2]",
        "palette": [{"state": s, "rgb": list(c) if c else None}
                    for s, c in zip(grid.states, grid.rgb or [None] * len(grid.states))],
        "encoding": enc,
        "count": grid.count(),
        "blocks": base64.b64encode(raw).decode("ascii"),
    }
    if grid.colours is not None:
        doc["colors"] = base64.b64encode(np.ascontiguousarray(grid.colours.transpose(1, 2, 0, 3)).tobytes()).decode()
    if meta:
        doc["meta"] = meta
    Path(path).write_text(json.dumps(doc))
