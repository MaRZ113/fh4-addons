"""Project missing road-car settings into observed native FH4 operating ranges."""

from collections import Counter
import math
from pathlib import PureWindowsPath
from statistics import median

from .fh4_dynamics import finite_between, shift_accepted


AXLES = ("Front", "Rear")
SCALARS = tuple(axis + axle + "Scalar" + suffix
                for axis in ("Lat", "LongBrake", "LongAccel") for axle in AXLES for suffix in ("Max", "Clamp"))
DRIVELINE = ("FlexSpringConstant", "FlexDampingConstant", "FlexDampingConstantOffroad",
             "OneOverSpringConstant", "MultipleOfEngineTorqueForSpring", "FlexMaxAngle")
DEFAULT_CLAMPS = (140., 260.)
SUSPENSION_FAMILIES = {
    "strut": {"fwd_strut","strut"},
    "torsion_beam": {"fwd_trailingbeam","trailingbeam"},
    "wishbone": {"modernwishbone","vintagewishbone"},
}


def repeated_profile(rows, predicate, *, minimum=5, share=.7):
    count = sum(bool(predicate(row)) for row in rows)
    if count < minimum or count < share * len(rows):
        raise ValueError(f"Insufficient native evidence for a shared behaviour ({count}/{len(rows)})")
    return {"support": count, "samples": len(rows)}


class TrialDynamics:
    """Source-based road-car conversion with an audited stock settings cohort."""

    def __init__(self, db, source, shift):
        self.db = db
        self.source = source
        self.car = source.record("Data_Car")
        self.layout = source.record("Data_Drivetrain")
        self.shift = shift
        self.peers = self.road_peers() if self.car["CarTypeID"] in (1, 3) and shift_accepted(shift) else []
        if self.peers is not None and len(self.peers) < 5 and self.car["CarTypeID"] in (1, 3) and shift_accepted(shift):
            # Rare layouts (front-engine RWD race car, FWD hybrid): same drive and engine position, any gearbox.
            widened = self.road_peers(any_shift=True)
            if len(widened) >= 5:
                self.peers = widened
        self.suspension_types = {}

    def road_peers(self, *, relaxed=False, any_shift=False):
        rows = self.db.execute(
            """SELECT c.*, d.DrivetypeID, d.ShiftSystemID, l.FlexSpringConstant, l.FlexDampingConstant,
                      l.FlexDampingConstantOffroad, l.OneOverSpringConstant, l.MultipleOfEngineTorqueForSpring,
                      l.FlexMaxAngle, f.SuspensionPhysicsTypeID AS front_type, r.SuspensionPhysicsTypeID AS rear_type
               FROM Data_Car c JOIN List_UpgradeDrivetrain u ON u.Ordinal=c.Id AND u.IsStock=1
               JOIN Data_Drivetrain d ON d.DrivetrainId=u.DrivetrainId
               JOIN List_UpgradeDrivetrainDriveline l ON l.DrivetrainId=d.DrivetrainId AND l.IsStock=1
               JOIN List_UpgradeSpringDamper s ON s.Ordinal=c.Id AND s.IsStock=1
               JOIN List_SpringDamperPhysics f ON f.SpringDamperPhysicsID=s.FrontSpringDamperPhysicsID
               JOIN List_SpringDamperPhysics r ON r.SpringDamperPhysicsID=s.RearSpringDamperPhysicsID
               WHERE c.CarTypeID IN (1, ?) AND c.IsDrivable=1 AND c.Id<12000 AND c.ContentId<>99
                 AND d.DrivetypeID=? AND c.EnginePlacementID=? AND (d.ShiftSystemID=? OR ?) ORDER BY c.Id""",
            (self.car["CarTypeID"], self.layout["DrivetypeID"], self.car["EnginePlacementID"], self.shift["id"],
             int(any_shift))
        ).fetchall()
        features = ("Year", "CurbWeight", "SimPeakTorque", "FrontTireWidthMM", "RearTireWidthMM")
        scales = (16., .35, .55, 45., 45.)
        seen, eligible = set(), []
        for row in rows:
            key = (row["MakeID"], *(row[f] for f in features))
            if key in seen or min(key[1:])<=0:
                continue
            seen.add(key)
            deltas = [abs(math.log(row[f] / self.car[f])) if f in ("CurbWeight", "SimPeakTorque")
                      else abs(row[f] - self.car[f]) for f in features]
            if relaxed or all(delta<=2.1*scale for delta,scale in zip(deltas,scales,strict=True)):
                eligible.append((sum((d/s)**2 for d,s in zip(deltas,scales)), row["Id"], row))
        return [row for _,_,row in sorted(eligible)[:15]]

    def car_fields(self):
        peers = self.peers
        if len(peers)<5 and self.car["CarTypeID"] in (1, 3) and shift_accepted(self.shift):
            peers = self.road_peers(relaxed=True)
            if len(peers)<5:
                peers = self.road_peers(relaxed=True, any_shift=True)
        if len(peers)<5:
            return {}, {}
        values = {field: median(row[field] for row in peers) for field in SCALARS}
        if not all(finite_between(v,.15,2.5) for v in values.values()):
            raise ValueError("Road scalar projection outside the stock-supported road envelope")
        road = self.db.execute("SELECT FrontDownforceClampKG, RearDownforceClampKG FROM Data_Car "
                               "WHERE CarTypeID=1 AND IsDrivable=1 AND Id<12000").fetchall()
        caps = repeated_profile(road, lambda row: tuple(row)==DEFAULT_CLAMPS, share=.7)
        ffb = self.db.execute("SELECT FFB FROM Data_Car WHERE CarTypeID=1 AND IsDrivable=1 AND Id<12000").fetchall()
        ffb_support = repeated_profile(ffb,lambda row:row[0]==1.,share=.95)
        values.update(zip(("FrontDownforceClampKG","RearDownforceClampKG"),DEFAULT_CLAMPS,strict=True))
        values["FFB"] = 1.
        return values, {"rule": "Per-field same-layout stock medians and common road aero/FFB settings",
                        "cohort_car_ids":[row["Id"] for row in peers], "aero_caps":caps,"ffb":ffb_support,
                        "relaxed_distance":peers is not self.peers}

    def twin_spring(self, axle, legacy_mode, topology):
        """The stock axle of the native FH4 car built on the same chassis (profile `native_twin`). FH4 does
        not keep the real topology for every layout (its DC2, ACU_IntegraR_01, runs RevoKnuckle and
        FWD_TrailingBeam on a double-wishbone car), so the twin is stronger evidence than a family rule."""
        twin = topology["native_twin"]
        row = self.db.execute(
            f"""SELECT p.SuspensionPhysicsTypeID,p.UseProgressiveSpring,p.BumpStopHeight,t.Path,t.Name
                FROM Data_Car c JOIN List_UpgradeSpringDamper s ON s.Ordinal=c.Id AND s.IsStock=1
                JOIN List_SpringDamperPhysics p ON p.SpringDamperPhysicsID=s.{axle}SpringDamperPhysicsID
                JOIN List_SuspensionPhysicsType t ON t.SuspensionPhysicsTypeID=p.SuspensionPhysicsTypeID
                WHERE c.MediaName=? AND c.Id<12000""", (twin,)).fetchone()
        if row is None or row[4] != "ARTICULATED" or not row[3] or row[1] not in (0, 1)                 or not finite_between(row[2], -.1, .1):
            return {}, {}
        self.suspension_types[axle] = row[0]
        return {"SuspensionPhysicsTypeID":row[0],"UseProgressiveSpring":row[1],"BumpStopHeight":row[2]}, {
            "rule":"Stock axle of the native FH4 car on the same chassis","source_mode":legacy_mode,
            "source_topology":topology.get(axle),"topology_reference":topology["reference"],
            "native_twin":twin,"resource":row[3]}

    def spring(self, axle, legacy_mode):
        source_profile = (self.shift or {}).get("manufacturer_evidence") or {}
        topology = source_profile.get("suspension",{})
        if topology.get("native_twin") and legacy_mode == "INDEPENDANT":
            return self.twin_spring(axle, legacy_mode, topology)
        family = SUSPENSION_FAMILIES.get(topology.get(axle))
        if not family:
            return self.peer_spring(axle, legacy_mode)
        if legacy_mode != "INDEPENDANT":
            return {}, {}
        paths = {kind:path for kind,path in self.db.execute("SELECT SuspensionPhysicsTypeID,Path "
                    "FROM List_SuspensionPhysicsType WHERE Name='ARTICULATED' AND length(Path)>0")
                 if PureWindowsPath(path).stem.casefold() in family}
        rows = self.db.execute(
            f"""SELECT p.SuspensionPhysicsTypeID,p.UseProgressiveSpring,p.BumpStopHeight,d.DrivetypeID
                FROM List_SpringDamperPhysics p
                JOIN List_UpgradeSpringDamper s ON s.{axle}SpringDamperPhysicsID=p.SpringDamperPhysicsID AND s.IsStock=1
                JOIN Data_Car c ON c.Id=s.Ordinal AND c.CarTypeID=1 AND c.ContentId<>99 AND c.Id<12000
                JOIN List_UpgradeDrivetrain u ON u.Ordinal=c.Id AND u.IsStock=1
                JOIN Data_Drivetrain d ON d.DrivetrainId=u.DrivetrainId""").fetchall()
        stock_modes = Counter(r[0] for r in rows if r[0] in paths)
        same_layout = Counter(r[0] for r in rows if r[0] in paths and r[3]==self.layout["DrivetypeID"])
        if not same_layout:
            return {}, {}
        kind = max(same_layout,key=lambda k:(same_layout[k],stock_modes[k],-k))
        if stock_modes[kind]<10:
            return {}, {}
        stock = [(r[1],r[2]) for r in rows if r[0]==kind]
        profile, support = Counter(stock).most_common(1)[0]
        if support < 3 or support < .5*len(stock):
            return {}, {}
        progression,height = profile
        if progression not in (0, 1) or not finite_between(height,-.1,.1):
            return {}, {}
        self.suspension_types[axle] = kind
        return {"SuspensionPhysicsTypeID":kind,"UseProgressiveSpring":progression,"BumpStopHeight":height}, {
            "rule":"Shared articulated suspension and modal paired bump-stop configuration on that axle",
            "source_mode":legacy_mode,"source_topology":topology[axle],"topology_reference":topology["reference"],
            "resource":paths[kind],"same_drive_usage":same_layout[kind],"all_road_usage":stock_modes[kind],
            "paired_native_support":support,"paired_native_rows":len(stock)}

    def peer_spring(self, axle, legacy_mode):
        """No authored topology (cars beyond the first fleet): the stock suspension template the most similar
        native cars use on this axle (the dynamics cohort), with its modal progression/bump-stop pair.
        EXPANDED_GARAGE_PEER_SUSPENSION=off leaves the axle unresolved."""
        import os
        if os.environ.get("EXPANDED_GARAGE_PEER_SUSPENSION") == "off":
            return {}, {}
        peers = self.peers or (self.road_peers(relaxed=True, any_shift=True) if self.shift else [])
        column = "front_type" if axle == "Front" else "rear_type"
        paths = {kind: path for kind, path in self.db.execute(
            "SELECT SuspensionPhysicsTypeID,Path FROM List_SuspensionPhysicsType WHERE Name='ARTICULATED' AND length(Path)>0")}
        # Axle-specific templates only: a kind natives mount more often on the other axle (modernwishbone is a
        # steered front template, BMW_6_M3GTR_01 cohort put it on the rear) fails the installer's XML readback.
        usage = {a: Counter(r[0] for r in self.db.execute(
            f"""SELECT p.SuspensionPhysicsTypeID FROM List_UpgradeSpringDamper s
                JOIN List_SpringDamperPhysics p ON p.SpringDamperPhysicsID=s.{a}SpringDamperPhysicsID
                WHERE s.IsStock=1 AND s.Ordinal<12000""")) for a in AXLES}
        other = "Rear" if axle == "Front" else "Front"
        kinds = Counter(row[column] for row in peers if row[column] in paths
                        and usage[axle][row[column]] > usage[other][row[column]])
        if not kinds:
            return {}, {}
        kind, support = kinds.most_common(1)[0]
        if support < 2:
            return {}, {}
        rows = self.db.execute(
            f"""SELECT p.UseProgressiveSpring,p.BumpStopHeight FROM List_SpringDamperPhysics p
                JOIN List_UpgradeSpringDamper s ON s.{axle}SpringDamperPhysicsID=p.SpringDamperPhysicsID AND s.IsStock=1
                WHERE p.SuspensionPhysicsTypeID=? AND s.Ordinal<12000""", (kind,)).fetchall()
        if not rows:
            return {}, {}
        (progression, height), paired = Counter(tuple(r) for r in rows).most_common(1)[0]
        if progression not in (0, 1) or not finite_between(height, -.1, .1):
            return {}, {}
        self.suspension_types[axle] = kind
        return {"SuspensionPhysicsTypeID": kind, "UseProgressiveSpring": progression, "BumpStopHeight": height}, {
            "rule": "Most common stock template on this axle among the dynamics cohort; modal paired bump stop",
            "source_mode": legacy_mode, "resource": paths[kind], "cohort_support": support,
            "cohort_samples": len(peers), "paired_native_support": paired, "paired_native_rows": len(rows)}

    def anti_sway(self, axle):
        kind = self.suspension_types.get(axle)
        if not kind:
            return {}, {}
        topology = ((self.shift or {}).get("manufacturer_evidence") or {}).get("suspension",{})
        if twin := topology.get("native_twin"):
            row = self.db.execute(
                f"""SELECT b.BeamStiffness FROM Data_Car c
                    JOIN List_UpgradeAntiSway{axle} a ON a.Ordinal=c.Id AND a.IsStock=1
                    JOIN List_AntiSwayPhysics b ON b.AntiSwayPhysicsID=a.AntiSwayPhysicsID
                    WHERE c.MediaName=? AND c.Id<12000""", (twin,)).fetchone()
            if row is not None and finite_between(row[0],0,1500):
                return {"BeamStiffness":row[0]}, {"rule":"Stock beam stiffness of the native FH4 twin",
                                                  "native_twin":twin,"template_id":kind}
        rows = self.db.execute(
            f"""SELECT b.BeamStiffness FROM List_UpgradeAntiSway{axle} a
                JOIN List_AntiSwayPhysics b ON b.AntiSwayPhysicsID=a.AntiSwayPhysicsID
                JOIN List_UpgradeSpringDamper s ON s.Ordinal=a.Ordinal AND s.IsStock=1
                JOIN List_SpringDamperPhysics p ON p.SpringDamperPhysicsID=s.{axle}SpringDamperPhysicsID
                WHERE a.IsStock=1 AND p.SuspensionPhysicsTypeID=?""", (kind,)
        ).fetchall()
        values = [row[0] for row in rows if finite_between(row[0],0,1500)]
        if len(values)<3:
            return {}, {}
        return {"BeamStiffness":median(values)}, {"rule":"Median stock beam stiffness for the same shared axle template",
                                                 "template_id":kind,"sample_rows":len(values)}

    def twin_driveline(self):
        """Stock driveline of the native FH4 car on the same chassis, when too few similar stock cars
        share this layout for the peer rule (RUF CTR2 on the 993: POR_911GT2_95)."""
        topology = ((self.shift or {}).get("manufacturer_evidence") or {}).get("suspension",{})
        twin = topology.get("native_twin")
        if not twin:
            return {}, {}
        row = self.db.execute(
            f"""SELECT {",".join("l."+k for k in DRIVELINE)} FROM Data_Car c
                JOIN List_UpgradeDrivetrain u ON u.Ordinal=c.Id AND u.IsStock=1
                JOIN Data_Drivetrain d ON d.DrivetrainId=u.DrivetrainId
                JOIN List_UpgradeDrivetrainDriveline l ON l.DrivetrainId=d.DrivetrainId AND l.IsStock=1
                WHERE c.MediaName=? AND c.Id<12000 AND d.DrivetypeID=?""",
            (twin, self.layout["DrivetypeID"])).fetchone()
        if row is None:
            return {}, {}
        values = dict(zip(DRIVELINE,row,strict=True))
        if not (all(finite_between(values[k],1,10000) for k in DRIVELINE[:3])
                and finite_between(values["OneOverSpringConstant"],.00001,.01)
                and finite_between(values["MultipleOfEngineTorqueForSpring"],.5,12)
                and values["FlexMaxAngle"] == 0.):
            return {}, {}
        return values, {"rule":"Stock driveline of the native FH4 twin on the same chassis and layout",
                        "native_twin":twin}

    def driveline(self):
        if len(self.peers)<5:
            return self.twin_driveline()
        tuples = Counter(tuple(row[key] for key in DRIVELINE[:3]) for row in self.peers if row["FlexMaxAngle"]==0.)
        if not tuples:
            return {}, {}
        profile,count = tuples.most_common(1)[0]
        related = [row for row in self.peers if tuple(row[key] for key in DRIVELINE[:3])==profile
                   and row["FlexMaxAngle"]==0.]
        if count<3 or not all(finite_between(v,1,10000) for v in profile):
            return self.twin_driveline()
        values = dict(zip(DRIVELINE[:3],profile,strict=True))
        values[DRIVELINE[3]] = median(r[DRIVELINE[3]] for r in related)
        values[DRIVELINE[4]] = median(r[DRIVELINE[4]] for r in related)
        if not finite_between(values[DRIVELINE[3]],.00001,.01) or not finite_between(values[DRIVELINE[4]],.5,12):
            return {}, {}
        values["FlexMaxAngle"] = 0.
        return values, {"rule":"Repeated native stiffness/damping triplet and same-family torque/spring medians",
                        "cohort_car_ids":[r["Id"] for r in related]}

    def brakes(self, axle, type_id):
        rows = self.db.execute(
            f"SELECT {axle}BrakeTorqueClamp,{axle}BrakeNonSpinningMass FROM List_UpgradeBrakes "
            f"WHERE IsStock=1 AND {axle}BrakeTypeID=?", (type_id,)).fetchall()
        if not rows:
            return {}, {}
        caps = repeated_profile(rows,lambda r:r[0]==1000000.,share=.95)
        mass = median(row[1] for row in rows)
        if not finite_between(mass,2,15):
            return {}, {}
        return {axle+"BrakeTorqueClamp":1000000.,axle+"BrakeNonSpinningMass":mass}, {
            "rule":"Standard high torque ceiling and median non-spinning mass for the same stock brake type",
            "native_type":type_id,"evidence":caps}
