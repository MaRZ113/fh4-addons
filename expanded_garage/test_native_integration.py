"""Failure-path checks for v1.1 wire formats and transaction boundaries."""

from contextlib import closing
from pathlib import Path
import sqlite3
import struct
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from .database_graph import DatabaseGraph
from .fh4_db import snapshot
from . import fh4_installer as installer
from .fh4_presentation import StringTable
from .fh4_vehicle_systems import build_collision, read_collision
from .vehicle_ir import sha256


class GraphTests(unittest.TestCase):
    def test_topological_insert_preserves_original_rows(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.sqlite"
            with closing(sqlite3.connect(original)) as db, db:
                db.executescript("CREATE TABLE make (Id INTEGER PRIMARY KEY, text TEXT);"
                                 "CREATE TABLE car (Id INTEGER PRIMARY KEY, make INTEGER REFERENCES make(Id));"
                                 "INSERT INTO make VALUES (1,'stock');")
            previous = sha256(original)
            with closing(snapshot(original)) as db:
                graph = DatabaseGraph(db)
                graph.add("car", 21, "a", {"make": 61}, "fixture", (graph.key("make", 61),))
                graph.add("make", 61, "a", {"text": "new"}, "fixture")
                self.assertEqual([r.key.table for r in graph.ordered()], ["make", "car"])
                proof = graph.stage(original, root / "generated.sqlite")
                self.assertEqual(proof["insertions"], {"make": 1, "car": 1})
            self.assertEqual(sha256(original), previous)

    def test_missing_typed_reference_fails_before_write(self):
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("CREATE TABLE car (Id INTEGER PRIMARY KEY)")
            graph = DatabaseGraph(db)
            graph.add("car", 1, "fixture", {}, "fixture", (graph.key("car", 2),))
            with self.assertRaisesRegex(ValueError, "Missing native catalogue dependency"):
                graph.ordered()


class WireTests(unittest.TestCase):
    def test_version27_pair_and_padding(self):
        payload, evidence = build_collision([(-1, 0, -2), (1, 0.1, -1), (0, 1.5, 1), (-1, 0.5, 2)])
        envelope, clouds, remainder = read_collision(payload)
        self.assertEqual(envelope["shape_count"], 2)
        self.assertEqual(evidence["point_count"], 4)
        self.assertEqual(remainder, bytes(12))
        self.assertAlmostEqual(float(clouds[1][1][:, 1].min()), 0.2)
        with self.assertRaisesRegex(ValueError, "structure"):
            read_collision(payload[:160] + b"\0"*8 + payload[168:])

    def test_localization_append_keeps_existing_payload(self):
        def pool(number, text):
            buf = text.encode("utf-16-le") + b"\0\0"
            return struct.pack("<II", number, 0), buf
        body, strings = pool(101, "original")
        index, names = pool(101, "IDS_Original")
        size = len(body) + len(strings)
        front = b"\0\4Data_Car".ljust(132, b"\0")
        previous = (front + struct.pack("<5I", 140, 152 + size, size, len(strings), 1)
                    + body + strings + struct.pack("<3I", len(index)+len(names), len(names), 1) + index + names)
        table = StringTable(previous, "Data_Car")
        self.assertEqual(previous, table.encode())
        key = table.append("IDS_Expanded", "New car")
        check = StringTable(table.encode(), "Data_Car")
        self.assertEqual(check.rows[101], ("original", "IDS_Original"))
        self.assertEqual(check.rows[key], ("New car", "IDS_Expanded"))


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.host, self.bundle = root / "host", root / "package"
        self.host.mkdir()
        self.bundle.mkdir()
        self.files = []
        for name in ("media/cars/EG_FIXTURE.zip", installer.MANIFEST_PATH, installer.DB_PATH):
            old = self.host / name
            old.parent.mkdir(parents=True, exist_ok=True)
            if name in installer.REPLACEABLE:
                old.write_bytes(f"old {name}".encode())
            new = self.bundle / name
            new.parent.mkdir(parents=True, exist_ok=True)
            new.write_bytes(f"new {name}".encode())
            self.files.append({"path": name, "before": installer.current(old), "after": sha256(new)})

    @staticmethod
    def guard(path):
        pass

    def assert_preimage(self):
        for item in self.files:
            self.assertEqual(installer.current(self.host / item["path"]), item["before"])

    def test_install_and_restore_on_fixture(self):
        record = installer.transaction(self.host, self.bundle, self.files, self.guard)
        for item in self.files:
            self.assertEqual(installer.current(self.host / item["path"]), item["after"])
        folder = self.host / ".expanded-garage" / record["transaction"]
        restored = installer.recover(self.host, folder, record, self.guard)
        self.assertEqual(restored["state"], "rolled-back")
        self.assert_preimage()

    def test_failure_after_manifest_restores_the_preimage(self):
        replace = installer.sync_replace

        def fail(path, source, before, after):
            if path == self.host / installer.DB_PATH and source.is_relative_to(self.bundle):
                raise OSError("simulated native database rename failure")
            replace(path, source, before, after)

        with patch.object(installer, "sync_replace", side_effect=fail):
            with self.assertRaisesRegex(OSError, "simulated"):
                installer.transaction(self.host, self.bundle, self.files, self.guard)
        self.assert_preimage()
