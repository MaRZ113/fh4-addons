"""Read and patch MatI shader parameter blocks (MTPR).

Layout (ForzaTools.Bundles MaterialShaderParameterBlob): a parameter count (u8 before v2.1, u16 from
v2.1), then per parameter: version major/minor, u32 CRC-32 name hash, [v3.1+: flag byte (+u32)],
u8 type, [v3+: 16-byte GUID], value; v2.0+ blocks end with three u32. Value sizes: vector, colour,
swizzle, range 16; float, bool, int 4; texture a 7-bit-length path plus a u32 path hash (v2+);
sampler 8 (+4 from v1.1); gradient u32 count of float4; vector2 8 (+8 in v1).
"""

import struct
import zlib

VECTOR, COLOR, FLOAT, BOOL, INT, SWIZZLE, TEXTURE, SAMPLER, GRADIENT, RANGE, VECTOR2 = 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11


def name_hash(name):
    """The engine's parameter name hash: CRC-32 (IEEE), case-sensitive."""
    return zlib.crc32(name.encode("ascii")) & 0xFFFFFFFF


def _varint(data, offset):
    value = shift = 0
    while True:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, offset


def parse(data, version=(2, 0)):
    """[(name hash, type, value offset, record bytes)], trailer bytes."""
    if tuple(version) >= (2, 1):
        count, offset = struct.unpack_from("<H", data, 0)[0], 2
    else:
        count, offset = data[0], 1
    records = []
    for _ in range(count):
        start = offset
        major, minor = data[offset], data[offset + 1]
        key = struct.unpack_from("<I", data, offset + 2)[0]
        offset += 6
        if (major, minor) >= (3, 1):
            offset += 5 if data[offset] else 1
        kind = data[offset]
        offset += 1
        if major >= 3:
            offset += 16
        value = offset
        if kind in (VECTOR, COLOR, SWIZZLE, RANGE):
            offset += 16
        elif kind in (FLOAT, BOOL, INT):
            offset += 4
        elif kind == TEXTURE:
            length, offset = _varint(data, offset)
            offset += length + (4 if major >= 2 else 0)
        elif kind == SAMPLER:
            offset += 8 + (4 if minor >= 1 else 0)
        elif kind == GRADIENT:
            offset += 4 + 16 * struct.unpack_from("<I", data, offset)[0]
        elif kind == VECTOR2:
            offset += 8 + (8 if major == 1 else 0)
        else:
            raise ValueError(f"Unsupported MTPR parameter type {kind}")
        records.append((key, kind, value - start, bytes(data[start:offset])))
    return records, bytes(data[offset:])


def build(records, trailer, version=(2, 0)):
    head = struct.pack("<H", len(records)) if tuple(version) >= (2, 1) else bytes([len(records)])
    return head + b"".join(record for *_, record in records) + trailer


def patch(data, values, version=(2, 0)):
    """Return `data` with the int/bool/float/colour parameters replaced (each must exist). Keys are
    parameter names or raw u32 hashes (some names in public tables are labels, not the hashed name)."""
    records, trailer = parse(data, version)
    wanted = {(name if isinstance(name, int) else name_hash(name)): (name, value) for name, value in values.items()}
    out, seen = [], set()
    for key, kind, at, record in records:
        if key in wanted:
            name, value = wanted[key]
            if kind in (INT, BOOL):
                payload = struct.pack("<i", int(value))
            elif kind == FLOAT:
                payload = struct.pack("<f", float(value))
            elif kind in (COLOR, VECTOR):
                payload = struct.pack("<4f", *value)
            else:
                raise ValueError(f"Cannot patch MTPR parameter {name} of type {kind}")
            record = record[:at] + payload + record[at + len(payload):]
            seen.add(key)
        out.append((key, kind, at, record))
    missing = sorted(wanted[k][0] for k in wanted.keys() - seen)
    if missing:
        raise ValueError(f"MTPR lacks parameters {missing}")
    return build(out, trailer, version)


def values(data, version=(2, 0)):
    """{name hash: decoded scalar/colour} for int, bool, float and colour parameters."""
    result = {}
    for key, kind, at, record in parse(data, version)[0]:
        if kind in (INT, BOOL):
            result[key] = struct.unpack_from("<i", record, at)[0]
        elif kind == FLOAT:
            result[key] = struct.unpack_from("<f", record, at)[0]
        elif kind in (COLOR, VECTOR):
            result[key] = struct.unpack_from("<4f", record, at)
    return result


def texture_path_hash(path):
    """Texture parameters store the CRC-32 of the lower-case path after it."""
    return zlib.crc32(path.lower().encode("ascii")) & 0xFFFFFFFF


def set_textures(data, paths, version=(2, 0)):
    """Return `data` with the texture parameters keyed by name hash pointed at new paths."""
    records, trailer = parse(data, version)
    out, seen = [], set()
    for key, kind, at, record in records:
        if key in paths:
            if kind != TEXTURE:
                raise ValueError(f"MTPR parameter {key:#x} is not a texture")
            major = record[0]
            encoded = paths[key].encode("ascii")
            length, size = len(encoded), b""
            while True:
                byte = length & 0x7F
                length >>= 7
                size += bytes([byte | (0x80 if length else 0)])
                if not length:
                    break
            value = size + encoded + (struct.pack("<I", texture_path_hash(paths[key])) if major >= 2 else b"")
            record = record[:at] + value
            seen.add(key)
        out.append((key, kind, at, record))
    if seen != set(paths):
        raise ValueError(f"MTPR lacks texture parameters {sorted(set(paths) - seen)}")
    return build(out, trailer, version)
