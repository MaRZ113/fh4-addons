"""Serialize FH4 Grub 1.1 rigid Modelbin and skeleton bundles from typed data."""

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
import json
import os
import struct

import numpy as np


HEADER = struct.Struct("<IBBHIII")
BLOB = struct.Struct("<IBBHIIII")
METADATA = struct.Struct("<IHH")
UV_IDENTITY = (0.0, 1.0, 0.0, 1.0)
IDENTITY = tuple(float(i % 5 == 0) for i in range(16))


def tag(value):
    return int.from_bytes(value.encode("ascii"), "big")


def string(value):
    data = value.encode("utf-8")
    return struct.pack("<I", len(data)) + data


def metadata(name, data, version=0):
    return name, version, data


def name_meta(name):
    return metadata("Name", name.encode("ascii"))


def id_meta(number):
    return metadata("Id  ", struct.pack("<i", number))


def box_meta(low, high):
    return metadata("BBox", struct.pack("<6f", *low, *high))


@dataclass(frozen=True)
class Chunk:
    name: str
    version: tuple
    data: bytes
    meta: tuple = ()


def bundle(chunks):
    data = bytearray(HEADER.size + len(chunks) * BLOB.size)
    tables = []
    for chunk in chunks:
        data.extend(bytes(-len(data) % 4))
        start = len(data)
        tables.append(start)
        data.extend(bytes(len(chunk.meta) * METADATA.size))
        for i, (name, version, payload) in enumerate(chunk.meta):
            pos = start + i * METADATA.size
            METADATA.pack_into(data, pos, tag(name), (len(payload) << 4) | version, len(data) - pos)
            data.extend(payload)
    data.extend(bytes(-len(data) % 4))
    header_size = len(data)
    for i, (chunk, table) in enumerate(zip(chunks, tables, strict=True)):
        data.extend(bytes(-len(data) % 4))
        length = len(chunk.data)
        BLOB.pack_into(data, HEADER.size + i * BLOB.size, tag(chunk.name), *chunk.version,
                       len(chunk.meta), table, len(data), length, length)
        data.extend(chunk.data)
    data.extend(bytes(-len(data) % 4))
    HEADER.pack_into(data, 0, tag("Grub"), 1, 1, 0, header_size, len(data), len(chunks))
    return bytes(data)


@dataclass(frozen=True)
class Bone:
    name: str
    parent: int
    translation: tuple = (0.0, 0.0, 0.0)
    rotation: tuple = (0.0, 0.0, 0.0, 1.0)


def transform(translation, rotation):
    """Row-vector affine matrix in the model and Granny bind convention."""
    x, y, z, w = rotation
    if not np.isfinite(rotation).all() or not np.isclose(x*x+y*y+z*z+w*w, 1.0, atol=1e-6):
        raise ValueError("Rigid rotation quaternion must be normalized")
    return np.array(((1-2*(y*y+z*z), 2*(x*y+z*w), 2*(x*z-y*w), 0),
                     (2*(x*y-z*w), 1-2*(x*x+z*z), 2*(y*z+x*w), 0),
                     (2*(x*z+y*w), 2*(y*z-x*w), 1-2*(x*x+y*y), 0),
                     (*translation, 1)), dtype=np.float64)


def skeleton(bones):
    names = [b.name for b in bones]
    if len(set(names)) != len(names) or not bones or bones[0].parent != -1:
        raise ValueError("Invalid skeleton root or duplicate names")
    children = [[] for _ in bones]
    siblings = {}
    for i, bone in enumerate(bones[1:], start=1):
        if not 0 <= bone.parent < i:
            raise ValueError(f"Invalid parent of bone {bone.name}")
        group = children[bone.parent]
        if group:
            siblings[group[-1]] = i
        group.append(i)
    payload = bytearray(struct.pack("<H", len(bones)))
    for i, bone in enumerate(bones):
        payload.extend(string(bone.name))
        payload.extend(struct.pack("<3h16f", bone.parent, next(iter(children[i]), -1),
                                   siblings.get(i, -1), *transform(bone.translation, bone.rotation).flat))
    payload.extend(struct.pack("<I", 0))
    return Chunk("Skel", (1, 0), bytes(payload))


def stats(meshes, buffers, layouts, materials, lod_mask, low, high):
    body = struct.pack("<4H?bbxHBB", meshes, buffers, layouts, materials, bool(lod_mask),
                       5 if lod_mask else 0, -1 if lod_mask else 0, lod_mask, 1, 0)
    return Chunk("Modl", (1, 2), body, (box_meta(low, high),))


FLT_MAX = float(np.finfo(np.float32).max)


def rig_model(bones):
    # Native skeleton Modelbins carry the empty box sentinel (min=+FLT_MAX, max=-FLT_MAX).
    # EXPANDED_GARAGE_SKELETON_BOX=zero restores the pre-v1.4.6 zero box (bisection switch).
    if os.environ.get("EXPANDED_GARAGE_SKELETON_BOX") == "zero":
        return bundle([skeleton(bones), stats(0, 0, 0, 0, 0, (0.0,) * 3, (0.0,) * 3)])
    return bundle([skeleton(bones), stats(0, 0, 0, 0, 0, (FLT_MAX,) * 3, (-FLT_MAX,) * 3)])


def vertex_layout(index, position_only):
    semantics = ["POSITION", "NORMAL", "TEXCOORD", "TANGENT", "COLOR"][:1 if position_only else 5]
    fields = [(0, 0, 0, 13, 49)]
    if not position_only:
        fields += [(1, 0, 1, 37, 52)]
        fields += [(2, i, 1, 35, 46) for i in range(5)]
        fields += [(3, i, 1, 24, 48) for i in range(2)]
        fields += [(4, 0, 1, 28, 22)]
    payload = struct.pack("<H", len(semantics)) + b"".join(map(string, semantics))
    payload += struct.pack("<H", len(fields))
    payload += b"".join(struct.pack("<4h3i", semantic, i, slot, 0, fmt, -1, 0)
                        for semantic, i, slot, fmt, _ in fields)
    payload += struct.pack(f"<{len(fields) + 1}I", *(f[-1] for f in fields), 0 if position_only else 0x4FF)
    return Chunk("VLay", (1, 1), payload, (id_meta(index),))


@lru_cache(maxsize=1)
def native_parameters():
    """Donor MTPR blocks per shared library member (fh4-material-parameters-v1.json)."""
    record = json.loads(files(__package__).joinpath("fh4-material-parameters-v1.json").read_text(encoding="utf-8"))
    return {member: bytes.fromhex(item["mtpr"]) for member, item in record["parameters"].items()}


def material(index, name, library_path):
    path = ("Game:\\Media\\cars\\_library\\materials\\" + library_path.replace("/", "\\")).encode("ascii")
    if len(path) >= 128:
        raise ValueError(f"Material path exceeds the supported one-byte length {library_path}")
    resource = Chunk("MATI", (1, 0), bytes([len(path)]) + path,
                     (name_meta(name), metadata("ATST", b"\0\0", 2)))
    parameters = Chunk("MTPR", (2, 0), native_parameters().get(library_path.lower(), bytes(13)))
    return Chunk("MatI", (1, 0), bundle([resource, parameters]), (name_meta(name), id_meta(index)))


def buffer(kind, index, data, stride, fmt):
    if len(data) % stride:
        raise ValueError("Invalid stream length")
    payload = struct.pack("<iiHBBi", len(data) // stride, len(data), stride, 1, 0, fmt) + data
    return Chunk(kind, (1, 0), payload, (id_meta(index),))


def lod_mask(lod):
    if not 0 <= lod <= 5:
        raise ValueError(f"Unsupported FH4 LOD {lod}")
    return 3 if lod == 0 else 2 << lod


def steps(positions, normals, uv, indices):
    """Accumulate UV tangents and define a stable basis for zero-area UVs."""
    a, b, c = indices.T
    e1, e2 = positions[b] - positions[a], positions[c] - positions[a]
    u1, u2 = uv[b] - uv[a], uv[c] - uv[a]
    determinant = u1[:, 0] * u2[:, 1] - u2[:, 0] * u1[:, 1]
    inv = np.divide(1, determinant, out=np.zeros_like(determinant), where=np.abs(determinant) > 1e-12)
    tangent = (e1 * u2[:, 1:2] - e2 * u1[:, 1:2]) * inv[:, None]
    bitangent = (e2 * u1[:, 0:1] - e1 * u2[:, 0:1]) * inv[:, None]
    t, bt = np.zeros_like(positions), np.zeros_like(positions)
    for corner in (a, b, c):
        np.add.at(t, corner, tangent)
        np.add.at(bt, corner, bitangent)
    t -= np.sum(t * normals, axis=1, keepdims=True) * normals
    lengths = np.linalg.norm(t, axis=1)
    zero = lengths < 1e-10
    t[zero] = np.cross(normals[zero], np.eye(3)[np.argmin(np.abs(normals[zero]), axis=1)])
    t /= np.linalg.norm(t, axis=1, keepdims=True)
    w = np.where(np.sum(np.cross(normals, t) * bt, axis=1) >= 0, 3, 0).astype("<u4")
    fields = np.rint(np.clip((t + 1) * 511.5, 0, 1023)).astype("<u4")
    return fields[:, 0] | fields[:, 1] << 10 | fields[:, 2] << 20 | w << 30


@dataclass(frozen=True)
class Draw:
    name: str
    lod: int
    source_material: str
    target_material: str
    transparent: bool
    positions: np.ndarray
    normals: np.ndarray
    uv0: np.ndarray
    uv1: np.ndarray
    indices: np.ndarray
    origin: dict


def pack_streams(draw):
    p, n, tri = draw.positions, draw.normals, draw.indices
    if len(p) == 0 or any(not np.isfinite(v).all() for v in (p, n, draw.uv0, draw.uv1)):
        raise ValueError(f"Missing or nonfinite geometry {draw.name}")
    if len(tri) == 0 or tri.min() < 0 or tri.max() >= len(p):
        raise ValueError(f"Invalid triangle indices {draw.name}")
    lengths = np.linalg.norm(n, axis=1, keepdims=True)
    if np.any(lengths < 1e-12):
        raise ValueError(f"Zero normal in {draw.name}")
    n = n / lengths
    lo, hi = p.min(axis=0), p.max(axis=0)
    center, scale = (hi + lo) * 0.5, (hi - lo) * 0.5
    scale[scale < 1e-8] = 1
    packed_positions = np.empty((len(p), 4), dtype="<i2")
    packed_positions[:, :3] = np.rint(np.clip((p - center) / scale, -1, 1) * 32767)
    packed_positions[:, 3] = np.rint(np.clip(n[:, 0], -1, 1) * 32767)
    if not np.allclose(packed_positions[:, :3] / 32767 * scale + center, p,
                       rtol=0, atol=max(scale) / 32767 + 1e-6):
        raise ValueError(f"Position encoding exceeds quantization for {draw.name}")
    attrs = np.zeros((len(p), 36), dtype=np.uint8)
    attrs[:, 0:4] = np.rint(np.clip(n[:, 1:], -1, 1) * 32767).astype("<i2").view(np.uint8)
    transforms = []
    for i, uv in enumerate((draw.uv0, draw.uv1)):
        uv_min, uv_max = uv.min(axis=0), uv.max(axis=0)
        span = np.where(uv_max - uv_min < 1e-8, 1, uv_max - uv_min)
        encoding = np.rint(np.clip((uv - uv_min) / span, 0, 1) * 65535)
        attrs[:, 4 + 4*i:8 + 4*i] = encoding.astype("<u2").view(np.uint8)
        transforms.append((uv_min[0], span[0], uv_min[1], span[1]))
    tangent = steps(p, n, draw.uv0, tri).reshape(-1, 1).view(np.uint8)
    attrs[:, 24:28] = tangent
    attrs[:, 28:32] = tangent
    attrs[:, 32:36] = 255
    return packed_positions.tobytes(), attrs.tobytes(), transforms + [UV_IDENTITY] * 3, lo, hi, scale, center


def mesh(draw, mat_id, bone, index_start, position_offset, vb_id, transforms, low, high, scale, center, shadow,
         lod_fields=None):
    if shadow:
        material_id, layout, bucket = -1, 1, 9
    else:
        material_id, layout, bucket = mat_id, 0, 4 if draw.transparent else 17
    num_indices = draw.indices.size
    mask, low_lod = lod_fields if lod_fields else (lod_mask(draw.lod), 0)
    fields = struct.pack("<5hHBBH5BH", -1, material_id, -1, -1, bone, mask,
                         low_lod, 255, bucket, 0, 0, 0, 0, 1, 4)
    fields += struct.pack("<6ifIii", 0, 0, index_start, 0, num_indices, num_indices // 3,
                          len(draw.positions) / (num_indices // 3), len(draw.positions), layout,
                          1 if shadow else 2)
    fields += struct.pack("<iIII", -1, 0, 8, position_offset)
    if not shadow:
        fields += struct.pack("<iIII", vb_id, 1, 36, 0)
    fields += struct.pack("<3iI", -1, -1, 0, 0)
    fields += struct.pack("<28f", *(x for transform in transforms for x in transform),
                          *scale, 1.0, *center, 0.0)
    return Chunk("Mesh", (1, 9), fields, (name_meta("Shadow" if shadow else draw.name), box_meta(low, high)))


PAINT = "exterior_misc/carpaint.materialbin"


def material_key(draw):
    """Native models bind every painted surface to one MatI named `carpaint`."""
    # EXPANDED_GARAGE_PAINT_MATI=source keeps one MatI per source name (bisection switch).
    if draw.target_material.lower() == PAINT and os.environ.get("EXPANDED_GARAGE_PAINT_MATI") != "source":
        return ("carpaint", draw.target_material)
    return (draw.source_material, draw.target_material)


def assemble(draws, bones, bone=1, shadows=True):
    if not draws or not 0 <= bone < len(bones):
        raise ValueError("Modelbin needs a draw and a valid rigid bone")
    chunks = [skeleton(bones), Chunk("Mrph", (0, 0), struct.pack("<H", 1) + string("weight[0]"))]
    materials = sorted({material_key(d) for d in draws})
    ids = {entry: i for i, entry in enumerate(materials)}
    chunks.extend(material(i, name, path) for i, (name, path) in enumerate(materials))
    bindings = [vertex_layout(0, False), vertex_layout(1, True)]
    positions, indices = bytearray(), bytearray()
    mask, count = 0, 0
    minima, maxima = [], []
    for i, draw in enumerate(draws):
        pos, attr, transforms, low, high, scale, center = pack_streams(draw)
        mat_id = ids[material_key(draw)]
        vb_id = i + 1
        params = (draw, mat_id, bone, len(indices) // 4, len(positions), vb_id, transforms, low, high, scale, center)
        chunks.append(mesh(*params, shadow=False))
        count += 1
        if shadows and not draw.transparent:
            chunks.append(mesh(*params, shadow=True))
            count += 1
        positions.extend(pos)
        indices.extend(draw.indices.astype("<u4", copy=False).tobytes())
        bindings.append(buffer("VerB", vb_id, attr, 36, 37))
        mask |= lod_mask(draw.lod)
        minima.append(low)
        maxima.append(high)
    chunks += [buffer("IndB", 0, bytes(indices), 4, 42), buffer("VerB", -1, bytes(positions), 8, 13)]
    chunks += bindings
    chunks.append(stats(count, len(draws)+2, 2, len(materials), mask,
                        np.min(minima, axis=0), np.max(maxima, axis=0)))
    return bundle(chunks), mask


def proxy_model(draws):
    """Native scene/ProxyLOD.modelbin: one merged exterior mesh on every LOD with an empty `shader` MatI.

    Measured on FOR_FiestaST_14 and present in 399 of 400 native archives: a single
    <root> bone, a zero-length MatI named `shader` (no library path, no MTPR), one
    render mesh plus its shadow with LOD mask 0xFFFF and minimum LOD 30, and a box
    equal to the whole car.
    """
    if not draws:
        raise ValueError("ProxyLOD needs exterior geometry")
    offsets = np.cumsum([0] + [len(d.positions) for d in draws[:-1]])
    merged = Draw("", 0, "shader", "", False,
                  np.concatenate([d.positions for d in draws]).astype(np.float32),
                  np.concatenate([d.normals for d in draws]).astype(np.float32),
                  np.zeros((sum(len(d.positions) for d in draws), 2), dtype=np.float32),
                  np.zeros((sum(len(d.positions) for d in draws), 2), dtype=np.float32),
                  np.concatenate([d.indices + o for d, o in zip(draws, offsets)]).astype(np.uint32), {})
    bones = [Bone("<root>", -1)]
    pos, attr, transforms, low, high, scale, center = pack_streams(merged)
    shader = Chunk("MatI", (1, 0), b"", (name_meta("shader"), id_meta(0)))
    params = (merged, 0, 0, 0, 0, 1, transforms, low, high, scale, center)
    chunks = [skeleton(bones), shader,
              mesh(*params, shadow=False, lod_fields=(0xFFFF, 30)),
              mesh(*params, shadow=True, lod_fields=(0xFFFF, 30)),
              buffer("IndB", 0, merged.indices.astype("<u4").tobytes(), 4, 42),
              buffer("VerB", -1, pos, 8, 13), vertex_layout(0, False), vertex_layout(1, True),
              buffer("VerB", 1, attr, 36, 37),
              # Native ProxyLOD Modl: counts, then zero LOD-range flags and mask 0xFFFF.
              Chunk("Modl", (1, 2), struct.pack("<4H?bbxHBB", 2, 3, 2, 1, False, 0, 0, 0xFFFF, 1, 0),
                    (box_meta(low, high),))]
    return bundle(chunks)
