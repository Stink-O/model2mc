"""A vanilla Java datapack (.zip) that places a build in front of the player, with an undo that puts back what was there.

In game:

    /function model2mc:NAME   puts the build on the ground ahead of you (height / 3 + 15 blocks away)
    /function model2mc:undo   takes the nearest placed build away and restores the area as it was

Only those two kinds of function are in the model2mc namespace, so `/function model2mc:` lists nothing else; every
internal step, tag, structure and the vault dimension live in model2mc_internal.

Before placing, the area (one block wider all round, and one below, so things that hang on a neighbour survive) is
copied into a void dimension, model2mc_internal:vault, at the same coordinates; undo copies it back, chests and all. Java only
loads a new dimension when a world opens, so after adding the pack the world has to be reopened once; until then the
functions say so instead of running.

Several packs made by model2mc can be installed together: the shared files (the vault dimension, undo, the load and
check functions) are identical in each, and every model joins the undo through the function tags
#model2mc_internal:undo_start and #model2mc_internal:undo_finish.

The commands come from a pack tested on a Java 26.3 server: after place, exactly the build's blocks changed; after
undo, all 329,400 blocks in the area matched the original, chest contents included. Two packs installed together
were placed and undone in two areas (about 330,000 blocks each) with chests restored. Notes from that test:
- `execute in <dimension> if loaded` is false for force-loaded chunks with no player in the dimension, but clone
  still works there, so loaded checks only run on the overworld side.
- clone runs with `strict`, so blocks are copied without block updates; otherwise plants and leaf litter on the
  backup's bottom layer pop off in the empty vault (203 wrong blocks in one test area, against 28 with strict, all of
  those ordinary world changes such as grass turning to dirt).
- Dedicated servers pause after 60 s with no players and scheduled functions don't fire; set
  pause-when-empty-seconds=-1 when testing headless.
- clone reports failure when it copies only air onto air, so clone results can't signal errors; place results can.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from .export import BlockGrid, safe_name, structure_tiles

PACK_FORMAT = 121            # data pack format of Java 26.3
CLONE_LIMIT = 32768          # the default commandModificationBlockLimit
WORLD_TOP = 319
OBJ = "model2mc_v"
NS, INTERNAL = "model2mc", "model2mc_internal"   # players see model2mc:<model> and model2mc:undo only
VAULT = f"{INTERNAL}:vault"


def _red(t: str) -> str:
    return json.dumps({"text": t, "color": "red"})


def _green(t: str) -> str:
    return json.dumps({"text": t, "color": "green"})


def _rel(*v: int) -> str:
    return " ".join(f"~{x}" if x else "~" for x in v)


def _slices(lo, hi):
    """Split the box into horizontal slabs that each fit one clone."""
    w, l = hi[0] - lo[0] + 1, hi[2] - lo[2] + 1
    if w * l > CLONE_LIMIT:
        raise ValueError(f"the build's footprint ({w} x {l}) is too big for the datapack's clone-based undo "
                         f"(at most {CLONE_LIMIT} blocks per layer); make it smaller or use .nbt/.schem")
    step, y = CLONE_LIMIT // (w * l), lo[1]
    while y <= hi[1]:
        y1 = min(hi[1], y + step - 1)
        yield (lo[0], y, lo[2], hi[0], y1, hi[2])
        y = y1 + 1


def _loaded(lo, hi) -> str:
    """`if loaded` for every chunk column the box touches (relative to the marker)."""
    xs = sorted(set(list(range(lo[0], hi[0], 16)) + [hi[0]]))
    zs = sorted(set(list(range(lo[2], hi[2], 16)) + [hi[2]]))
    return " ".join(f"if loaded {_rel(x, 0, z)}" for x in xs for z in zs)


def _checked(cmd: str) -> list[str]:
    return [f"execute store success score #ok {OBJ} run {cmd}",
            f"execute if score #ok {OBJ} matches 0 run scoreboard players set #fail {OBJ} 1"]


def build(grid: BlockGrid, name: str, data_version: int, distance: int | None = None) -> tuple[dict[str, bytes], dict]:
    """Every file of the pack, {path inside the zip: bytes}, and a summary."""
    N = safe_name(name).replace(".", "_").replace("-", "_")
    if N == "undo":
        N = "undo_model"  # model2mc:undo is the undo command
    tiles = structure_tiles(grid, N, data_version)
    if not tiles:
        raise ValueError("no blocks to put in the datapack")
    lo = [min(t.offset[k] for t in tiles) for k in range(3)]
    hi = [max(t.offset[k] + t.size[k] - 1 for t in tiles) for k in range(3)]
    height = hi[1] - lo[1] + 1
    dist = distance if distance is not None else height // 3 + 15
    if height > WORLD_TOP + 65:
        raise ValueError(f"a {height}-block build can't fit in the world (384 blocks from bottom to top)")

    files: dict[str, bytes] = {}
    put = lambda path, lines: files.__setitem__(path, ("\n".join(lines) + "\n").encode())  # noqa: E731
    js = lambda path, obj: files.__setitem__(path, (json.dumps(obj, indent=1) + "\n").encode())  # noqa: E731

    js("pack.mcmeta", {"pack": {"description": f"model2mc: {N} (place and undo)",
                                "min_format": PACK_FORMAT, "max_format": PACK_FORMAT}})
    js(f"data/{INTERNAL}/dimension/vault.json", {
        "type": "minecraft:overworld",
        "generator": {"type": "minecraft:flat",
                      "settings": {"biome": "minecraft:the_void", "layers": [], "lakes": False, "features": False}}})
    js("data/minecraft/tags/function/load.json", {"values": [f"{INTERNAL}:load"]})
    js(f"data/{INTERNAL}/tags/function/undo_start.json", {"values": [f"{INTERNAL}:{N}/undo1"]})
    js(f"data/{INTERNAL}/tags/function/undo_finish.json", {"values": [f"{INTERNAL}:{N}/undo2"]})
    for t in tiles:
        files[f"data/{INTERNAL}/structure/{t.name}.nbt"] = t.data

    # shared by every model2mc pack (identical text, so packs combine)
    f_int = f"data/{INTERNAL}/function"
    put(f"{f_int}/load.mcfunction", [f"scoreboard objectives add {OBJ} dummy"])
    put(f"{f_int}/vault_check.mcfunction", [
        f"scoreboard players set #vault {OBJ} 0",
        f"execute in {VAULT} run scoreboard players set #vault {OBJ} 1",
        f"execute if score #vault {OBJ} matches 0 run tellraw @a " + _red("Reopen this world once to finish installing model2mc."),
    ])
    put(f"data/{NS}/function/undo.mcfunction", [
        f"function {INTERNAL}:vault_check",
        f"execute if score #vault {OBJ} matches 1 run function {INTERNAL}:undo1",
    ])
    put(f"{f_int}/undo1.mcfunction", [
        f"tag @e[type=marker,tag={NS},tag=!{NS}_pending,tag=!{NS}_undo,sort=nearest,limit=1] add {NS}_undo",
        f"execute unless entity @e[type=marker,tag={NS}_undo] run tellraw @a " + _red("Nothing to undo nearby."),
        f"execute unless entity @e[type=marker,tag={NS}_undo] run return 0",
        f"function #{INTERNAL}:undo_start",
        f"schedule function {INTERNAL}:undo2 20t",
        'tellraw @a "Restoring..."',
    ])
    put(f"{f_int}/undo2.mcfunction", [f"function #{INTERNAL}:undo_finish"])

    # this model
    blo = [lo[0] - 1, lo[1] - 1, lo[2] - 1]
    bhi = [hi[0] + 1, hi[1], hi[2] + 1]
    fl = f"{_rel(blo[0], blo[2])} {_rel(bhi[0], bhi[2])}"
    tag = f"{NS}_{N}"
    me = f"@e[type=marker,tag={tag}"
    sl = list(_slices(blo, bhi))
    d = f"{f_int}/{N}"
    put(f"data/{NS}/function/{N}.mcfunction", [
        f"function {INTERNAL}:vault_check",
        f"execute if score #vault {OBJ} matches 1 run function {INTERNAL}:{N}/place1",
    ])
    put(f"{d}/place1.mcfunction", [
        f"execute rotated ~ 0 positioned ^ ^ ^{dist} positioned over motion_blocking_no_leaves run "
        f'summon marker ~ ~ ~ {{Tags:["{NS}","{tag}","{NS}_pending"]}}',
        f"execute as {me},tag={NS}_pending] if entity @s[y={WORLD_TOP + 1 - height + 1},dy=1000] run tellraw @a "
        + _red(f"Too high here for a {height}-block build. Try lower ground."),
        f"kill {me},tag={NS}_pending,y={WORLD_TOP + 1 - height + 1},dy=1000]",
        f"execute as {me},tag={NS}_pending] at @s in {VAULT} run forceload add {fl}",
        f"schedule function {INTERNAL}:{N}/place2 20t",
        f'execute if entity {me},tag={NS}_pending] run tellraw @a "Placing {N}..."',
    ])
    put(f"{d}/place2.mcfunction", [f"execute as {me},tag={NS}_pending] at @s run function {INTERNAL}:{N}/place3"])
    chk = [f"scoreboard players set #fail {OBJ} 1",
           f"execute {_loaded(blo, bhi)} run scoreboard players set #fail {OBJ} 0"]
    p3 = chk + [
        f"execute if score #fail {OBJ} matches 1 run tellraw @a "
        + _red("Can't back up the area here. Nothing placed."),
        f"execute if score #fail {OBJ} matches 1 in {VAULT} run forceload remove {fl}",
        f"execute if score #fail {OBJ} matches 1 run kill @s",
        f"execute if score #fail {OBJ} matches 1 run return 0",
    ]
    p3 += [f"clone from minecraft:overworld {_rel(*x[:3])} {_rel(*x[3:])} to {VAULT} {_rel(*x[:3])} strict" for x in sl]
    for t in tiles:
        p3 += _checked(f"place template {INTERNAL}:{t.name} {_rel(*t.offset)}")
    p3 += [
        f"execute in {VAULT} run forceload remove {fl}",
        f"tag @s remove {NS}_pending",
        f"execute if score #fail {OBJ} matches 1 run tellraw @a "
        + _red(f"{N} only partly placed. Undo: /function {NS}:undo"),
        f"execute if score #fail {OBJ} matches 0 run tellraw @a " + _green(f"{N} placed. Undo: /function {NS}:undo"),
    ]
    put(f"{d}/place3.mcfunction", p3)
    put(f"{d}/undo1.mcfunction", [
        f"execute as @e[type=marker,tag={NS}_undo,tag={tag}] at @s in {VAULT} run forceload add {fl}"])
    put(f"{d}/undo2.mcfunction", [
        f"execute as @e[type=marker,tag={NS}_undo,tag={tag}] at @s run function {INTERNAL}:{N}/undo3"])
    u3 = chk + [f"execute if score #fail {OBJ} matches 0 run clone from {VAULT} {_rel(*x[:3])} {_rel(*x[3:])} "
                f"to minecraft:overworld {_rel(*x[:3])} strict" for x in sl]
    u3 += [
        f"execute in {VAULT} run forceload remove {fl}",
        f"tag @s remove {NS}_undo",
        f"execute if score #fail {OBJ} matches 1 run tellraw @a " + _red("Couldn't restore it all. Move closer and try again."),
        f"execute if score #fail {OBJ} matches 0 run tellraw @a " + _green(f"{N} removed."),
        f"execute if score #fail {OBJ} matches 0 run kill @s",
    ]
    put(f"{d}/undo3.mcfunction", u3)

    summary = {"name": N, "tiles": len(tiles), "height": height, "distance": dist,
               "commands": [f"/function {NS}:{N}", f"/function {NS}:undo"]}
    return files, summary


def write(grid: BlockGrid, path, data_version: int, distance: int | None = None) -> dict:
    files, summary = build(grid, Path(path).stem, data_version, distance)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, files[name])
    Path(path).write_bytes(buf.getvalue())
    return summary
