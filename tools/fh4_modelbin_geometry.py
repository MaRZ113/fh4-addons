"""Inspect the FH4 Acura bumper bundles and mark the clone-only Modelbin."""

import argparse
from collections import Counter
from hashlib import sha256
import json
from math import hypot
from pathlib import Path
import struct
from zipfile import ZipFile

from fh4_asset_probe import MEMBER
from fh4_carbin import Reader
from fm4_bumper_obj import accessor


RACE_SHA = "6cdd50c6c0e5bc773a51836e0a20ef75e38b5c7eb5ec8cdcc7da9878e5e9f62f"
STOCK = "scene/Exterior/BumperF/bumperF_a.modelbin"


def blobs(data):
    tag, major, minor, _, header, total, count = struct.unpack_from("<IBBHIII", data)
    if (tag, major, minor, total) != (0x47727562, 1, 1, len(data)) or not 20+count*24 <= header <= total:
        raise ValueError("Expected an FH4 Acura Grub 1.1 bundle")
    for index in range(count):
        tag, major, minor, count, metadata, offset, size, _ = struct.unpack_from(
            "<IBBHIIII", data, 20+index*24,
        )
        if not header <= offset <= offset+size <= total or metadata+count*8 > header:
            raise ValueError("Invalid bundle blob bounds")
        fields = {}
        for meta in range(metadata, metadata+count*8, 8):
            kind, flags, relative = struct.unpack_from("<IHH", data, meta)
            start, size_meta = meta+relative, flags >> 4
            if start+size_meta > header:
                raise ValueError("Invalid metadata field bounds")
            fields[kind] = data[start:start+size_meta]
        identity = fields.get(0x49642020)
        yield dict(tag=tag.to_bytes(4, "big").decode("ascii"), version=(major,minor), offset=offset,
                   size=size, name=fields.get(0x4e616d65,b"").decode("ascii"),
                   id=struct.unpack("<I", identity)[0] if identity else None)


def maps(data):
    items = list(blobs(data))
    material = {item["id"]: item["name"] for item in items if item["tag"] == "MatI"}
    streams = {item["id"]: item for item in items if item["tag"] == "VerB"}
    indices, = [item for item in items if item["tag"] == "IndB"]
    layouts = [item for item in items if item["tag"] == "VLay"]
    return items, material, streams, indices, layouts


def buffer_info(data, blob):
    offset = blob["offset"]
    count, size, stride, _, _, _ = struct.unpack_from("<iiHBBi", data, offset)
    if blob["version"] != (1,0) or size != count*stride or size != blob["size"]-16:
        raise ValueError("Unexpected buffer header")
    return offset+16, count, stride


def mesh_info(data, blob, material, indices):
    offset = blob["offset"]
    if blob["version"] != (1,9):
        raise ValueError("Expected Mesh 1.9")
    mat = material.get(struct.unpack_from("<h", data, offset+2)[0])
    bone, lod, lod_min, lod_max = struct.unpack_from("<hHBB", data, offset+8)
    wide, topology = struct.unpack_from("<BH", data, offset+20)
    buffer, base, first, vertex, count, prim = struct.unpack_from("<6i", data, offset+23)
    start, length, stride = buffer_info(data, indices)
    index_bytes = 4 if wide else 2
    shift = base + first*index_bytes
    if (topology != 4 or buffer != 0 or count != 3*prim
            or not 0 <= shift <= shift+count*index_bytes <= length*stride):
        raise ValueError(f"Unsupported indexed triangle range: {blob['name']}")
    return dict(name=blob["name"], material=mat, bone=bone, lod=lod, min_lod=lod_min,
                max_lod=lod_max, start=start+shift, indices=count, index_bytes=index_bytes,
                triangles=prim, base_vertex=vertex, offset=offset, size=blob["size"])


def mark_towhook(data):
    if sha256(data).hexdigest() != RACE_SHA:
        raise ValueError("Expected the original FH4 race add-on data")
    items, materials, _, indices, _ = maps(data)
    meshes = [mesh_info(data, item, materials, indices) for item in items if item["tag"] == "Mesh"]
    marked = [m for m in meshes if m["material"] == "towhook"]
    other = [m for m in meshes if m["material"] != "towhook"]
    if not marked or not any(mesh["lod"] & 1 for mesh in marked):
        raise ValueError("Tow hook must include a near-camera mesh")
    result = bytearray(data)
    for mesh in marked:
        size = mesh["index_bytes"]
        start, end = mesh["start"], mesh["start"]+mesh["indices"]*size
        for draw in other:
            low, high = draw["start"], draw["start"]+draw["indices"]*draw["index_bytes"]
            if start < high and low < end:
                # Shadow draws reuse the same triangles in a wider range.
                if (draw["name"] != "Shadow" or draw["material"] is not None
                        or draw["base_vertex"] != mesh["base_vertex"] or draw["lod"] != mesh["lod"]
                        or draw["index_bytes"] != size or not low <= start < end <= high
                        or (start-low) % (3*size)):
                    raise ValueError("Tow-hook indices overlap an unrelated draw")
        for address in range(start, end, 3*size):
            result[address+size:address+3*size] = result[address:address+size]*2
    changed = bytes(result)
    if changed == data or list(blobs(changed)) != items:
        raise ValueError("Geometry marker did not preserve all bundle records")
    return changed, [{"mesh": m["name"], "lod_mask": m["lod"], "triangle_count": m["triangles"],
                      "index_file_offset": m["start"]} for m in marked]


def layout_elements(data, layout):
    pos = layout["offset"]
    names = []
    count, = struct.unpack_from("<H", data, pos)
    pos += 2
    for _ in range(count):
        length, = struct.unpack_from("<I", data, pos)
        pos += 4
        names.append(data[pos:pos+length].decode("ascii"))
        pos += length
    count, = struct.unpack_from("<H", data, pos)
    pos += 2
    offsets = Counter()
    formats = {10:8,13:8,24:4,28:4,31:4,34:4,35:4,36:4,37:4,41:4}
    for _ in range(count):
        name, index, slot, _input_class, fmt, declared, _step_rate = struct.unpack_from("<4h3i", data, pos)
        pos += 20
        # The Acura layouts use packed sequential fields per slot.
        if fmt not in formats or declared not in (-1, offsets[slot]):
            raise ValueError(f"Unsupported layout element: {names[name]} {fmt} offset {declared}")
        yield names[name], index, slot, fmt, offsets[slot]
        offsets[slot] += formats[fmt]


def components(data, address, fmt):
    if fmt in (10,34):
        return struct.unpack_from("<4e" if fmt == 10 else "<2e", data, address)
    if fmt in (13,37):
        raw = struct.unpack_from("<4h" if fmt == 13 else "<2h", data, address)
        return tuple(max(x/32767,-1.0) for x in raw)
    if fmt == 35:
        return tuple(x/65535 for x in struct.unpack_from("<2H",data,address))
    raise ValueError(f"Unsupported coordinate format {fmt}")


def bounds(rows):
    return [[min(axis), max(axis)] for axis in zip(*rows)]


def orientation(positions, normals, indices):
    counts = Counter()
    for a,b,c in zip(indices[::3], indices[1::3], indices[2::3]):
        ax,ay,az = (y-x for x,y in zip(positions[a],positions[b]))
        bx,by,bz = (y-x for x,y in zip(positions[a],positions[c]))
        dx,dy,dz = (ay*bz-az*by, az*bx-ax*bz, ax*by-ay*bx)
        nx,ny,nz = (sum(axis) for axis in zip(normals[a],normals[b],normals[c]))
        dot = dx*nx+dy*ny+dz*nz
        counts["aligned" if dot > 1e-14 else "opposed" if dot < -1e-14 else "degenerate_or_orthogonal"] += 1
    return dict(counts)


def stock_geometry(data):
    items, materials, streams, indices, layouts = maps(data)
    meshes = [mesh_info(data, blob, materials, indices) for blob in items if blob["tag"] == "Mesh"]
    for mesh in meshes:
        start, width, count = mesh["start"], mesh["index_bytes"], mesh["indices"]
        index = struct.unpack_from(f"<{count}{'I' if width == 4 else 'H'}", data, start)
        mesh["referenced_vertices"] = len(set(index))
        if not (mesh["lod"] & 1 and mesh["material"] is not None):
            continue
        base = mesh["offset"]
        layout, usage_count = struct.unpack_from("<ii", data, base+55)
        bindings = {}
        for i in range(usage_count):
            buffer, slot, stride, offset = struct.unpack_from("<iIII",data,base+63+16*i)
            bindings[slot] = (buffer, stride, offset)
        coordinate = {}
        position_w = {}
        normals = {}
        uv = []
        transformed_uv = []
        formats = {}
        scale = struct.unpack_from("<4f", data, base+mesh["size"]-32)
        translate = struct.unpack_from("<4f", data, base+mesh["size"]-16)
        uv_offset_u, uv_scale_u, uv_offset_v, uv_scale_v = struct.unpack_from(
            "<4f", data, base+mesh["size"]-112,
        )
        elements = sorted(layout_elements(data, layouts[layout]), key=lambda elem: elem[0] != "POSITION")
        for name, semantic, slot, fmt, element in elements:
            if name not in ("POSITION","NORMAL","TEXCOORD") or name == "TEXCOORD" and semantic != 0:
                continue
            stream, stride, shift = bindings[slot]
            stream_start, length, true_stride = buffer_info(data, streams[stream & 0xffffffff])
            if stride != true_stride:
                raise ValueError("Vertex stride mismatch")
            formats[name] = fmt
            for vertex in sorted(set(index)):
                offset = shift + (mesh["base_vertex"]+vertex)*stride+element
                if not 0 <= offset < length*stride:
                    raise ValueError("Reference outside vertex stream")
                decoded = components(data, stream_start+offset, fmt)
                if name == "POSITION":
                    coordinate[vertex] = tuple(decoded[i]*scale[i]+translate[i] for i in range(3))
                    position_w[vertex] = decoded[3]
                elif name == "NORMAL":
                    if fmt != 37:
                        raise ValueError("Expected the split FH4 SNORM normal")
                    normals[vertex] = (position_w[vertex], *decoded)
                else:
                    u,v = decoded
                    uv.append((u,v))
                    transformed_uv.append((u*uv_scale_u+uv_offset_u, v*uv_scale_v+uv_offset_v))
        mesh.update(position_bounds=bounds(coordinate.values()), raw_uv0_bounds=bounds(uv),
                    transformed_uv0_bounds=bounds(transformed_uv), format_codes=formats,
                    normal_pre_normalize_length=bounds((hypot(*n),) for n in normals.values())[0],
                    winding=orientation(coordinate,normals,index), index_min=min(index), index_max=max(index))
    return {"modelbin_sha256": sha256(data).hexdigest(), "meshes": meshes}


def anchors(data, carbin):
    identity = (1.0,0.0,0.0,0.0,0.0,1.0,0.0,0.0,0.0,0.0,1.0,0.0,0.0,0.0,0.0,1.0)
    skel, = [blob for blob in blobs(data) if blob["tag"] == "Skel"]
    offset = skel["offset"]
    count, = struct.unpack_from("<H",data,offset)
    pos = offset+2
    bones = []
    for _ in range(count):
        length, = struct.unpack_from("<I",data,pos)
        pos += 4
        name = data[pos:pos+length].decode("ascii")
        parent, _child, _sibling, *matrix = struct.unpack_from("<3h16f",data,pos+length)
        bones.append((name,parent,tuple(matrix)))
        pos += length+70
    reader = Reader(carbin)
    reader.scene()
    marker, = [(offset,name) for offset,name in reader.strings if name.endswith("\\bumperf_a.modelbin")]
    entry = struct.unpack_from("<16f",carbin,marker[0]+len(marker[1]))
    if bones[0] != ("<root>",-1,identity) or bones[1] != ("root",0,identity) or entry != identity:
        raise ValueError("The reviewed comparison expects identity root and Carbin attachment matrices")
    return {"root_and_carbin_attachment_identity": True,
            "wheel_hubs": {name: matrix[12:15] for name,parent,matrix in bones
                           if name in ("hubLF","hubRF","hubLR","hubRR") and parent == 1}}


def fm4_geometry(path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    uri = doc["buffers"][0]["uri"]
    if len(doc["buffers"]) != 1 or Path(uri).name != uri:
        raise ValueError("Expected one local sibling glTF buffer")
    data = path.with_name(uri).read_bytes()
    variations = {}
    for mesh_index, mesh in enumerate(doc["meshes"]):
        if mesh["name"] not in ("bumperFa","headlightL","headlightR"):
            continue
        lod = mesh["extras"]["carbin"]["lodKind"]
        if lod not in ("main","lod0"):
            continue
        nodes = [n for n in doc["nodes"] if n.get("mesh") == mesh_index]
        if len(nodes) != 1 or set(nodes[0]) - {"name","mesh"}:
            raise ValueError("Expected pretransformed FM4 car-space primitives")
        triangles, winding = Counter(), Counter()
        vertices = set()
        positions, uv0, uv1 = [], [], []
        normal_lengths = []
        for record in mesh["primitives"]:
            attrs = record["attributes"]
            attr = [accessor(doc,data,attrs[name]) for name in
                    ("POSITION","NORMAL","TEXCOORD_0","TEXCOORD_1")]
            indices = [x[0] for x in accessor(doc,data,record["indices"])]
            if (record.get("mode",4) != 4 or len(indices)%3 or not indices
                    or max(indices) >= min(map(len,attr))):
                raise ValueError("Unexpected FM4 primitive")
            p,n,u,v = attr
            triangles[doc["materials"][record["material"]]["name"]] += len(indices)//3
            winding.update(orientation(p,n,indices))
            for index in sorted(set(indices)):
                key = attrs["POSITION"], index
                if key not in vertices:
                    vertices.add(key)
                    positions.append(p[index])
                    normal_lengths.append(hypot(*n[index]))
                uv0.append(u[index])
                uv1.append(v[index])
        variations[f"{mesh['name']}:{lod}"] = {
            "mesh_index": mesh_index, "referenced_position_records": len(vertices),
            "triangles_by_material": dict(triangles), "position_bounds": bounds(positions),
            "uv0_bounds": bounds(uv0), "uv1_bounds": bounds(uv1), "winding": dict(winding),
            "normal_length_bounds": [min(normal_lengths),max(normal_lengths)],
            "index_component_type": sorted({doc["accessors"][p["indices"]]["componentType"]
                                            for p in mesh["primitives"]}),
        }
    return {"gltf_sha256": sha256(path.read_bytes()).hexdigest(), "buffer_sha256": sha256(data).hexdigest(),
            "variations": variations}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--fm4-gltf", type=Path)
    args = parser.parse_args()
    with ZipFile(args.archive) as archive:
        data = archive.read(STOCK)
        report = stock_geometry(data)
        report["rest_anchors"] = anchors(data,archive.read(MEMBER))
    if args.fm4_gltf:
        report["fm4"] = fm4_geometry(args.fm4_gltf)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
