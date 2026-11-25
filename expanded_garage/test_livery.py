"""Livery editor masks: native BC4 layout and FM4 carry-over."""

from io import BytesIO
from pathlib import Path
import unittest
from zipfile import ZipFile

import numpy as np
from PIL import Image

from . import fh4_livery

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "game/ForzaHorizon4/media/cars/ACU_RSXTypeS_02.zip"
FM4_XML = b"""<?xml version="1.0" ?>
<LiveryMasks >
""" + b"".join(b'\t<%s valid="true" xorigin="0" yorigin="0" top="-1" bottom="1" left="-1" right="1" xAxis="-z" '
               b'yAxis="+y" xScale="1" yScale="1" rotation="0" />\n' % side.capitalize().encode()
               for side in fh4_livery.SIDES) + b"</LiveryMasks>\n"


def tga(alpha):
    rgba = np.zeros(alpha.shape + (4,), np.uint8)
    rgba[..., 3] = alpha
    out = BytesIO()
    Image.fromarray(rgba, "RGBA").save(out, format="TGA")
    return out.getvalue()


class LiveryMaskTests(unittest.TestCase):
    def test_bc4_round_trip_is_exact_for_binary_masks(self):
        rng = np.random.default_rng(7)
        image = np.where(rng.random((64, 64)) > 0.5, 255, 0).astype(np.uint8)
        image[:8, :8] = 37   # flat block
        payload = fh4_livery.bc4_encode(image)
        self.assertEqual(len(payload), 64 * 64 // 2)
        np.testing.assert_array_equal(fh4_livery.bc4_decode(payload, 64, 64), image)

    def test_fm4_masks_become_native_members(self):
        alpha = np.zeros((1024, 1024), np.uint8)
        alpha[700:1000, 200:900] = 255
        files = {"masks.xml": FM4_XML, **{f"{side}.tga": tga(alpha) for side in fh4_livery.SIDES}}
        members = fh4_livery.masks_from_source(files)
        self.assertEqual(members[fh4_livery.XML], FM4_XML)
        names = {name.casefold(): name for name in members}
        self.assertEqual(fh4_livery.verify(lambda n: members[n], names), set(names))
        width, height, fmt, payload = fh4_livery.read_mask(members["LiveryMasks/left.swatchbin"])
        self.assertEqual((width, height, fmt), (1024, 1024, fh4_livery.BC4_FORMAT))
        np.testing.assert_array_equal(fh4_livery.bc4_decode(payload, width, height), alpha)

    def test_incomplete_set_is_rejected(self):
        files = {"masks.xml": FM4_XML, "left.tga": tga(np.full((1024, 1024), 255, np.uint8))}
        self.assertEqual(fh4_livery.masks_from_source(files), {})

    @unittest.skipUnless(NATIVE.is_file(), "local FH4 install required")
    def test_header_matches_native_acura_masks(self):
        with ZipFile(NATIVE) as archive:
            for side in fh4_livery.SIDES:
                native = archive.read(f"LiveryMasks/{side}.swatchbin")
                width, height, fmt, payload = fh4_livery.read_mask(native)
                ours = fh4_livery.mask_swatch(fh4_livery.bc4_decode(payload, width, height))
                self.assertEqual(len(ours), len(native))
                self.assertEqual(ours[:len(native) - len(payload)], native[:len(native) - len(payload)])


if __name__ == "__main__":
    unittest.main()
