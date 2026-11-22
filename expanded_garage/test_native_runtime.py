"""Tests of the common bridge against the local stock and preserved FM4 controls."""

from contextlib import closing
from pathlib import Path
from zipfile import ZipFile
import unittest

from . import fh4
from .fh4_db import snapshot
from .fh4_db_compiler import Source
from .fh4_dynamics import AERO_OFFSET_RANGES, NativeDynamicsCompiler, aero_offsets, drive_class
from .fh4_granny_animation import verify_clip, verify_gauge_graph
from .fh4_material_contract import MaterialUse, surface_binding
from .fh4_runtime_rig import verify_skeleton
from .fh4_tires import TireAssets
from .fh4_validation import drive_conclusions
from .vehicle_ir import load


ROOT = Path(__file__).resolve().parents[1]
CONTROLS = ROOT / ".scratch/expanded-garage/control-cars"
STOCK = ROOT / "game/ForzaHorizon4/media"
BASELINE = ROOT / ".scratch/phase7_1/camera-owner-test/baseline.sqlite"
MANIFEST = ROOT / ".scratch/phase3/fixed-layout/zipmanifest.plain.xml"
MATERIALS = STOCK / "cars/_library/Materials.zip"
FIXTURES = (("SUZ_SX4_11",12000,3,"L"),("HON_IntegraR_00",12002,2,"R"),("RUF_CTR2_95",12001,2,"L"))


class ScopeTests(unittest.TestCase):
    def test_active_aero_offsets_require_native_values_after_conversion(self):
        values = aero_offsets({"DragOffThrottleOffset": 48.0, "DownforceBrakingOffset": -50.0},
                              source=True)
        self.assertEqual(values, {
            "DragOffThrottleOffset": 48.0, "DownforceBrakingOffset": -50.0,
            "DownforceOffThrottleOffset": 0.0, "DragBrakingOffset": 0.0,
        })
        self.assertEqual(aero_offsets(values), values)
        for invalid in (None, float("nan"), float("inf"), 3.4028234663852886e38):
            with self.subTest(value=invalid), self.assertRaisesRegex(ValueError, "DragBrakingOffset"):
                aero_offsets({**values, "DragBrakingOffset": invalid})
        for field in AERO_OFFSET_RANGES:
            self.assertEqual(drive_class("List_AeroPhysics", field), "physics_startup")

    def test_accessory_and_fidelity_are_separate_from_the_drive_gate(self):
        report = drive_conclusions({"gr2_gsf_graph_structure":"structurally_verified",
                                    "native_animation_rig":"unverified_runtime"})
        self.assertFalse(report["drive_readiness"]["passed"])
        self.assertFalse(report["full_vehicle_fidelity"]["passed"])
        self.assertEqual(surface_binding("badge").use,MaterialUse.FIDELITY)
        self.assertEqual(surface_binding("gauge_emissive").use,MaterialUse.EFFECT)
        self.assertFalse(surface_binding("new_unknown").drive_resolved)
        self.assertTrue(surface_binding("tire").drive_resolved)
        self.assertEqual(surface_binding("solid","cockpit","seatL").trial_compatibility,"trial-compatible")
        self.assertNotEqual(surface_binding("solid","cockpit","seatL").member,
                            surface_binding("solid","cockpit","interior").member)
        self.assertFalse(surface_binding("misc","exterior","unclassified").drive_resolved)


@unittest.skipUnless(all((CONTROLS/c/"ir/vehicle.json").is_file() for c,*_ in FIXTURES)
                     and MATERIALS.is_file() and BASELINE.is_file() and MANIFEST.is_file(),
                     "Requires local stock FH4 and the three preserved FM4 Vehicle IRs")
class RealRuntimeTests(unittest.TestCase):
    def test_same_rig_compiler_authors_source_wheel_and_instrument_transforms(self):
        for media,car_id,gauges,side in FIXTURES:
            with self.subTest(media=media):
                source_path = CONTROLS/media/"ir"
                source = load(source_path)
                alias = "EG_"+media
                payloads, report, _ = fh4.make_models(
                    source_path,source,{"media_name":alias,"car_id":car_id,"body_id":car_id*1000},MATERIALS)
                motion = report["runtime_rig"]["motion_contract"]
                self.assertEqual(len(motion["gauges"]),gauges)
                self.assertEqual(report["driver_geometry"]["driver_side"],side)
                self.assertIn("steering",motion)
                skeletal = f"scene/{alias.casefold()}_skeleton.gr2"
                rig = {b["name"]:b for b in verify_skeleton(payloads[skeletal],alias.casefold())}
                for gauge in motion["gauges"]:
                    self.assertLess(gauge["max_source_fit_error_m"],3e-5)
                    self.assertEqual(verify_clip(payloads[gauge["member"]],gauge["clip"])[0],gauge["bone"])
                gsf = f"animations/{alias.casefold()}.gsf"
                with self.assertRaisesRegex(ValueError,"CarScene bone"):
                    verify_gauge_graph(payloads[gsf],payloads.__getitem__,rig,set())
                binary, directory = fh4.fh4_archive.pack(payloads)
                self.assertEqual(fh4.validate_zip(binary,alias,car_id),directory)

    def test_stock_left_only_tire_and_two_sided_profiles(self):
        assets = TireAssets(MANIFEST.read_bytes(),MATERIALS)
        self.assertFalse(assets.resolve("a")["has_distinct_right_resource"])
        self.assertTrue(assets.resolve("WET_b")["has_distinct_right_resource"])
        with closing(snapshot(BASELINE)) as db:
            count, = db.execute("SELECT count(*) FROM List_UpgradeTireCompound "
                                "WHERE IsStock=1 AND TireModelName='a'").fetchone()
        self.assertGreater(count,100)

    def test_rotor_density_is_resolved_by_native_type_name(self):
        root = CONTROLS/"SUZ_SX4_11"/"ir"
        source = Source(root,load(root))
        with closing(snapshot(BASELINE)) as db, ZipFile(STOCK/"stripped/stringtables/EN.zip") as strings:
            compiler = NativeDynamicsCompiler(db,strings)
            fields, proof, links = compiler.brakes(source,source.stock("List_UpgradeBrakes")["record"])
            shift = compiler.shift(source)
        self.assertFalse(shift["inferred"])
        self.assertEqual(shift["label"],"stick")
        self.assertGreater(len(shift["manufacturer_evidence"]["references"]),0)
        self.assertEqual(fields["FrontBrakeRotorMassDensity"],proof["Front"]["density_kg_m3"])
        self.assertTrue(links)
        self.assertIn("FrontBrakeRotorThicknessMM",fields)

    def test_backend_has_no_control_media_name_dispatch(self):
        for p in (ROOT/"expanded_garage").glob("fh4*.py"):
            source = p.read_text(encoding="utf-8").casefold()
            for media,*_ in FIXTURES:
                self.assertNotIn(media.casefold(),source,p.name)
