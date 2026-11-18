"""model2mc command line."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
from scipy import ndimage

from . import DEFAULT_DATA_VERSION, __version__, damage, display, export, mesh, palette, preview
from .blocklist import GROUPS
from .colour import linear_to_srgb
from .voxelize import INTERIOR, SHELL, voxelize

PROMO_URL = "https://tripo.pxf.io/xJdOKy?p.invite_code=8615S1"


MAX_SIZE = 1024       # blocks along any side; past this a build is slow to make and impractical to paste
WORLD_HEIGHT = 384    # an overworld column, -64 to 319


def _number(kind, lo=None, hi=None, lo_open=False):
    """argparse type: a number in range, with a plain message when it isn't."""
    def parse(text):
        try:
            v = kind(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{text!r} is not a {'whole ' if kind is int else ''}number") from None
        if lo is not None and (v <= lo if lo_open else v < lo) or hi is not None and v > hi:
            if hi is None:
                rng = f"above {lo}" if lo_open else f"{lo} or more"
            else:
                rng = f"{'above ' if lo_open else ''}{lo} to {hi}"
            raise argparse.ArgumentTypeError(f"{text} is out of range (use {rng})")
        return v
    return parse


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # one line and a pointer, not the whole usage block
        self.exit(2, f"model2mc: {message}\nRun model2mc --help to see the options.\n")


def build_parser() -> argparse.ArgumentParser:
    ap = _Parser(
        prog="model2mc",
        description="Turn a 3D model (GLB, glTF, OBJ, or STL in one colour) into Minecraft blocks.",
        epilog="example: model2mc dragon.glb --height 80 --out dragon.schem --preview dragon.png\n\n"
               "Set MODEL2MC_DEBUG=1 to see the full Python error "
               "if something fails.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="the model file (.glb, .gltf, .obj or .stl)")
    ap.add_argument("-o", "--out", action="append", default=[], metavar="FILE",
                    help="where to write; the extension picks the format: .schem (Sponge schematic), "
                         ".nbt (vanilla structure, cut into 48-block tiles if bigger) or .json (voxel dump). "
                         "Give it more than once for several. Default: the model's name with .schem")
    ap.add_argument("-p", "--preview", metavar="FILE.png",
                    help="also write a preview image: the model and the blocks side by side")
    size = ap.add_mutually_exclusive_group()
    size.add_argument("--height", type=_number(int, 1, MAX_SIZE), metavar="N", help="height in blocks (default 64)")
    size.add_argument("--longest", type=_number(int, 1, MAX_SIZE), metavar="N",
                      help="size of the longest side in blocks, instead of --height")
    ap.add_argument("--mode", choices=["shell", "solid"], default="shell",
                    help="shell: only the blocks that show (default); solid: filled inside too")
    ap.add_argument("--thickness", type=_number(int, 1, 64), default=1, metavar="N",
                    help="shell thickness in blocks (default 1)")
    ap.add_argument("--filler", default="auto", metavar="BLOCK",
                    help="what solid mode fills the inside with: a block id, or auto, the nearest outside block "
                         "(default auto)")
    ap.add_argument("--loose", action="store_true",
                    help="keep every block the surface touches (fatter, more blocks) instead of centres inside")
    ap.add_argument("--no-fill", action="store_true",
                    help="don't work out the inside at all (for open meshes: a single sheet, a terrain tile)")
    g = ap.add_argument_group("orientation")
    g.add_argument("--up", default="y", metavar="AXIS",
                   help="the model's up axis: y (glTF, default), z (many OBJ and STL files) or x; "
                        "-y, -z or -x if it comes out upside down")
    g.add_argument("--yaw", type=_number(float), default=0.0, metavar="DEG",
                   help="turn about the vertical (the front faces south; default 0)")
    g.add_argument("--pitch", type=_number(float), default=0.0, metavar="DEG",
                   help="tip about the east-west axis (default 0)")
    g.add_argument("--roll", type=_number(float), default=0.0, metavar="DEG",
                   help="tip about the north-south axis (default 0)")
    g.add_argument("--mirror", action="store_true", help="mirror east-west")
    g = ap.add_argument_group("blocks")
    g.add_argument("--palette", default=",".join(GROUPS), metavar="GROUPS",
                   help=f"block groups to use, comma separated: {', '.join(GROUPS)} (default all)")
    g.add_argument("--include", default="", metavar="BLOCKS",
                   help="add blocks, comma separated (e.g. gold_block,diamond_block)")
    g.add_argument("--exclude", default="", metavar="BLOCKS", help="leave blocks out, comma separated")
    g.add_argument("--textures", metavar="PATH", help="measure block colours from your own client .jar or a "
                                                      "resource pack (.zip or folder) instead of the built-in table")
    g.add_argument("--dither", type=_number(float, 0, 1), default=0.0, metavar="0-1",
                   help="ordered dithering: mixes two blocks where no single one is close (default 0, off)")
    g.add_argument("--noise-weight", type=_number(float, 0, 1), default=0.12, metavar="0-1",
                   help="how much to avoid blocks with busy textures, like cobblestone (default 0.12; 0 is off)")
    g = ap.add_argument_group("display mesh and damage")
    g.add_argument("--display-mesh", metavar="FILE", help="also write a decimated, colour-baked mesh for a shader "
                   "(see docs/display-mesh.md), in the same block units as the build")
    g.add_argument("--display-tris", type=_number(int, 100, 1_000_000), default=8000, metavar="N",
                   help="triangle budget of the display mesh (default 8000)")
    g.add_argument("--damage-seed", type=_number(int, 0), metavar="N",
                   help="convert the model already broken: remove ragged chunks picked by this seed "
                        "(same seed, same cuts; see docs/damage.md)")
    g.add_argument("--damage", metavar="SPEC", help="what to remove, e.g. cuts=4,size=0.25,rough=0.6,ymin=0.4,ymax=0.9 "
                   "(cuts; size = radius as a fraction of the height; rough 0-1; ymin and ymax = where the cuts "
                   "centre). On its own it uses seed 0")
    g = ap.add_argument_group("output")
    g.add_argument("--json", action="store_true",
                   help="print one JSON object to stdout when done: the size, the files written and hints. "
                        "On failure {\"ok\": false, \"error\": ...} and exit code 1. For scripts and AI agents")
    g.add_argument("-q", "--quiet", action="store_true",
                   help="don't print progress (errors, hints and the sponsor line still print)")
    g.add_argument("--no-promo", action="store_true",
                   help="don't print the sponsor line at the end (same as MODEL2MC_NO_PROMO=1)")
    g.add_argument("--data-version", type=_number(int, 1), default=DEFAULT_DATA_VERSION, metavar="N",
                   help=f"Minecraft data version written to the files (default {DEFAULT_DATA_VERSION}, Java 26.3)")
    g.add_argument("--schem-version", type=int, choices=[2, 3], default=3,
                   help="Sponge schematic version (default 3; 2 for older WorldEdit)")
    g.add_argument("--dump-colors", action="store_true", help="add each block's sampled colour to the .json dump")
    g.add_argument("--spacing", type=_number(float, 0.05, 1), default=0.3, metavar="N",
                   help="how far apart the surface samples are, in blocks (default 0.3)")
    ap.add_argument("--version", action="version", version=f"model2mc {__version__}")
    return ap


def _fix_negative_axis(argv: list[str]) -> list[str]:
    """argparse reads '--up -z' as two options; join it into '--up=-z'."""
    out, i = [], 0
    while i < len(argv):
        if argv[i] == "--up" and i + 1 < len(argv) and argv[i + 1] in ("-x", "-y", "-z"):
            out.append(f"--up={argv[i + 1]}")
            i += 2
        else:
            out.append(argv[i])
            i += 1
    return out


def _csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def convert(a) -> dict:
    """Run the whole pipeline for parsed arguments; returns a summary dict."""
    say = (lambda *m: None) if a.quiet else (lambda *m: print(*m, file=sys.stderr))
    # check everything that doesn't need the model first, so a typo fails at once
    if Path(a.input).is_dir():
        raise ValueError(f"{a.input}: is a folder, not a model file")
    outs = a.out or [str(Path(a.input).with_suffix(".schem").name)]
    for o in outs:
        if Path(o).suffix.lower() not in (".schem", ".nbt", ".json"):
            raise ValueError(f"don't know how to write {o} (use .schem, .nbt or .json)")
        if Path(o).is_dir():
            raise ValueError(f"{o} is a folder; give a file name, like {Path(o) / Path(a.input).stem}.schem")
    if a.preview and Path(a.preview).suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        raise ValueError(f"--preview {a.preview}: give an image file name ending in .png")
    turn = mesh.orientation_matrix(a.up, a.yaw, a.pitch, a.roll, a.mirror)
    spec = damage.Spec.parse(a.damage) if a.damage is not None or a.damage_seed is not None else None
    table = palette.load_table()
    if a.textures:
        table, missing = palette.measure(a.textures, base=table)
    blocks = palette.select(table, _csv(a.palette), _csv(a.include), _csv(a.exclude))
    if a.filler != "auto" and a.filler.removeprefix("minecraft:") not in {b.id for b in table}:
        raise ValueError(f"--filler: {palette.unknown_block(a.filler.removeprefix('minecraft:'), {b.id for b in table})}")

    t0 = time.perf_counter()
    times = {}
    model = mesh.load(a.input)
    times["load"] = time.perf_counter() - t0
    say(f"loaded {a.input}: {_n(model.triangle_count, 'triangle')} in {_n(len(model.parts), 'part')}")
    if a.textures:
        say(f"measured block colours from {a.textures}"
            + (f" ({_n(len(missing), 'block')} not in it kept the built-in colours)" if missing else ""))
    model = model.transformed(turn)

    cut = None
    if spec is not None:
        seed = a.damage_seed or 0
        cut = lambda shape: damage.cut_mask(shape, seed, spec)  # noqa: E731

    t = time.perf_counter()
    vox = voxelize(model, height=a.height or (None if a.longest else 64), longest=a.longest, mode=a.mode,
                   thickness=a.thickness, spacing=a.spacing, fill_inside=not a.no_fill,
                   loose=a.loose, cut=cut)
    times["voxelize"] = time.perf_counter() - t

    t = time.perf_counter()
    matcher = palette.Matcher(blocks, a.noise_weight)
    states = [export.AIR] + [b.state for b in blocks]
    rgb = [None] + [b.side for b in blocks]
    tops = [(0, 0, 0)] + [b.top for b in blocks]

    grid = np.zeros(vox.shape, np.uint16)
    shell = vox.kind == SHELL
    coords = np.argwhere(shell)
    grid[shell] = matcher.match(vox.colour[shell], coords, a.dither) + 1
    inner = vox.kind == INTERIOR
    if inner.any():
        if a.filler == "auto":
            _, idx = ndimage.distance_transform_edt(~shell, return_indices=True)
            grid[inner] = grid[idx[0][inner], idx[1][inner], idx[2][inner]]
        else:
            fid = a.filler.removeprefix("minecraft:")
            known = {b.id: b for b in table}
            state = f"minecraft:{fid}"
            state, c = known[fid].state, known[fid]
            rgb.append(c.side)
            tops.append(c.top)
            states.append(state)
            grid[inner] = len(states) - 1
    times["match"] = time.perf_counter() - t

    colours = None
    if a.dump_colors:
        colours = (linear_to_srgb(vox.colour) * 255).round().astype(np.uint8)
        colours[vox.kind == 0] = 0
    full = export.BlockGrid(grid, states, rgb, colours)
    top_rgb = np.array(tops, np.uint8)
    side_rgb = np.array([c or (0, 0, 0) for c in rgb], np.uint8)
    bg = full.compact()

    X0, Y0, Z0 = bg.shape
    t = time.perf_counter()
    written = []
    for o in outs:
        p = Path(o)
        ext = p.suffix.lower()
        p.parent.mkdir(parents=True, exist_ok=True)
        if ext == ".schem":
            export.write_schem(bg, p, a.data_version, a.schem_version, name=model.name)
            written.append(p)
        elif ext == ".nbt":
            written += export.write_structures(bg, p, a.data_version)
            if max(bg.shape) > 48:
                say(f"note: {X0} x {Y0} x {Z0} is bigger than a structure block's 48 x 48 x 48, so it's cut into tiles; "
                    f"run the commands in {p.stem}_place.txt to place them")
        elif ext == ".json":
            export.write_voxels(bg, p, {"source": Path(a.input).name, "mode": a.mode,
                                        "blocks_per_unit": vox.scale})
            written.append(p)
    times["export"] = time.perf_counter() - t

    X, Y, Z = bg.shape
    if a.display_mesh:
        t = time.perf_counter()
        dm = display.build(vox.model, vox.shape, a.display_tris)
        Path(a.display_mesh).parent.mkdir(parents=True, exist_ok=True)
        nbytes = display.write(dm, a.display_mesh)
        times["display"] = time.perf_counter() - t
        written.append(Path(a.display_mesh))
        say(f"display mesh: {len(dm.tris):,} triangles, {len(dm.verts):,} vertices, {nbytes:,} bytes "
            f"({nbytes // 4:,} words)")
    summary = {"size": (X, Y, Z), "blocks": bg.count(), "types": len(bg.states) - 1,
               "written": [str(w) for w in written], "hints": hints(X, Y, Z, bg.count(), len(bg.states) - 1, a)}
    if a.preview:
        t = time.perf_counter()
        # map compacted states back to colours
        lookup = {s: i for i, s in enumerate(states)}
        ids = np.array([lookup[s] for s in bg.states])
        sheet = preview.preview(vox.model, bg.blocks, top_rgb[ids], side_rgb[ids], model.name,
                                f"{X} x {Y} x {Z}  ·  {_n(bg.count(), 'block')} of {_n(len(bg.states) - 1, 'kind')}  ·  {a.mode}")
        Path(a.preview).parent.mkdir(parents=True, exist_ok=True)
        sheet.save(a.preview)
        times["preview"] = time.perf_counter() - t
        summary["written"].append(a.preview)
    times["total"] = time.perf_counter() - t0
    summary["times"] = times
    say(f"{X} x {Y} x {Z} (W x H x L), {_n(bg.count(), 'block')} of {_n(len(bg.states) - 1, 'kind')}")
    say("time: " + ", ".join(f"{k} {v:.2f}s" for k, v in times.items()))
    for w in summary["written"]:
        say(f"wrote {w}")
    if not a.json:  # hints are warnings: they print even with -q (with --json they're in the JSON)
        for h in summary["hints"]:
            print(f"hint: {h}", file=sys.stderr)
    return summary


def hints(X: int, Y: int, Z: int, count: int, types: int, a) -> list[str]:
    """Things worth checking in the preview, for people and for agents that can't look at it."""
    out = []
    if Y * 2 < max(X, Z) and a.up.lstrip("+-") == "y" and not (a.pitch or a.roll):
        out.append(f"the build is {Y} tall but {max(X, Z)} {'long' if Z >= X else 'wide'} ({X} x {Y} x {Z}). "
                   "If the model should stand up, it is probably lying down: run again with --up z (or --up x)")
    if Y > WORLD_HEIGHT:
        out.append(f"the build is {Y} tall, more than the world's {WORLD_HEIGHT}-block build height, so the top "
                   "will be cut off; use a smaller --height")
    if types == 1 and count > 1:
        out.append("every block is the same: the model may have no texture or colours (STL files never do)")
    if count == 0:
        out.append("no blocks were made: the model may be too small or open; try a bigger --height or --no-fill")
    return out


def _n(k: int, word: str) -> str:
    return f"{k:,} {word}{'' if k == 1 else 's'}"


def main(argv=None):
    a = build_parser().parse_args(_fix_negative_axis(sys.argv[1:] if argv is None else list(argv)))
    try:
        summary = convert(a)
    except Exception as e:  # one clean line (or JSON) instead of a traceback; MODEL2MC_DEBUG=1 shows it
        if os.environ.get("MODEL2MC_DEBUG") == "1":
            raise
        msg = str(e) if isinstance(e, (ValueError, OSError)) else f"{type(e).__name__}: {e}"
        if isinstance(e, OSError) and getattr(e, "filename", None) and str(e.filename) not in msg:
            msg = f"{e.filename}: {msg}"
        if a.json:
            print(json.dumps({"ok": False, "error": msg}))
        raise SystemExit(f"model2mc: {msg}")
    if a.json:
        out = {"ok": True, "size": dict(zip(("width", "height", "length"), summary["size"])),
               "blocks": summary["blocks"], "block_types": summary["types"], "written": summary["written"],
               "hints": summary["hints"], "seconds": round(summary["times"]["total"], 2)}
        print(json.dumps(out))
    if not (a.no_promo or os.environ.get("MODEL2MC_NO_PROMO") == "1"):
        print(f"Made with Tripo (sponsored): {PROMO_URL}", file=sys.stderr)


if __name__ == "__main__":
    main()
