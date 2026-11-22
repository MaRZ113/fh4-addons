"""Write FH4 Granny clips and parameter-driven GSF accessory graphs."""

from dataclasses import dataclass
import math
import struct

from .fh4_runtime_rig import (
    ARRAY, ART, FLOAT, INT, PTR_ARRAY, REF, ROOT, SKELETON_TAG, STRING, TRANSFORM, TYPE, VARIANT,
    SectionBuilder, granny_container, read_raw_sections,
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


GSF_SCHEMA = {
    "Character": ((INT, "NumUniqueTokenized"), (VARIANT, "StateMachine"),
                  (PTR_ARRAY, "AnimationSlots", 0, "Slot"), (PTR_ARRAY, "AnimationSets", 0, "Set"),
                  (STRING, "ModelNameHint"), (INT, "ModelIndexHint"),
                  *f(VARIANT, "EditorData ExtendedData")),
    "Slot": ((STRING, "Name"), (INT, "SlotIndex")),
    "Set": ((STRING, "Name"), (PTR_ARRAY, "SourceFileReferences", 0, "Source"),
            (ARRAY, "AnimationSpecs", 0, "Spec")),
    "Source": ((STRING, "SourceFilename"), (INT, "ExpectedAnimCount"), (U32, "AnimCRC"), (EMPTY_REF, "SourceInfo")),
    "Spec": ((REF, "SourceReference", 0, "Source"), (INT, "AnimationIndex"), (STRING, "ExpectedName")),
    "Node": ((STRING, "Name"), (INT, "NameIsDefault"), *f(FLOAT, "EditingPosX EditingPosY"),
             (ARRAY, "InputEdges", 0, "Edge"), (ARRAY, "OutputEdges", 0, "Edge"),
             (ARRAY, "Transitions", 0, "Variant"), (VARIANT, "ParentToken"),
             (STRING, "UserKeyValues"), (VARIANT, "AnyTransition")),
    "Edge": ((STRING, "EdgeName"), *f(INT, "EdgeType IsInternal ForwardEdge"),
             (VARIANT, "OtherNode"), (INT, "OtherNodeEdge")),
    "Variant": ((VARIANT, "Variant"),),
    "Container": ((ARRAY, "ChildTokens", 0, "Variant"), *f(INT, "EditingPosX EditingPosY"), (ARRAY, "Comments")),
    "state_machineExtra": (*f(INT, "StartState UseLastActive"), (ARRAY, "OutputMaps", 0, "OutputMap"),
                           (ARRAY, "Conditionals", 0, "Variant"),
                           *f(INT, "AllowAnyTransition AllowTransitionExit AlwaysUseAnimEvents"),
                           (FLOAT, "AnyTransitionDuration"), (VARIANT, "DynamicTransition"), (ARRAY, "MorphMeshNames")),
    "OutputMap": ((ARRAY, "Entries", 0, "Integer"),),
    "Integer": ((INT, "Int32"),),
    "blend_graphExtra": (*f(INT, "DummyMember AllowAnyTransition AllowTransitionExit"), (FLOAT, "AnyTransitionDuration")),
    "rest_poseExtra": ((INT, "Dummy"),),
    "subtree_sampleExtra": ((INT, "DummyMember"),),
    "timeselectExtra": ((INT, "TimeIsUnitRelative"),),
    "linear_equationExtra": f(FLOAT, "AValue BValue"),
    "linear_remapExtra": ((ARRAY, "RemapPoints", 0, "RemapPoint"),),
    "RemapPoint": f(U16, "x y"),
    "anim_sourceExtra": ((REF, "AnimationSlot", 0, "Slot"), (INT, "ClampedLooping"), (FLOAT, "Speed"),
                         *f(ARRAY, "EventEdges ScalarEdges MorphEdges"),
                         *f(INT, "AllowAnyTransition AllowTransitionExit"), (FLOAT, "AnyTransitionDuration")),
    "parametersExtra": ((ARRAY, "Parameters", 0, "Parameter"), (INT, "ClampToInts")),
    "Parameter": (*f(FLOAT, "MinValue MinRandomValue MaxValue DefaultValue MaxSpeed Accel"), *f(INT, "Random Type ClampToInts")),
}
CONTAINER_ROLES = {"state_machine", "blend_graph"}
for role in ("state_machine", "blend_graph", "rest_pose", "subtree_sample", "timeselect", "linear_equation",
             "linear_remap", "anim_source", "parameters"):
    GSF_SCHEMA[role] = ((U32, "ContextUID_" + role), (REF, "nodeToken", 0, "Node"),
                        *(((REF, "containerToken", 0, "Container"),) if role in CONTAINER_ROLES else ()),
                        (REF, role + "Token", 0, role + "Extra"))


# Invariant per-role animation-list CRCs in Golf, Civic and Porsche GSF sets. Individual clip bytes differ.
ROLE_CRCS = {"fuel": 1977315228, "speedometer": 3604870771, "tachometer": 3804047908}


def gauge_graph(gauges):
    """Connect native gauge parameters, remaps and clip slots to separate bone subtrees."""
    next_uid = 3

    def node(kind, name, **extra):
        nonlocal next_uid
        values = {"ContextUID_" + kind: next_uid,
                  "nodeToken": record("Node", Name=name),
                  kind + "Token": record(kind + "Extra", **extra)}
        next_uid += 1
        if kind in CONTAINER_ROLES:
            values["containerToken"] = record("Container")
        return record(kind, **values)

    def edge(name, kind=0, target=None, index=-1, *, forward=-1):
        args = {"EdgeName": name, "EdgeType": kind, "ForwardEdge": forward, "OtherNodeEdge": index}
        if target is not None:
            args["OtherNode"] = target
        return record("Edge", **args)

    def connect(item, *, inputs=(), outputs=()):
        item.values["nodeToken"].values.update(InputEdges=inputs, OutputEdges=outputs)
        return item

    def children(parent, children):
        parent.values["containerToken"].values["ChildTokens"] = [record("Variant", Variant=c) for c in children]
        for child in children:
            child.values["nodeToken"].values["ParentToken"] = parent

    root = node("state_machine", "CharacterRoot", StartState=0, AnyTransitionDuration=0.25,
                OutputMaps=[record("OutputMap", Entries=[record("Integer", Int32=-1)]),
                            record("OutputMap", Entries=[record("Integer", Int32=-1)])])
    connect(root, inputs=[edge("Pose Output", forward=0), edge("AudioEvent", 3, forward=1)],
            outputs=[edge("Pose Output", forward=0), edge("AudioEvent", 3, forward=1)])
    blend = node("blend_graph", "Pose", AnyTransitionDuration=0.25)
    # Native order of the gauges parameter node (ACU_RSXTypeS_02): tach, speed, fuel.
    parameter_ids = ("tach", "speed", "fuel")
    by_parameter = {g["input"]:g for g in gauges}
    parameters = connect(node("parameters", "gauges", Parameters=[
        record("Parameter", MinRandomValue=1., MaxValue=by_parameter.get(p, {}).get("input_max", 1.))
        for p in parameter_ids]), outputs=[edge(p, 1, forward=0) for p in parameter_ids])
    rest = connect(node("rest_pose", "Rest Pose"), outputs=[edge("Pose", forward=0)])
    combined = node("subtree_sample", "Subtree Sample")
    # Nodes read parameters only through their container's input edges: the blend graph takes each
    # parameter from the sibling parameters node, and the remaps inside reference the blend graph's
    # input edge (native ACU_RSXTypeS_02 gauges graph). A remap pointing straight at the parameters node
    # in another container never received a value, so the needles stood still.
    connect(blend, inputs=[edge("Pose", target=combined, index=0, forward=0)]
            + [edge(p, 1, parameters, k) for k, p in enumerate(parameter_ids)],
            outputs=[edge("Pose", forward=0)])
    inputs = [edge("BasePose", target=rest, index=0)]
    actors = [combined, rest]
    slots, sources, specs = [], [], []
    for i, gauge in enumerate(gauges):
        name, bone = gauge["clip"], gauge["bone"]
        slot = record("Slot", Name=name, SlotIndex=i)
        slots.append(slot)
        source = record("Source", SourceFilename=".\\" + name + ".gr2", ExpectedAnimCount=1, AnimCRC=ROLE_CRCS[name])
        sources.append(source)
        specs.append(record("Spec", SourceReference=source, AnimationIndex=0, ExpectedName=name))
        control = node("anim_source", name, AnimationSlot=slot, ClampedLooping=1, Speed=1., AnyTransitionDuration=.25)
        connect(control, outputs=[edge("Animation", forward=0)])
        remap = node("linear_remap", name + "_Remap", RemapPoints=[
            record("RemapPoint", x=x, y=y) for x,y in gauge["packed_remap"]])
        connect(remap, inputs=[edge("Input", 1, blend, 1 + parameter_ids.index(gauge["input"]))],
                outputs=[edge("Output", 1, forward=0)])
        selection = node("timeselect", name + "_TimeSelect", TimeIsUnitRelative=1)
        connect(selection, inputs=[edge("Time", 1, remap, 0), edge("Pose", 0, control, 0)],
                outputs=[edge("Pose", forward=0)])
        actors.extend((selection, remap, control))
        inputs.append(edge(bone, target=selection, index=0))
    connect(combined, inputs=inputs, outputs=[edge("Result", forward=0)])
    children(blend, actors)
    children(root, [blend, parameters])
    # Output maps index the children of the root state machine.
    for mapping in root.values["state_machineToken"].values["OutputMaps"]:
        mapping.values["Entries"].append(record("Integer", Int32=-1))
    return ObjectCompiler(GSF_SCHEMA).finish(
        record("Character", NumUniqueTokenized=next_uid, StateMachine=root, AnimationSlots=slots,
               AnimationSets=[record("Set", Name="Base", SourceFileReferences=sources, AnimationSpecs=specs)]),
        GSF_TAG)


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
    if (rotations[0] != [0,0,0,1] or rotations[-1] == rotations[0] or
            any(not math.isclose(sum(a*a for a in q),1.,abs_tol=1e-5) for q in rotations)):
        raise ValueError("Granny motion has invalid rotation controls")
    pivot = reader.fields(*reader.fields(*track["PositionCurve"])["CurveData"])["Controls"]
    if not all(map(math.isfinite,pivot)):
        raise ValueError("Invalid Granny clip bind position")
    return track["Name"],pivot


def verify_gauge_graph(blob, assets, rig, bound_names):
    reader = RawObjects(blob,GSF_TAG,GSF_SCHEMA)
    char = reader.fields("Character",0)
    if not char["AnimationSlots"] or len(char["AnimationSets"])!=1:
        raise ValueError("GSF does not contain a non-empty native animation set")
    clip_set = reader.fields(*char["AnimationSets"][0])
    slots = [reader.fields(*ref) for ref in char["AnimationSlots"]]
    specs = [reader.fields(*ref) for ref in clip_set["AnimationSpecs"]]
    refs = clip_set["SourceFileReferences"]
    if clip_set["Name"]!="Base" or not len(slots)==len(specs)==len(refs):
        raise ValueError("Unbalanced GSF slots, clips and sources")
    target = {}
    for i,(slot,spec) in enumerate(zip(slots,specs,strict=True)):
        name = slot["Name"]
        if (name not in ROLE_CRCS or slot["SlotIndex"] != i or spec["ExpectedName"] != name
                or spec["SourceReference"] != refs[i] or spec["AnimationIndex"]!=0):
            raise ValueError("GSF slot to animation mapping is inconsistent")
        source = reader.fields(*refs[i])
        if (source["SourceFilename"],source["ExpectedAnimCount"],source["AnimCRC"]) != (
                ".\\"+name+".gr2",1,ROLE_CRCS[name]):
            raise ValueError("GSF source file reference disagrees with its native role")
        path = f"animations/{name}.gr2"
        bone,pivot = verify_clip(assets(path),name)
        if bone not in bound_names or bone not in rig or tuple(rig[bone]["local_m"]) != pivot:
            raise ValueError("GSF clip does not target its CarScene bone and bind")
        target[name] = (bone,path)
    seen, clip_nodes, live_edges = set(), set(), set()
    def walk(token,parent=None):
        node_type,_ = token
        row = reader.fields(*token)
        uid = row["ContextUID_"+node_type]
        if uid in seen:
            raise ValueError("Reused GSF context identifier")
        seen.add(uid)
        node = reader.fields(*row["nodeToken"])
        if node.get("ParentToken") != parent:
            raise ValueError("GSF node parent does not match its container")
        for ref in node["InputEdges"]:
            edge = reader.fields(*ref)
            if neighbor := edge.get("OtherNode"):
                other = reader.fields(*reader.fields(*neighbor)["nodeToken"])
                j = edge["OtherNodeEdge"]
                # A reference to the enclosing container reads that container's input edge (native rule).
                pool = other["InputEdges"] if neighbor == parent else other["OutputEdges"]
                if not 0<=j<len(pool) or reader.fields(*pool[j])["EdgeType"]!=edge["EdgeType"]:
                    raise ValueError("GSF input edge does not resolve a compatible output")
                if node_type=="subtree_sample" and neighbor[0]=="timeselect":
                    live_edges.add(edge["EdgeName"])
        if node_type=="anim_source":
            source = reader.fields(*row["anim_sourceToken"])
            clip_nodes.add(reader.fields(*source["AnimationSlot"])["Name"])
        if container:=row.get("containerToken"):
            for child in reader.fields(*container)["ChildTokens"]:
                walk(reader.fields(*child)["Variant"],token)
    if char["StateMachine"][0]!="state_machine":
        raise ValueError("GSF has no root state machine")
    walk(char["StateMachine"])
    if max(seen)+1 != char["NumUniqueTokenized"] or clip_nodes != target.keys() or live_edges != {t[0] for t in target.values()}:
        raise ValueError("GSF graph does not connect all authored runtime clips")
    return {path for _,path in target.values()}
