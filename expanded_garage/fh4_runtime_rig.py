"""Author and read back the observed FH4 Granny skeleton subset."""

from dataclasses import dataclass
import math
import struct
import zlib
import numpy as np

from .fh4_model import Bone, IDENTITY, transform


MAGIC = bytes.fromhex("e59b495e6f631f141e13eba990beedc4")
HEADER_SIZE = 104
SECTION = struct.Struct("<11I")
FIXUP = struct.Struct("<3I")
TYPE = struct.Struct("<IQQIIIIQ")
SKELETON_TAG = 0x80000039
SECTION_COUNT = 8
REF, ARRAY, PTR_ARRAY, VARIANT, STRING, TRANSFORM, FLOAT, INT = (2, 3, 4, 5, 8, 9, 10, 19)
ART = (
    (STRING, "FromArtToolName"), (INT, "ArtToolMajorRevision"), (INT, "ArtToolMinorRevision"),
    (INT, "ArtToolPointerSize"), (FLOAT, "UnitsPerMeter"), (FLOAT, "Origin", 3), (FLOAT, "RightVector", 3),
    (FLOAT, "UpVector", 3), (FLOAT, "BackVector", 3), (VARIANT, "ExtendedData"),
)
BONE = ((STRING, "Name"), (INT, "ParentIndex"), (TRANSFORM, "Transform"),
        (FLOAT, "InverseWorldTransform", 16), (FLOAT, "LODError"), (VARIANT, "ExtendedData"))
SKELETON = ((STRING, "Name"), (ARRAY, "Bones", 0, "Bone"), (INT, "LODType"), (VARIANT, "ExtendedData"))
MODEL = ((STRING, "Name"), (REF, "Skeleton", 0, "Skeleton"), (TRANSFORM, "InitialPlacement"),
         (ARRAY, "MeshBindings"), (VARIANT, "ExtendedData"))
ROOT = ((REF, "ArtToolInfo", 0, "Art"), (REF, "ExporterInfo"), (STRING, "FromFileName"),
        (PTR_ARRAY, "Textures"), (PTR_ARRAY, "Materials"), (PTR_ARRAY, "Skeletons", 0, "Skeleton"),
        (PTR_ARRAY, "VertexDatas"), (PTR_ARRAY, "TriTopologies"), (PTR_ARRAY, "Meshes"),
        (PTR_ARRAY, "Models", 0, "Model"), (PTR_ARRAY, "TrackGroups"),
        (PTR_ARRAY, "Animations"), (VARIANT, "ExtendedData"))
BONE_SIZE = 164
# Granny samples only bones whose LODError reaches the allowed error of the current animation LOD
# (bones sorted by descending error). Native car skeletons are LODType 1 (estimated) with errors from
# 81 (root) down to ~3.7; a zero error left every bone below the threshold, so the gauge graph never
# posed the needles. One constant error keeps the required descending order.
SKELETON_LOD_TYPE = 1
BONE_LOD_ERROR = 100.0


class SectionBuilder:
    def __init__(self):
        self.raw = bytearray()
        self.relocations = []
        self.strings = {}

    def reserve(self, size):
        start = len(self.raw)
        self.raw.extend(bytes(size))
        return start

    def string(self, name):
        if name not in self.strings:
            self.strings[name] = len(self.raw)
            self.raw.extend(name.encode("ascii") + b"\0")
        return self.strings[name]

    def pointer(self, source, section, target):
        self.relocations.append((source, section, target))


def granny_container(main, types, root_type, tag):
    output = bytearray(HEADER_SIZE + SECTION_COUNT * SECTION.size)
    output[:16] = MAGIC
    struct.pack_into("<I", output, 16, len(output))
    struct.pack_into("<10I", output, 32, 7, 0, 0, 72, SECTION_COUNT, 6, root_type, 0, 0, tag)
    sections = {0: main, 6: types}
    for index, entry in sections.items():
        output.extend(bytes(-len(output) % 4))
        offset = len(output)
        output.extend(entry.raw)
        output.extend(bytes(-len(output) % 4))
        fixup = len(output)
        for info in sorted(entry.relocations):
            output.extend(FIXUP.pack(*info))
        SECTION.pack_into(output, HEADER_SIZE + index * SECTION.size, 0, offset,
                          len(entry.raw), len(entry.raw), 4,
                          len(entry.raw), len(entry.raw), fixup, len(entry.relocations), len(output), 0)
    for index in set(range(SECTION_COUNT)) - sections.keys():
        SECTION.pack_into(output, HEADER_SIZE + index * SECTION.size, 0, len(output), 0, 0, 4,
                          0, 0, len(output), 0, len(output), 0)
    struct.pack_into("<II", output, 36, len(output), zlib.crc32(output[HEADER_SIZE:]))
    return bytes(output)


# Native Granny skeletons group animated accessories under helper bones that the Modelbin skeleton
# lacks (ACU_RSXTypeS_02: boneFuel/boneSpeed/boneTach/boneTemp are children of root_boneGauges). The
# native car graph samples the gauge pose as the subtree of `root_boneGauges`; without that bone the
# needles never received it. The game matches the two skeletons by bone name.
GROUP_BONES = {"root_boneGauges": ("boneFuel", "boneSpeed", "boneTach", "boneTemp")}


def granny_bones(bones):
    """The Granny rig: Modelbin bones plus identity group bones under `root` for present children."""
    from .fh4_model import Bone
    names = [b.name for b in bones]
    if len(names) < 2 or names[1] != "root":
        return list(bones)
    out, remap = list(bones[:2]), {0: 0, 1: 1}
    groups = {}
    for group, members in GROUP_BONES.items():
        if any(m in names for m in members):
            groups[group] = len(out)
            out.append(Bone(group, 1))
    owner = {m: g for g, ms in GROUP_BONES.items() if g in groups for m in ms}
    for i, bone in enumerate(bones[2:], start=2):
        remap[i] = len(out)
        parent = groups[owner[bone.name]] if bone.name in owner and bone.parent == 1 else remap[bone.parent]
        out.append(Bone(bone.name, parent, bone.translation, bone.rotation))
    return out


def modelbin_view(rig):
    """Granny readback rows with the identity group bones folded away (Modelbin bone order)."""
    keep, remap = [], {}
    for i, row in enumerate(rig):
        if row["name"] in GROUP_BONES:
            if any(row["local_m"]) or "local_rotation" in row:
                raise ValueError("Granny group bone is not an identity frame")
            remap[i] = remap[row["parent"]]
            continue
        remap[i] = len(keep)
        keep.append(dict(row, parent=remap[row["parent"]] if row["parent"] >= 0 else -1))
    return keep


def skeleton_file(alias, bones):
    bones = granny_bones(bones)
    main, types = SectionBuilder(), SectionBuilder()
    declarations = {"Root": ROOT, "Art": ART, "Skeleton": SKELETON, "Bone": BONE, "Model": MODEL}
    offsets = {name: types.reserve((len(fields) + 1) * TYPE.size) for name, fields in declarations.items()}
    for name, fields in declarations.items():
        for index, (kind, label, *args) in enumerate(fields):
            start = offsets[name] + TYPE.size * index
            width = args[0] if args else 0
            TYPE.pack_into(types.raw, start, kind, 0, 0, width, 0, 0, 0, 0)
            types.pointer(start + 4, 6, types.string(label))
            if len(args) > 1:
                types.pointer(start + 12, 6, offsets[args[1]])

    root = main.reserve(148)
    art = main.reserve(88)
    skeleton_ptrs = main.reserve(8)
    model_ptrs = main.reserve(8)
    skeleton = main.reserve(40)
    model = main.reserve(112)
    array = main.reserve(BONE_SIZE * len(bones))
    main.pointer(root, 0, art)
    main.pointer(root + 16, 0, main.string(alias + "_skeleton.gr2"))
    struct.pack_into("<I", main.raw, root + 48, 1)
    main.pointer(root + 52, 0, skeleton_ptrs)
    main.pointer(skeleton_ptrs, 0, skeleton)
    struct.pack_into("<I", main.raw, root + 96, 1)
    main.pointer(root + 100, 0, model_ptrs)
    main.pointer(model_ptrs, 0, model)
    main.pointer(art, 0, main.string("Expanded Garage"))
    struct.pack_into("<iiif12f", main.raw, art + 8, 1, 2, 64, 1.0,
                     0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, -1)
    main.pointer(skeleton, 0, main.string("GrannyRootBone"))
    struct.pack_into("<I", main.raw, skeleton + 8, len(bones))
    struct.pack_into("<i", main.raw, skeleton + 20, SKELETON_LOD_TYPE)
    main.pointer(skeleton + 12, 0, array)
    main.pointer(model, 0, main.string(alias))
    main.pointer(model + 8, 0, skeleton)
    scale = (1, 0, 0, 0, 1, 0, 0, 0, 1)
    struct.pack_into("<I3f4f9f", main.raw, model + 16, 0, 0, 0, 0, 0, 0, 0, 1, *scale)
    world = []
    for i, bone in enumerate(bones):
        if i == 0 and bone.parent != -1 or i > 0 and not 0 <= bone.parent < i:
            raise ValueError("Invalid Granny bone ancestry")
        parent = world[bone.parent] if bone.parent >= 0 else np.eye(4)
        bind = transform(bone.translation, bone.rotation) @ parent
        if not np.isfinite(bind).all():
            raise ValueError("Non-finite Granny bind position")
        world.append(bind)
        offset = array + BONE_SIZE * i
        name = "GrannyRootBone" if i == 0 else bone.name
        main.pointer(offset, 0, main.string(name))
        flags = int(any(bone.translation)) | (2 if tuple(bone.rotation) != (0, 0, 0, 1) else 0)
        struct.pack_into("<iI3f4f9f", main.raw, offset + 8, bone.parent, flags,
                         *bone.translation, *bone.rotation, *scale)
        struct.pack_into("<16f", main.raw, offset + 80, *np.linalg.inv(bind).flat)
        struct.pack_into("<f", main.raw, offset + 144, BONE_LOD_ERROR)
    return granny_container(main, types, offsets["Root"], SKELETON_TAG)


def read_raw_sections(data, type_tag):
    """Check the raw v7 section envelope before reading typed objects."""
    if data[:16] != MAGIC or len(data) < HEADER_SIZE + SECTION_COUNT * SECTION.size:
        raise ValueError("Missing Granny v7 envelope")
    magic_size, = struct.unpack_from("<I", data, 16)
    header = struct.unpack_from("<10I", data, 32)
    if magic_size != HEADER_SIZE + SECTION_COUNT * SECTION.size or header != (
            7, len(data), zlib.crc32(data[HEADER_SIZE:]), 72, SECTION_COUNT, 6, 0, 0, 0, type_tag):
        raise ValueError("Wrong native Granny header")
    sections, relocation_tables, extents = [], [], []
    for index in range(SECTION_COUNT):
        comp, offset, size, expanded, align, first16, first8, fixes, count, marshal, marshal_count = (
            SECTION.unpack_from(data, HEADER_SIZE + index * SECTION.size))
        if (comp or size != expanded or align != 4 or offset % align or not magic_size <= offset <= offset + size <= fixes
                or (first16, first8) != (size, size) or count > size // 4
                or not fixes <= fixes + count * FIXUP.size == marshal <= len(data) or marshal_count):
            raise ValueError("Invalid raw Granny skeleton section extent")
        if size:
            extents.append((offset, marshal))
        sections.append(data[offset:offset + size])
        relocation_tables.append((fixes, count))
    extents.sort()
    if any(end > start for (_, end), (start, _) in zip(extents, extents[1:])):
        raise ValueError("Overlapping Granny sections")
    relocations = {}
    for index, (fixes, count) in enumerate(relocation_tables):
        for i in range(count):
            source, target, address = FIXUP.unpack_from(data, fixes + i * FIXUP.size)
            if (source + 8 > len(sections[index]) or target >= SECTION_COUNT or address >= len(sections[target])
                    or (index, source) in relocations):
                raise ValueError("Out-of-range or duplicate Granny relocation")
            relocations[index, source] = (target, address)
    return sections, relocations


def verify_skeleton(data, alias):
    """Check section extents, relocations, schema, root links and the authored bind pose."""
    sections, relocations = read_raw_sections(data, SKELETON_TAG)

    def pointer(section, offset, expected):
        dest = relocations.get((section, offset))
        if not dest or dest[0] != expected:
            raise ValueError("Missing required Granny pointer")
        return dest[1]

    def string(section, offset):
        start = pointer(section, offset, section)
        raw = sections[section]
        end = raw.find(b"\0", start)
        if end < 0:
            raise ValueError("Unterminated Granny string")
        return raw[start:end].decode("ascii")

    def declaration(offset, fields):
        for i, (kind, name, *args) in enumerate(fields):
            field = offset + i * TYPE.size
            actual_kind, _, _, width, *_ = TYPE.unpack_from(sections[6], field)
            if (actual_kind, width) != (kind, args[0] if args else 0) or string(6, field + 4) != name:
                raise ValueError("Incompatible Granny skeleton schema")
        if TYPE.unpack_from(sections[6], offset + len(fields)*TYPE.size)[0] != 0:
            raise ValueError("Unterminated Granny skeleton schema")

    declaration(0, ROOT)
    declaration(pointer(6, 12, 6), ART)
    skeleton_type = pointer(6, 5 * TYPE.size + 12, 6)
    declaration(skeleton_type, SKELETON)
    declaration(pointer(6, skeleton_type + TYPE.size + 12, 6), BONE)
    declaration(pointer(6, 9 * TYPE.size + 12, 6), MODEL)
    main = sections[0]
    if (string(0, 16) != alias + "_skeleton.gr2"
            or struct.unpack_from("<I", main, 48)[0] != 1
            or struct.unpack_from("<I", main, 96)[0] != 1):
        raise ValueError("Wrong Granny file identity or root list cardinality")
    skel = pointer(0, pointer(0, 52, 0), 0)
    model = pointer(0, pointer(0, 100, 0), 0)
    if string(0, skel) != "GrannyRootBone" or string(0, model) != alias or pointer(0, model + 8, 0) != skel:
        raise ValueError("Granny rig model links do not resolve to the generated skeleton")
    size, = struct.unpack_from("<I", main, skel + 8)
    if struct.unpack_from("<i", main, skel + 20)[0] != SKELETON_LOD_TYPE:
        raise ValueError("Granny skeleton LOD type differs from native car rigs")
    start = pointer(0, skel + 12, 0)
    if not 1 <= size <= (len(main) - start) // BONE_SIZE:
        raise ValueError("Out-of-range Granny bone array")
    world, names, bones = [], set(), []
    for i in range(size):
        idx = start + i * BONE_SIZE
        name = string(0, idx)
        parent, flags, *values = struct.unpack_from("<iI3f4f", main, idx + 8)
        local, rotation = values[:3], values[3:]
        bad_parent = (name != "GrannyRootBone" or parent != -1) if i == 0 else not 0 <= parent < i
        flags_expected = int(any(local)) | (2 if rotation != [0,0,0,1] else 0)
        if name in names or flags != flags_expected or bad_parent or not all(map(math.isfinite, local)):
            raise ValueError("Invalid Granny bone identity, flags or parent")
        base = world[parent] if parent >= 0 else np.eye(4)
        local_matrix = transform(local, rotation)
        bind = local_matrix @ base
        inverse = struct.unpack_from("<16f", main, idx + 80)
        if not np.allclose(inverse, np.linalg.inv(bind).ravel(), atol=2e-6, rtol=0):
            raise ValueError("Granny inverse bind disagrees with hierarchy")
        error, = struct.unpack_from("<f", main, idx + 144)
        if not (math.isfinite(error) and error > 0) or bones and error > bones[-1]["lod_error"]:
            raise ValueError("Granny bone LOD errors must be positive and non-increasing")
        world.append(bind)
        names.add(name)
        row = {"name": name, "parent": parent, "local_m": local, "bind_m": bind[3,:3].tolist(),
               "lod_error": error}
        if flags & 2:
            row["local_rotation"] = rotation
        bones.append(row)
    return bones


@dataclass(frozen=True)
class NativeRuntimeRigCompiler:
    """Build the shared rest rig and its source-owned accessory motion."""

    def bind_motion(self, alias, ir, bones, points, grouped):
        from .fh4_motion import compile_motion

        return compile_motion(alias,ir,bones,points,grouped)

    def compile(self, alias, bones, components, seat):
        data = skeleton_file(alias, bones)
        readback = verify_skeleton(data, alias)
        roles = {"steering_wheel": "steering", "trunk": "trunk", "hooda": "hood",
                 "tach_min": "tachometer", "speed_min": "speedometer"}
        retained = sorted({roles[name] for name in components if name in roles})
        return data, {"gr2": {"state": "structurally_verified",
                               "member": f"scene/{alias}_skeleton.gr2", "bones": readback},
                      "gsf": {"state": "missing_dependency", "member": f"animations/{alias}.gsf",
                              "reason": "Native state graph and bound vehicle-specific clips have no qualified writer."},
                      "source_motion_roles": retained, "driver_side": seat.get("driver_side"),
                      "wheel_runtime_composition": "unverified"}
