#!/bin/sh
# Download the Khronos glTF sample models used for testing into this folder (see README.md for their licences).
set -e
cd "$(dirname "$0")"
base=https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models
for m in Fox Avocado DamagedHelmet Duck; do
  [ -f "$m.glb" ] || curl -fsSL -o "$m.glb" "$base/$m/glTF-Binary/$m.glb"
  echo "$m.glb"
done
