"""Export a reviewed carbin-garage mesh from local glTF to a scratch OBJ."""

import argparse
from collections import Counter
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import struct

from fh4_gamedb import ROOT


def accessor(doc, blob, index):
    acc = doc["accessors"][index]
    view = doc["bufferViews"][acc["bufferView"]]
    component = {5123: "H", 5125: "I", 5126: "f"}[acc["componentType"]]
    count = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}[acc["type"]]
    item = struct.Struct("<" + component * count)
    offset = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = view.get("byteStride", item.size)
    end = offset + max(0, acc["count"]-1)*stride + item.size
    if (acc.get("sparse") or acc.get("normalized") or view["buffer"] != 0
            or stride < item.size or end > min(len(blob), view.get("byteOffset", 0)+view["byteLength"])):
        raise ValueError("Unsupported or invalid glTF accessor")
    return [item.unpack_from(blob, offset+i*stride) for i in range(acc["count"])]


def build(path, output, name, lod):
    doc = json.loads(path.read_text(encoding="utf-8"))
    uri = doc["buffers"][0]["uri"]
    if len(doc["buffers"]) != 1 or Path(uri).name != uri:
        raise ValueError("Expected a single sibling binary buffer")
    blob = path.with_name(uri).read_bytes()
    matches = [(i,m) for i,m in enumerate(doc["meshes"])
               if m["name"] == name and m["extras"]["carbin"]["lodKind"] == lod]
    if len(matches) != 1:
        raise ValueError("Expected one mesh with the requested name and LOD")
    index, mesh = matches[0]
    nodes = [n for n in doc["nodes"] if n.get("mesh") == index]
    if len(nodes) != 1 or set(nodes[0]) - {"mesh", "name"}:
        raise ValueError("This exporter expects the source baked in identity-node space")
    lines = [f"# FM4 local source: {name}, {lod}", f"mtllib {output.stem}.mtl", f"o {name}"]
    materials = {}
    used_positions = []
    used_uv = []
    normal_lengths = []
    triangles = 0
    head = 1
    counts = Counter()
    for p, primitive in enumerate(mesh["primitives"]):
        if primitive.get("mode", 4) != 4:
            raise ValueError("Expected indexed triangles")
        attr = primitive["attributes"]
        pos, norms, uv = (accessor(doc, blob, attr[key]) for key in ("POSITION","NORMAL","TEXCOORD_0"))
        indices = [value[0] for value in accessor(doc, blob, primitive["indices"])]
        if len(indices) % 3 or not indices or max(indices) >= min(len(pos),len(norms),len(uv)):
            raise ValueError("Invalid triangle indices")
        material = primitive["material"]
        label = doc["materials"][material]["name"]
        materials[material] = f"m{material}_{re.sub('[^a-zA-Z0-9_]', '_', label)}"
        name_in_obj = materials[material]
        lines.extend((f"g {name}_{p:02}", f"usemtl {name_in_obj}"))
        mapping = {}
        for v in dict.fromkeys(indices):
            values = pos[v] + uv[v] + norms[v]
            if not all(math.isfinite(x) for x in values):
                raise ValueError("Non-finite vertex field")
            mapping[v] = head
            head += 1
            used_positions.append(pos[v])
            used_uv.append(uv[v])
            normal_lengths.append(math.sqrt(sum(x*x for x in norms[v])))
            for key, fields in (("v",pos[v]), ("vt",uv[v]), ("vn",norms[v])):
                lines.append(key + " " + " ".join(format(i, ".9g") for i in fields))
        for i in range(0,len(indices),3):
            triangle = [mapping[v] for v in indices[i:i+3]]
            lines.append("f " + " ".join(f"{v}/{v}/{v}" for v in triangle))
        triangles += len(indices)//3
        counts[label] += len(indices)//3

    def bounds(rows):
        return [[min(col),max(col)] for col in zip(*rows)]

    output = output.resolve()
    if not output.is_relative_to(ROOT / ".scratch") or output.suffix.lower() != ".obj":
        raise ValueError("Output must be a new OBJ under .scratch")
    mtl, report_path = (output.with_suffix(suffix) for suffix in (".mtl",".json"))
    if any(p.exists() for p in (output,mtl,report_path)):
        raise ValueError("Output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines)+"\n", encoding="ascii")
    mtl.write_text("\n".join(f"newmtl {name_in_obj}\nKd 0.7 0.7 0.7\n"
                             for name_in_obj in materials.values()), encoding="ascii")
    report = {
        "mesh_index": index, "mesh": name, "lod": lod, "source": mesh["extras"]["carbin"]["sourceCarbin"],
        "vertices_exported": head-1, "triangles": triangles,
        "position_bounds_xyz": bounds(used_positions), "uv0_bounds": bounds(used_uv),
        "normal_length_bounds": [min(normal_lengths),max(normal_lengths)], "material_triangles": counts,
        "source_gltf_sha256": sha256(path.read_bytes()).hexdigest(), "source_buffer_sha256": sha256(blob).hexdigest(),
        "obj_sha256": sha256(output.read_bytes()).hexdigest(),
        "policy": "Identity transform, original winding, UV0 and normals. MTL labels only; no shader conversion.",
    }
    report_path.write_text(json.dumps(report,indent=2)+"\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gltf",type=Path)
    parser.add_argument("output",type=Path)
    parser.add_argument("--mesh",default="bumperFa")
    parser.add_argument("--lod",default="lod0")
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.gltf,args.output,args.mesh,args.lod),indent=2))
    except (OSError,ValueError,KeyError,struct.error) as error:
        parser.exit(1,f"{error}\n")


if __name__ == "__main__":
    main()
