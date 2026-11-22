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
                            {"CamHoodZOffset": 0.31, "CamBumperLowZOffset": 0.4}, "test")
            row = graph.rows[key]
            self.assertEqual(row.values["CamFollowLowMaxHeightOffset"], 0.0)   # native mode
            self.assertEqual(row.values["CamNoWheelCockpitOffsetZ"], 0.25)     # native median
            self.assertEqual(row.values["CamHoodZOffset"], -0.6)               # re-authored in FH4
            self.assertEqual(row.values["CamBumperLowZOffset"], 0.4)           # carried from source
            self.assertNotIn("Note", row.values)                               # nullable natively
            self.assertIn("CamHoodZOffset", row.receipt()["native_defaults"])

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
