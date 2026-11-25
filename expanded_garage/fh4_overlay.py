"""Overlay payloads on top of an installed fleet: native cars built against the installed game (FH3 Wraith pilot).

An overlay is a folder with `files/<game-relative path>` and a `receipt.json` listing every file with its
`sha256` and the `preimage` it replaces (null: the path must be absent). The FH3 Wraith pilot
(`FH3_Wraith_Pilot/build_wraith.py`) writes exactly that from the installed game, so its database, manifest,
strings and audio maps already contain the installed fleet.

`apply` checks every preimage against the host, keeps the replaced files under
`<host>/.expanded-garage/overlay-<name>/previous/`, copies the payload and verifies it. `rollback` restores the
backups (or deletes files that did not exist) after checking the host still holds the overlay. An overlay must be
rolled back before the fleet transaction under it, and re-applied (rebuilt against the new fleet) after.
"""

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _state(host, name):
    return Path(host) / ".expanded-garage" / f"overlay-{name}"


def apply(host, overlay, name):
    host, overlay = Path(host), Path(overlay)
    receipt = json.loads((overlay / "receipt.json").read_text(encoding="utf-8"))
    state = _state(host, name)
    if (state / "journal.json").exists():
        raise ValueError(f"Overlay {name} is already applied; roll it back first")
    files = receipt["files"]
    for item in files:
        target = host / item["path"]
        source = overlay / "files" / item["path"]
        if digest(source) != item["sha256"]:
            raise ValueError(f"Overlay payload differs from its receipt: {item['path']}")
        current = digest(target) if target.exists() else None
        if current != item["preimage"]:
            raise ValueError(f"Host file is not the overlay's preimage (rebuild the overlay): {item['path']}")
    previous = state / "previous"
    previous.mkdir(parents=True)
    for item in files:
        target = host / item["path"]
        if target.exists():
            backup = previous / item["path"]
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
    for item in files:
        target = host / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(overlay / "files" / item["path"], target)
        if digest(target) != item["sha256"]:
            raise ValueError(f"Overlay copy failed verification: {item['path']}")
    journal = {"name": name, "overlay": str(overlay), "applied_utc": datetime.now(UTC).isoformat(), "files": files}
    (state / "journal.json").write_text(json.dumps(journal, indent=1) + "\n", encoding="utf-8")
    return journal


def rollback(host, name):
    host = Path(host)
    state = _state(host, name)
    journal = json.loads((state / "journal.json").read_text(encoding="utf-8"))
    for item in journal["files"]:
        target = host / item["path"]
        if not target.exists() or digest(target) != item["sha256"]:
            raise ValueError(f"Host no longer holds the overlay file: {item['path']}")
    for item in journal["files"]:
        target = host / item["path"]
        if item["preimage"] is None:
            target.unlink()
            continue
        shutil.copy2(state / "previous" / item["path"], target)
        if digest(target) != item["preimage"]:
            raise ValueError(f"Restored file differs from its preimage: {item['path']}")
    shutil.rmtree(state)
    return {"name": name, "state": "rolled-back", "files": len(journal["files"])}


def applied(host):
    root = Path(host) / ".expanded-garage"
    return sorted(p.name.removeprefix("overlay-") for p in root.glob("overlay-*") if (p / "journal.json").exists())
