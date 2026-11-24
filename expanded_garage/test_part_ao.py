"""Per-model AO maps: unwrap, bake and native texture layout."""

import struct
import unittest

import numpy as np

from . import fh4_model, fh4_part_ao


def quad(centre, size, normal_axis, sign=1.0, name="q"):
    """Two triangles of a square facing +/- along an axis."""
    u, v = [a for a in range(3) if a != normal_axis]
    corners = []
    for du, dv in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        p = np.array(centre, float)
        p[u] += du * size / 2
        p[v] += dv * size / 2
        corners.append(p)
    positions = np.array(corners, np.float32)
    normal = np.zeros(3, np.float32)
    normal[normal_axis] = sign
    indices = np.array([[0, 1, 2], [0, 2, 3]])
    face = np.cross(positions[1] - positions[0], positions[2] - positions[0])
    if face[normal_axis] * sign < 0:
        indices = indices[:, ::-1]
    return fh4_model.Draw(name, 0, "plastic", "interior_plastic/plastic.materialbin", False, positions,
                          np.tile(normal, (4, 1)), np.zeros((4, 2), np.float32), np.zeros((4, 2), np.float32),
                          indices, {})


class PartAoTests(unittest.TestCase):
    def test_unwrap_fills_texcoord2_inside_unit_square_without_overlap(self):
        draws = [quad((0, 0, 0), 0.5, 1), quad((0, 0.3, 0), 0.5, 1, -1.0, "lid")]
        out, side = fh4_part_ao.unwrap(draws)
        self.assertGreaterEqual(side, fh4_part_ao.MIN_SIZE)
        boxes = []
        for d in out:
            self.assertEqual(d.uv2.shape, (len(d.positions), 2))
            self.assertTrue(np.all((d.uv2 >= 0) & (d.uv2 <= 1)))
            boxes.append((d.uv2.min(axis=0), d.uv2.max(axis=0)))
        (a0, a1), (b0, b1) = boxes
        self.assertTrue(np.any(a1 <= b0 + 1e-6) or np.any(b1 <= a0 + 1e-6))   # separate charts

    def test_covered_surface_is_darker_than_open_one(self):
        floor = quad((0, 0, 0), 0.6, 1)
        open_floor = quad((2, 0, 0), 0.6, 1, name="open")
        lid = quad((0, 0.1, 0), 0.8, 1, -1.0, "lid")
        occluders = fh4_part_ao.Occluders(fh4_part_ao.occluder_triangles([[floor], [open_floor], [lid]]))
        points = np.array([[0, 0, 0], [2, 0, 0]], float)
        up = np.array([[0, 1, 0], [0, 1, 0]], float)
        covered, free = occluders.visibility(points, up)
        self.assertLess(covered, 0.3)
        self.assertGreater(free, 0.9)

    def test_texture_is_native_r8_with_mips(self):
        data = fh4_part_ao.texture("x", np.full((64, 64), 200, np.uint8))
        _, _, _, _, meta, offset, size, _ = fh4_model.BLOB.unpack_from(data, fh4_model.HEADER.size)
        _, flags, relative = struct.unpack_from("<IHH", data, meta)
        fields = struct.unpack_from("<17I", data, meta + relative)
        self.assertEqual((fields[6], fields[7], fields[16], fields[9] >> 16), (64, 64, 19, 7))
        self.assertEqual(size, sum((64 >> k) ** 2 for k in range(7)))


if __name__ == "__main__":
    unittest.main()
