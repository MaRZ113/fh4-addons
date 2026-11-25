"""Write FH4 Granny clips and parameter-driven GSF accessory graphs."""

import base64
from dataclasses import dataclass
from functools import lru_cache
import json
import math
from pathlib import Path
import struct
import zlib

from .fh4_runtime_rig import (
    ARRAY, ART, FLOAT, INT, PTR_ARRAY, REF, ROOT, SKELETON_TAG, STRING, TRANSFORM, TYPE, VARIANT,
    HEADER_SIZE, SectionBuilder, granny_container, read_raw_sections,
)


GSF_TAG = 0x806574E2
INLINE, U8, U16, U32, EMPTY_REF = 1, 12, 16, 20, 22
FORMATS = {FLOAT: "f", INT: "i", U8: "B", U16: "H", U32: "I", TRANSFORM: "I16f"}
IDENTITY_XFORM = (0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 1, 0, 0, 0, 1)


@dataclass(eq=False)
class Record:
    type: str
    values: dict


def record(kind, **values):
    return Record(kind, values)


def declaration_size(fields, schemas):
    return sum(field_size(f, schemas) for f in fields)


def field_size(field, schemas):
    kind, _, *args = field
    width = (args[0] or 1) if args else 1
    if kind in FORMATS:
        return struct.calcsize("<" + FORMATS[kind]) * width
    if kind == INLINE:
        return declaration_size(schemas[args[1]], schemas) * width
    return {REF: 8, STRING: 8, EMPTY_REF: 8, ARRAY: 12, PTR_ARRAY: 12, VARIANT: 16}[kind]


class ObjectCompiler:
    """Serialize typed objects, including cyclic GSF links, to Granny fixups."""

    def __init__(self, schemas):
        self.schemas = schemas
        self.types, self.main = SectionBuilder(), SectionBuilder()
        self.offsets = {name: self.types.reserve((len(fields) + 1) * TYPE.size)
                        for name, fields in schemas.items()}
        for name, fields in schemas.items():
            for i, (kind, label, *args) in enumerate(fields):
                pos = self.offsets[name] + i * TYPE.size
                TYPE.pack_into(self.types.raw, pos, kind, 0, 0, args[0] if args else 0, 0, 0, 0, 0)
                self.types.pointer(pos + 4, 6, self.types.string(label))
                if len(args) > 1:
                    self.types.pointer(pos + 12, 6, self.offsets[args[1]])
        self.objects = {}

    def add(self, obj, offset=None):
        if obj in self.objects:
            if offset is not None and offset != self.objects[obj]:
                raise ValueError("Granny inline object would have two different addresses")
            return self.objects[obj]
        fields = self.schemas[obj.type]
        if obj.values.keys() - {field[1] for field in fields}:
            raise ValueError(f"Unknown fields in Granny type {obj.type}")
        if offset is None:
            offset = self.main.reserve(declaration_size(fields, self.schemas))
        self.objects[obj] = pos = offset
        for f in fields:
            kind, label, *args = f
            size = field_size(f, self.schemas)
            if label in obj.values:
                self.put(kind, pos, args, obj.values[label])
            pos += size
        return offset

    def put(self, kind, pos, args, value):
        if kind in FORMATS:
            values = value if isinstance(value, (tuple, list)) else (value,)
            width = (args[0] or 1) if args else 1
            struct.pack_into("<" + FORMATS[kind]*width, self.main.raw, pos, *values)
        elif kind == STRING:
            self.main.pointer(pos, 0, self.main.string(value))
        elif kind == VARIANT:
            self.main.pointer(pos, 6, self.offsets[value.type])
            self.main.pointer(pos + 8, 0, self.add(value))
        elif kind == REF:
            if value.type != args[1]:
                raise ValueError("Unexpected Granny reference type")
            self.main.pointer(pos, 0, self.add(value))
        elif kind == INLINE:
            if value.type != args[1]:
                raise ValueError("Unexpected Granny inline type")
            self.add(value, pos)
        elif kind in (ARRAY, PTR_ARRAY):
            if not value:
                return
            if any(item.type != args[1] for item in value):
                raise ValueError("Unexpected Granny array item type")
            stride = 8 if kind == PTR_ARRAY else declaration_size(self.schemas[args[1]], self.schemas)
            first = self.main.reserve(len(value) * stride)
            struct.pack_into("<I", self.main.raw, pos, len(value))
            self.main.pointer(pos + 4, 0, first)
            if kind == ARRAY:
                # Reserve every inline address before a child can reference a later array element.
                for i, item in enumerate(value):
                    self.add(item, first + stride*i)
            else:
                for i, item in enumerate(value):
                    self.main.pointer(first + 8*i, 0, self.add(item))
        else:
            raise ValueError(f"Cannot write Granny member kind {kind}")

    def finish(self, root, tag):
        if self.add(root) or self.offsets[root.type]:
            raise ValueError("Granny root object and type must start their sections")
        return granny_container(self.main, self.types, 0, tag)


def f(kind, names):
    return tuple((kind, name) for name in names.split())


CLIP_SCHEMA = {
    "File": tuple((kind, label, 0, ref) if label in ("ArtToolInfo", "TrackGroups", "Animations") else (kind, label)
                  for kind, label, ref in ((k, n, {"ArtToolInfo":"Art", "TrackGroups":"TrackGroup",
                                                   "Animations":"Clip"}.get(n)) for k,n,*_ in ROOT)),
    "Art": ART,
    "TrackGroup": ((STRING, "Name"), (ARRAY, "VectorTracks"), (ARRAY, "TransformTracks", 0, "TransformTrack"),
                   (ARRAY, "TransformLODErrors"), (ARRAY, "TextTracks"), (TRANSFORM, "InitialPlacement"),
                   (INT, "AccumulationFlags"), (FLOAT, "LoopTranslation", 3), (REF, "PeriodicLoop"),
                   (VARIANT, "ExtendedData")),
    "TransformTrack": ((STRING, "Name"), (INT, "Flags"), (INLINE, "OrientationCurve", 0, "Curve"),
                       (INLINE, "PositionCurve", 0, "Curve"), (INLINE, "ScaleShearCurve", 0, "Curve")),
    "Curve": ((VARIANT, "CurveData"),),
    "CurveHeader": f(U8, "Format Degree"),
    "DaK32fC32f": ((INLINE, "CurveDataHeader_DaK32fC32f", 0, "CurveHeader"), (U16, "Padding"),
                  (ARRAY, "Knots", 0, "Float"), (ARRAY, "Controls", 0, "Float")),
    "D3Constant32f": ((INLINE, "CurveDataHeader_D3Constant32f", 0, "CurveHeader"),
                      (U16, "Padding"), (FLOAT, "Controls", 3)),
    "DaIdentity": ((INLINE, "CurveDataHeader_DaIdentity", 0, "CurveHeader"), (U16, "Dimension")),
    "Float": ((FLOAT, "Real32"),),
    "Clip": ((STRING, "Name"), *f(FLOAT, "Duration TimeStep Oversampling"),
             (PTR_ARRAY, "TrackGroups", 0, "TrackGroup"), *f(INT, "DefaultLoopCount Flags"),
             (VARIANT, "ExtendedData")),
}


def animation_clip(name, bone, pivot, keys):
    """Write parent-local motion with source rotations at normalized sample times."""
    def curve(kind, format_number, degree=0, **values):
        data = record(kind, **{"CurveDataHeader_" + kind: record("CurveHeader", Format=format_number, Degree=degree)},
                      **values)
        return record("Curve", CurveData=data)

    def floats(values):
        return [record("Float", Real32=x) for x in values]

    art = record("Art", FromArtToolName="Expanded Garage", ArtToolMajorRevision=1, ArtToolMinorRevision=3,
                 ArtToolPointerSize=64, UnitsPerMeter=1.0, Origin=(0., 0., 0.),
                 RightVector=(1.,0.,0.), UpVector=(0.,1.,0.), BackVector=(0.,0.,-1.))
    track = record("TransformTrack", Name=bone, Flags=0,
                   OrientationCurve=curve("DaK32fC32f", 1, 1, Knots=floats([t for t, _ in keys]),
                                          Controls=floats([x for _, q in keys for x in q])),
                   PositionCurve=curve("D3Constant32f", 4, Controls=pivot),
                   ScaleShearCurve=curve("DaIdentity", 2, Dimension=9))
    group = record("TrackGroup", Name="GrannyRootBone", TransformTracks=[track],
                   InitialPlacement=IDENTITY_XFORM, AccumulationFlags=6)
    clip = record("Clip", Name=name, Duration=1.0, TimeStep=1.0/60.0, Oversampling=1.,
                  TrackGroups=[group], DefaultLoopCount=1, Flags=1)
    return ObjectCompiler(CLIP_SCHEMA).finish(
        record("File", ArtToolInfo=art, FromFileName=name + ".gr2", TrackGroups=[group], Animations=[clip]),
        SKELETON_TAG)


GAUGE_GRAPH = Path(__file__).with_name("fh4-gauge-graph-v1.json")


@lru_cache(maxsize=None)
def gauge_template():
    """Native gauges-only GSF (see research/extract_gauge_graph.py) and the offsets of its numbers."""
    record = json.loads(GAUGE_GRAPH.read_text(encoding="utf-8"))
    return base64.b64decode(record["gsf"]), record


def gauge_graph(gauges):
    """Native analog-gauge graph with the car's input ranges.

    Native graphs drive gauges only inside LODSelect (InRace_Focus / Autovista states chosen from the
    GameInputs AnimationLOD parameter); a minimal graph with the gauges under the root never moved its
    needles. The template's Linear Eqn. nodes give clip time = input / max, and the dial's own
    calibration is baked into each clip, so only AValue and the parameter MaxValue change."""
    data, record = gauge_template()
    out = bytearray(data)
    ranges = {g["input"]: g["input_max"] for g in gauges}
    if set(ranges) != set(record["parameters"]):
        raise ValueError(f"Gauge graph template animates {sorted(record['parameters'])}, not {sorted(ranges)}")
    for name, offsets in record["parameters"].items():
        maximum = float(ranges[name])
        if not (math.isfinite(maximum) and maximum > 0):
            raise ValueError(f"Invalid {name} gauge range")
        struct.pack_into("<f", out, offsets["max_value"], maximum)
        for offset in offsets["a_value"]:
            struct.pack_into("<f", out, offset, 1.0 / maximum)
    struct.pack_into("<I", out, 40, zlib.crc32(out[HEADER_SIZE:]))
    return bytes(out)


def template_slots():
    """Animated clip names of the gauge graph template, in slot order."""
    return list(gauge_template()[1]["slots"])


class RawObjects:
    """Read the authored graph by its wire declarations and resolved relocations."""

    def __init__(self, blob, tag, schema):
        self.sections, self.pointers = read_raw_sections(blob, tag)
        self.schema = schema
        self.type_offsets = {}
        pos = 0
        for name, declaration in schema.items():
            self.type_offsets[name] = pos
            for kind, label, *args in declaration:
                actual, _, _, width, *_ = TYPE.unpack_from(self.sections[6],pos)
                string = self.pointer(6,pos+4,6)
                if (actual,width,self.string(6,string)) != (kind,args[0] if args else 0,label):
                    raise ValueError("Unexpected Granny type declaration")
                pos += TYPE.size
            if TYPE.unpack_from(self.sections[6],pos)[0]:
                raise ValueError("Unterminated Granny type declaration")
            pos += TYPE.size
        self.type_names = {offset:name for name,offset in self.type_offsets.items()}
        for name,declaration in schema.items():
            for i,field in enumerate(declaration):
                loc = self.type_offsets[name] + i*TYPE.size + 12
                if len(field) == 4 and self.pointer(6,loc,6) != self.type_offsets[field[3]]:
                    raise ValueError("Wrong Granny declaration reference")

    def pointer(self, section, pos, target, optional=False):
        value = self.pointers.get((section,pos))
        if value is None and optional:
            return None
        if value is None or value[0] != target:
            raise ValueError("Granny relocation is missing or targets the wrong section")
        return value[1]

    def string(self, section, pos):
        data = self.sections[section]
        return data[pos:data.index(0,pos)].decode("ascii")

    def fields(self, name, pos):
        out = {}
        data = self.sections[0]
        for f in self.schema[name]:
            kind,label,*args = f
            try:
                if kind in FORMATS:
                    width = (args[0] or 1) if args else 1
                    values = struct.unpack_from("<"+FORMATS[kind]*width,data,pos)
                    out[label] = values[0] if len(values)==1 else values
                elif kind == STRING:
                    at = self.pointer(0,pos,0,True)
                    out[label] = self.string(0,at) if at is not None else None
                elif kind == VARIANT:
                    schema = self.pointer(0,pos,6,True)
                    target = self.pointer(0,pos+8,0,True)
                    if (schema is None) != (target is None):
                        raise ValueError("Incomplete Granny variant")
                    if schema is not None:
                        out[label] = (self.type_names[schema],target)
                elif kind == REF:
                    target = self.pointer(0,pos,0,True)
                    if target is not None:
                        out[label] = (args[1],target)
                elif kind == INLINE:
                    out[label] = (args[1],pos)
                elif kind in (ARRAY,PTR_ARRAY):
                    count, = struct.unpack_from("<I",data,pos)
                    at = self.pointer(0,pos+4,0,not count)
                    if not count:
                        out[label] = []
                    else:
                        stride = 8 if kind == PTR_ARRAY else declaration_size(self.schema[args[1]],self.schema)
                        if count > (len(data)-at)//stride:
                            raise ValueError("Granny array exceeds its section")
                        out[label] = [(args[1],self.pointer(0,at+i*stride,0) if kind==PTR_ARRAY else at+i*stride)
                                      for i in range(count)]
            except (IndexError,KeyError,struct.error) as error:
                raise ValueError(f"Invalid Granny object {name}.{label}") from error
            pos += field_size(f,self.schema)
        return out


def verify_clip(blob, clip_name):
    reader = RawObjects(blob,SKELETON_TAG,CLIP_SCHEMA)
    root = reader.fields("File",0)
    track_group, = root["TrackGroups"]
    clip = reader.fields(*root["Animations"][0])
    if (clip["Name"],clip["Duration"],clip["TrackGroups"],root["FromFileName"]) != (
            clip_name,1.,[track_group],clip_name+".gr2") or len(root["Animations"]) != 1:
        raise ValueError("Granny clip identity did not resolve")
    group = reader.fields(*track_group)
    track = reader.fields(*group["TransformTracks"][0])
    if group["Name"] != "GrannyRootBone" or len(group["TransformTracks"]) != 1:
        raise ValueError("Wrong Granny animation target")
    curve = reader.fields(*reader.fields(*track["OrientationCurve"])["CurveData"])
    knots, controls = [[reader.fields(*ref)["Real32"] for ref in curve[key]] for key in ("Knots","Controls")]
    if len(knots) < 3 or len(controls) != 4*len(knots) or knots[0] != 0 or knots[-1] != 1 or (
            any(a>=b for a,b in zip(knots,knots[1:]))):
        raise ValueError("Invalid Granny orientation curve dimensions")
    rotations = [controls[i:i+4] for i in range(0,len(controls),4)]
    # The dial calibration is baked into the clip: zero input may already sit off the rest pose.
    if (rotations[-1] == rotations[0] or
            any(not math.isclose(sum(a*a for a in q),1.,abs_tol=1e-5) for q in rotations)):
        raise ValueError("Granny motion has invalid rotation controls")
    pivot = reader.fields(*reader.fields(*track["PositionCurve"])["CurveData"])["Controls"]
    if not all(map(math.isfinite,pivot)):
        raise ValueError("Invalid Granny clip bind position")
    return track["Name"],pivot


def verify_gauge_graph(blob, assets, rig, bound_names):
    """The template graph with only its gauge numbers changed, and a clip per slot on a bound bone."""
    data, record = gauge_template()
    if len(blob) != len(data) or zlib.crc32(blob[HEADER_SIZE:]) != struct.unpack_from("<I", blob, 40)[0]:
        raise ValueError("GSF is not the native gauge graph template")
    variable = {40, 41, 42, 43}
    for name, offsets in record["parameters"].items():
        maximum, = struct.unpack_from("<f", blob, offsets["max_value"])
        if not (math.isfinite(maximum) and maximum > 0):
            raise ValueError(f"Invalid {name} gauge range")
        for offset in offsets["a_value"]:
            if not math.isclose(struct.unpack_from("<f", blob, offset)[0], 1.0 / maximum, rel_tol=1e-6):
                raise ValueError(f"GSF {name} equation disagrees with its range")
            variable.update(range(offset, offset + 4))
        variable.update(range(offsets["max_value"], offsets["max_value"] + 4))
    if any(x != y for i, (x, y) in enumerate(zip(blob, data)) if i not in variable):
        raise ValueError("GSF differs from the native gauge graph template outside its gauge numbers")
    used = set()
    for name in record["slots"]:
        path = f"animations/{name}.gr2"
        bone, pivot = verify_clip(assets(path), name)
        # Placeholder slots (fh4_motion static gauges: no needle to drive) sit on an unbound bone at the origin.
        placeholder = bone not in bound_names and tuple(pivot) == (0.0, 0.0, 0.0)
        if (bone not in bound_names and not placeholder) or bone not in rig or tuple(rig[bone]["local_m"]) != pivot:
            raise ValueError("GSF clip does not target its CarScene bone and bind")
        used.add(path)
    return used
