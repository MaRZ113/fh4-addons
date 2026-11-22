"""Vehicle IR storage, validation, and immutable source receipts."""

import hashlib
import json
import re
from pathlib import Path


VERSION = 1
SECTIONS = ("geometry", "structure", "materials", "effects", "physics", "audio")
COORDINATE_SYSTEM = {"x": "right", "y": "up", "z": "forward", "length": "m",
                     "triangle_order": "oriented triples"}


def media_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", value):
        raise ValueError(f"Invalid MediaName {value!r}")
    return value


def member_path(value):
    value = value.replace("\\", "/")
    if any(not re.fullmatch(r"[A-Za-z0-9_. +()@&'!,\[\]{}=~-]+", part)
           or part in (".", "..") or part.endswith((" ", ".")) for part in value.split("/")):
        raise ValueError(f"Unsafe archive path {value!r}")
    return value


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8", newline="\n")


def provenance(asset, locator, units, *transforms):
    return {"asset": asset, "locator": locator, "units": units, "transforms": list(transforms)}


class Assets:
    def __init__(self, root):
        self.root = root
        self.records = {}

    def add(self, data, container, entry, role):
        digest = hashlib.sha256(data).hexdigest()
        record = self.records.get(digest)
        source = {"container": str(container), "entry": entry, "role": role}
        if record is None:
            rel = f"blobs/{digest}"
            target = self.root / rel
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(data)
            record = self.records[digest] = {"path": rel, "size": len(data), "sources": []}
        if source not in record["sources"]:
            record["sources"].append(source)
        return digest


def load(root):
    ir = json.loads((root / "vehicle.json").read_text(encoding="utf-8"))
    if (ir.get("schema_version") != VERSION or ir.get("coordinate_system") != COORDINATE_SYSTEM
            or set(ir.get("sections", {})) != set(SECTIONS)):
        raise ValueError("Unsupported Vehicle IR version or section inventory")
    media_name(ir["vehicle"]["media_name"])
    assets = ir["assets"]
    for digest, record in assets.items():
        if not re.fullmatch("[a-f0-9]{64}", digest) or record["path"] != f"blobs/{digest}":
            raise ValueError("Invalid content-addressed IR path")
        path = root / record["path"]
        if path.stat().st_size != record["size"] or sha256(path) != digest or not record["sources"]:
            raise ValueError(f"Invalid IR asset {digest}")
    pending = [ir["raw_members"], ir["presentation"]]
    for section in ir["sections"].values():
        if section["status"] not in {"decoded", "partial", "opaque", "unavailable"}:
            raise ValueError("Unsupported section status")
        pending.append(section["data"])
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if "provenance" in value:
                p = value["provenance"]
                if p["asset"] not in assets or not p["locator"] or not p["units"]:
                    raise ValueError(f"Unresolved provenance {p}")
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return ir


def read_asset(root, ir, digest):
    return (Path(root) / ir["assets"][digest]["path"]).read_bytes()
