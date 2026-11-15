"""Block colours and matching colours to blocks.

The shipped table (blocks.json) holds only numbers: each block's average top, side and bottom colour (sRGB) and how
much its texture varies. No textures are bundled. `measure()` works the same numbers out of a Minecraft client jar or
a resource pack (a .jar/.zip or a folder), which is how blocks.json was made and how `--textures` overrides it at
runtime (a resource pack that only changes some blocks keeps the shipped numbers for the rest).
"""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import numpy as np
from PIL import Image

from .blocklist import BLOCKS, GROUPS, OPT_IN
from .colour import ciede2000, linear_to_lab, linear_to_srgb, srgb_to_linear


@dataclass
class Block:
    id: str  # "white_concrete"
    props: str  # "axis=y" or ""
    group: str
    top: tuple[int, int, int]
    side: tuple[int, int, int]
    bottom: tuple[int, int, int]
    noise: float  # mean CIELAB distance of the texture's pixels from its average

    @property
    def state(self) -> str:
        """Full block state string, e.g. minecraft:hay_block[axis=y]."""
        return f"minecraft:{self.id}" + (f"[{self.props}]" if self.props else "")

    def match_colour_linear(self) -> np.ndarray:
        """The colour the block reads as from a few blocks away: mostly its sides, some of its top."""
        side = srgb_to_linear(np.array(self.side) / 255.0)
        top = srgb_to_linear(np.array(self.top) / 255.0)
        return 0.7 * side + 0.3 * top


# ---------------------------------------------------------------------------------------------------- measuring

class _Source:
    """Read files from a jar/zip or a folder (a resource pack or an extracted jar)."""

    def __init__(self, path: Path):
        self.path = path
        self.zip = zipfile.ZipFile(path) if path.is_file() else None

    def read(self, rel: str) -> bytes | None:
        if self.zip is not None:
            try:
                return self.zip.read(rel)
            except KeyError:
                return None
        p = self.path / rel
        return p.read_bytes() if p.is_file() else None


def _strip(ref: str) -> str:
    return ref.split(":", 1)[1] if ":" in ref else ref


def _resolve_model(src: _Source, model_id: str):
    """Merge a block model with its parents: (textures, elements)."""
    textures: dict[str, str] = {}
    elements = None
    seen = 0
    mid = _strip(model_id)
    while mid and seen < 16:
        raw = src.read(f"assets/minecraft/models/{mid}.json")
        if raw is None:
            break
        m = json.loads(raw)
        for k, v in m.get("textures", {}).items():
            textures.setdefault(k, v)
        if elements is None and "elements" in m:
            elements = m["elements"]
        mid = _strip(m["parent"]) if "parent" in m else None
        seen += 1
    return textures, elements


def _texture_ref(textures: dict[str, str], ref: str) -> str | None:
    for _ in range(8):
        if not ref.startswith("#"):
            return _strip(ref)
        ref = textures.get(ref[1:], "")
        if not ref:
            return None
    return None


def _texture_stats(src: _Source, tex: str):
    raw = src.read(f"assets/minecraft/textures/{tex}.png")
    if raw is None:
        return None
    img = Image.open(io.BytesIO(raw)).convert("RGBA")
    a = np.asarray(img, dtype=np.float32) / 255.0
    h, w = a.shape[:2]
    if h > w and h % w == 0:  # animated strip: first frame
        a = a[:w]
    alpha = a[..., 3].reshape(-1)
    lin = srgb_to_linear(a[..., :3]).reshape(-1, 3)
    wt = alpha / max(alpha.sum(), 1e-9)
    mean = (lin * wt[:, None]).sum(0)
    lab = linear_to_lab(lin)
    noise = float((np.linalg.norm(lab - linear_to_lab(mean), axis=1) * wt).sum())
    return mean, noise


def _measure_block(src: _Source, block_id: str, props: str):
    raw = src.read(f"assets/minecraft/blockstates/{block_id}.json")
    if raw is None:
        return None
    bs = json.loads(raw)
    model = None
    if "variants" in bs:
        variants = bs["variants"]
        v = variants.get(props) or variants.get("") or next(iter(variants.values()))
        model = (v[0] if isinstance(v, list) else v)["model"]
    elif "multipart" in bs:
        v = bs["multipart"][0]["apply"]
        model = (v[0] if isinstance(v, list) else v)["model"]
    if model is None:
        return None
    textures, elements = _resolve_model(src, model)
    faces: dict[str, str] = {}
    if elements:
        for name, face in elements[0].get("faces", {}).items():
            t = _texture_ref(textures, face.get("texture", ""))
            if t:
                faces[name] = t
    if not faces:
        t = _texture_ref(textures, "#all") or _texture_ref(textures, "#particle")
        if not t:
            return None
        faces = {f: t for f in ["up", "down", "north", "south", "east", "west"]}
    stats = {f: _texture_stats(src, t) for f, t in faces.items()}
    if any(s is None for s in stats.values()):
        return None

    def avg(names):
        got = [stats[n] for n in names if n in stats]
        return np.mean([g[0] for g in got], 0), float(np.mean([g[1] for g in got]))

    side, noise = avg(["north", "south", "east", "west"])
    top, _ = avg(["up"])
    bottom, _ = avg(["down"])

    def s8(c):
        return tuple(int(round(v)) for v in linear_to_srgb(c) * 255)

    return s8(top), s8(side), s8(bottom), round(noise, 2)


def measure(path: str | Path, base: list[Block] | None = None) -> tuple[list[Block], list[str]]:
    """Measure every listed block in a client jar / resource pack. Blocks it doesn't have keep `base`'s numbers
    (or are dropped if there's no base). Returns (blocks, names that were missing)."""
    src = _Source(Path(path))
    by_id = {b.id: b for b in base or []}
    out, missing = [], []
    for block_id, props, group in BLOCKS:
        m = _measure_block(src, block_id, props)
        if m is None:
            missing.append(block_id)
            if block_id in by_id:
                out.append(by_id[block_id])
            continue
        top, side, bottom, noise = m
        out.append(Block(block_id, props, group, top, side, bottom, noise))
    return out, missing


def block_exists(path: str | Path, block_id: str) -> bool:
    return _Source(Path(path)).read(f"assets/minecraft/blockstates/{block_id}.json") is not None


# ---------------------------------------------------------------------------------------------------- the table

def load_table(path: str | Path | None = None) -> list[Block]:
    if path is None:
        raw = resources.files("model2mc").joinpath("blocks.json").read_text()
    else:
        raw = Path(path).read_text()
    data = json.loads(raw)
    return [Block(b["id"], b.get("props", ""), b["group"], tuple(b["top"]), tuple(b["side"]), tuple(b["bottom"]),
                  b["noise"]) for b in data["blocks"]]


def save_table(blocks: list[Block], path: str | Path, source: str):
    data = {
        "about": "Average colours (sRGB 0-255) of block textures, measured by model2mc.palette.measure. "
                 "Numbers only; no textures are included.",
        "measured_from": source,
        "blocks": [{"id": b.id, "props": b.props, "group": b.group, "top": list(b.top), "side": list(b.side),
                    "bottom": list(b.bottom), "noise": b.noise} for b in blocks],
    }
    rows = ",\n".join("  " + json.dumps(b, separators=(", ", ": ")) for b in data["blocks"])
    head = json.dumps({k: v for k, v in data.items() if k != "blocks"}, indent=1)[:-2]
    Path(path).write_text(head + ',\n "blocks": [\n' + rows + "\n ]\n}\n")


def select(blocks: list[Block], groups: list[str] | None = None, include: list[str] | None = None,
           exclude: list[str] | None = None) -> list[Block]:
    """Pick the palette: by group (default every group), minus the opt-in blocks unless named, +include, -exclude."""
    include = {i.removeprefix("minecraft:") for i in include or []}
    exclude = {e.removeprefix("minecraft:") for e in exclude or []}
    groups = set(groups or GROUPS)
    bad = groups - set(GROUPS)
    if bad:
        raise ValueError(f"unknown palette group(s) {sorted(bad)}; choose from {GROUPS}")
    out = [b for b in blocks if (b.group in groups and b.id not in OPT_IN) or b.id in include]
    out = [b for b in out if b.id not in exclude]
    if not out:
        raise ValueError("the palette is empty")
    return out


# ---------------------------------------------------------------------------------------------------- matching

def _bayer3d() -> np.ndarray:
    """A 4x4x4 ordered-dither threshold cube, values in (-0.5, 0.5): the 4x4 Bayer matrix across x-z, interleaved
    over four layers of y so every value 0..63 appears once."""
    b4 = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]])
    layer = np.array([0, 2, 3, 1])
    m = b4[:, None, :] * 4 + layer[None, :, None]
    return (m + 0.5) / 64 - 0.5


class Matcher:
    def __init__(self, blocks: list[Block], noise_weight: float = 0.12):
        self.blocks = blocks
        self.lab = linear_to_lab(np.array([b.match_colour_linear() for b in blocks]))
        self.noise = np.array([b.noise for b in blocks]) * noise_weight

    def match_lab(self, lab: np.ndarray) -> np.ndarray:
        """Index of the nearest block (CIEDE2000 plus a small penalty for noisy textures) for each Lab colour."""
        q = np.round(lab * 2) / 2  # match each half-unit Lab colour once
        uniq, inv = np.unique(q.reshape(-1, 3), axis=0, return_inverse=True)
        best = np.empty(len(uniq), np.int64)
        step = max(1, 400_000 // len(self.blocks))
        for s in range(0, len(uniq), step):
            d = ciede2000(uniq[s:s + step, None, :], self.lab[None, :, :]) + self.noise[None, :]
            best[s:s + step] = d.argmin(1)
        return best[inv.reshape(-1)].reshape(lab.shape[:-1])

    def match(self, linear_rgb: np.ndarray, coords: np.ndarray | None = None, dither: float = 0.0) -> np.ndarray:
        """Block indices for linear RGB colours (m, 3); `coords` (m, 3) ints place the dither pattern."""
        lab = linear_to_lab(np.clip(linear_rgb, 0, 1))
        if dither > 0 and coords is not None:
            t = _bayer3d()[coords[:, 0] % 4, coords[:, 1] % 4, coords[:, 2] % 4]
            lab = lab + (t * dither * 12.0)[:, None] * np.array([1.0, 0.6, 0.6])
        return self.match_lab(lab)
