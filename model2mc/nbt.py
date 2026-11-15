"""A small NBT writer and reader (Java Edition, big-endian), enough for schematics and structure files.

Values are plain Python with a few wrappers to pick the tag type: Byte, Short, Int, Long, Float, Double, ByteArray,
IntArray, LongArray. A str is a String, a dict a Compound and a list a List (of the first element's type).
"""
from __future__ import annotations

import gzip
import struct

import numpy as np

END, BYTE, SHORT, INT, LONG, FLOAT, DOUBLE, BYTE_ARRAY, STRING, LIST, COMPOUND, INT_ARRAY, LONG_ARRAY = range(13)


class _Num:
    __slots__ = ("v",)

    def __init__(self, v):
        self.v = v

    def __eq__(self, o):
        return type(o) is type(self) and o.v == self.v

    def __repr__(self):
        return f"{type(self).__name__}({self.v!r})"


class Byte(_Num): tag = BYTE
class Short(_Num): tag = SHORT
class Int(_Num): tag = INT
class Long(_Num): tag = LONG
class Float(_Num): tag = FLOAT
class Double(_Num): tag = DOUBLE


class ByteArray(bytes):
    tag = BYTE_ARRAY


class IntArray(list):
    tag = INT_ARRAY


class LongArray(list):
    tag = LONG_ARRAY


_FMT = {BYTE: ">b", SHORT: ">h", INT: ">i", LONG: ">q", FLOAT: ">f", DOUBLE: ">d"}


def _tag_of(v) -> int:
    if hasattr(v, "tag"):
        return v.tag
    if isinstance(v, str):
        return STRING
    if isinstance(v, dict):
        return COMPOUND
    if isinstance(v, list):
        return LIST
    if isinstance(v, bool):
        return BYTE
    if isinstance(v, int):
        return INT
    if isinstance(v, float):
        return DOUBLE
    raise TypeError(f"no NBT type for {type(v).__name__}")


def _str(s: str) -> bytes:
    b = s.encode("utf-8")  # modified UTF-8 only differs for NUL and astral characters
    return struct.pack(">H", len(b)) + b


def _payload(v, tag: int, out: list):
    if tag in _FMT:
        x = v.v if isinstance(v, _Num) else v
        out.append(struct.pack(_FMT[tag], int(x) if tag <= LONG else float(x)))
    elif tag == BYTE_ARRAY:
        out.append(struct.pack(">i", len(v)))
        out.append(bytes(v))
    elif tag == STRING:
        out.append(_str(v))
    elif tag == LIST:
        et = _tag_of(v[0]) if v else END
        out.append(struct.pack(">bi", et, len(v)))
        for e in v:
            _payload(e, et, out)
    elif tag == COMPOUND:
        for k, e in v.items():
            t = _tag_of(e)
            out.append(struct.pack(">b", t) + _str(k))
            _payload(e, t, out)
        out.append(b"\x00")
    elif tag == INT_ARRAY:
        out.append(struct.pack(">i", len(v)))
        out.append(np.asarray(v, dtype=">i4").tobytes())
    elif tag == LONG_ARRAY:
        out.append(struct.pack(">i", len(v)))
        out.append(np.asarray(v, dtype=">i8").tobytes())


def dumps(root: dict, name: str = "") -> bytes:
    out = [struct.pack(">b", COMPOUND), _str(name)]
    _payload(root, COMPOUND, out)
    return b"".join(out)


def write(path, root: dict, name: str = ""):
    with gzip.open(path, "wb", compresslevel=6) as f:
        f.write(dumps(root, name))


# ---------------------------------------------------------------------------------------------------- reading

class _Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.i = 0

    def take(self, n):
        b = self.d[self.i:self.i + n]
        self.i += n
        return b

    def unpack(self, fmt):
        n = struct.calcsize(fmt)
        return struct.unpack(fmt, self.take(n))[0]

    def string(self):
        return self.take(self.unpack(">H")).decode("utf-8")

    def payload(self, tag):
        if tag in _FMT:
            v = self.unpack(_FMT[tag])
            return {BYTE: Byte, SHORT: Short, INT: Int, LONG: Long, FLOAT: Float, DOUBLE: Double}[tag](v)
        if tag == BYTE_ARRAY:
            return ByteArray(self.take(self.unpack(">i")))
        if tag == STRING:
            return self.string()
        if tag == LIST:
            et, n = self.unpack(">b"), self.unpack(">i")
            return [self.payload(et) for _ in range(n)]
        if tag == COMPOUND:
            out = {}
            while True:
                t = self.unpack(">b")
                if t == END:
                    return out
                k = self.string()
                out[k] = self.payload(t)
        if tag == INT_ARRAY:
            n = self.unpack(">i")
            return IntArray(np.frombuffer(self.take(4 * n), ">i4").tolist())
        if tag == LONG_ARRAY:
            n = self.unpack(">i")
            return LongArray(np.frombuffer(self.take(8 * n), ">i8").tolist())
        raise ValueError(f"bad tag {tag}")


def loads(data: bytes) -> tuple[str, dict]:
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    r = _Reader(data)
    if r.unpack(">b") != COMPOUND:
        raise ValueError("root is not a compound")
    name = r.string()
    return name, r.payload(COMPOUND)


def read(path) -> tuple[str, dict]:
    with open(path, "rb") as f:
        return loads(f.read())
