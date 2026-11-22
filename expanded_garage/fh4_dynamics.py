"""Map mechanically understood FM4 fields and identify unresolved FH4 dynamics."""

import json
import math
from pathlib import Path
import numpy as np

from .fh4_physics import stock_mechanical
from .fh4_presentation import StringTable


STEERING = ("SteerMaxAngle", "SteerMaxAngleFiltered", "SteerMaxAngVelTurning",
            "SteerMaxAngVelStraighten", "SteerAngVelCountersteer", "SteerAngVelDynFindPeak",
            "SteerAccelTimeToMaxRate", "SteerSpeedSensitiveMaxGees", "SteerSpeedSensitiveMinMaxAngle",
            "SteerSpeedSensitiveSlowSpeed", "SteerSpeedSensitiveFastSpeed", "SteerSpeedSensitiveFastRateScale")
STEERING_POLICY = {"SteerMaxAngVelTurning": {100.0, 105.0}, "SteerAngVelCountersteer": {105.0, 140.0}}
AERO_OFFSET_RANGES = {
    "DragOffThrottleOffset": (0.0, 400.0),
    "DownforceOffThrottleOffset": (-92.0, 92.0),
    "DragBrakingOffset": (0.0, 400.0),
    "DownforceBrakingOffset": (-110.0, 110.0),
}

TARGET_FIELDS = {
    "Data_Car": {
        "FFB": "native_default_unverified",
        "AssistsTCSControlSpeed": "native_default_unverified",
        "AssistsTCSFullEffectFricDiff": "unmapped",
        **{f"AssistsTCSFullSteerSlipScale{drive}": "unmapped" for drive in ("FWD", "RWD", "AWD")},
        **{f"{axis}{axle}Scalar{limit}": "unmapped" for axis in ("Lat", "LongBrake", "LongAccel")
           for axle in ("Front", "Rear") for limit in ("Max", "Clamp")},
        "FrontDownforceClampKG": "unmapped", "RearDownforceClampKG": "unmapped",
    },
    "Data_Engine": {"FuelBurnPerHorsepowerHour": "unmapped"},
    "Data_Drivetrain": {"ShiftSystemID": "required"},
    "List_SpringDamperPhysics": {
        "BumpStopHeight": "unmapped", "UseProgressiveSpring": "unmapped",
        **{f"{limit}Dampen{direction}RateHighVelocity": "derived_linear"
           for limit in ("Min", "Max") for direction in ("Bump", "Rebound")},
        **{f"Dampen{direction}HighVelocityThreshold": "native_breakpoint_equal_slopes"
           for direction in ("Bump", "Rebound")},
    },
    "List_AntiSwayPhysics": {"BeamStiffness": "unmapped"},
    "List_UpgradeBrakes": {
        **{f"{axle}BrakeSizeMM": "derived_inches_to_mm" for axle in ("Front", "Rear")},
        **{f"{axle}Brake{field}": "unmapped" for axle in ("Front", "Rear")
           for field in ("RotorMassDensity", "TorqueClamp", "NonSpinningMass", "DrumDepthMM", "RotorThicknessMM")},
    },
    "List_UpgradeDrivetrainDriveline": {
        field: "unmapped" for field in ("MultipleOfEngineTorqueForSpring", "FlexDampingConstantOffroad",
                                        "OneOverSpringConstant", "FlexSpringConstant", "FlexDampingConstant", "FlexMaxAngle")
    },
    "List_AeroPhysics": dict.fromkeys(AERO_OFFSET_RANGES, "native_runtime_offset"),
}


def drive_class(table, field):
    """Classify impact separately from confidence in a conversion rule."""
    if field == "ShiftSystemID" or table == "List_AeroPhysics" and field in AERO_OFFSET_RANGES:
        return "physics_startup"
    if field.startswith("Assists") or field == "FFB":
        return "driving_assistance"
    if table in ("Data_Engine","List_AeroPhysics") or "DrumDepth" in field:
        return "fidelity_calibration"
    if field.endswith("RotorMassDensity"):
        return "shared_native_setting"
    return "stable_motion"


def finite_between(value, lower, upper):
    return isinstance(value, (int, float)) and math.isfinite(value) and lower <= value <= upper


def aero_offsets(row, *, source=False):
    """Require finite FH4 aero additions within the target version's catalogue range."""
    values = {}
    for field, (minimum, maximum) in AERO_OFFSET_RANGES.items():
        value = row.get(field)
        if value is None and source:
            # FM4 has the baseline curves but no conditional addition for this input.
            # The FH4 loader treats a missing column value as FLT_MAX.
            value = 0.0
        if not finite_between(value, minimum, maximum):
            raise ValueError(f"List_AeroPhysics.{field} is missing or outside the FH4 offset range")
        values[field] = value
    return values


def ratios(row, forward=None):
    count = row["NumGears"]
    if type(count) is not int or not 2 <= count <= 9 or forward is not None and forward + 1 != count:
        raise ValueError("Invalid transmission count including reverse")
    result = [row.get(f"GearRatio{i}") for i in range(count)]
    if not finite_between(result[0], -30, -0.01) or not all(finite_between(x, 0.01, 30) for x in result[1:]):
        raise ValueError("Invalid active reverse or forward ratio")
    if not finite_between(row["FinalDriveRatio"], 0.01, 30):
        raise ValueError("Invalid final drive ratio")
    if not all(row.get(f"GearRatio{i}") in (-1, 0, None) for i in range(count, 9)):
        raise ValueError("Active ratio outside the declared gear count")
    return result


def localized_rows(db, archive, table, pk, label, *, leaves_only=False):
    strings = StringTable(archive.read(table + ".str"), table).rows
    result = {}
    condition = " WHERE ParentBucketId>0" if leaves_only else ""
    for row in db.execute(f"SELECT {pk}, {label} FROM {table}{condition}"):
        token = row[1]
        text = strings.get(int(token[2:]) & 0xffffffff) if token and token.startswith("_&") else (token, "")
        if text and text[0]:
            if text[0].casefold() in result:
                raise ValueError(f"Ambiguous native label for {table}: {text[0]}")
            result[text[0].casefold()] = row[0]
    return result


def car_attributes(ir):
    return next((x for path, x in ir["sections"]["structure"]["data"]["xml"].items()
                 if path.endswith("/carattribs.xml") and "xml" in x), None)


class NativeDynamicsCompiler:
    def __init__(self, db, strings):
        self.db = db
        self.shift_types = (localized_rows(db, strings, "List_ShiftSystem", "ShiftSystemID", "ShiftSystem")
                            if strings else {})
        self.shift_profiles = json.loads(Path(__file__).with_name("fh4-shift-profiles-v1.json").read_text(
            encoding="utf-8"))["profiles"]

    def analyze(self, source):
        car = source.record("Data_Car")
        layout = source.record("Data_CarBody")
        trans = source.stock("List_UpgradeDrivetrainTransmission")["record"]
        gearing = ratios(trans, car["NumGears"])
        for name in STEERING:
            if not finite_between(car.get(name), 0, 2000):
                raise ValueError(f"Unsupported original steering value {name}")
        if not 0 < car["SteerSpeedSensitiveMinMaxAngle"] <= car["SteerMaxAngleFiltered"] <= car["SteerMaxAngle"] < 90:
            raise ValueError("Invalid original steering angle limits")
        if not car["SteerSpeedSensitiveSlowSpeed"] < car["SteerSpeedSensitiveFastSpeed"]:
            raise ValueError("Invalid speed-sensitive steering range")
        physical = stock_mechanical(source.tables)
        mass = physical.get("curb_mass_kg")
        load = physical.get("weight_fraction_front")
        if not finite_between(mass, 200, 15000) or not finite_between(load, 0.05, 0.95):
            raise ValueError("Invalid original mass or static load fraction")
        if not finite_between(layout["ModelWheelbase"], 1, 8):
            raise ValueError("Invalid original wheelbase")
        curve = physical.get("unmodified_torque_curve_rpm_nm")
        if not curve or any(not math.isfinite(torque) for rpm, torque in curve):
            raise ValueError("Incomplete or non-finite source torque curve")
        peak_torque = max(torque for rpm, torque in curve)
        peak_power = max(rpm * math.tau / 60 * torque / 1000 for rpm, torque in curve)
        if not finite_between(peak_torque, 10, 10000) or not finite_between(peak_power / mass, 0.005, 2):
            raise ValueError("Invalid source torque, power or power-to-mass")
        projected = {k: car[k] for k in STEERING}
        evidence = {}
        for field, default_range in STEERING_POLICY.items():
            if car[field] in default_range and car["CarTypeID"] == 1:
                projected[field] = 105.0
                evidence[field] = "FH4 applies 105 to every exact stock pair in the 192-car comparison."
        return {"forward_gears": car["NumGears"], "ratios_including_reverse": gearing,
                "final_drive": trans["FinalDriveRatio"], "mass_kg": mass, "front_weight_fraction": load,
                "source_curve_peak_torque_nm": peak_torque, "source_curve_peak_power_kw": peak_power,
                "wheelbase_m": layout["ModelWheelbase"], "steering_source_fields": {k: car[k] for k in STEERING},
                "steering_target_fields": projected, "steering_conversions": evidence}

    def shift(self, source):
        attrs = car_attributes(source.ir)
        driver = next((n["attributes"] for n in attrs["xml"]["children"] if n["tag"] == "Driver"), {}) if attrs else {}
        transmission = source.stock("List_UpgradeDrivetrainTransmission")["record"]
        explicit = driver.get("ShiftSystem")
        evidence = None
        if explicit and explicit.casefold() in self.shift_types:
            label, rule = explicit.casefold(), "FM4 authored Driver.ShiftSystem"
        elif transmission["CVTShiftTime"] > 0:
            label, rule = "cvt", "positive FM4 CVTShiftTime"
        elif driver.get("HasPaddleShifter") == "1":
            label, rule = "paddle", "FM4 Driver.HasPaddleShifter"
        elif (evidence := self.identify_gearbox(source,transmission)) is not None:
            label, rule = evidence["mode"], "manufacturer specification matched to FM4 mechanical evidence"
        elif driver.get("HasPaddleShifter") == "0" and source.stock("List_UpgradeDrivetrainClutch") is not None:
            # FM4 non-paddle layouts include automatics. This is a candidate for an observed
            # discrete, clutch-equipped road drivetrain and needs independent gearbox evidence.
            label, rule = "stick", "discrete road gearbox and non-paddle FM4 driver rig; inferred"
            if source.record("Data_Car")["CarTypeID"] != 1:
                return None
        else:
            return None
        key = self.shift_types.get(label)
        if key is None:
            return None
        return {"id": key, "label": label, "rule": rule, "inferred": rule.endswith("; inferred"),
                "source": attrs["provenance"] if attrs else None,"manufacturer_evidence":evidence}

    def identify_gearbox(self, source, transmission):
        """Corroborate the shift mode from independent mechanical traits, keeping FM4 ratios intact."""
        car = source.record("Data_Car")
        make = source.record("List_CarMake")["ManufacturerCode"]
        drive = source.record("Data_Drivetrain")["DrivetypeID"]
        body = source.record("Data_CarBody")
        weight = source.stock("List_UpgradeCarBodyWeight")["record"]["Mass"]
        gears = ratios(transmission,car["NumGears"])
        matches = []
        for profile in self.shift_profiles:
            if ((make,car["Displacement"],drive,car["EnginePlacementID"],len(gears)-1) !=
                    (profile["make"],profile["displacement_cc"],profile["drive_type"],
                     profile["engine_placement"],profile["gears"])):
                continue
            if not profile["years"][0]<=car["Year"]<=profile["years"][1] or not math.isclose(
                    body["ModelWheelbase"],profile["wheelbase_m"],abs_tol=.035):
                continue
            if "mass_kg" in profile and not math.isclose(weight,profile["mass_kg"],abs_tol=20):
                continue
            if "ratios" in profile:
                equal = sum(math.isclose(abs(a),b,abs_tol=.004)
                            for a,b in zip(gears,profile["ratios"],strict=True))
                if equal < profile["minimum_matching_ratios"] or not math.isclose(
                        transmission["FinalDriveRatio"],profile["final_drive"],abs_tol=.02):
                    continue
            matches.append(profile)
        if len(matches)>1:
            raise ValueError("Conflicting manufacturer transmission evidence")
        return matches[0] if matches else None

    def brakes(self, source, row):
        """Translate the brake enum by name and measure the rotor friction section."""
        from .fh4 import load_draws

        output, proof, links = {}, {}, []
        for axle in ("Front","Rear"):
            field = axle+"BrakeTypeID"
            original, = source.raw_records("List_BrakeType","BrakeTypeID",row[field])
            source_type = original["record"]
            native = self.db.execute("SELECT BrakeTypeID,BrakeType,PartName,Density FROM List_BrakeType "
                                     "WHERE BrakeType=? AND PartName=?",
                                     (source_type["BrakeType"],source_type["PartName"])).fetchone()
            if native is None or not finite_between(native["Density"],500,15000):
                continue
            output.update({field:native["BrakeTypeID"],axle+"BrakeSizeMM":row[axle+"BrakeSize"]*25.4,
                           axle+"BrakeRotorMassDensity":native["Density"]})
            links.append(native["BrakeTypeID"])
            evidence = {"type":native["BrakeType"],"density_kg_m3":native["Density"],"source_type":original["provenance"]}
            measures = {}
            if native["BrakeType"]!="Drum":
                for model in source.ir["sections"]["geometry"]["data"]["models"]:
                    if model["role"]!="rotor":
                        continue
                    best = list(load_draws(source.root,model))
                    relevant = [(name,d) for name,d in best if name.casefold().endswith(("l"+axle[0].lower(),"r"+axle[0].lower()))]
                    if not relevant:
                        continue
                    corner = relevant[0][0][-2:].upper()
                    lod = min(d.lod for _,d in relevant)
                    xyz = np.concatenate([d.positions for _,d in relevant if d.lod==lod]).astype(np.float64)
                    yz = xyz[:,1:]
                    middle = .5*(yz.min(0)+yz.max(0))
                    radii = np.linalg.norm(yz-middle,axis=1)
                    swept = xyz[radii>=.84*radii.max(),0]
                    if len(swept)<12:
                        continue
                    measure = float(np.ptp(swept)*1000)
                    if finite_between(measure,3,60):
                        measures[corner] = {"thickness_mm":measure,"source":model["provenance"]}
                if len(measures)==2:
                    thickness = [r["thickness_mm"] for r in measures.values()]
                    if abs(thickness[0]-thickness[1])<=.5:
                        output[axle+"BrakeRotorThicknessMM"] = sum(thickness)*.5
                        evidence["outer_annulus_measures"] = measures
            proof[axle] = evidence
        return output,proof,links

    @staticmethod
    def spring(row):
        if not 0 < row["MinRideHeight"] <= row["DefRideHeight"] <= row["MaxRideHeight"]:
            raise ValueError("Invalid suspension ride height ordering")
        if row["MaxCompressHeight"] < 0 or row["MaxStretchDeltaFromRideHeight"] <= 0:
            raise ValueError("Invalid original suspension travel")
        for label in ("SpringRate", "DampenBumpRate", "DampenReboundRate"):
            low, nominal, high = (row[prefix + label] for prefix in ("Min", "Def", "Max"))
            if not 0 < low <= nominal <= high:
                raise ValueError(f"Invalid source suspension bounds for {label}")
        return {**{f"{bound}Dampen{direction}RateHighVelocity": row[f"{bound}Dampen{direction}Rate"]
                   for bound in ("Min", "Max") for direction in ("Bump", "Rebound")},
                "DampenBumpHighVelocityThreshold": 0.2, "DampenReboundHighVelocityThreshold": 0.2}
