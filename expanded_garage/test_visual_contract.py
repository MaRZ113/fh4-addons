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
        # The face keeps metres (hub plane 0.1 m behind the outer face); only the barrel stretches to X=1.
        self.assertAlmostEqual(receipt["spindle_x"], 0.1, places=5)
        face = np.concatenate([d.positions[:, 0] for d in normalised if d.lod == 1])
        self.assertAlmostEqual(float(np.sort(np.unique(face.round(4)))[0]), 0.0, places=4)
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
        ids = {b["id"] for b in morphs}
        # Native: MBuf ids are dense from 0 and each pairs with the attribute VerB of the same id.
        self.assertEqual(ids, set(range(len(morphs))))
        self.assertLessEqual(ids, {b["id"] for b in raw.blobs(model) if b["tag"] == "VerB"})
        # The Modl buffer count covers IndB, every VerB and every MBuf.
        modl = next(b for b in raw.blobs(model) if b["tag"] == "Modl")
        buffers = sum(b["tag"] in ("IndB", "VerB", "MBuf") for b in raw.blobs(model))
        self.assertEqual(struct.unpack_from("<H", model, modl["offset"] + 2)[0], buffers)
        for b in raw.blobs(model):
            if b["tag"] == "Mesh":
                _, streams = struct.unpack_from("<ii", model, b["offset"] + 55)
                morph, = struct.unpack_from("<i", model, b["offset"] + 63 + 16 * streams)
                self.assertIn(morph, ids, b["name"])

    def test_face_depth_stays_metric(self):
        # A dish 0.04 m deep must stay 0.04 m deep; the barrel behind 0.1 m stretches to X=1.
        draws = [ring(0.236, -0.10, -0.10, "outer_rim", 1), ring(0.05, -0.06, -0.06, "rim", 1),
                 ring(0.236, 0.10, 0.10, "inner_rim", 1), ring(0.317, -0.10, 0.10, "tire", 1)]
        normalised, _, receipt = fh4_wheel_model.normalise(draws)
        x = {d.source_material: float(d.positions[:, 0].mean()) for d in normalised}
        self.assertAlmostEqual(x["outer_rim"], 0.0, places=4)
        self.assertAlmostEqual(x["rim"], 0.04, places=4)
        self.assertAlmostEqual(x["inner_rim"], 1.0, places=4)
        self.assertAlmostEqual(receipt["barrel_scale"], 9.0, places=4)

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


class MaterialAtstTest(unittest.TestCase):
    def test_glass_is_translucent_and_paint_is_not(self):
        from .fh4_model import atst
        self.assertEqual(atst("exterior_glass/gls_windsheild.materialbin"), b"\0\1")
        self.assertEqual(atst("exterior_glass/gls_window.materialbin"), b"\0\1")
        self.assertEqual(atst("specificuse/blackglass.materialbin"), b"\0\0")
        self.assertEqual(atst("unknown/thing.materialbin", transparent=True), b"\0\1")
        self.assertEqual(atst("unknown/thing.materialbin"), b"\0\0")


class GlassBindingTest(unittest.TestCase):
    def test_panes_use_native_glazing(self):
        self.assertEqual(surface_binding("window", "exterior", "glassF").member, "exterior_glass/gls_windsheild.materialbin")
        self.assertEqual(surface_binding("glass", "exterior", "glassR").member, "exterior_glass/gls_window.materialbin")
        self.assertEqual(surface_binding("window", "exterior", "glassLF").member, "exterior_glass/gls_window.materialbin")
        self.assertEqual(surface_binding("window", "cockpit", "glassLF").member,
                         "interior_glass/gls_window_interior.materialbin")
        self.assertEqual(surface_binding("detail_glass_red", "exterior", "taillightL").member,
                         "exterior_glass/gls_colored.materialbin")



class SteeringPivotTest(unittest.TestCase):
    def test_pivot_moves_to_rim_centre_keeping_axial_depth(self):
        from .fh4_motion import rim_center
        angles = np.linspace(0, 2 * np.pi, 64, endpoint=False)
        centre = np.array([-0.37, 0.80, -0.10])
        rim = centre + np.c_[0.18 * np.cos(angles), 0.18 * np.sin(angles), np.zeros(64)]
        hub = centre + np.c_[0.03 * np.cos(angles), 0.03 * np.sin(angles), np.full(64, 0.04)]
        draw = Draw("steering_wheel", 0, "plastic", "interior_plastic/plastic.materialbin", False,
                    np.r_[rim, hub].astype(np.float32), np.zeros((128, 3), np.float32),
                    np.zeros((128, 2), np.float32), np.zeros((128, 2), np.float32),
                    np.zeros((1, 3), np.uint32), {})
        authored = centre + np.array([0.0, -0.05, 0.02])
        pivot = rim_center([draw], authored, (0.0, 0.0, 1.0))
        np.testing.assert_allclose(pivot, centre + np.array([0.0, 0.0, 0.02]), atol=1e-4)


class LampParameterTest(unittest.TestCase):
    def test_lamp_techniques_get_native_light_functions(self):
        from . import fh4_mtpr
        from .fh4_model import lamp_parameters, lamp_donor, LAMP_MATERIAL
        hashes = lamp_donor()["hashes"]
        version = tuple(lamp_donor()["version"])
        for name, function in (("head_light", 71), ("tail_light", 69), ("reverse_light", 1)):
            self.assertEqual(surface_binding(name, "exterior", "headlightL").member, LAMP_MATERIAL)
            found = fh4_mtpr.values(lamp_parameters(name, LAMP_MATERIAL), version)
            self.assertEqual(found[hashes["function"]], function)
            self.assertEqual(found[hashes["light_map"]], 0)
        self.assertIsNone(lamp_parameters("chrome", LAMP_MATERIAL))
        self.assertIsNone(lamp_parameters("head_light", "exterior_glass/gls_clear.materialbin"))



class AoVolumeTest(unittest.TestCase):
    def test_box_shadows_the_ground_and_round_trips(self):
        from . import fh4_ao_volume as ao
        corners = np.array([[x, y, z] for x in (-0.8, 0.8) for y in (0.2, 1.2) for z in (-2.0, 2.0)])
        faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
        tris = np.array([corners[[a, b, c]] for q in faces for a, b, c in ((q[0], q[1], q[2]), (q[0], q[2], q[3]))])
        volume = ao.read(ao.build(tris))
        self.assertEqual(volume["dims"], (128, 64, 128))
        np.testing.assert_allclose(volume["centre"], (0.0, 0.7, 0.0), atol=1e-6)
        (o0, _, coarse), _, (o2, _, fine) = volume["blocks"]

        def voxel(origin, data, resolution, point):
            index = np.floor((np.array(point) - (np.array(volume["centre"]) - ao.HALF_EXTENT))
                             / (2 * ao.HALF_EXTENT) * resolution).astype(int) - origin
            return data[index[2], index[1], index[0]]
        under = voxel(o2, fine, ao.DIMS, (0.0, 0.05, 0.0))
        self.assertGreater(int(under[3]), 150)          # strong occlusion under the body
        self.assertLess(int(under[1]), 127)             # open direction points down
        self.assertEqual(int(voxel(o2, fine, ao.DIMS, (0.0, 0.7, 0.0))[3]), 254)
        self.assertLess(int(voxel(o0, coarse, ao.DIMS // 4, (4.7, 0.7, 4.7))[3]), 10)
        side = voxel(o2, fine, ao.DIMS, (1.2, 0.7, 0.0))
        self.assertLess(int(side[0]), 127)              # +x side: open direction +x, stored as -x



class GroundAoTest(unittest.TestCase):
    def test_ground_shadow_is_dark_under_the_body_and_front_is_row_zero(self):
        from . import fh4_ao_volume as ao
        # Body box raised 0.15 m over a thin "tyre" slab at its front end (+z) so the shadow is asymmetric.
        def box(lo, hi):
            c = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
            faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
            return [c[[a, b, d]] for q in faces for a, b, d in ((q[0], q[1], q[2]), (q[0], q[2], q[3]))]
        tris = np.array(box((-0.8, 0.15, -2.0), (0.8, 1.3, 2.0)) + box((-0.9, 0.0, 1.0), (0.9, 0.15, 1.6)))
        occ = ao.ground_occlusion(tris)
        self.assertGreater(occ[64, 64], 0.8)        # centre of the 10 m square: under the body
        self.assertLess(occ[2, 2], 0.05)            # corner: open sky
        rows = occ[:, 64]
        front, rear = rows[:64].argmax(), rows[64:].argmax() + 64
        self.assertLess(front, 64)                  # +z (front, tyre slab) maps to the top half
        self.assertGreater(rows[:64].sum(), rows[64:].sum())


if __name__ == "__main__":
    unittest.main()
