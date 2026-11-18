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

PROMO_URL = ("https://studio.tripo3d.ai/cli?invite_code=8615S1&utm_source=X&utm_medium=kol"
             "&utm_campaign=kol_Stinkysunstep")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="model2mc",
        description="Turn a 3D model (GLB, glTF, OBJ; STL in one colour) into Minecraft blocks.",
        epilog="example: model2mc dragon.glb --height 80 --out dragon.schem --preview dragon.png")
    ap.add_argument("input", help="model file (.glb, .gltf, .obj, .stl)")
    ap.add_argument("-o", "--out", action="append", default=[],
                    help="output file; the extension picks the format: .schem (Sponge schematic), "
                         ".nbt (vanilla structure, split into 48^3 tiles if bigger), .json (voxel dump). "
                         "Give it more than once for several. Default: INPUT.schem")
    ap.add_argument("-p", "--preview", help="write a preview PNG (model and blocks side by side)")
    size = ap.add_mutually_exclusive_group()
    size.add_argument("--height", type=int, help="height in blocks (default 64)")
    size.add_argument("--longest", type=int, help="size of the longest side in blocks, instead of --height")
    ap.add_argument("--mode", choices=["shell", "solid"], default="shell",
                    help="shell: only the blocks that show (default); solid: filled inside too")
    ap.add_argument("--thickness", type=int, default=1, help="shell thickness in blocks (default 1)")
    ap.add_argument("--filler", default="auto",
                    help="solid mode's inside block: a block id, or auto (the nearest outside block; default)")
    ap.add_argument("--loose", action="store_true",
                    help="keep every block the surface touches (fatter, more blocks) instead of centres inside")
    ap.add_argument("--no-fill", action="store_true",
                    help="don't work out the inside at all (for open meshes: a single sheet, a terrain tile)")
    g = ap.add_argument_group("orientation")
    g.add_argument("--up", default="y", help="the model's up axis: y (glTF, default), z (many OBJ/STL), x, -y...")
    g.add_argument("--yaw", type=float, default=0.0, help="turn about the vertical, degrees (the front faces south)")
    g.add_argument("--pitch", type=float, default=0.0, help="tip about the east-west axis, degrees")
    g.add_argument("--roll", type=float, default=0.0, help="tip about the north-south axis, degrees")
    g.add_argument("--mirror", action="store_true", help="mirror east-west")
    g = ap.add_argument_group("blocks")
    g.add_argument("--palette", default=",".join(GROUPS),
                   help=f"block groups to use, comma separated (default all): {', '.join(GROUPS)}")
    g.add_argument("--include", default="", help="extra block ids, comma separated (e.g. gold_block)")
    g.add_argument("--exclude", default="", help="block ids to leave out, comma separated")
    g.add_argument("--textures", help="measure block colours from your own client .jar or a resource pack "
                                      "(.zip or folder) instead of the shipped table")
    g.add_argument("--dither", type=float, default=0.0, help="ordered dithering strength, 0-1 (default 0)")
    g.add_argument("--noise-weight", type=float, default=0.12,
                   help="how much to avoid busy textures (cobblestone, ores of colour); default 0.12")
    g = ap.add_argument_group("display mesh and damage")
    g.add_argument("--display-mesh", metavar="OUT", help="also write a decimated, colour-baked render mesh "
                   "(binary, see docs/display-mesh.md) in the same block units as the voxels")
    g.add_argument("--display-tris", type=int, default=8000, help="triangle budget of the display mesh (default 8000)")
    g.add_argument("--damage-seed", type=int, help="convert the model already broken: remove seeded ragged chunks "
                   "(same seed, same cuts; see docs/damage.md)")
    g.add_argument("--damage", metavar="SPEC", help="what to remove, e.g. cuts=4,size=0.25,rough=0.6,ymin=0.4,ymax=0.9 "
                   "(cuts, size = radius as a fraction of height, rough 0-1, ymin/ymax = where centres go); "
                   "alone it uses seed 0")
    g = ap.add_argument_group("output details")
    g.add_argument("--data-version", type=int, default=DEFAULT_DATA_VERSION,
                   help=f"Minecraft data version written to files (default {DEFAULT_DATA_VERSION}, Java 26.3)")
    g.add_argument("--schem-version", type=int, choices=[2, 3], default=3,
                   help="Sponge schematic version (default 3; 2 for older WorldEdit)")
    g.add_argument("--dump-colors", action="store_true", help="add each cell's sampled colour to the .json dump")
    g.add_argument("--spacing", type=float, default=0.3, help="surface sample spacing in blocks (default 0.3)")
    g.add_argument("-q", "--quiet", action="store_true")
    g.add_argument("--json", action="store_true",
                   help="print one JSON object to stdout when done (sizes, files written, hints), or "
                        "{\"ok\": false, \"error\": ...} on failure. For scripts and AI agents")
    ap.add_argument("--no-promo", action="store_true",
                    help="don't print the sponsor line at the end (same as MODEL2MC_NO_PROMO=1)")
    ap.add_argument("--version", action="version", version=f"model2mc {__version__}")
    return ap


def _csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def convert(a) -> dict:
    """Run the whole pipeline for parsed arguments; returns a summary dict."""
    say = (lambda *m: None) if a.quiet else (lambda *m: print(*m, file=sys.stderr))
    # check the cheap things before loading anything
    outs = a.out or [str(Path(a.input).with_suffix(".schem").name)]
    for o in outs:
        if Path(o).suffix.lower() not in (".schem", ".nbt", ".json"):
            raise ValueError(f"don't know how to write {o} (use .schem, .nbt or .json)")
    turn = mesh.orientation_matrix(a.up, a.yaw, a.pitch, a.roll, a.mirror)
    t0 = time.perf_counter()
    times = {}
    model = mesh.load(a.input)
    times["load"] = time.perf_counter() - t0
    say(f"loaded {a.input}: {model.triangle_count:,} triangles in {len(model.parts)} part(s)")
    model = model.transformed(turn)

    cut = None
    if a.damage is not None or a.damage_seed is not None:
        spec = damage.Spec.parse(a.damage)
        seed = a.damage_seed or 0
        cut = lambda shape: damage.cut_mask(shape, seed, spec)  # noqa: E731

    t = time.perf_counter()
    vox = voxelize(model, height=a.height if a.height or a.longest else 64, longest=a.longest, mode=a.mode,
                   thickness=a.thickness, spacing=a.spacing, fill_inside=not a.no_fill,
                   loose=a.loose, cut=cut)
    times["voxelize"] = time.perf_counter() - t

    t = time.perf_counter()
    table = palette.load_table()
    if a.textures:
        table, missing = palette.measure(a.textures, base=table)
        say(f"measured block colours from {a.textures}" + (f" ({len(missing)} kept from the table)" if missing else ""))
    blocks = palette.select(table, _csv(a.palette), _csv(a.include), _csv(a.exclude))
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
            if fid in known:
                state, c = known[fid].state, known[fid]
                rgb.append(c.side)
                tops.append(c.top)
            else:
                rgb.append((128, 128, 128))
                tops.append((128, 128, 128))
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
                say(f"note: {bg.shape} is bigger than a structure block's 48x48x48; wrote tiles and {p.stem}_place.txt")
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
                                f"{X}x{Y}x{Z}  {bg.count():,} blocks  {len(bg.states) - 1} block types  {a.mode}")
        Path(a.preview).parent.mkdir(parents=True, exist_ok=True)
        sheet.save(a.preview)
        times["preview"] = time.perf_counter() - t
        summary["written"].append(a.preview)
    times["total"] = time.perf_counter() - t0
    summary["times"] = times
    say(f"{X} x {Y} x {Z} (W x H x L), {bg.count():,} blocks, {len(bg.states) - 1} block types")
    say("time: " + ", ".join(f"{k} {v:.2f}s" for k, v in times.items()))
    for w in summary["written"]:
        say(f"wrote {w}")
    for h in summary["hints"]:
        say(f"hint: {h}")
    return summary


def hints(X: int, Y: int, Z: int, count: int, types: int, a) -> list[str]:
    """Things worth checking in the preview, for people and for agents that can't look at it."""
    out = []
    if Y * 2 < max(X, Z) and a.up.lstrip("+-") == "y" and not (a.pitch or a.roll):
        out.append(f"the build is {X} wide, {Y} tall and {Z} long, much flatter than it is wide. If the model "
                   "should stand up, it is probably lying down: run again with --up z (or --up x)")
    if types == 1 and count > 0:
        out.append("every block is the same: the model may have no texture or colours (STL files never do)")
    if count == 0:
        out.append("no blocks were made: the model may be too small or open; try a bigger --height or --no-fill")
    return out


def main(argv=None):
    a = build_parser().parse_args(argv)
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
