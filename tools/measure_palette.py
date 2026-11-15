#!/usr/bin/env python3
"""Measure the block colour table (model2mc/blocks.json) from your own Minecraft client jar or resource pack.

    python tools/measure_palette.py ~/.minecraft/versions/26.3/26.3.jar

Only average colours (numbers) are written; no textures are copied.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from model2mc import palette  # noqa: E402

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("source", help="client .jar, resource pack .zip, or an extracted folder")
ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "model2mc" / "blocks.json"))
ap.add_argument("--label", default=None, help="what to record as the source (default: the file name)")
a = ap.parse_args()
blocks, missing = palette.measure(a.source)
if missing:
    print("not found (left out):", ", ".join(missing), file=sys.stderr)
palette.save_table(blocks, a.out, a.label or Path(a.source).name)
print(f"{len(blocks)} blocks -> {a.out}")
