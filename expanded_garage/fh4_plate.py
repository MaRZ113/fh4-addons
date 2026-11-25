"""Native licence plate and plate lamp in the FM4 car's plate recess.

FM4 cars carry no plate (the game draws none on player cars). FH4 cars draw theirs from native models
scene/Exterior/Plate/Plate{EU,US}_a.modelbin and PlateLight{EU,US}_a.modelbin: car-space geometry on library
materials only (plates/plateUK_*, plateUS_*, plate_light; the game prints the registration on the atlas MatI),
bound in the Carbin with an identity transform (FOR_FiestaST_14: body group, draw 1, LOD mask 127, category
Plate). The owner's ACU_IntegraR_01 port (car 3477) shows one with a lit plate lamp; ours had none.

The recess is found on the rear of the stock exterior: the rearmost rear-facing surface is rasterised into a
5 mm depth map and plate-sized rectangles centred on the car's axis are fitted with a plane; the flattest one
wins (EU 520x110 mm or US 305x152 mm, picking the donor model). The donor models are moved there by shifting
each mesh's quantisation centre and the boxes (vertex data and normals stay byte for byte), so the light rig
role `plate` (fh4-light-rig-v1.json, `^platelight`) places the plate lamp light inside the moved lamp model.
"""

import os
import struct
from pathlib import Path
from zipfile import ZipFile

import numpy as np

STEP = 0.005
GRID_X, GRID_Y = (-0.6, 0.6), (0.0, 1.5)
SIZES = {"EU": (0.52, 0.11), "US": (0.305, 0.152)}
DONORS = {"EU": ("FOR_FiestaST_14", "PlateEU_a", "PlateLightEU_a"),
          "US": ("ACU_IntegraR_01", "PlateUS_a", "PlatelightUS_a")}
FOLDER = "scene/Exterior/Plate/"
MAX_RMS_M = 0.006      # flatness of an accepted recess
REAR_DEPTH_M = 0.6     # the tail section searched, from the rearmost point
RING = 6               # cells (3 cm) of surrounding body compared with the recess
MIN_RECESS_M = 0.004   # a recessed candidate beats any flat one
PROBE = (0.20, 0.07)   # smaller than any plate
PLANE_TOLERANCE_M = 0.003
PLANE_TOLERANCES_M = (0.003, 0.002, 0.0015, 0.001)
PLATE_WIDTH, PLATE_HEIGHT = (0.28, 0.70), (0.09, 0.24)
HOLDS_PLATE = (0.33, 0.13)
EU_MIN_WIDTH = 0.48    # a recess this wide takes the 520 mm EU plate, narrower ones the 305 mm US plate
MAX_SLOPE = 0.6        # dz/dy of an accepted recess (about 31 degrees from vertical)
STANDOFF_M = 0.004     # plate centre in front of the recess plane
# dz/dy of the donor plate faces (plane fit of the LODS0 base mesh: Fiesta normal 0, 0.319, -0.948; ACU 0, 0.091, -0.996)
DONOR_SLOPE = {"EU": 0.336, "US": 0.091}
BBOX = 0x42426F78


def depth_map(triangles):
    """Rearmost z per 5 mm (x, y) cell of rear-facing triangles (n, 3, 3)."""
    xs = np.arange(*GRID_X, STEP)
    ys = np.arange(*GRID_Y, STEP)
    depth = np.full((len(ys), len(xs)), np.inf)
    for a, b, c in triangles:
        low, high = np.minimum(np.minimum(a, b), c), np.maximum(np.maximum(a, b), c)
        i0, i1 = np.searchsorted(xs, [low[0], high[0]])
        j0, j1 = np.searchsorted(ys, [low[1], high[1]])
        if i1 <= i0 or j1 <= j0:
            continue
        gx, gy = np.meshgrid(xs[i0:i1], ys[j0:j1])
        v0, v1 = b[:2] - a[:2], c[:2] - a[:2]
        den = v0[0] * v1[1] - v1[0] * v0[1]
        if abs(den) < 1e-12:
            continue
        px, py = gx - a[0], gy - a[1]
        u = (px * v1[1] - v1[0] * py) / den
        v = (v0[0] * py - px * v0[1]) / den
        inside = (u >= 0) & (v >= 0) & (u + v <= 1)
        z = a[2] + u * (b[2] - a[2]) + v * (c[2] - a[2])
        cell = depth[j0:j1, i0:i1]
        cell[inside] = np.minimum(cell[inside], z[inside])
    return xs, ys, depth


def grow(mask, seed):
    """Cells of `mask` 4-connected to the `seed` cells."""
    region = seed & mask
    while True:
        grown = region.copy()
        grown[1:] |= region[:-1]
        grown[:-1] |= region[1:]
        grown[:, 1:] |= region[:, :-1]
        grown[:, :-1] |= region[:, 1:]
        grown &= mask
        if (grown == region).all():
            return region
        region = grown


def find_recess(draws):
    """{'kind', 'centre' (x, y, z), 'slope' dz/dy, 'rms', 'size_m', 'recess_depth_m'} of the plate recess, or None.

    A probe smaller than any plate slides up the car's axis; each flat probe position is grown into the
    connected surface within 3 mm of its plane, and that region (its box) is the candidate recess, compared
    with the body in a 3 cm ring around it. Recessed candidates of plate size win (flattest first), then the
    largest flat panel that holds a plate."""
    triangles = []
    for d in draws:
        t = d.positions[d.indices].astype(np.float64)
        n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
        triangles.append(t[n[:, 2] < -0.3])
    if not triangles:
        return None
    triangles = np.concatenate(triangles)
    if not len(triangles):
        return None
    # Only the tail of the car: rear-facing surfaces further forward (the inside of the front bumper) would
    # fill cells the rear leaves open.
    rear = triangles[:, :, 2].mean(axis=1) < triangles[:, :, 2].min() + REAR_DEPTH_M
    xs, ys, depth = depth_map(triangles[rear])
    finite = np.isfinite(depth)
    gy, gx = np.mgrid[0:depth.shape[0], 0:depth.shape[1]]
    centre_column = int(np.searchsorted(xs, 0.0))
    half, rows = int(PROBE[0] / 2 / STEP), int(PROBE[1] / STEP)
    best, seen = None, set()
    for j in range(0, len(ys) - rows, 2):
        patch = depth[j:j + rows, centre_column - half:centre_column + half]
        if not np.isfinite(patch).all():
            continue
        basis = np.c_[gx[j:j + rows, centre_column - half:centre_column + half].ravel(),
                      gy[j:j + rows, centre_column - half:centre_column + half].ravel(), np.ones(patch.size)]
        coef, *_ = np.linalg.lstsq(basis, patch.ravel(), rcond=None)
        if np.sqrt(np.mean((patch.ravel() - basis @ coef) ** 2)) > PLANE_TOLERANCE_M / 2:
            continue
        plane = coef[0] * gx + coef[1] * gy + coef[2]
        seed = np.zeros_like(finite)
        seed[j:j + rows, centre_column - half:centre_column + half] = True
        # Tighten the tolerance until the region is plate-sized: a shallow recess pressed into a flat panel
        # (SX4 tailgate: the trapezoid under the chrome strip) runs into the panel at 3 mm.
        for tolerance in PLANE_TOLERANCES_M:
            region = grow(finite & (np.abs(depth - plane) < tolerance), seed)
            rr, cc = np.nonzero(region)
            box = (rr.min(), rr.max() + 1, cc.min(), cc.max() + 1)
            width, height = (box[3] - box[2]) * STEP, (box[1] - box[0]) * STEP
            if width <= PLATE_WIDTH[1] and height <= PLATE_HEIGHT[1]:
                break
        if box in seen:
            continue
        seen.add(box)
        flat = region
        ring = np.zeros_like(flat)
        ring[max(box[0] - RING, 0):box[1] + RING, max(box[2] - RING, 0):box[3] + RING] = True
        ring[box[0]:box[1], box[2]:box[3]] = False
        ring &= finite
        sunk = float(np.median(depth[region]) - np.median(depth[ring])) if ring.any() else 0.0
        residual = depth[region] - plane[region]
        rms = float(np.sqrt(np.mean(residual ** 2)))
        slope = float(coef[1] / STEP)
        plate_sized = PLATE_WIDTH[0] <= width <= PLATE_WIDTH[1] and PLATE_HEIGHT[0] <= height <= PLATE_HEIGHT[1]
        if abs(slope) > MAX_SLOPE:
            continue
        centre_row, centre_col = (box[0] + box[1]) / 2, (box[2] + box[3]) / 2
        y = float(GRID_Y[0] + centre_row * STEP)
        z = float(coef[0] * centre_col + coef[1] * centre_row + coef[2])
        kind = "EU" if width >= EU_MIN_WIDTH else "US"
        candidate = {"kind": kind, "centre": (0.0, y, z), "slope": slope, "rms": rms,
                     "size_m": (width, height), "recess_depth_m": sunk}
        holds = width >= HOLDS_PLATE[0] and height >= HOLDS_PLATE[1]
        if plate_sized and sunk > MIN_RECESS_M:
            rank = (0, rms)
        elif holds:
            # No recess (SX4: the plate panel between the tail lamps is one flat pressing): the largest flat
            # panel on the axis that holds a plate.
            rank = (1, -width * height)
        else:
            continue
        if best is None or rank < best[0]:
            best = (rank, candidate)
    return best[1] if best else None


def _blobs(data):
    _, _, _, _, header, total, count = struct.unpack_from("<IBBHIII", data)
    for index in range(count):
        tag, _, _, fields, metadata, offset, size, _ = struct.unpack_from("<IBBHIIII", data, 20 + index * 24)
        yield tag.to_bytes(4, "big").decode("ascii", "replace"), fields, metadata, offset, size


def _box_offset(data, fields, metadata):
    for meta in range(metadata, metadata + fields * 8, 8):
        kind, _, relative = struct.unpack_from("<IHH", data, meta)
        if kind == BBOX:
            return meta + relative
    return None


def translate(data, delta):
    """The Modelbin moved by `delta` (m): mesh quantisation centres and every BBox shift; streams untouched."""
    out = bytearray(data)
    delta = np.asarray(delta, dtype=np.float64)
    for tag, fields, metadata, offset, size in _blobs(data):
        box = _box_offset(data, fields, metadata)
        if box is not None and tag in ("Mesh", "Modl"):
            values = np.array(struct.unpack_from("<6f", data, box)) + np.tile(delta, 2)
            struct.pack_into("<6f", out, box, *values)
        if tag == "Mesh":
            at = offset + size - 112 + 24 * 4   # centre: floats 24-26 of the trailing 28 (scale, 1, centre, 0)
            centre = np.array(struct.unpack_from("<3f", data, at)) + delta
            struct.pack_into("<3f", out, at, *centre)
    return bytes(out)


def base_centre(data):
    """Centre of the plate itself: the box of the densest LODS0 mesh (the plate body), decoded from the shared
    position stream (int16 xyz / 32767 * mesh scale + mesh centre). The Modl box also holds the shadow card,
    which reaches above the plate (ACU: plate 0.195-0.347 m, box to 0.359) and put the plate low (owner)."""
    from .fh4 import previous_readers
    _, geometry = previous_readers()
    items = list(geometry.blobs(data))
    stream = next(b for b in items if b["tag"] == "VerB" and b["id"] in (None, 0xFFFFFFFF))
    start, _, _ = geometry.buffer_info(data, stream)
    best = None
    for b in items:
        if b["tag"] != "Mesh" or "LODS0" not in b["name"]:
            continue
        at = b["offset"]
        count, offset = struct.unpack_from("<I", data, at + 51)[0], struct.unpack_from("<I", data, at + 75)[0]
        trailer = struct.unpack_from("<28f", data, at + b["size"] - 112)
        raw = np.frombuffer(data, "<i2", count * 4, start + offset).reshape(-1, 4)[:, :3]
        points = raw / 32767 * np.array(trailer[20:23]) + np.array(trailer[24:27])
        if best is None or len(points) > len(best):
            best = points
    if best is None:
        raise ValueError("Plate model without an LODS0 mesh")
    return (best.min(axis=0) + best.max(axis=0)) / 2


def plate_models(cars_folder, draws):
    """({archive member: bytes}, receipt); no members when no recess or donor is found."""
    if os.environ.get("EXPANDED_GARAGE_PLATE") == "off":
        return {}, {"generated": False, "reason": "EXPANDED_GARAGE_PLATE=off"}
    recess = find_recess(draws)
    if recess is None:
        return {}, {"generated": False, "reason": "no flat plate-sized recess on the rear"}
    car, plate, lamp = DONORS[recess["kind"]]
    archive = Path(cars_folder) / f"{car}.zip"
    if not archive.is_file():
        return {}, {"generated": False, "reason": f"donor archive {archive.name} missing"}
    with ZipFile(archive) as donor:
        names = {n.casefold(): n for n in donor.namelist()}
        sources = {stem: donor.read(names[(FOLDER + stem + ".modelbin").casefold()]) for stem in (plate, lamp)}
    slope = recess["slope"]
    normal = np.array([0.0, slope, -1.0]) / np.hypot(1.0, slope)
    # Only translated: a recess tilted unlike the donor plate would bury one edge, so stand off by half the
    # plate height times the tilt difference as well.
    tilt = abs(np.arctan(slope) - np.arctan(DONOR_SLOPE[recess["kind"]]))
    target = np.asarray(recess["centre"]) + normal * (STANDOFF_M + np.sin(tilt) * SIZES[recess["kind"]][1] / 2)
    delta = target - base_centre(sources[plate])
    payloads = {FOLDER + stem + ".modelbin": translate(data, delta) for stem, data in sources.items()}
    receipt = {"generated": True, "donor": car, "kind": recess["kind"], "recess_centre_m": recess["centre"],
               "recess_slope": slope, "flatness_rms_m": recess["rms"], "shift_m": delta.tolist(),
               "models": sorted(payloads)}
    return payloads, receipt


def plate_draws(grouped_exterior, nonstock):
    """Finest-LOD opaque stock exterior draws the recess search looks at (no wheels, glass, exhausts)."""
    out = []
    for component, draws in grouped_exterior.items():
        name = component.casefold()
        if name in nonstock or name.startswith(("wheel", "glass", "exhaust", "undercarriage")):
            continue
        finest = min(d.lod for d in draws)
        out += [d for d in draws if d.lod == finest and not d.transparent]
    return out
