"""Typed insert-only graph for the native catalogue."""

from collections import Counter
from dataclasses import dataclass, field
import sqlite3
import tempfile
from pathlib import Path
from contextlib import closing

from .fh4_db import snapshot
from .fh4_native_defaults import NativeDefaults
from .vehicle_ir import sha256


def ident(value):
    return '"' + value.replace('"', '""') + '"'


@dataclass(frozen=True)
class Key:
    table: str
    field: str
    value: int


@dataclass
class Row:
    key: Key
    owner: str
    values: dict
    rule: str
    dependencies: tuple = field(default_factory=tuple)
    native_defaults: dict = field(default_factory=dict)

    def receipt(self):
        receipt = {"key": vars(self.key), "owner": self.owner, "rule": self.rule,
                   "values": self.values, "dependencies": [vars(key) for key in self.dependencies]}
        if self.native_defaults:
            receipt["native_defaults"] = {c: {"rule": r, "value": v}
                                          for c, (r, v) in sorted(self.native_defaults.items())}
        return receipt


class DatabaseGraph:
    def __init__(self, db):
        self.db = db
        self.rows = {}
        self.spec = {}
        self.unresolved = []
        self.defaults = NativeDefaults(db)

    def schema(self, table):
        if table not in self.spec:
            row = self.db.execute("SELECT type FROM sqlite_schema WHERE name=?", (table,)).fetchone()
            if row is None or row[0] != "table":
                raise ValueError(f"Unknown native table {table}")
            cols = self.db.execute(f"PRAGMA table_info({ident(table)})").fetchall()
            primary = [c[1] for c in cols if c[5]]
            if not primary and table == "CarPartPositions" and "ID" in [c[1] for c in cols]:
                primary = ["ID"]
            if len(primary) != 1:
                raise ValueError(f"{table} requires a single native primary key")
            self.spec[table] = (primary[0], [c[1] for c in cols])
        return self.spec[table]

    def key(self, table, value):
        if type(value) is not int or not 0 <= value <= 2147483647:
            raise ValueError(f"Invalid {table} ID {value!r}")
        return Key(table, self.schema(table)[0], value)

    def exists(self, key):
        return self.db.execute(f"SELECT 1 FROM {ident(key.table)} WHERE {ident(key.field)}=?",
                               (key.value,)).fetchone() is not None

    def add(self, table, value, owner, fields, rule, links=(), keep=()):
        key = self.key(table, value)
        pk, columns = self.schema(table)
        if key in self.rows or self.exists(key):
            raise ValueError(f"Occupied native primary key {key}")
        unknown = set(fields) - set(columns)
        if unknown:
            raise ValueError(f"Unknown native fields in {table}: {sorted(unknown)}")
        values = {column: fields[column] for column in columns if column in fields and column != pk}
        values, filled = self.defaults.complete(table, pk, values, keep)
        values = {column: values[column] for column in columns if column in values}
        values[pk] = value
        self.rows[key] = Row(key, owner, values, rule, tuple(dict.fromkeys(links)), filled)
        return key

    def ordered(self):
        """Topologically order insertions and resolve checked shared keys."""
        ordered, visiting, emitted = [], set(), set()

        def visit(key):
            if key in emitted:
                return
            if key in visiting:
                raise ValueError(f"Cyclic native catalogue dependency {key}")
            row = self.rows.get(key)
            if row is None:
                if not self.exists(key):
                    raise ValueError(f"Missing native catalogue dependency {key}")
            else:
                visiting.add(key)
                for link in row.dependencies:
                    visit(link)
                visiting.remove(key)
                ordered.append(row)
            emitted.add(key)

        for key in self.rows:
            visit(key)
        return ordered

    def receipt(self):
        return {"rows": [row.receipt() for row in self.ordered()],
                "unresolved": self.unresolved}

    def stage(self, source, output):
        ordered = self.ordered()
        baseline = sha256(source)
        if output.exists() or source.resolve() == output.resolve():
            raise ValueError(f"Database output must be new: {output}")
        expected = Counter(row.key.table for row in ordered)
        with tempfile.TemporaryDirectory(dir=output.parent, prefix="catalogue-") as tmp:
            scratch = Path(tmp) / "gamedb.sqlite"
            with closing(sqlite3.connect(scratch)) as db:
                self.db.backup(db)
                db.execute("PRAGMA foreign_keys=ON")
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    for row in ordered:
                        cols = ", ".join(map(ident, row.values))
                        masks = ", ".join("?" for _ in row.values)
                        try:
                            db.execute(f"INSERT INTO {ident(row.key.table)} ({cols}) VALUES ({masks})",
                                       tuple(row.values.values()))
                        except sqlite3.IntegrityError as error:
                            failing = [fk for fk in db.execute(f"PRAGMA foreign_key_list({ident(row.key.table)})")
                                       if fk[3] in row.values and db.execute(
                                           f"SELECT 1 FROM {ident(fk[2])} WHERE {ident(fk[4] or 'rowid')}=?",
                                           (row.values[fk[3]],)).fetchone() is None]
                            raise ValueError(f"{error} inserting {row.key}: "
                                             + ", ".join(f"{fk[3]}={row.values[fk[3]]!r} -> {fk[2]}.{fk[4]}"
                                                         for fk in failing)) from error
            with closing(snapshot(scratch)) as db, closing(snapshot(source)) as before:
                delta = audit_insertions(before, db, expected)
                for row in ordered:
                    key = row.key
                    got = db.execute(f"SELECT * FROM {ident(key.table)} WHERE {ident(key.field)}=?",
                                     (key.value,)).fetchone()
                    if got is None or any(got[c] != v for c, v in row.values.items()):
                        raise ValueError(f"Catalogue readback differs for {key}")
                    nulls = self.defaults.violations(key.table, got)
                    if nulls:
                        raise ValueError(f"NULL in native-required columns of {key}: {nulls}")
            if sha256(source) != baseline:
                raise ValueError("Database baseline changed while staging")
            scratch.rename(output)
        return {"baseline_sha256": baseline, "output_sha256": sha256(output),
                "insertions": delta, "total_rows_inserted": sum(delta.values())}


def audit_insertions(before, after, expected):
    schema = "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
    if list(map(tuple, before.execute(schema))) != list(map(tuple, after.execute(schema))):
        raise ValueError("Staging modified the baseline schema")
    if after.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise ValueError("Staged catalogue integrity check failed")
    if Counter(map(tuple, before.execute("PRAGMA foreign_key_check"))) != Counter(
            map(tuple, after.execute("PRAGMA foreign_key_check"))):
        raise ValueError("Declared foreign keys differ from baseline")
    counts = {}
    for name, in before.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        sql = f"SELECT * FROM {ident(name)}"
        old = Counter(map(tuple, before.execute(sql)))
        new = Counter(map(tuple, after.execute(sql)))
        removed, added = old - new, new - old
        if removed or added.total() != expected.get(name, 0):
            raise ValueError(f"Unexpected stock record changes in {name}")
        if added:
            counts[name] = added.total()
    if counts != dict(expected):
        raise ValueError("Unexpected staged table inventory")
    return counts
