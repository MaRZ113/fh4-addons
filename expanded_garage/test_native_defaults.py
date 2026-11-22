import sqlite3
import tempfile
import unittest
from pathlib import Path

from .database_graph import DatabaseGraph


def baseline(path):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE CameraOverrides (CarId INTEGER PRIMARY KEY, CamFollowLowMaxHeightOffset REAL,"
               " CamNoWheelCockpitOffsetZ REAL, CamHoodZOffset REAL, CamBumperLowZOffset REAL, Note TEXT)")
    db.executemany("INSERT INTO CameraOverrides VALUES (?,?,?,?,?,?)",
                   [(1, 0.0, 0.2, -0.6, 0.1, None), (2, 0.0, 0.3, -0.5, 0.0, "x"), (3, 0.85, 0.25, -0.9, 0.0, None)])
    db.commit()
    return db


class NativeDefaultsTest(unittest.TestCase):
    def test_fills_required_columns_and_replaces_reauthored(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = baseline(Path(tmp) / "b.sqlite")
            self.addCleanup(db.close)
            graph = DatabaseGraph(db)
            key = graph.add("CameraOverrides", 12000, "car",
                            {"CamHoodZOffset": 0.31, "CamBumperLowZOffset": 0.05}, "test")
            row = graph.rows[key]
            self.assertEqual(row.values["CamFollowLowMaxHeightOffset"], 0.0)   # native mode
            self.assertEqual(row.values["CamNoWheelCockpitOffsetZ"], 0.25)     # native median
            self.assertEqual(row.values["CamHoodZOffset"], -0.6)               # re-authored in FH4
            self.assertEqual(row.values["CamBumperLowZOffset"], 0.05)          # carried from source
            self.assertNotIn("Note", row.values)                               # nullable natively
            self.assertIn("CamHoodZOffset", row.receipt()["native_defaults"])

    def test_native_envelope_constant_and_clamp(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = sqlite3.connect(Path(tmp) / "e.sqlite")
            self.addCleanup(db.close)
            db.execute("CREATE TABLE Springs (Id INTEGER PRIMARY KEY, Ratio REAL, Stiffness REAL, PartId INT)")
            db.executemany("INSERT INTO Springs VALUES (?,?,?,?)",
                           [(i, 0.0, 50.0 + i, i) for i in range(1, 30)])
            graph = DatabaseGraph(db)
            key = graph.add("Springs", 12000, "car", {"Ratio": 0.25, "Stiffness": 200.0, "PartId": 999}, "test")
            row = graph.rows[key]
            self.assertEqual(row.values["Ratio"], 0.0)          # constant over all native rows
            self.assertEqual(row.values["Stiffness"], 79.0)     # clamped to native max
            self.assertEqual(row.values["PartId"], 999)         # identifiers are exempt
            self.assertEqual(row.native_defaults["Ratio"][0], "native_constant")

    def test_stage_rejects_null_in_required_column(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            source = Path(tmp) / "b.sqlite"
            baseline(source).close()
            db = sqlite3.connect(source)
            graph = DatabaseGraph(db)
            key = graph.add("CameraOverrides", 12000, "car", {}, "test")
            graph.rows[key].values["CamNoWheelCockpitOffsetZ"] = None
            with self.assertRaisesRegex(ValueError, "NULL in native-required columns"):
                graph.stage(source, Path(tmp) / "out.sqlite")
            db.close()


if __name__ == "__main__":
    unittest.main()
