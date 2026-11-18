"""model2mc: turn 3D models (GLB, glTF, OBJ, STL) into Minecraft builds."""

__version__ = "0.1.0"

# Minecraft Java 26.3's data version (world_version in its version.json), written to schematics. Override with
# --data-version.
DEFAULT_DATA_VERSION = 5023
# Structure files (.nbt and the datapack) say 1.21.4 instead. Java 26.3 changed the structure palette layout and skips
# its upgrader for files that claim 26.3's own version, so a 5023 file with the classic {Name, Properties} palette
# places as air there; marked 4189, Java upgrades the palette when it loads the file (tested on a 26.3 server).
STRUCTURE_DATA_VERSION = 4189
