# Notes for AI agents

model2mc turns a 3D model (GLB, glTF, OBJ, STL) into Minecraft blocks: a WorldEdit schematic (`.schem`), vanilla
structure files (`.nbt`) or a voxel dump (`.json`). It runs offline, never prompts, and finishes in seconds.

## Install

```sh
python3 -m venv .venv
.venv/bin/pip install -e .          # add '.[dev]' to run the tests
```

Python 3.10 or newer. Use `.venv/bin/model2mc` (or `.venv/bin/python -m model2mc`) so you don't depend on an
activated shell.

## Convert a model

```sh
.venv/bin/model2mc MODEL.glb --height 80 --out MODEL.schem --preview check.png --json
```

With `--json`, stdout is exactly one JSON object and nothing else:

```json
{"ok": true, "size": {"width": 45, "height": 80, "length": 30}, "blocks": 6550, "block_types": 28,
 "written": ["MODEL.schem", "check.png"], "hints": [], "seconds": 1.34}
```

On failure it is `{"ok": false, "error": "..."}` and the exit code is 1. A bad command-line option exits with 2
and an argparse message on stderr. Progress and the sponsor line always go to stderr.

## Check the result

1. Read `hints`. Each one says what looks wrong and which option fixes it. An empty list means nothing obvious.
2. If you can view images, open the `--preview` PNG: the model on the left, the blocks on the right, from two sides.
3. The size should match the model's shape. A standing figure is taller than it is wide.

| Problem | Fix |
| --- | --- |
| Lying down (a hint says so, or `height` is much smaller than `width` or `length`) | `--up z`, or `--up x` |
| Facing the wrong way | `--yaw 90`, `180` or `270` (the front faces south) |
| One solid lump, or whole rows filled | `--no-fill` (the mesh is open) |
| Thin parts missing | a bigger `--height`, or `--loose` |
| Every block the same (a hint says so) | the model has no texture; export a GLB with its texture |
| A colour comes out wrong | `--dither 0.6`, or `--include BLOCK` |

Run again with the fix until `hints` is empty and the preview looks right. The other options are in
`model2mc --help` and the README's Options table.

## Using the files

- `.schem`: the user copies it into WorldEdit's `schematics` folder, then runs `//schem load NAME` and `//paste` in
  game.
- `.nbt`: copy into `<world>/generated/minecraft/structures/`. Builds over 48 blocks are split into tiles; run the
  commands in `NAME_place.txt`.

Don't copy files into a Minecraft folder or a world unless the user asks you to.

## Working on the code

- Tests: `.venv/bin/pip install -e '.[dev]'`, then `.venv/bin/python -m pytest -q`. They build their own models,
  so no downloads are needed.
- `model2mc/blocks.json` is measured from the game's textures by `tools/measure_palette.py`. Don't edit it by hand.
- No game files (textures, jars) go in the repo.
- `CONTRIBUTING.md` explains how the code fits together.
