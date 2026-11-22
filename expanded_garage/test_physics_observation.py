import struct
import unittest

from .research.watch_physics_initialization import motion_key, motion_state
from .research.watch_physics_writes import (
    WATCH_CONTROL, invalid_y_changes, previous_instruction, watch_registers,
)


def state(*, speed_y=0, position_y=0, position_w=0):
    raw = bytearray(0x50)
    for offset, value in ((0x24, speed_y), (0x44, position_y), (0x4C, position_w)):
        struct.pack_into("<I", raw, offset, value)
    return motion_state(raw)


class PhysicsObservationTests(unittest.TestCase):
    def test_initialization_and_world_placement_are_distinct(self):
        zero = state()
        staging = state(position_y=0xC47C1BF9)
        placed = state(position_y=0x43C2B457, position_w=0x3F800000)
        self.assertEqual(len({motion_key(zero), motion_key(staging), motion_key(placed)}), 3)

    def test_nan_comparison_keeps_the_sign_and_the_hardware_slot(self):
        before = state(position_y=0x43C2B457, position_w=0x3F800000)
        after = state(speed_y=0x7FC00000, position_y=0xFFC00000, position_w=0x3F800000)
        self.assertEqual(invalid_y_changes(before, after, 1), ["linear_velocity"])
        self.assertEqual(invalid_y_changes(before, after, 2), ["position"])
        self.assertEqual(after["position"]["bits"][1], "ffc00000")
        self.assertEqual(invalid_y_changes(after, after, 3), [])
        self.assertEqual(invalid_y_changes(before, state(speed_y=0x7F800000), 1), [])

    def test_watchpoint_packing_does_not_displace_an_active_slot(self):
        self.assertEqual(watch_registers((0, 0, 0x21, 0, 3, 0x10), 0x1000),
                         (0x1024, 0x1044, 0x21, 0, 0, WATCH_CONTROL | 0x10))
        with self.assertRaises(ValueError):
            watch_registers((0, 0, 0, 0, 0, 4), 0x1000)

    def test_store_decoder_resolves_a_vector_store_that_overlaps_y(self):
        registers = {"rip": 0x404, "rcx": 0x1000}
        stores = previous_instruction(bytes.fromhex("0f117120"), 0x400, registers, (0x1024,))
        self.assertEqual(stores, [{"address": "0x400", "bytes": "0f117120",
                                  "assembly": "movups xmmword ptr [rcx + 0x20], xmm6"}])
        self.assertFalse(previous_instruction(bytes.fromhex("0f107120"), 0x400, registers, (0x1024,)))
        self.assertFalse(previous_instruction(bytes.fromhex("0f117120"), 0x400, registers, (0x1044,)))
