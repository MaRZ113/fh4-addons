import struct
import unittest

import numpy as np

from . import fh4_paint, fh4_wheel_model
from .fh4_model import Draw, assemble, rig_model, FLT_MAX
from .fh4_material_contract import surface_binding


def ring(radius, x0, x1, material, lod, segments=24):
    angles = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    rim = np.c_[np.zeros(segments), radius * np.cos(angles), radius * np.sin(angles)]
    positions = np.r_[rim + [x0, 0, 0], rim + [x1, 0, 0]].astype(np.float32)
    normals = np.c_[np.zeros(2 * segments), np.r_[np.cos(angles), np.cos(angles)],
                    np.r_[np.sin(angles), np.sin(angles)]].astype(np.float32)
    i = np.arange(segments)
    j = (i + 1) % segments
    indices = np.r_[np.c_[i, j, i + segments], np.c_[j, j + segments, i + segments]].astype(np.uint32)
    uv = np.zeros((2 * segments, 2), dtype=np.float32)
    return Draw("wheel", lod, material, "wheelmaterials/aluminum_machined_satin.materialbin", False,
                positions, normals, uv, uv, indices, {})


class WheelModelTest(unittest.TestCase):
    def test_normalises_rim_and_removes_tyre(self):
        draws = [ring(0.236, -0.10, 0.10, "outer_rim", 0), ring(0.317, -0.10, 0.10, "outer_rim", 0),
                 ring(0.236, -0.10, 0.10, "inner_rim", 1), ring(0.317, -0.10, 0.10, "tire", 1)]
        normalised, bones, receipt = fh4_wheel_model.normalise(draws)
        points = np.concatenate([d.positions for d in normalised])
        self.assertAlmostEqual(float(points[:, 0].min()), 0.0, places=5)
        self.assertAlmostEqual(float(points[:, 0].max()), 1.0, places=5)
        self.assertLessEqual(float(np.hypot(points[:, 1], points[:, 2]).max()), 0.14 + 1e-5)
        self.assertEqual([b.name for b in bones], ["<root>", "spindle", "root"])
        self.assertAlmostEqual(receipt["spindle_x"], 0.5, places=5)
        self.assertEqual({d.name for d in normalised}, {"wheel_LODS0", "wheel_LOD1"})
        model, _ = assemble(normalised, bones, 0, wheel=True)
        self.assertTrue(model.startswith(b"burG"))
        import fh4_modelbin_geometry as raw
        roles = {struct.unpack_from("<5hHBBH5BH", model, b["offset"])[11]
                 for b in raw.blobs(model) if b["tag"] == "Mesh"}
        self.assertEqual(roles, {2})
        morphs = [b for b in raw.blobs(model) if b["tag"] == "MBuf"]
        self.assertEqual(len(morphs), len(normalised))
        count, size, stride, entries, _, fmt = struct.unpack_from("<iiHBBi", model, morphs[0]["offset"])
        self.assertEqual((stride, entries, fmt, size), (32, 4, 10, count * 32))
        first = np.frombuffer(model, "<f2", count * 16, morphs[0]["offset"] + 16).reshape(count, 16)
        lip = normalised[0].positions
        np.testing.assert_allclose(first[:, 5:7], lip[:, 1:3] * 1.318, atol=2e-3)
        self.assertIn(b"weight[1]", model)

    def test_each_lod_is_normalised_by_its_own_lip(self):
        # FM4 LOD0 rims are stretched to the tyre radius and carry no tyre; LOD1+ keep the true rim size.
        draws = [ring(0.317, -0.10, 0.10, "outer_rim", 0), ring(0.037, -0.08, -0.07, "wheel_emblem", 0),
                 ring(0.236, -0.10, 0.10, "inner_rim", 1), ring(0.028, -0.08, -0.07, "wheel_emblem", 1),
                 ring(0.317, -0.10, 0.10, "tire", 1)]
        normalised, _, receipt = fh4_wheel_model.normalise(draws)
        emblem = {d.lod: float(np.hypot(d.positions[:, 1], d.positions[:, 2]).max())
                  for d in normalised if d.source_material == "wheel_emblem"}
        lip = {d.lod: float(np.hypot(d.positions[:, 1], d.positions[:, 2]).max())
               for d in normalised if d.source_material != "wheel_emblem"}
        self.assertAlmostEqual(lip[0], 0.14, places=5)
        self.assertAlmostEqual(lip[1], 0.14, places=5)
        self.assertAlmostEqual(emblem[0], emblem[1], delta=0.001)
        self.assertAlmostEqual(receipt["lod_rim_radius_m"]["0"], 0.317, places=5)
        self.assertAlmostEqual(receipt["lod_rim_radius_m"]["1"], 0.236, places=5)

    def test_skeleton_box_is_the_native_empty_sentinel(self):
        data = rig_model([fh4_wheel_model.Bone("<root>", -1)])
        self.assertIn(struct.pack("<6f", *(FLT_MAX,) * 3, *(-FLT_MAX,) * 3), data)


class PaintTest(unittest.TestCase):
    def test_manufacturer_colors_round_trip(self):
        available = set(fh4_paint.palette())
        rows = [{"Sequence": 2, "RGB": 0x202F4D, "Metallic": 1}, {"Sequence": 1, "RGB": 0xA82727, "Metallic": 0}]
        data, receipt = fh4_paint.manufacturer_colors(rows, available)
        parsed = fh4_paint.read_manufacturer_colors(data)
        self.assertEqual([r["sequence"] for r in receipt], [1, 2])
        self.assertEqual([m for _, m in parsed], [r["material"] for r in receipt])
        self.assertAlmostEqual(parsed[0][0][0], 0xA8 / 255, places=6)
        self.assertEqual(fh4_paint.finish(receipt[1]["material"]), "metallic")


class CarbinMaterialListTest(unittest.TestCase):
    def test_colour_slot_flags_follow_the_roster(self):
        from .fh4 import material_list
        data = material_list([("window", "exterior_glass/gls_window.materialbin"),
                              ("carpaint", "exterior_misc/carpaint.materialbin"),
                              ("Chrome", "exterior_metal/chrome.materialbin")])
        count, = struct.unpack_from("<I", data)
        entries, offset = [], 4
        for _ in range(count):
            size, = struct.unpack_from("<I", data, offset)
            name = data[offset + 4:offset + 4 + size].decode()
            flags, = struct.unpack_from("<I", data, offset + 4 + size)
            entries.append((name, flags))
            offset += 8 + size
        self.assertEqual(offset, len(data))
        self.assertEqual(entries, [("Chrome", 0), ("carpaint", 1), ("window", 128)])


class CarbinGlassValueTest(unittest.TestCase):
    def test_glass_models_are_breakable_and_others_are_not(self):
        from .fh4 import breakable_glass
        self.assertEqual(breakable_glass([("window", "exterior_glass/gls_windsheild.materialbin")]), 0.5)
        self.assertEqual(breakable_glass([("lens", "exterior_glass/gls_clear.materialbin"),
                                          ("chrome", "exterior_metal/chrome.materialbin")]), 0.5)
        self.assertEqual(breakable_glass([("body", "exterior_misc/carpaint.materialbin")]), -1.0)
        self.assertEqual(breakable_glass([]), -1.0)


class GlassBindingTest(unittest.TestCase):
    def test_panes_use_native_glazing(self):
        self.assertEqual(surface_binding("window", "exterior", "glassF").member, "exterior_glass/gls_windsheild.materialbin")
        self.assertEqual(surface_binding("glass", "exterior", "glassR").member, "exterior_glass/gls_rearglass.materialbin")
        self.assertEqual(surface_binding("window", "exterior", "glassLF").member, "exterior_glass/gls_window.materialbin")
        self.assertEqual(surface_binding("window", "cockpit", "glassLF").member,
                         "interior_glass/gls_window_interior.materialbin")
        self.assertEqual(surface_binding("tail_light", "exterior", "taillightL").member,
                         "exterior_glass/gls_colored.materialbin")


if __name__ == "__main__":
    unittest.main()
