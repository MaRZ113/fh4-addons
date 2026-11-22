"""Recover source-owned rigid motion and separate rendered parts from pose samples."""

from dataclasses import dataclass
import math

import numpy as np

from .fh4_dynamics import car_attributes
from .fh4_granny_animation import animation_clip, gauge_graph, template_slots
from .fh4_model import Bone


GAUGES = (
    ("fuel", "Fuel", "fuel", "boneFuel", "fuel"),
    ("speed", "Speed", "speedometer", "boneSpeed", "speed"),
    ("tach", "Tach", "tachometer", "boneTach", "tach"),
)


@dataclass(frozen=True)
class Attachment:
    index: int
    point: tuple
    rotation: tuple = (0., 0., 0., 1.)


def fit_rotation(first, after):
    centers = first.mean(0), after.mean(0)
    a, singular, b = np.linalg.svd((after-centers[1]).T @ (first-centers[0]))
    if singular[1] < 1e-10:
        raise ValueError("Needle source has fewer than two independent dimensions")
    reflection = np.diag([1, 1, np.linalg.det(a @ b)])
    matrix = a @ reflection @ b
    offset = centers[1] - matrix @ centers[0]
    worst = np.linalg.norm(first @ matrix.T + offset - after, axis=1).max()
    if worst > 3e-5:
        raise ValueError(f"Needle samples are not a matched rigid mesh ({worst:.6f} m)")
    return matrix, offset, float(worst)


def needle_pose(cockpit, label):
    poses = []
    for state in ("min", "mid", "max"):
        options = [d for d in cockpit.get(f"{label}_{state}", ()) if d.source_material.casefold() == "gauge_needle"]
        if not options:
            raise ValueError(f"Missing FM4 {label}_{state} needle geometry")
        lod = min(d.lod for d in options)
        poses.append([d for d in options if d.lod == lod])
    a, b, c = poses
    if not (len(a) == len(b) == len(c)):
        raise ValueError("Needle sample draw counts differ")
    for sampled in (b, c):
        if any(not np.array_equal(d.indices, e.indices) or d.positions.shape != e.positions.shape
               for d,e in zip(a,sampled,strict=True)):
            raise ValueError("Needle poses have incompatible topology")
    a, b, c = (np.concatenate([d.positions for d in ds], axis=0).astype(np.float64) for ds in poses)
    fs = [fit_rotation(a,b),fit_rotation(a,c)]
    matrix = np.vstack([np.eye(3)-r for r, _, _ in fs])
    shifts = np.concatenate([t for _,t,_ in fs])
    _, singular, vt = np.linalg.svd(matrix,full_matrices=False)
    if singular[1] < .05 or singular[2] > .020:
        raise ValueError("Needle transforms do not share a stable rotation axis")
    axis = vt[-1]
    pivot = np.linalg.lstsq(np.vstack([matrix,axis]),np.append(shifts,axis @ a.mean(0)),rcond=None)[0]
    if np.max(np.abs(matrix @ pivot - shifts)) > 8e-5 or np.linalg.norm(a.mean(0)-pivot) > .15:
        raise ValueError("Needle pivot fails original motion readback")

    def signed_angle(r):
        sine = .5 * axis @ np.array([r[2,1]-r[1,2],r[0,2]-r[2,0],r[1,0]-r[0,1]])
        return math.atan2(sine, .5*(np.trace(r)-1))

    if signed_angle(fs[0][0]) < 0:
        axis = -axis
    mid, end = (signed_angle(r) % math.tau for r,_,_ in fs)
    if not .01 < mid < end < math.tau-.01:
        raise ValueError("Needle sweep is ambiguous")
    keys = []
    for step in range(17):
        t = step/16
        angle = (2*t*mid if t <= .5 else mid + (2*t-1)*(end-mid)) * .5
        keys.append((t, (*tuple(map(float,axis*math.sin(angle))),math.cos(angle))))
    return tuple(map(float,pivot)), keys, {"axis": axis.tolist(), "sweep_degrees": math.degrees(end),
            "mid_degrees": math.degrees(mid), "max_source_fit_error_m": max(e for _,_,e in fs),
            "source": [d.origin for ds in poses for d in ds]}


def gauge_ranges(ir):
    attrs = car_attributes(ir)
    if not attrs:
        return {}
    gauges = next((n for n in attrs["xml"]["children"] if n["tag"] == "Gauges"),None)
    if not gauges:
        return {}
    return {n["tag"]: [(float(q["attributes"]["Value"]), float(q["attributes"]["Param"]))
                       for q in n["children"] if q["tag"] == "TickMark"] for n in gauges["children"]}


def parameter_remap(ticks, label):
    if len(ticks) < 2 or ticks[-1][0] <= 0 or not all(math.isfinite(v) and 0 <= p <= 1 for v,p in ticks):
        raise ValueError("A source gauge needs ordered, finite calibration ticks")
    if any(v1 <= v0 or p1 < p0 for (v0,p0),(v1,p1) in zip(ticks,ticks[1:])):
        raise ValueError("Source gauge ticks are not monotonic")
    # FM4 Speed is m/s and Tach is RPM. FH4 samples expose speed in mph and tach in thousands of RPM.
    factor = {"fuel": 1/ticks[-1][0], "speed": 1/0.44704, "tach": .001}[label]
    max_input = ticks[-1][0]*factor
    values = [(round(v*factor/max_input*65535),round(p*65535)) for v,p in ticks]
    if values[0][0] > 0:
        values.insert(0,(0,values[0][1]))
    return max_input, values


def calibrated(keys, ticks, samples=64):
    """Clip keys over the input fraction: the FM4 needle sweep `keys` (clip time -> rotation about one
    axis) sampled through the dial's tick calibration `ticks` ((input, sweep) pairs packed to 0..65535).
    The native graph then only maps input to time linearly (time = input / max)."""
    times = np.array([t for t,_ in keys])
    quats = np.array([q for _,q in keys], dtype=np.float64)
    axis = quats[np.argmax(np.linalg.norm(quats[:,:3],axis=1)),:3]
    axis = axis/np.linalg.norm(axis)
    angles = np.unwrap(2*np.arctan2(quats[:,:3] @ axis, quats[:,3]))
    tx, ty = (np.array(v, dtype=np.float64)/65535 for v in zip(*ticks))
    xs = np.unique(np.concatenate([np.linspace(0,1,samples+1), tx[(tx>0)&(tx<1)]]))
    out = []
    for x in xs:
        half = .5*float(np.interp(np.interp(x,tx,ty),times,angles))
        out.append((float(x),(*map(float,axis*math.sin(half)),math.cos(half))))
    return out


def steering_rotation(draws, pivot):
    best = min(d.lod for d in draws)
    vertices = np.concatenate([d.positions for d in draws if d.lod == best]).astype(np.float64)
    if not (np.all(vertices.min(0) <= pivot) and np.all(vertices.max(0) >= pivot)):
        raise ValueError("Authored steering hardpoint is outside source steering geometry")
    center = vertices.mean(0)
    _, singular, vh = np.linalg.svd(vertices-center,full_matrices=False)
    if singular[-1] >= .65 * singular[1]:
        raise ValueError("Source steering wheel does not have a stable rotation plane")
    z = vh[-1] * (1 if vh[-1,2] > 0 else -1)
    q = np.array((-z[1],z[0],0.,1.+z[2]))
    return tuple(map(float,q/np.linalg.norm(q))), z.tolist()


def rim_center(draws, pivot, axis):
    """Steering pivot on the rim's axis. Native steering wheels have the rim centred on the spindle
    (within 3 mm over sampled cars); FM4's authored hardpoint sat ~5 cm off it, so the wheel swung
    around off-centre. Keeps the hardpoint's depth along the axis and moves it in the rim plane to the
    centre of a circle fitted to the outermost vertices."""
    best = min(d.lod for d in draws)
    vertices = np.concatenate([d.positions for d in draws if d.lod == best]).astype(np.float64)
    pivot, axis = np.asarray(pivot, float), np.asarray(axis, float)
    u = np.cross(axis, (1.0, 0.0, 0.0) if abs(axis[0]) < .9 else (0.0, 1.0, 0.0))
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    plane = np.stack([(vertices - pivot) @ u, (vertices - pivot) @ v], 1)
    center = np.zeros(2)
    for _ in range(4):
        radius = np.linalg.norm(plane - center, axis=1)
        rim = plane[radius >= np.percentile(radius, 85)]
        a = np.c_[2 * rim, np.ones(len(rim))]
        (cx, cy, _), *_ = np.linalg.lstsq(a, (rim ** 2).sum(1), rcond=None)
        center = np.array((cx, cy))
    if np.linalg.norm(center) > 0.15:
        raise ValueError("Steering rim centre is implausibly far from the authored hardpoint")
    return tuple(map(float, pivot + center[0] * u + center[1] * v))


def compile_motion(alias, ir, bones, points, grouped):
    cockpit, exterior = grouped["cockpit"], grouped["exterior"]
    attachments, report, payloads = {}, {"gauges": [], "unresolved": []}, {}
    steering = cockpit.get("steering_wheel") or exterior.get("steering_wheel")
    if steering and 9 in points:
        try:
            rotation, axis = steering_rotation(steering, points[9].meters)
            pivot = rim_center(steering, points[9].meters, axis)
            i = len(bones)
            bones += [Bone("boneSteeringWheel",1,pivot,rotation),Bone("boneSteeringWheelSpindle",i)]
            attachments["steering_wheel"] = Attachment(i+1,pivot,rotation)
            report["steering"] = {"bone":"boneSteeringWheelSpindle", "pivot_m":pivot,
                                  "authored_pivot_m":points[9].meters,
                                  "axis_vehicle":axis, "source":points[9].source_asset, "runtime":"unverified"}
        except ValueError as error:
            report["unresolved"].append({"role":"steering","reason":str(error)})
    else:
        report["unresolved"].append({"role":"steering","reason":"Missing source steering geometry or hardpoint"})
    ranges = gauge_ranges(ir)
    for key, label, clip, bone, parameter in GAUGES:
        try:
            pivot, keys, fit = needle_pose(cockpit,key)
            max_input, ticks = parameter_remap(ranges.get(label,[]), parameter)
        except ValueError as error:
            report["unresolved"].append({"role":clip,"reason":str(error)})
            continue
        attachments[key+"_min"] = Attachment(len(bones),pivot)
        bones.append(Bone(bone,1,pivot))
        if clip not in template_slots():
            # The needle stays bound to its bone at rest; the native gauge graph has no slot for it.
            report["unresolved"].append({"role":clip,"reason":"Native gauge graph template has no slot"})
            continue
        member = f"animations/{clip}.gr2"
        payloads[member] = animation_clip(clip,bone,pivot,calibrated(keys,ticks))
        report["gauges"].append({"clip":clip,"bone":bone,"input":parameter,"input_max":max_input,
                                 "packed_remap":ticks, "pivot_m":pivot, "member":member, **fit})
    if {g["clip"] for g in report["gauges"]} == set(template_slots()):
        payloads[f"animations/{alias}.gsf"] = gauge_graph(report["gauges"])
    elif report["gauges"]:
        # The native graph template needs every one of its clips; partial clusters stay at rest.
        for gauge in report["gauges"]:
            payloads.pop(gauge["member"])
            report["unresolved"].append({"role":gauge["clip"],"reason":"Gauge cluster lacks a template clip"})
        report["gauges"] = []
    for role in ("doors","hood","trunk","wipers","driver_ik"):
        report["unresolved"].append({"role":role,"reason":"Source pivot or runtime controller remains unqualified"})
    return attachments, payloads, report
