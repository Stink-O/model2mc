"""End-to-end and format checks. Run with: python -m pytest -q

The schematic and structure files are read back with nbtlib (an independent NBT library) as well as our own reader.
"""
import base64
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from model2mc import DEFAULT_DATA_VERSION, STRUCTURE_DATA_VERSION, export, nbt, palette  # noqa: E402
from model2mc.blocklist import BLOCKS  # noqa: E402
from model2mc.cli import build_parser, convert  # noqa: E402

nbtlib = pytest.importorskip("nbtlib")


@pytest.fixture(scope="session")
def samples(tmp_path_factory):
    import make_samples
    out = tmp_path_factory.mktemp("samples")
    make_samples.OUT = out
    sys.argv = ["make_samples", "--no-blob"]
    make_samples.main()
    return out


def run(args):
    return convert(build_parser().parse_args(args + ["-q"]))


def test_varints_roundtrip():
    v = np.array([0, 1, 127, 128, 255, 300, 16383, 16384, 2 ** 21, 2 ** 28 + 5])
    assert export.read_varints(export.varints(v), len(v)).tolist() == v.tolist()
    assert export.varints(np.array([1, 128])) == bytes([1, 0x80, 0x01])


def test_cube_faces_and_schem_v3(samples, tmp_path):
    out = tmp_path / "cube.schem"
    s = run([str(samples / "cube.glb"), "--height", "16", "-o", str(out), "-o", str(tmp_path / "cube.json")])
    assert s["size"] == (16, 16, 16)
    assert s["blocks"] == 6 * 16 * 16 - 12 * 16 + 8  # a closed one-block shell

    f = nbtlib.load(out)  # independent reader
    sch = f["Schematic"]
    assert int(sch["Version"]) == 3 and int(sch["DataVersion"]) == DEFAULT_DATA_VERSION
    assert (int(sch["Width"]), int(sch["Height"]), int(sch["Length"])) == (16, 16, 16)
    pal = {str(k): int(v) for k, v in sch["Blocks"]["Palette"].items()}
    assert pal["minecraft:air"] == 0 and sorted(pal.values()) == list(range(len(pal)))
    data = bytes(np.array(sch["Blocks"]["Data"], dtype=np.int8).view(np.uint8))
    flat = export.read_varints(data, 16 ** 3)
    grid = flat.reshape(16, 16, 16).transpose(2, 0, 1)  # (y, z, x) -> (x, y, z)
    names = {v: k for k, v in pal.items()}

    def face(sel):
        vals, counts = np.unique(grid[sel], return_counts=True)
        return names[int(vals[counts.argmax()])]

    # the cube's faces: up yellow, east red, south blue (see tools/make_samples.py)
    assert "yellow" in face((slice(2, 14), 15, slice(2, 14)))
    assert "red" in face((15, slice(2, 14), slice(2, 14)))
    assert "blue" in face((slice(2, 14), slice(2, 14), 15))

    ours = export.read_schem(out)
    assert np.array_equal(ours.blocks, grid)

    dump = json.loads((tmp_path / "cube.json").read_text())
    assert dump["size"] == [16, 16, 16] and dump["palette"][0]["state"] == "minecraft:air"
    raw = np.frombuffer(base64.b64decode(dump["blocks"]), np.uint8 if dump["encoding"] == "u8" else "<u2")
    assert raw.size == 16 ** 3 and int((raw != 0).sum()) == dump["count"]


def test_obj_matches_glb(samples, tmp_path):
    a = run([str(samples / "cube.glb"), "--height", "12", "-o", str(tmp_path / "a.schem")])
    b = run([str(samples / "cube.obj"), "--height", "12", "-o", str(tmp_path / "b.schem")])
    ga, gb = export.read_schem(tmp_path / "a.schem"), export.read_schem(tmp_path / "b.schem")
    assert a["size"] == b["size"]
    sa = np.array(ga.states, object)[ga.blocks]
    sb = np.array(gb.states, object)[gb.blocks]
    assert (sa == sb).mean() > 0.97


def test_solid_and_v2(samples, tmp_path):
    out = tmp_path / "s.schem"
    shell = run([str(samples / "sphere.glb"), "--height", "20", "-o", str(tmp_path / "sh.schem")])
    solid = run([str(samples / "sphere.glb"), "--height", "20", "--mode", "solid", "-o", str(out),
                 "--schem-version", "2", "--filler", "stone"])
    assert solid["blocks"] > shell["blocks"] * 1.5
    # a sphere of radius 10 holds about 4/3 pi 10^3 = 4189 blocks
    assert 3700 < solid["blocks"] < 4700
    f = nbtlib.load(out)
    root = f if "Version" in f else f["Schematic"]
    assert int(root["Version"]) == 2 and int(root["PaletteMax"]) == len(root["Palette"])
    assert "minecraft:stone" in root["Palette"]
    assert len(root["BlockData"]) >= 20 * 20 * 20


def test_structure_tiles(samples, tmp_path):
    out = tmp_path / "t.nbt"
    run([str(samples / "rainbow.glb"), "--height", "60", "-o", str(out)])
    tiles = sorted(tmp_path.glob("t_x*.nbt"))
    assert len(tiles) >= 2 and (tmp_path / "t_place.txt").exists()
    total = 0
    for t in tiles:
        f = nbtlib.load(t)
        size = [int(v) for v in f["size"]]
        assert all(1 <= v <= 48 for v in size)
        assert int(f["DataVersion"]) == STRUCTURE_DATA_VERSION  # Java 26.3 upgrades it (a 5023 file places as air)
        for b in f["blocks"]:
            assert all(0 <= int(p) < s for p, s in zip(b["pos"], size))
        total += len(f["blocks"])
    assert total > 1000


def test_stl_is_one_colour(samples, tmp_path):
    s = run([str(samples / "part.stl"), "--height", "10", "--up", "z", "-o", str(tmp_path / "p.schem")])
    assert s["types"] == 1


def test_nbt_writer_roundtrip(tmp_path):
    root = {"a": nbt.Byte(-3), "b": nbt.Short(40000 - 65536), "l": nbt.Long(2 ** 40), "f": nbt.Float(1.5),
            "s": "héllo", "ia": nbt.IntArray([1, -2]), "la": nbt.LongArray([2 ** 50]), "ba": nbt.ByteArray(b"\x00\xff"),
            "list": [{"x": nbt.Int(1)}, {"x": nbt.Int(2)}], "empty": []}
    nbt.write(tmp_path / "x.nbt", root, "root")
    name, back = nbt.read(tmp_path / "x.nbt")
    assert name == "root" and back["a"] == nbt.Byte(-3) and back["s"] == "héllo" and back["list"][1]["x"].v == 2
    f = nbtlib.load(tmp_path / "x.nbt")
    assert int(f["l"]) == 2 ** 40 and list(f["ia"]) == [1, -2]


def test_table_covers_block_list():
    table = {b.id for b in palette.load_table()}
    assert table == {b for b, _, _ in BLOCKS}


JAR = os.environ.get("MINECRAFT_JAR")


@pytest.mark.skipif(not JAR, reason="set MINECRAFT_JAR to your client jar to check block ids against it")
def test_block_ids_exist_in_jar():
    missing = [b for b, _, _ in BLOCKS if not palette.block_exists(JAR, b)]
    assert not missing


# ---- display mesh, damage, promo line ----

def test_display_mesh_roundtrip_and_scale(samples, tmp_path):
    from model2mc import display
    out = tmp_path / "s.m2md"
    s = run([str(samples / "sphere.glb"), "--height", "40", "-o", str(tmp_path / "s.schem"),
             "--display-mesh", str(out), "--display-tris", "300"])
    m = display.read(out)
    assert m.grid == s["size"]
    assert 100 < len(m.tris) <= 300
    lo, hi = m.bounds
    assert lo[1] == pytest.approx(0, abs=0.5) and hi[1] == pytest.approx(40, abs=0.5)  # same height in blocks
    assert m.tris.max() < len(m.verts) and m.rgb.shape == (len(m.verts), 3)
    raw = out.read_bytes()
    assert raw[:4] == b"M2MD" and len(raw) % 4 == 0
    assert len(raw) == (20 + 5 * len(m.verts)) * 4 + ((3 * len(m.tris) + 1) // 2) * 4
    # the normals are unit vectors that point out of the sphere
    out_dir = m.verts - (lo + hi) / 2
    out_dir /= np.linalg.norm(out_dir, axis=1, keepdims=True)
    assert np.allclose(np.linalg.norm(m.normals, axis=1), 1, atol=0.01)
    assert ((m.normals * out_dir).sum(1) > 0.8).all()


def _sphere_display(samples, tmp_path, budget=300):
    from model2mc import display
    out = tmp_path / "s.m2md"
    run([str(samples / "sphere.glb"), "--height", "40", "-o", str(tmp_path / "s.schem"),
         "--display-mesh", str(out), "--display-tris", str(budget)])
    return display.read(out)


def _check_sphere(m, budget=300):
    assert 100 < len(m.tris) <= budget
    lo, hi = m.bounds
    out_dir = m.verts - (lo + hi) / 2
    out_dir /= np.linalg.norm(out_dir, axis=1, keepdims=True)
    assert np.allclose(np.linalg.norm(m.normals, axis=1), 1, atol=0.01)
    assert ((m.normals * out_dir).sum(1) > 0.8).all()
    assert m.glow is not None and m.glow.shape == (len(m.verts),)


def test_display_mesh_clustering_fallback(samples, tmp_path, monkeypatch):
    """Without fast-simplification the mesh is made by vertex clustering."""
    from model2mc import display
    monkeypatch.setitem(sys.modules, "fast_simplification", None)  # makes `import fast_simplification` fail
    called = []
    real = display._cluster
    monkeypatch.setattr(display, "_cluster", lambda *a: called.append(1) or real(*a))
    _check_sphere(_sphere_display(samples, tmp_path))
    assert called


def test_display_mesh_edge_collapse(samples, tmp_path, monkeypatch):
    """With fast-simplification installed the mesh is made by edge collapse, and a closed sphere stays closed."""
    pytest.importorskip("fast_simplification")
    from model2mc import display
    monkeypatch.setattr(display, "_cluster", lambda *a: pytest.fail("clustering used although collapse is available"))
    m = _sphere_display(samples, tmp_path)
    _check_sphere(m)
    assert display.bad_edges(m.tris.astype(np.int64)) == 0


def test_display_mesh_reads_old_versions(tmp_path):
    """Version 1 and 2 files have 4-word vertices: no normal word and no header word 15."""
    import struct
    from model2mc import display
    verts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32)
    for ver, top in ((1, 255), (2, 40)):
        head = struct.pack("<4sIII3f3f3IIII", b"M2MD", ver, 3, 1, 0, 0, 0, 1, 1, 0, 2, 2, 2, 1, 20, 0)
        head += b"\0" * (80 - len(head))
        w = np.zeros((3, 4), np.uint32)
        w[:, :3] = verts.view(np.uint32)
        w[:, 3] = 10 | 20 << 8 | 30 << 16 | top << 24
        idx = np.array([0, 1, 2, 0], "<u2").tobytes()
        p = tmp_path / f"v{ver}.m2md"
        p.write_bytes(head + w.astype("<u4").tobytes() + idx)
        m = display.read(p)
        assert np.array_equal(m.verts, verts) and m.tris.tolist() == [[0, 1, 2]] and m.grid == (2, 2, 2)
        assert m.rgb[0].tolist() == [10, 20, 30] and m.normals is None
        assert (m.glow is None) if ver == 1 else (m.glow.tolist() == [40, 40, 40])


@pytest.mark.skipif(not (ROOT / "samples" / "DamagedHelmet.glb").exists(),
                    reason="run sh samples/download.sh for the DamagedHelmet sample")
def test_display_mesh_budget(samples, tmp_path):
    from model2mc import display
    out = tmp_path / "b.m2md"
    run([str(ROOT / "samples" / "DamagedHelmet.glb"), "--height", "30", "-o", str(tmp_path / "b.json"),
         "--display-mesh", str(out), "--display-tris", "2000"])
    n = len(display.read(out).tris)
    assert 1700 <= n <= 2000


def test_damage_is_reproducible_and_seeded():
    from model2mc import damage
    spec = damage.Spec.parse("cuts=3,size=0.25")
    a = damage.cut_mask((30, 60, 30), 5, spec)
    assert (a == damage.cut_mask((30, 60, 30), 5, spec)).all()
    assert not (a == damage.cut_mask((30, 60, 30), 6, spec)).all()
    assert 0 < a.sum() < a.size // 2
    assert damage.hash3(1, 2, 3, 4) == damage.hash3(1, 2, 3, 4)
    # pinned values, so a port can check itself against these
    assert int(damage.hash3(1, 0, 0, 0)) == 1753845952
    assert int(damage.hash3(12345, 1, -2, 3)) == 2801712057
    assert int(damage.cut_mask((30, 60, 30), 5, damage.Spec()).sum()) == 12113
    with pytest.raises(ValueError):
        damage.Spec.parse("bogus=1")


def test_damage_removes_blocks(samples, tmp_path):
    whole = run([str(samples / "sphere.glb"), "--height", "30", "--mode", "solid", "-o", str(tmp_path / "w.json")])
    broken = run([str(samples / "sphere.glb"), "--height", "30", "--mode", "solid", "-o", str(tmp_path / "d.json"),
                  "--damage-seed", "3", "--damage", "cuts=2,size=0.3"])
    again = run([str(samples / "sphere.glb"), "--height", "30", "--mode", "solid", "-o", str(tmp_path / "d2.json"),
                 "--damage-seed", "3", "--damage", "cuts=2,size=0.3"])
    assert broken["blocks"] < whole["blocks"]
    assert json.loads((tmp_path / "d.json").read_text())["blocks"] == json.loads((tmp_path / "d2.json").read_text())["blocks"]


def test_promo_line(samples, tmp_path, capsys, monkeypatch):
    from model2mc.cli import main
    args = [str(samples / "cube.glb"), "--height", "8", "-o", str(tmp_path / "c.schem")]
    monkeypatch.delenv("MODEL2MC_NO_PROMO", raising=False)
    main(args)
    err = capsys.readouterr().err
    assert err.count("Made with Tripo (sponsored): https://tripo.pxf.io/xJdOKy?p.invite_code=8615S1") == 1
    main(args + ["--no-promo"])
    assert "Tripo" not in capsys.readouterr().err
    monkeypatch.setenv("MODEL2MC_NO_PROMO", "1")
    main(args)
    assert "Tripo" not in capsys.readouterr().err
    assert b"Tripo" not in (tmp_path / "c.schem").read_bytes()


# --- for scripts and AI agents -------------------------------------------------------------------------------------

def test_json_output(samples, tmp_path, capsys):
    from model2mc.cli import main
    out = tmp_path / "new" / "folder" / "c.schem"  # missing folders are made
    main([str(samples / "cube.glb"), "--height", "8", "-o", str(out), "-p", str(tmp_path / "p" / "c.png"),
          "--json", "--no-promo"])
    cap = capsys.readouterr()
    r = json.loads(cap.out)  # stdout is exactly one JSON object
    assert r["ok"] is True and r["size"] == {"width": 8, "height": 8, "length": 8}
    assert r["blocks"] > 0 and r["block_types"] >= 6 and r["hints"] == []
    assert r["written"] == [str(out), str(tmp_path / "p" / "c.png")] and out.exists()
    assert "wrote" in cap.err


@pytest.mark.parametrize("args, says", [
    (["missing.glb"], "no such file"),
    (["{cube}", "-o", "{tmp}/c.txt"], "don't know how to write"),
    (["{cube}", "--up", "q"], "--up must be"),
])
def test_json_errors(samples, tmp_path, capsys, args, says):
    from model2mc.cli import main
    args = [x.format(cube=samples / "cube.glb", tmp=tmp_path) for x in args]
    with pytest.raises(SystemExit) as e:
        main(args + ["--json"])
    assert e.value.code == f"model2mc: {json.loads(capsys.readouterr().out)['error']}"  # exit status 1
    assert says in str(e.value.code)
    assert not list(tmp_path.iterdir())  # nothing half-written


def test_hints():
    from model2mc.cli import hints
    a = build_parser().parse_args(["m.glb"])
    assert hints(30, 80, 20, 5000, 20, a) == []
    assert "--up z" in hints(120, 40, 217, 5000, 20, a)[0]  # lying down
    assert "no texture" in hints(30, 80, 20, 5000, 1, a)[0]


# --- bad input fails early with a plain message --------------------------------------------------------------------

@pytest.mark.parametrize("args, says", [
    (["--height", "0"], "out of range"),
    (["--height", "5000"], "out of range"),
    (["--dither", "5"], "out of range"),
    (["--thickness", "0"], "out of range"),
    (["--spacing", "0"], "out of range"),
    (["--height", "abc"], "not a whole number"),
])
def test_option_ranges(samples, capsys, args, says):
    from model2mc.cli import main
    with pytest.raises(SystemExit) as e:
        main([str(samples / "cube.glb")] + args)
    assert e.value.code == 2
    assert says in capsys.readouterr().err


@pytest.mark.parametrize("args, says", [
    (["--include", "gold_blok"], "did you mean gold_block"),
    (["--exclude", "notablock"], "not one of model2mc's blocks"),
    (["--mode", "solid", "--filler", "notablock"], "--filler"),
    (["--palette", "bogus"], "unknown group bogus"),
    (["--textures", "/no/such/pack.zip"], "no such file or folder"),
    (["-p", "preview"], "ending in .png"),
])
def test_bad_names(samples, tmp_path, args, says):
    with pytest.raises(ValueError, match=says):
        run([str(samples / "cube.glb"), "-o", str(tmp_path / "c.schem")] + args)


def test_bad_model_files(tmp_path):
    (tmp_path / "empty.glb").write_bytes(b"")
    (tmp_path / "text.glb").write_text("hello")
    (tmp_path / "folder.glb").mkdir()
    for name, says in [("empty.glb", "empty"), ("text.glb", "not a readable GLB"), ("folder.glb", "folder"),
                       ("missing.glb", "no such file"), ("model.fbx", "can't read .fbx")]:
        with pytest.raises(ValueError, match=says):
            run([str(tmp_path / name), "-o", str(tmp_path / "x.schem")])


def test_negative_up_axis(samples, tmp_path):
    from model2mc.cli import main
    main([str(samples / "cube.glb"), "--height", "8", "--up", "-z", "-o", str(tmp_path / "c.schem"), "-q",
          "--no-promo"])
    assert (tmp_path / "c.schem").exists()


def test_old_tiles_are_replaced(samples, tmp_path):
    out = tmp_path / "s.nbt"
    run([str(samples / "sphere.glb"), "--height", "60", "-o", str(out)])
    assert len(list(tmp_path.glob("s_x*_y*_z*.nbt"))) > 1 and (tmp_path / "s_place.txt").exists()
    (tmp_path / "s_notes.txt").write_text("not ours")
    run([str(samples / "sphere.glb"), "--height", "20", "-o", str(out)])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["s.nbt", "s_notes.txt"]


def test_too_tall_hint():
    from model2mc.cli import hints
    a = build_parser().parse_args(["m.glb"])
    assert "build height" in hints(200, 450, 150, 5000, 20, a)[0]


# --- the Java datapack --------------------------------------------------------------------------------------------

def _pack(path):
    import zipfile
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def test_datapack(samples, tmp_path):
    import gzip
    import re
    s = run([str(samples / "rainbow.glb"), "--height", "60", "-o", str(tmp_path / "Ring Model.zip")])
    files = _pack(tmp_path / "Ring Model.zip")
    meta = json.loads(files["pack.mcmeta"])["pack"]
    assert meta["min_format"] == meta["max_format"] == 121
    assert json.loads(files["data/model2mc_internal/dimension/vault.json"])["generator"]["settings"]["layers"] == []
    assert json.loads(files["data/minecraft/tags/function/load.json"]) == {"values": ["model2mc_internal:load"]}
    assert json.loads(files["data/model2mc_internal/tags/function/undo_start.json"]) == {"values": ["model2mc_internal:ring_model/undo1"]}
    assert s["datapack"]["commands"] == ["/function model2mc:ring_model", "/function model2mc:undo"]
    # players only see the place and undo commands under model2mc:; everything else is internal
    assert sorted(n for n in files if n.startswith("data/model2mc/")) == [
        "data/model2mc/function/ring_model.mcfunction", "data/model2mc/function/undo.mcfunction"]

    functions = {n for n in files if n.endswith(".mcfunction")}
    structures = {n for n in files if n.startswith("data/model2mc_internal/structure/")}
    assert len(structures) == s["datapack"]["tiles"] >= 2
    text = "\n".join(files[n].decode() for n in functions)
    # every structure the functions place is in the pack, and every structure is placed
    placed = set(re.findall(r"place template model2mc_internal:([a-z0-9_/.]+)", text))
    assert {f"data/model2mc_internal/structure/{p}.nbt" for p in placed} == structures
    # every function a function or tag calls exists
    def fn_path(ref):
        ns, path = ref.split(":")
        return f"data/{ns}/function/{path}.mcfunction"
    calls = set(re.findall(r"function (model2mc(?:_internal)?:[a-z0-9_/.]+)", text))
    for tag in ("undo_start", "undo_finish"):
        calls |= set(json.loads(files[f"data/model2mc_internal/tags/function/{tag}.json"])["values"])
    assert calls and all(fn_path(c) in functions for c in calls)
    # structures are gzipped NBT at the version Java upgrades
    for n in structures:
        root = nbtlib.File.parse(__import__("io").BytesIO(gzip.decompress(files[n])))
        assert int(root["DataVersion"]) == STRUCTURE_DATA_VERSION


def test_two_datapacks_combine(samples, tmp_path):
    run([str(samples / "cube.glb"), "--height", "8", "-o", str(tmp_path / "a.zip")])
    run([str(samples / "sphere.glb"), "--height", "10", "-o", str(tmp_path / "b.zip")])
    a, b = _pack(tmp_path / "a.zip"), _pack(tmp_path / "b.zip")
    shared = set(a) & set(b)
    # the files both packs have must be identical, or the second pack would break the first
    assert all(a[n] == b[n] for n in shared - {"pack.mcmeta", "data/model2mc_internal/tags/function/undo_start.json",
                                              "data/model2mc_internal/tags/function/undo_finish.json"})
    assert "data/model2mc/function/undo.mcfunction" in shared
