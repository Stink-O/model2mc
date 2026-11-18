# Contributing to model2mc

Thanks for looking. Help of any size is welcome: a bug report with a model that converts badly, a fix, or a new
output format.

## Where to talk

- **[Discussions](https://github.com/Stink-O/model2mc/discussions):** questions, ideas, and builds you made.
- **[Issues](https://github.com/Stink-O/model2mc/issues):** bugs and concrete tasks. For a bad conversion, attach the
  preview PNG (`--preview out.png`) and, if you can share it, the model.
- **Pull requests:** welcome, including small ones and drafts.

## How it fits together

One run goes through these modules in order (`model2mc/cli.py`, `convert()`):

- **`mesh.py`** loads the model with [trimesh](https://trimesh.org) into flat triangle arrays, with a way to colour
  any point on any triangle (texture, vertex colours or flat colour).
- **`voxelize.py`** scales the model, samples every triangle into the grid, finds the inside, and gives each surface
  block a colour.
- **`palette.py`** and **`colour.py`** match each colour to a block in CIELAB with CIEDE2000. The block list is
  `blocklist.py`; the colours are `blocks.json`.
- **`export.py`** and **`nbt.py`** write `.schem`, `.nbt` and `.json`. `nbt.py` is a small NBT writer and reader.
- **`datapack.py`** builds the `.zip` datapack (place in front of the player, undo through a void `vault` dimension).
  Its docstring lists what was learned testing it on a Java 26.3 server.
- **`preview.py`** draws the model and the blocks side by side.
- **`damage.py`** and **`display.py`** are the optional `--damage-seed` and `--display-mesh` outputs. Their formats
  are written down in [docs/damage.md](docs/damage.md) and [docs/display-mesh.md](docs/display-mesh.md) so other
  programs can read or repeat them.

Axes everywhere are Minecraft's: x east, y up, z south.

## Building and testing

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev,display]'
python -m pytest -q
```

Most tests build their own models in code (`tools/make_samples.py`), so they need no downloads. Two are skipped
unless you give them more: one converts the DamagedHelmet sample (run `sh samples/download.sh` first), and one checks
every block id against a real client jar:

```sh
MINECRAFT_JAR=~/.minecraft/versions/26.3/26.3.jar python -m pytest -q
```

## Ground rules

- **No game files in the repository.** No Minecraft textures or code. `blocks.json` holds numbers only, measured
  with `tools/measure_palette.py` from the player's own client jar.
- **No models and no large output.** Sample models are downloaded (`samples/download.sh`) or built in code. Only the
  small files in `examples/` are committed.
- **Keep the formats in step with their docs.** A change to the `.json` dump, the display mesh or the damage rule
  changes `docs/` in the same pull request.
- **No network requests, no telemetry.** The tool reads the model and writes files, nothing else. The one sponsor
  line printed after a conversion stays optional (`--no-promo`, `MODEL2MC_NO_PROMO=1`).
- **Show evidence.** For a change to the output, attach a before and after preview of the same model.
- **Use plain English** in the README, the help text and the error messages.

## Licence

model2mc is MIT licensed ([LICENSE](LICENSE)). By contributing you agree that your contribution is released under the
same licence.
