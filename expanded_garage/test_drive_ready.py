"""Regression tests for the v1.2 native systems and deployment gates."""

from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zlib

from . import fh4_archive, fh4_installer as installer
from .fh4_dynamics import ratios
from .fh4_model import Bone
from .fh4_runtime_rig import HEADER_SIZE, SECTION, skeleton_file, verify_skeleton
from .fh4_tires import TireAssets, UnknownTire, family_name
from .fh4_validation import DRIVE_GATES, drive_blockers, drive_conclusions, trial_qualification, verify_qualification


class DriveArchitectureTests(unittest.TestCase):
    def test_offline_trial_requires_static_assets_but_not_gameplay(self):
        features = dict.fromkeys((*DRIVE_GATES,"wheel_bind_frames","gr2_skeleton","gr2_gsf_graph_structure",
                                  "trial_material_dependencies","trial_controls","trial_collision"),
                                 "structurally_verified")
        for key in ("source_owned_wheel_rest","stock_v27_collision","native_animation_rig"):
            features[key] = "unverified_runtime"
        missing = {}
        report = {"features":features,"cars":[{"car_id":12000,"trial_prerequisites":{"missing":missing}}]}
        self.assertFalse(drive_conclusions(features)["drive_readiness"]["passed"])
        qualified = trial_qualification(report)
        self.assertTrue(qualified["passed"])
        self.assertFalse(qualified["runtime_acceptance"]["passed"])
        report.update(offline_trial_eligibility=qualified, first_drive_blockers=[], installable=True,
                      state="offline-trial-eligible", conclusions=drive_conclusions(features))
        self.assertEqual(verify_qualification(report),[])
        missing["trial_controls"] = ["Missing own GSF"]
        self.assertEqual(trial_qualification(report)["blockers"],["12000: Missing own GSF"])
        with self.assertRaisesRegex(ValueError,"static checks"):
            verify_qualification(report)

    def test_offline_trial_mode_is_explicit(self):
        result = {"state":"offline-trial-eligible","installable":True,"validation_sha256":"1"*64,
                  "car_ids":[12000],"files":[]}
        with TemporaryDirectory() as directory, patch.object(installer,"preflight",return_value=result), \
                patch.object(installer,"transaction") as transaction:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError,"matching offline installation"):
                installer.install(root,root,offline_test=True)
            transaction.assert_not_called()
            installer.install(root,root,offline_trial=True)
            self.assertEqual(transaction.call_args.kwargs["test_evidence"]["state"],"offline-trial-eligible")

    def test_gear_count_includes_reverse_and_excludes_unused_slots(self):
        row = {"NumGears": 3, "GearRatio0": -3, "GearRatio1": 3, "GearRatio2": 2,
               "GearRatio3": -1, "FinalDriveRatio": 4}
        self.assertEqual(ratios(row, 2), [-3, 3, 2])
        with self.assertRaisesRegex(ValueError, "count"):
            ratios(row, 3)
        with self.assertRaisesRegex(ValueError, "outside"):
            ratios(dict(row, GearRatio3=1), 2)

    def test_raw_granny_bind_and_out_of_range_relocation(self):
        bones = [Bone("<root>", -1), Bone("root", 0), Bone("hubLF", 1, (-0.75, 0.125, 1.25)),
                 Bone("spindleLF", 2)]
        payload = bytearray(skeleton_file("eg_fixture", bones))
        verified = verify_skeleton(payload, "eg_fixture")
        self.assertEqual(verified[-1]["local_m"], [0.0, 0.0, 0.0])
        self.assertEqual(verified[-1]["bind_m"], [-0.75, 0.125, 1.25])
        with self.assertRaisesRegex(ValueError, "identity"):
            verify_skeleton(payload, "eg_other")
        fixes = SECTION.unpack_from(payload, HEADER_SIZE)[7]
        struct.pack_into("<I", payload, fixes + 8, len(payload))
        struct.pack_into("<I", payload, 40, zlib.crc32(payload[HEADER_SIZE:]))
        with self.assertRaisesRegex(ValueError, "relocation"):
            verify_skeleton(payload, "eg_fixture")

    def test_tire_mount_requires_existing_model_and_correct_extent(self):
        self.assertEqual(family_name("6_Modern_High Performance (7)"), "modernhighperformance")
        with TemporaryDirectory() as directory:
            base = Path(directory)
            tires = base / "scene" / "tires"
            tires.mkdir(parents=True)
            data, dims = fh4_archive.pack({"tireL_a.modelbin": b"burGshared"})
            (tires / "tire_a.zip").write_bytes(data)
            mount = (f'<ZipFiles><Zip path="game:\\media\\cars\\_library\\scene\\tires\\tire_a.zip" '
                     f'dirstart="{dims.start}" dirsize="{dims.size}" direntries="{dims.entries}"/></ZipFiles>').encode()
            verified = TireAssets(mount, base / "Materials.zip")
            self.assertEqual(verified.resolve("A")["left"][0]["member"], "tireL_a.modelbin")
            self.assertFalse(verified.resolve("A")["has_distinct_right_resource"])
            with self.assertRaisesRegex(UnknownTire, "no left"):
                verified.resolve("other")
            bad_mount = mount.replace(b'direntries="1"', b'direntries="2"')
            with self.assertRaisesRegex(UnknownTire, "mount extent"):
                TireAssets(bad_mount, base / "Materials.zip").resolve("a")

    def test_missing_gsf_keeps_offline_install_closed(self):
        features = dict.fromkeys(DRIVE_GATES, "structurally_verified")
        self.assertFalse(drive_blockers(features))
        features["native_animation_rig"] = "missing_dependency"
        blockers = drive_blockers(features)
        self.assertEqual(blockers, [DRIVE_GATES["native_animation_rig"]])
        with TemporaryDirectory() as directory, patch.object(installer, "preflight", return_value={
                "blockers": blockers, "installable": False}), patch.object(installer, "transaction") as transaction:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "--offline-test"):
                installer.install(root, root)
            with self.assertRaisesRegex(ValueError, "GR2/GSF"):
                installer.install(root, root, offline_test=True)
            transaction.assert_not_called()

    def test_runtime_acceptance_records_owner_checks_after_rollback(self):
        with TemporaryDirectory() as directory, patch.object(installer, "closed"):
            host = Path(directory)
            token = "1" * 32
            ledger = host / ".expanded-garage" / token
            ledger.mkdir(parents=True)
            evidence = {"observer": "owner", "cars": {"12000": dict.fromkeys(installer.ACCEPTANCE_CHECKS, True)}}
            record = {"transaction": token, "state": "installed", "files": [],
                      "offline_test": {"state": "drive-ready-candidate", "car_ids": [12000],
                                       "validation_sha256": "2" * 64}}
            installer.write_journal(ledger, record)
            with self.assertRaisesRegex(ValueError, "rolled-back"):
                installer.record_runtime_acceptance(host, token, evidence)
            installer.write_journal(ledger, dict(record, state="rolled-back"))
            with self.assertRaisesRegex(ValueError, "must cover"):
                installer.record_runtime_acceptance(host, token, dict(evidence, cars={}))
            self.assertEqual(installer.record_runtime_acceptance(host, token, evidence)["state"], "runtime-accepted")
            self.assertTrue(installer.read_json(ledger / "journal.json")["runtime_acceptance"]["rollback_verified"])
