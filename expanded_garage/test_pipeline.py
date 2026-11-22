"""Regression checks for wire offsets, isolated identities, and geometry serialization."""

from contextlib import closing
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
from zipfile import ZipFile

import numpy as np

from . import fh4, fh4_archive, fh4_db, fh4_model, fh4_physics


class ArchiveTests(unittest.TestCase):
    def test_central_only_native_offset_and_direct_read(self):
        blob, directory = fh4_archive.pack({"a.carbin": b"car", "scene/body.modelbin": b"body" * 9000})
        with ZipFile(BytesIO(blob)) as archive:
            for item, extra, payload in fh4_archive.directories(blob, archive):
                self.assertEqual(struct.unpack_from("<H", blob, item.header_offset + 28)[0], 0)
                self.assertEqual(fh4_archive.OFFSET_EXTRA.unpack_from(blob, extra), (0x1123, 4, payload))
        changed = bytearray(blob)
        struct.pack_into("<I", changed, extra + 4, payload + 1)
        with self.assertRaisesRegex(ValueError, "0x1123"):
            fh4_archive.verify(bytes(changed))
        self.assertEqual(directory.entries, 2)
        self.assertEqual(len(blob) - directory.start, directory.size)
        self.assertEqual(blob, fh4_archive.pack({"scene/body.modelbin": b"body" * 9000, "a.carbin": b"car"})[0])

    def test_mount_preserves_existing_rows_and_rejects_duplicate(self):
        stock = (b'<ZipFiles version="3">\r\n  <Zip version="1" path="game:\\media\\cars\\stock.zip" '
                 b'priority="80" dirstart="11" dirsize="22" direntries="3" />\r\n</ZipFiles>\r\n')
        layout = fh4_archive.Directory(100, 35, 2)
        result = fh4_archive.add_mount(stock, "EG_Test", layout)
        self.assertIn(stock.splitlines()[1], result)
        self.assertIn(b'path="game:\\media\\cars\\eg_test.zip"', result)
        with self.assertRaisesRegex(ValueError, "already exists"):
            fh4_archive.add_mount(result, "EG_TEST", layout)


class RegistryTests(unittest.TestCase):
    def test_reservation_is_stable_and_collision_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            target, registry = folder / "target.sqlite", folder / "registry.sqlite"
            with closing(sqlite3.connect(target)) as db, db:
                db.execute("CREATE TABLE Data_Car(Id INT, ModelId INT, MediaName TEXT)")
                db.execute("CREATE TABLE Data_CarBody(Id INT)")
                db.execute("INSERT INTO Data_Car VALUES (5,12000,'FAKE')")
                db.execute("INSERT INTO Data_CarBody VALUES (12001000)")
            original = sha256(target.read_bytes()).digest()
            car = {"source_game": "fm4", "source_id": 5, "media_name": "TEST_A"}
            first = fh4_db.reserve(registry, target, car)
            self.assertEqual(first["car_id"], 12002)
            self.assertEqual(first, fh4_db.reserve(registry, target, car))
            self.assertEqual(original, sha256(target.read_bytes()).digest())
            with closing(sqlite3.connect(target)) as db, db:
                db.execute("INSERT INTO Data_Car VALUES (12002,5,'NEW')")
            with self.assertRaisesRegex(ValueError, "collides"):
                fh4_db.reserve(registry, target, car)


class ModelTests(unittest.TestCase):
    def test_independent_render_package(self):
        positions = np.asarray(((0, 0, 0), (1, 0, 0), (0, 1, 0)), dtype="<f4")
        normals = np.tile((0, 0, 1), (3, 1)).astype("<f4")
        uv = positions[:, :2]
        indices = np.asarray([[0, 1, 2]], dtype="<u4")
        draw = fh4_model.Draw("triangle", 0, "body", "exterior_misc/carpaint.materialbin", False,
                              positions, normals, uv, uv, indices, {})
        bones = [fh4_model.Bone("<root>", -1), fh4_model.Bone("root", 0)]
        model, mask = fh4_model.assemble([draw], bones)
        target = {"car_id": 12000, "media_name": "EG_Test"}
        skeleton_name = "scene/eg_test_skeleton.modelbin"
        scene = fh4.carbin(target, bones, skeleton_name,
                           [fh4.Model("scene/body.modelbin", "exterior", mask, 0, (0, 0, 0))],
                           ((0, 0, 0), (1, 1, 0)))
        blob, layout = fh4_archive.pack({"eg_test.carbin": scene, "scene/body.modelbin": model,
                                         skeleton_name: fh4_model.rig_model(bones)})
        self.assertEqual(layout, fh4.validate_zip(blob, "EG_Test", 12000))
        with self.assertRaisesRegex(ValueError, "identity"):
            fh4.validate_zip(blob, "EG_Test", 12001)

    def test_generated_envelope_contains_source_points(self):
        corners = np.asarray([(x, y, z) for x in (-1, 1) for y in (-2, 2) for z in (-3, 3)])
        hull = fh4_physics.hull_envelope(corners)
        self.assertEqual(set(map(tuple, corners)), set(hull["vertices_m"]))
        for p in hull["planes"]:
            self.assertTrue(np.all(corners @ p["outward_normal"] <= p["distance_m"] + 1e-7))


if __name__ == "__main__":
    unittest.main()
