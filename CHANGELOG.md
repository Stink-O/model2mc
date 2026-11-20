# Changelog

## 0.1.0 (2026-11-20)

First public release.

- Converts a textured 3D model (`.glb`, `.gltf`, `.obj` or `.stl`) into a Minecraft build at the height you choose.
- Writes a Sponge schematic (`.schem`, v3 or v2) for WorldEdit and FAWE, vanilla structure files (`.nbt`, cut into
  48-block tiles with the `/place` commands written out), a Java datapack (`.zip`) that places the build in front of
  you and can undo it, and a `.json` voxel dump.
- Colours each block from the model's texture, vertex colours or material, matched to 168 solid blocks in CIELAB
  with CIEDE2000, with optional dithering.
- Shell or solid builds, and a `--preview` image of the model next to the blocks.
- Made to be run by coding agents: `--json` prints one result object, hints flag a build lying down or untextured,
  and errors are one plain line. See [AGENTS.md](AGENTS.md).
- Tested with Minecraft Java 26.3 and Horizon, the Rust rewrite of Minecraft Java.
