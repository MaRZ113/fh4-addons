"""Surface detail: native TEXCOORD1 density, per-channel tangents, BC7 swatch headers, opaque black trims."""

from pathlib import Path
import os
import struct
from unittest import mock
import unittest
from zipfile import ZipFile

import numpy as np

from . import fh4_model
from .fh4_material_contract import surface_binding
from .fh4_presentation import mip_header

NATIVE = Path(__file__).resolve().parents[1] / "game/ForzaHorizon4/media/cars/ACU_RSXTypeS_02.zip"


class DetailUvTests(unittest.TestCase):
    def test_projection_splits_axes_and_uses_native_density(self):
        # A fold: one triangle facing +z, one facing +y, sharing an edge.
        positions = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], float)
        normals = np.tile([0.0, 0.0, 1.0], (4, 1))
        indices = np.array([[0, 1, 2], [0, 3, 1]])
        p, n, u0, tri, uv1 = fh4_model.projected_detail(positions, normals, np.zeros((4, 2)), indices,
                                                         "exterior_misc/carpaint.materialbin")
        density = fh4_model.detail_densities()["materials"]["exterior_misc/carpaint.materialbin"]
        self.assertEqual(len(p), 6)   # the shared edge (vertices 0, 1) is duplicated per projection
        np.testing.assert_allclose(p[tri], positions[indices])
        first = uv1[tri[0]] / density
        np.testing.assert_allclose(first, positions[indices[0]][:, :2])   # +z face: (x, y)
        second = uv1[tri[1]] / density
        np.testing.assert_allclose(second, positions[indices[1]][:, (0, 2)])   # +y face: (x, z)

    def test_missing_unwrap_projects_on_dominant_axis(self):
        positions = np.array([[0, 0, 0], [1, 0, 0], [0, 0, 1]], float)
        normals = np.tile([0.0, 1.0, 0.0], (3, 1))
        detail = fh4_model.detail_uv(positions, normals, np.zeros((3, 2)), "unknown.materialbin")
        default = fh4_model.detail_densities()["default"]
        np.testing.assert_allclose(detail, np.array([[0, 0], [1, 0], [0, 1]]) * default)

    def test_second_tangent_follows_texcoord1(self):
        positions = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float)
        normals = np.tile([0.0, 0.0, 1.0], (3, 1))
        tri = np.array([[0, 1, 2]])
        along_x = fh4_model.steps(positions, normals, positions[:, :2], tri)
        along_y = fh4_model.steps(positions, normals, positions[:, 1::-1], tri)
        self.assertFalse(np.array_equal(along_x, along_y))


class BlackGlassTests(unittest.TestCase):
    def test_black_glass_stays_native_glass_and_opaque_variant_is_opt_in(self):
        import os
        frit = surface_binding("black_glass", "exterior", "body")
        self.assertEqual(frit.member, "wheelmaterials/paint_glossblack.materialbin")   # FM4 glass-like gloss
        self.assertFalse(frit.transparent)
        # On a pane model the frit stays the native glass-family blackGlass.
        self.assertEqual(surface_binding("black_glass", "exterior", "glassR").member,
                         "specificuse/blackglass.materialbin")
        os.environ["EXPANDED_GARAGE_BLACK_GLASS"] = "native"
        try:
            native = surface_binding("black_glass", "exterior", "body")
            self.assertEqual(native.member, "specificuse/blackglass.materialbin")
            self.assertFalse(native.transparent)   # native blackGlass: opaque, bucket 17 (visual-40)
        finally:
            del os.environ["EXPANDED_GARAGE_BLACK_GLASS"]
        os.environ["EXPANDED_GARAGE_BLACK_GLASS"] = "opaque"
        try:
            trim = surface_binding("black_glass", "exterior", "body")
            self.assertEqual(trim.member, "exterior_plastic/plastic_mgl.materialbin")
            self.assertFalse(trim.transparent)
        finally:
            del os.environ["EXPANDED_GARAGE_BLACK_GLASS"]
        os.environ["EXPANDED_GARAGE_BLACK_GLASS"] = "translucent"
        try:
            self.assertTrue(surface_binding("black_glass", "exterior", "body").transparent)   # visual-27 frit
        finally:
            del os.environ["EXPANDED_GARAGE_BLACK_GLASS"]


class TrimNormalTests(unittest.TestCase):
    def test_bent_normals_on_flat_trim_are_rebuilt_and_good_ones_kept(self):
        positions = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], float)
        normals = np.array([[0, 0, 1], [0, 0, 1], [0.5, 0, 0.866], [0, 0, -1]], float)   # 30 deg bent, one flipped
        indices = np.array([[0, 1, 2], [1, 3, 2]])
        out, replaced = fh4_model.geometric_normals(positions, normals, indices)
        self.assertEqual(replaced, 1)
        np.testing.assert_allclose(out[2], [0, 0, 1], atol=1e-6)
        np.testing.assert_allclose(out[3], [0, 0, -1])   # agrees up to orientation: kept as authored


class ShadowTests(unittest.TestCase):
    def test_panes_cast_no_shadow_like_native_glass(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        from fh4_modelbin_geometry import blobs
        positions = np.asarray(((0, 0, 0), (1, 0, 0), (0, 1, 0)), dtype="<f4")
        normals = np.tile((0, 0, 1), (3, 1)).astype("<f4")
        indices = np.asarray([[0, 1, 2]], dtype="<u4")
        bones = [fh4_model.Bone("<root>", -1), fh4_model.Bone("root", 0)]
        def shadows(transparent, member):
            draw = fh4_model.Draw("t", 0, "x", member, transparent, positions, normals, positions[:, :2],
                                  positions[:, :2], indices, {})
            model, _ = fh4_model.assemble([draw], bones)
            return sum(1 for b in blobs(model) if b["tag"] == "Mesh" and b["name"] == "Shadow")
        self.assertEqual(shadows(True, "exterior_glass/gls_window.materialbin"), 0)
        self.assertEqual(shadows(False, "exterior_misc/carpaint.materialbin"), 1)


class LiveryUvTests(unittest.TestCase):
    def test_opaque_surfaces_carry_uv0_as_texcoord3(self):
        positions = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32)
        draw = fh4_model.Draw("t", 0, "body", "exterior_misc/carpaint.materialbin", False, positions,
                              np.tile(np.float32([0, 0, 1]), (3, 1)), np.float32([[0.1, 0.2], [0.5, 0.2], [0.1, 0.9]]),
                              np.zeros((3, 2), np.float32), np.array([[0, 1, 2]]), {})
        _, attrs, transforms, *_ = fh4_model.pack_streams(draw)
        self.assertEqual(transforms[3], transforms[0])
        raw = np.frombuffer(attrs, np.uint8).reshape(3, 36)
        np.testing.assert_array_equal(raw[:, 4:8], raw[:, 16:20])


class MipHeaderTests(unittest.TestCase):
    @unittest.skipUnless(NATIVE.is_file(), "local FH4 install required")
    def test_header_matches_native_gauge_diffuse(self):
        with ZipFile(NATIVE) as archive:
            name = next(n for n in archive.namelist() if "gauges_DIFF" in n)
            data = archive.read(name)
        _, _, _, _, meta, _, _, _ = struct.unpack_from("<IBBHIIII", data, 20)
        _, flags, relative = struct.unpack_from("<IHH", data, meta)
        native = data[meta + relative:meta + relative + (flags >> 4)]
        fields = struct.unpack_from(f"<{len(native) // 4}I", native)
        mips = [fields[19 + 3 * level] for level in range((len(native) - 76) // 12)]
        self.assertEqual(mip_header(native[8:24], fields[6], fields[7], fields[16], mips), native)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless((Path(__file__).resolve().parents[1] / "game/ForzaHorizon4/media/cars/_library/Materials.zip").is_file(),
                     "stock material library not present")
class NeedleColourTests(unittest.TestCase):
    def test_needle_reads_the_cockpit_atlas_under_a_white_colour(self):
        from . import fh4, fh4_mtpr
        library = Path(__file__).resolve().parents[1] / "game/ForzaHorizon4/media/cars/_library/Materials.zip"
        atlas = r"Game:\Media\cars\x\textures\gauges\swatches\x_gauges_diff.swatchbin"
        (key, (version, block)), = fh4.needle_parameters(library, atlas).items()
        self.assertEqual(key, ("gauge_needle", fh4.NEEDLE_MATERIAL))
        values = fh4_mtpr.values(block, version)
        self.assertEqual(values[fh4_mtpr.name_hash("CH1DiffColTextureSwitchBool")], 1)
        self.assertEqual(values[fh4_mtpr.name_hash("DiffuseColorColorParam")][:3], (1.0, 1.0, 1.0))
        self.assertIn(atlas.encode("ascii"), block)


class DirtMaskTests(unittest.TestCase):
    def quad(self, corners, uvs, normal):
        positions = np.asarray(corners, float)
        return fh4_model.Draw(name="q", lod=0, source_material="body", target_material="x", transparent=False,
                              positions=positions, normals=np.tile(normal, (4, 1)).astype(float),
                              uv0=np.asarray(uvs, float), uv1=np.zeros((4, 2)),
                              indices=np.array([[0, 1, 2], [0, 2, 3]]), origin={})

    def test_sills_are_dirty_roof_is_clean_and_sides_marked(self):
        from . import fh4_dirt
        side = self.quad([[1, 0, -2], [1, 0, 2], [1, 1, 2], [1, 1, -2]], [[0, 1], [0.5, 1], [0.5, 0], [0, 0]], [1, 0, 0])
        roof = self.quad([[-1, 1.4, -1], [1, 1.4, -1], [1, 1.4, 1], [-1, 1.4, 1]], [[0.5, 1], [1, 1], [1, 0], [0.5, 0]], [0, 1, 0])
        rgba = fh4_dirt.splatter_rgba([side, roof], [((0.8, 0.3, 1.3), 0.3, 0.2)], size=64)
        sill, door_top, roof_px = rgba[60, 16], rgba[4, 16], rgba[32, 48]
        self.assertGreater(sill[1], 100)          # G: dirt at the bottom of the side
        self.assertLess(door_top[1], 10)          # clean high on the side
        self.assertLess(roof_px[1], 10)
        self.assertGreater(sill[0], 200)          # R: vertical panel
        self.assertLess(roof_px[0], 10)           # roof is not a side

    def test_wiper_ramps_cover_the_lower_windscreen(self):
        from . import fh4_dirt
        pane = self.quad([[-0.7, 0.9, 0.8], [0.7, 0.9, 0.8], [0.7, 1.3, 0.2], [-0.7, 1.3, 0.2]],
                         [[0, 0], [1, 0], [1, 1], [0, 1]], [0, 0.8, 0.6])
        rgba = fh4_dirt.wiper_rgba([pane], "L", size=64)
        self.assertTrue(rgba[..., 0].any() and rgba[..., 1].any())
        self.assertGreater(rgba[..., 0].max(), 200)


class VertexDirtTests(unittest.TestCase):
    def test_dirt_high_low_down_and_exposure_high_on_the_roof(self):
        from . import fh4_dirt
        positions = np.array([[0.9, 0.2, 1.0], [0.9, 0.9, 0.0], [0.0, 1.5, 0.0]])
        normals = np.array([[1.0, 0, 0], [1.0, 0, 0], [0, 1.0, 0]])
        colours = fh4_dirt.vertex_colors(positions, normals, [((0.8, 0.3, 1.3), 0.3, 0.2)], 0.0)
        sill, door, roof = colours
        self.assertGreater(sill[1], 140)          # splatter next to the wheel
        self.assertLess(door[1], 40)
        self.assertEqual(roof[1], 0)
        self.assertLess(sill[0], 40)              # R ~0 at the sills: the (1 - 2R) dust layer is densest there
        self.assertGreater(door[0], sill[0])      # thinning out up the door
        self.assertGreater(roof[0], 128)          # and none on the roof (R > 0.5)
        self.assertTrue((colours[:, 3] == 255).all())

    def test_instrument_cover_is_clear_glass_not_the_window_shader(self):
        self.assertEqual(surface_binding("glass", "cockpit", "interior").member, "interior_glass/gls_clear.materialbin")
        self.assertEqual(surface_binding("window", "cockpit", "glassF").member,
                         "interior_glass/gls_windsheild_interior.materialbin")


class ReflectorTests(unittest.TestCase):
    def test_textured_reflector_reads_the_lamp_atlas_on_the_badge_shader(self):
        self.assertEqual(surface_binding("textured_reflector", "exterior", "bumperRa").member,
                         "mfr_emblems/m_002.materialbin")
        # FM4 `reflector` is a lamp technique (LightsSampler): the lamp atlas too, not plain chrome.
        self.assertEqual(surface_binding("reflector", "exterior", "headlightL").member,
                         "mfr_emblems/m_002.materialbin")


class SourceAtlasTests(unittest.TestCase):
    def test_fm4_base_sampler_classes_take_the_car_atlas(self):
        plastic = "exterior_plastic/plastic_ch1_custom_diffusemap.materialbin"
        self.assertEqual(surface_binding("matte_colors", "exterior", "bumperFa").member, plastic)
        self.assertEqual(surface_binding("plastic2", "cockpit", "hoodb").member, plastic)
        self.assertEqual(surface_binding("misc", "brake", "rotorLF").member, plastic)
        emblem = surface_binding("emblem", "cockpit", "hooda")
        self.assertEqual((emblem.member, emblem.transparent), ("exterior_misc/sticker_mfr_cutsom.materialbin", True))
        self.assertEqual(surface_binding("rim", "wheel", "wheel").member, "mfr_emblems/m_002.materialbin")
        self.assertEqual(surface_binding("grille1", "exterior", "hoodb").member,
                         "exterior_metal/metal_ch1_custom.materialbin")
        self.assertEqual(surface_binding("detail_glass_amber", "exterior", "bumperFk").member,
                         "exterior_glass/gls_colored.materialbin")
        # Cabin parts keep the cockpit atlas.
        self.assertEqual(surface_binding("plastic", "cockpit", "interior").member,
                         "interior_plastic/plastic_perforated_004.materialbin")

    def test_switch_restores_the_untextured_bindings(self):
        with mock.patch.dict(os.environ, {"EXPANDED_GARAGE_SOURCE_ATLAS": "off"}):
            self.assertEqual(surface_binding("matte_colors", "exterior", "bumperFa").member,
                             "exterior_misc/carpaint.materialbin")
