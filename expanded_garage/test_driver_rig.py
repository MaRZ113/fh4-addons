import unittest
from dataclasses import dataclass

import numpy as np

from . import fh4_driver_rig as rig
from .fh4_model import Bone


@dataclass
class Point:
    name: str
    meters: tuple


def steering_bones(x=-0.33):
    return [Bone("<root>", -1), Bone("root", 0), Bone("boneSteeringWheel", 1, (x, 0.85, 0.30)),
            Bone("boneSteeringWheelSpindle", 2)]


class DriverRigTest(unittest.TestCase):
    def test_anchors_follow_steering_spindle(self):
        bones = steering_bones()
        receipt = rig.add_driver_bones(bones, "L")
        names = [b.name for b in bones]
        for anchor in ("anchor_butt", "anchor_lefthand", "anchor_righthand", "anchor_gas", "anchor_brake"):
            self.assertIn(anchor, names)
        self.assertEqual(names[:4], [b.name for b in steering_bones()])   # existing indices stay stable
        butt = bones[names.index("anchor_butt")].translation
        donor = rig.load_rig()["bones"][0]["offset_from_steering_m"]
        np.testing.assert_allclose(np.subtract(butt, (-0.33, 0.85, 0.30)), donor, atol=1e-9)
        self.assertFalse(receipt["mirrored"])
        rig.validate_ik_anchor_xml(rig.ik_anchor_xml(), names)

    def test_right_hand_drive_mirrors_root_offsets(self):
        bones = steering_bones(0.33)
        rig.add_driver_bones(bones, "R")
        butt = dict((b.name, b) for b in bones)["anchor_butt"].translation
        self.assertGreater(butt[0], 0)

    def test_ik_xml_rejects_missing_bones(self):
        with self.assertRaisesRegex(ValueError, "IK anchors without skeleton bones"):
            rig.validate_ik_anchor_xml(rig.ik_anchor_xml(), ["root"])

    def test_locators_fit_car_hardpoints(self):
        points = {0: Point("headlightL", (-0.61, 0.54, 1.68)), 1: Point("headlightR", (0.61, 0.54, 1.68)),
                  2: Point("taillightL", (-0.68, 0.75, -1.70)), 3: Point("taillightR", (0.68, 0.75, -1.70)),
                  9: Point("steeringWheel", (-0.33, 0.85, 0.30))}
        authored = {"carLocator_" + p.name for p in points.values()}
        extra, receipt = rig.transferred_locators(points, authored, "L")
        names = {e["name"] for e in extra}
        self.assertTrue({"carLocator_entryDoorLF", "carLocator_seatCamLF", "carLocator_doorHandleLF"} <= names)
        self.assertFalse(names & authored)
        self.assertLess(receipt["max_hardpoint_residual_m"], 0.15)
        self.assertEqual(rig.mirrored_name("carLocator_bumperR"), "carLocator_bumperR")
        self.assertEqual(rig.mirrored_name("carLocator_entryDoorLF"), "carLocator_entryDoorRF")


if __name__ == "__main__":
    unittest.main()
