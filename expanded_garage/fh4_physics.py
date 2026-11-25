"""Source physics readback and generator inputs for FH4 collision research."""

from contextlib import closing
from itertools import combinations, product
import math

import numpy as np

from .fh4_db import snapshot
from .fm4 import rows_for
from .vehicle_ir import sha256


CAR_FIELDS = ("CurbWeight", "WeightDistribution", "FrontTireWidthMM", "RearTireWidthMM",
              "FrontStockRideHeight", "RearStockRideHeight", "SteerMaxAngle", "BodyAeroLongitudinalDrag",
              "SimPeakTorque", "GameDragScale", "PerformanceIndex")
BODY_FIELDS = ("ModelWheelbase", "ModelFrontTrackOuter", "ModelRearTrackOuter",
               "ModelFrontStockRideHeight", "ModelRearStockRideHeight", "BottomCenterWheelbasePosZ")
SUPPORT_DIRECTIONS = np.array([p for p in product((-1, 0, 1), repeat=3) if any(p)], dtype=np.float64)
SUPPORT_DIRECTIONS /= np.linalg.norm(SUPPORT_DIRECTIONS, axis=1, keepdims=True)


def catalogue(db):
    query = ("SELECT c.*, b.* FROM Data_Car c JOIN List_UpgradeCarBody u ON u.Ordinal=c.Id AND u.IsStock=1 "
             "JOIN Data_CarBody b ON b.Id=u.CarBodyID ORDER BY c.Id")
    result = {}
    for row in db.execute(query):
        key = (row["MediaName"].casefold(), row["Year"])
        if key not in result:
            result[key] = row
        else:
            result[key] = None
    return {key: row for key, row in result.items() if row is not None}


def compare(source, target):
    with closing(snapshot(source)) as src, closing(snapshot(target)) as dst:
        fm4, fh4 = catalogue(src), catalogue(dst)
        common = sorted(fm4.keys() & fh4.keys())
        fields = {}
        for field in (*CAR_FIELDS, *BODY_FIELDS):
            pairs = [(key, float(fm4[key][field]), float(fh4[key][field])) for key in common
                     if isinstance(fm4[key][field], (int, float)) and isinstance(fh4[key][field], (int, float))
                     and math.isfinite(float(fm4[key][field])) and math.isfinite(float(fh4[key][field]))]
            if len(pairs) < 6:
                continue
            departures = sorted(((abs(t-s), media, year, s, t) for (media, year), s, t in pairs), reverse=True)
            x, y = np.asarray([(s, t) for _, s, t in pairs]).T
            test = np.arange(len(pairs)) % 5 == 0
            if np.ptp(x[~test]) > 1e-10:
                slope, intercept = np.linalg.lstsq(np.column_stack((x[~test], np.ones(np.count_nonzero(~test)))),
                                                   y[~test], rcond=None)[0]
                fit = {"slope": float(slope), "intercept": float(intercept),
                       "holdout_median_abs_error": float(np.median(abs(y[test] - (x[test] * slope + intercept))))}
            else:
                fit = None
            fields[field] = {"pairs": len(pairs), "holdout_pairs": int(test.sum()),
                             "median_abs_delta": float(np.median(abs(y - x))),
                             "holdout_unchanged_median_abs_error": float(np.median(abs(y[test] - x[test]))),
                             "equal_within_1e-5": int(np.count_nonzero(abs(y - x) <= 1e-5)),
                             "fit": fit, "largest_deltas": departures[:5]}
    return {"source_sha256": sha256(source), "target_sha256": sha256(target),
            "pair_rule": "One stock body per case-insensitive exact MediaName and Year",
            "pairs": [{"media_name": media, "year": year} for media, year in common],
            "fields": fields, "use": "Comparison only. Field names, fits and matching values do not establish physics semantics."}


def hull_envelope(points):
    """Approximate a convex shell by fixed support directions in the vehicle frame."""
    supports = []
    for direction in SUPPORT_DIRECTIONS:
        supports.append((points @ direction).max())
    distances = np.asarray(supports)
    vertices = set()
    for triple in combinations(range(len(distances)), 3):
        rows = list(triple)
        basis = SUPPORT_DIRECTIONS[rows]
        if abs(np.linalg.det(basis)) < 1e-10:
            continue
        point = np.linalg.solve(basis, distances[rows])
        if np.all(SUPPORT_DIRECTIONS @ point <= distances + 1e-7):
            vertices.add(tuple(map(float, np.round(point, decimals=7))))
    if len(vertices) < 4:
        raise ValueError("Degenerate collision envelope")
    return {"vertices_m": sorted(vertices), "planes": [
        {"outward_normal": direction.tolist(), "distance_m": float(distance)}
        for direction, distance in zip(SUPPORT_DIRECTIONS, distances, strict=True)]}


def maxdata_spheres(structure):
    for name, source in structure["xml"].items():
        if not name.endswith("/maxdata.xml") or "xml" not in source:
            continue
        collision = next((c for c in source["xml"]["children"] if c["tag"] == "Collision"), None)
        if collision is None:
            continue
        node = next((c for c in collision["children"] if c["tag"] == "CollSpheres"), None)
        if node is None:
            continue
        attrs = node["attributes"]
        offsets = [float(attrs.get("PosOffset" + axis, 0)) for axis in "XYZ"]
        pos_scale = float(attrs.get("PosScale", 1))
        rad_scale = float(attrs.get("RadiusScale", 1))
        entries = []
        for item in node["children"]:
            a = item["attributes"]
            entries.append({"source_name": item["tag"], "flags": int(a["Flags"]) if "Flags" in a else None,
                            "center_m": [float(a["Pos" + axis]) * pos_scale + offset
                                         for axis, offset in zip("XYZ", offsets, strict=True)],
                            "radius_m": float(a["Radius"]) * rad_scale})
        return {"frame": "FM4 MAXData collision authoring frame", "spheres": entries, "source": source["provenance"]}
    return None


def stock_mechanical(rows):
    """Decode stock measured values whose units are known from the FM4 tables."""
    weight = next((r for r in rows_for(rows, "List_UpgradeCarBodyWeight") if r["IsStock"] == 1), None)
    transmission = next((r for r in rows_for(rows, "List_UpgradeDrivetrainTransmission") if r["IsStock"] == 1), None)
    camshaft = next((r for r in rows_for(rows, "List_UpgradeEngineCamshaft") if r["IsStock"] == 1), None)
    curve = next((r for r in rows_for(rows, "List_TorqueCurve")
                  if camshaft and r["TorqueCurveID"] == camshaft["TorqueCurveFullThrottleID"]), None)
    motor = next(iter(rows_for(rows, "Data_Motor")), None)
    if camshaft is None and motor is not None:
        # Electric car (NIS_Leaf_11): the motor's own curve over 0..RedlineRPM.
        camshaft = {"TorqueCurveMaxRPM": motor["RedlineRPM"], "RedlineRPM": motor["RedlineRPM"]}
        curve = next((r for r in rows_for(rows, "List_TorqueCurve")
                      if r["TorqueCurveID"] == motor["TorqueCurveFullThrottleID"]), None)
    result = {}
    if weight:
        result["curb_mass_kg"] = weight["Mass"]
        result["weight_fraction_front"] = weight["CMBackFront"]
        result["center_of_mass_height_m"] = weight["CMHeight"]
    if transmission:
        result["transmission_ratios"] = [transmission[f"GearRatio{i}"] for i in range(transmission["NumGears"])]
        result["final_drive_ratio"] = transmission["FinalDriveRatio"]
    if curve and curve["NumTorqueValues"] >= 2:
        count = curve["NumTorqueValues"]
        step = camshaft["TorqueCurveMaxRPM"] / (count - 1)
        result["unmodified_torque_curve_rpm_nm"] = [
            [i * step, curve[f"v{i}"] * curve["TorqueScale"]] for i in range(count)]
        result["redline_rpm"] = camshaft["RedlineRPM"]
    return result
