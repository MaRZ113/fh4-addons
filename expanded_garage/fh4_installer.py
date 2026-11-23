"""Hash-pinned file transactions for closed FH4 Steam offline hosts."""

from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256 as hash_bytes
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from .vehicle_ir import sha256
from .fh4_presentation import EN_PATH


EXE_SHA = "b838a79a65c6d1e12f46deaba3584e4c88ebccdfdc4214b4535ece4ba3c0abbe"
CRYPTO_SHA = "e7de706754590c8e8666d3c0185b0ffa6b523d8a92d56f4938f6b9951d3581a0"
DB_PATH = "media/stripped/gamedbRC.slt"
MANIFEST_PATH = "media/zipmanifest.xml"
REPLACEABLE = (DB_PATH, MANIFEST_PATH, EN_PATH)
HEX = re.compile(r"[0-9a-f]{64}")
ARCHIVE = re.compile(r"media/cars/EG_[a-zA-Z0-9_]+\.zip")
THUMBNAIL = re.compile(r"media/stripped/RC[0-9]{5,10}(HiRes)?\.zip")
PRESENTATION = re.compile(r"media/expanded_garage/(?:manufacturers/[A-Za-z0-9_]+\.swatchbin|thumbnails/thumbnail_[0-9]+\.png)")
MATERIALS = "media/cars/_library/Materials.zip"
ACCEPTANCE_CHECKS = ("acquired_and_visible_in_garage", "loaded_without_null_car", "four_wheels_and_road_contact",
                     "accelerated_steered_and_braked", "saved_and_reloaded_after_restart", "original_roster_intact")


def plain_path(path):
    for node in [path, *path.parents]:
        if os.path.lexists(node):
            info = node.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError(f"Link or reparse point in deployment path: {node}")
    return path


def native_path(host, relative):
    if relative not in REPLACEABLE and not (
            ARCHIVE.fullmatch(relative) or PRESENTATION.fullmatch(relative) or THUMBNAIL.fullmatch(relative)):
        raise ValueError(f"Unowned FH4 installation path {relative}")
    return plain_path(host.joinpath(*PurePosixPath(relative).parts))


def current(path):
    plain_path(path)
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f"Expected a regular file: {path}")
    return sha256(path)


def closed(exe):
    if current(exe) != EXE_SHA:
        raise ValueError(f"Expected FH4 Steam 1.478.564.0: {exe}")
    if os.name != "nt":
        raise ValueError("FH4 host deployment requires Windows")
    query = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "$ErrorActionPreference='Stop'; @(Get-Process -Name ForzaHorizon4 -ErrorAction SilentlyContinue) "
             "| ForEach-Object { if (!$_.Path) { exit 2 }; $_.Path }; exit 0"]
    run = subprocess.run(query, capture_output=True, text=True, timeout=15)
    if run.returncode or any(Path(line.strip()).resolve() == exe.resolve()
                             for line in run.stdout.splitlines() if line.strip()):
        raise ValueError("Close the targeted FH4 host before installing or rolling back")


def read_json(path):
    plain_path(path)
    return json.loads(path.read_text(encoding="utf-8"))


def sync_replace(path, source, expected_before, expected_after):
    if current(path) != expected_before:
        raise ValueError(f"Deployment preimage changed: {path}")
    if expected_after is None:
        path.unlink()
        return
    if current(source) != expected_after:
        raise ValueError(f"Deployment payload hash changed: {source}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".garage-", delete=False) as temp:
        new_path = Path(temp.name)
    try:
        with source.open("rb") as src, new_path.open("wb") as dest:
            shutil.copyfileobj(src, dest)
            dest.flush()
            os.fsync(dest.fileno())
        if current(new_path) != expected_after or current(path) != expected_before:
            raise ValueError(f"Unexpected file change before replacement: {path}")
        os.replace(new_path, path)
    finally:
        new_path.unlink(missing_ok=True)


def write_journal(folder, record):
    path = folder / "journal.json"
    payload = json.dumps(record, indent=2, sort_keys=True).encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=folder, prefix="journal-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        sync_replace(path, temporary, current(path), hash_bytes(payload).hexdigest())
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def host_lock(host):
    root = plain_path(host / ".expanded-garage")
    root.mkdir(exist_ok=True)
    path = plain_path(root / "host.lock")
    with path.open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield root


def check_entries(files):
    paths = [entry["path"].casefold() for entry in files]
    if len(set(paths)) != len(files):
        raise ValueError("Duplicate install paths")
    for item in files:
        if not isinstance(item["after"], str) or not HEX.fullmatch(item["after"]):
            raise ValueError("Invalid package payload hash")
        if item["before"] is not None and (not isinstance(item["before"], str) or not HEX.fullmatch(item["before"])):
            raise ValueError("Invalid package preimage hash")
        core = item["path"] in REPLACEABLE
        if core != (item["before"] is not None):
            raise ValueError("Only the pinned GameDB, manifest and verified locale archive may replace existing files")


def recover(host, folder, record, guard):
    files = record["files"]
    check_entries(files)
    for i, item in enumerate(files):
        path = native_path(host, item["path"])
        if current(path) not in {item["before"], item["after"]}:
            raise ValueError(f"Rollback would overwrite an unrecognized file: {path}")
        if item["before"] is not None and current(folder / "previous" / str(i)) != item["before"]:
            raise ValueError(f"Rollback backup is invalid: {path}")
    for i in reversed(range(len(files))):
        item = files[i]
        path = native_path(host, item["path"])
        if current(path) != item["before"]:
            guard(host / "ForzaHorizon4.exe")
            sync_replace(path, folder / "previous" / str(i), item["after"], item["before"])
    record["state"] = "rolled-back"
    write_journal(folder, record)
    return record


def check_preimages(host, package, files, guard):
    check_entries(files)
    guard(host / "ForzaHorizon4.exe")
    for item in files:
        target = native_path(host, item["path"])
        incoming = native_path(package, item["path"])
        if current(target) != item["before"] or current(incoming) != item["after"]:
            raise ValueError(f"Host or package hash mismatch: {item['path']}")
        if ARCHIVE.fullmatch(item["path"]):
            name = Path(item["path"]).stem
            for alternate in (host / "media/cars" / name, host / "mediapc/cars" / name,
                              host / "mediapc/cars" / f"{name}.zip"):
                if os.path.lexists(alternate):
                    raise ValueError(f"Competing vehicle path: {alternate}")
        if THUMBNAIL.fullmatch(item["path"]):
            relative = Path(item["path"])
            for alternate in (host/relative.with_suffix(""),host/"mediapc/stripped"/relative.name,
                              host/"mediapc/stripped"/relative.stem):
                if os.path.lexists(alternate):
                    raise ValueError(f"Competing thumbnail archive path: {alternate}")


def transaction(host, package, files, guard=closed, *, test_evidence=None):
    files = sorted(files, key=lambda item: (item["path"] in (MANIFEST_PATH, DB_PATH), item["path"] == DB_PATH, item["path"]))
    ledger_id = uuid.uuid4().hex
    with host_lock(host) as state:
        check_preimages(host, package, files, guard)
        for path in state.glob("*/journal.json"):
            old = read_json(path)
            if old["state"] == "prepared":
                raise ValueError(f"Recover unfinished transaction {old['transaction']} first")
        folder = state / ledger_id
        backups = folder / "previous"
        backups.mkdir(parents=True)
        for i, item in enumerate(files):
            if item["before"] is not None:
                sync_replace(backups / str(i), native_path(host, item["path"]), None, item["before"])
        record = {"transaction": ledger_id, "state": "prepared", "files": files}
        if test_evidence:
            record["offline_test"] = test_evidence
        write_journal(folder, record)
        try:
            for item in files:
                guard(host / "ForzaHorizon4.exe")
                sync_replace(native_path(host, item["path"]), native_path(package, item["path"]),
                             item["before"], item["after"])
            record["state"] = "installed"
            write_journal(folder, record)
        except BaseException:
            recover(host, folder, record, guard)
            raise
    return record


def preflight(host, package):
    from .fh4 import TARGET_BUILD, validate_zip
    from .fh4_data_car import wheel_load_contract
    from .fh4_validation import audit_trial_assets, suspension_path, verify_qualification
    from .fh4_thumbnail import audit_archive as audit_thumbnail_archive, thumbnail_archives

    host, package = plain_path(host.absolute()).resolve(strict=True), plain_path(package.absolute()).resolve(strict=True)
    validation = read_json(package / "validation.json")
    plan = read_json(package / "deployment.json")
    if (plan["executable_sha256"] != EXE_SHA or plan["validation_sha256"] != sha256(package / "validation.json")
            or plan.get("wrapper_roundtrip") is not True or plan.get("target") != TARGET_BUILD):
        raise ValueError("Deployment receipt references unexpected evidence")
    if validation.get("target") != TARGET_BUILD or validation.get("gameplay_confirmed") is not False:
        raise ValueError("Unexpected target or runtime state in the build receipt")
    trial = "offline_trial_eligibility" in validation
    blockers = verify_qualification(validation)
    if plan.get("state") != validation["state"] or plan.get("installable") != validation["installable"]:
        raise ValueError("The deployment receipt disagrees with the individual drive gates")
    if trial and not blockers and any("native_load_contract" not in car for car in validation["cars"]):
        blockers = ["Recompile this offline trial with the native Data_Car and wheel MassLevel checks."]
    files = plan["files"]
    required = {DB_PATH, MANIFEST_PATH}
    if not required <= {item["path"] for item in files} or not any(ARCHIVE.fullmatch(i["path"]) for i in files):
        raise ValueError("Incomplete native deployment")
    if os.path.lexists(host / "mediapc/zipmanifest.xml"):
        raise ValueError("Competing host PC manifest overrides the target manifest")
    check_preimages(host, package, files, closed)
    if current(host / MATERIALS) != validation["shared_materials_sha256"]:
        raise ValueError("Host material library differs from the compilation input")
    cars = {f"media/cars/{c['archive']['name']}": c for c in validation["cars"]}
    suspension_proof = plan.get("shared_suspension",{})
    for item in files:
        relative = item["path"]
        if not ARCHIVE.fullmatch(relative):
            continue
        car = cars.pop(relative, None)
        if car is None or car["archive"]["sha256"] != item["after"]:
            raise ValueError(f"Vehicle archive is absent from the integration evidence: {relative}")
        archive = native_path(package, relative)
        asset_checks = {} if trial else None
        if vars(validate_zip(archive.read_bytes(), archive.stem, car["car_id"], trial=asset_checks)) != car["archive"]["directory"]:
            raise ValueError(f"Archive directory differs from the audited manifest range: {relative}")
        if not blockers:
            stem = archive.stem.casefold()
            with ZipFile(archive) as members:
                required_assets = {"physicsdefinition.bin", f"scene/{stem}_skeleton.gr2", f"animations/{stem}.gsf"}
                if not required_assets <= {m.casefold() for m in members.namelist()}:
                    raise ValueError(f"Drive-ready claim disagrees with the staged runtime assets: {relative}")
        if trial:
            trial_assets = car["trial_prerequisites"]
            with ZipFile(archive) as members:
                assets = audit_trial_assets(members,car["wheel_frames"],asset_checks)
            if any(trial_assets[key]!=value for key,value in assets.items()):
                raise ValueError(f"Staged car differs from the audited runtime prerequisites: {relative}")
            if not blockers and (assets["control_missing"] or not assets["collision"]
                    or set(trial_assets["suspension_assets"]) != {"Front","Rear"}):
                raise ValueError("Incomplete runtime or shared suspension evidence")
            car_suspension = suspension_proof.get(str(car["car_id"]),{})
            for axle, entry in trial_assets["suspension_assets"].items():
                path = suspension_path(entry["path"])
                if current(host / path) != entry["sha256"]:
                    raise ValueError(f"Host suspension asset differs from the audited input: {path}")
                proof = car_suspension.get(axle,{})
                if not blockers and (proof.get("protected_sha256") != entry["sha256"]
                        or proof.get("steered") is not (axle=="Front")
                        or not HEX.fullmatch(proof.get("xml_sha256",""))):
                    raise ValueError(f"Missing or incompatible native suspension readback: {path}")
    if cars:
        raise ValueError("Deployment is missing audited vehicle archives")
    supplied_thumbnails = {item["path"]:item["after"] for item in files if THUMBNAIL.fullmatch(item["path"])}
    for car in validation["cars"]:
        if "native_load_contract" not in car:
            continue
        wheel = car["native_load_contract"]["stock_wheel"]
        if wheel_load_contract(wheel["Mass"],wheel["MassLevel"])!=wheel:
            raise ValueError("Invalid audited stock wheel mass settings")
        entries = car["thumbnail_archives"]
        if not blockers and [x["path"] for x in entries]!=list(thumbnail_archives(car["car_id"])):
            raise ValueError("Native-load candidate needs both mounted thumbnail archives")
        for entry in entries:
            path = entry["path"]
            if supplied_thumbnails.pop(path,None)!=entry["sha256"] or (
                    audit_thumbnail_archive(native_path(package,path).read_bytes(),car["car_id"],entry["hires"])
                    !=entry["directory"]):
                raise ValueError(f"Invalid deployed thumbnail archive: {path}")
    if supplied_thumbnails:
        raise ValueError("Unexpected deployment thumbnail archives")
    for car in validation["cars"]:
        for row in car["tires"]["rows"]:
            for model in row["models"].values():
                for role in ("left", "right"):
                    for entry in model[role]:
                        path = entry["mount"].casefold().removeprefix("game:\\").replace("\\", "/")
                        if (not re.fullmatch(r"media/cars/_library/scene/tires/tirer?_[a-z0-9_]+\.zip", path)
                                or current(host / path) != entry["archive_sha256"]):
                            raise ValueError(f"Target shared tire archive differs from the audited input: {path}")
    return {"state": validation["state"], "installable": not blockers, "blockers": blockers,
            "validation_sha256": plan["validation_sha256"], "files": files,
            "car_ids": sorted(c["car_id"] for c in validation["cars"])}


def install(host, package, *, offline_test=False, offline_trial=False):
    if offline_test == offline_trial:
        raise ValueError("Use --offline-trial or legacy --offline-test for an isolated offline FH4 copy")
    host, package = plain_path(host.absolute()).resolve(strict=True), plain_path(package.absolute()).resolve(strict=True)
    result = preflight(host, package)
    if not result["installable"]:
        raise ValueError("Offline integration preview is blocked. " + " ".join(result["blockers"]))
    required_mode = "offline-trial-eligible" if offline_trial else "drive-ready-candidate"
    if result["state"] != required_mode:
        raise ValueError(f"Package state {result['state']} requires its matching offline installation mode")
    metadata = {key: result[key] for key in ("state", "validation_sha256", "car_ids")}
    return transaction(host, package, result["files"], test_evidence=metadata)


def rollback(host, transaction_id):
    host = plain_path(host.absolute()).resolve(strict=True)
    if not re.fullmatch(r"[0-9a-f]{32}", transaction_id):
        raise ValueError("Invalid transaction ID")
    closed(host / "ForzaHorizon4.exe")
    with host_lock(host) as root:
        folder = plain_path(root / transaction_id)
        record = read_json(folder / "journal.json")
        if record["transaction"] != transaction_id:
            raise ValueError("Unexpected rollback journal")
        return recover(host, folder, record, closed)


def record_runtime_acceptance(host, transaction_id, evidence):
    host = plain_path(host.absolute()).resolve(strict=True)
    if not re.fullmatch(r"[0-9a-f]{32}", transaction_id):
        raise ValueError("Invalid transaction ID")
    closed(host / "ForzaHorizon4.exe")
    with host_lock(host) as root:
        folder = plain_path(root / transaction_id)
        record = read_json(folder / "journal.json")
        test = record.get("offline_test", {})
        if (record["transaction"] != transaction_id or record["state"] != "rolled-back"
                or test.get("state") not in {"drive-ready-candidate","offline-trial-eligible"}
                or "runtime_acceptance" in record):
            raise ValueError("Runtime acceptance requires a rolled-back qualified test transaction")
        observations = evidence.get("cars")
        if (not isinstance(evidence.get("observer"), str) or not evidence["observer"].strip()
                or not isinstance(observations, dict) or set(observations) != {str(c) for c in test["car_ids"]}
                or any(not isinstance(checks, dict) or any(checks.get(k) is not True for k in ACCEPTANCE_CHECKS)
                       for checks in observations.values())):
            raise ValueError("Owner observations must cover all acquisition, driving and save checks for every test car")
        if any(current(native_path(host, f["path"])) != f["before"] for f in record["files"]):
            raise ValueError("Runtime acceptance requires verified restored host preimages")
        record["runtime_acceptance"] = {"state": "runtime-accepted", "observer": evidence["observer"],
                                        "cars": observations, "rollback_verified": True,
                                        "recorded_utc": datetime.now(UTC).isoformat(),
                                        "validation_sha256": test["validation_sha256"]}
        write_journal(folder, record)
        return record["runtime_acceptance"]


def suspension_xml_evidence(raw, axle):
    # Stock comments contain XML-incompatible double hyphens, and generated templates (RevoKnuckle,
    # Strut_DW_Linear_Camber, 5link_hyper1) open with `//` header lines. Retain the full native
    # plaintext digest and omit both comment forms only from structural readback.
    # They also run attributes together (x="..."y="...") and spell Steered="TRUE".
    text = re.sub(rb"(?m)^[ \t]*//[^\n]*\n?",b"",re.sub(rb"<!--.*?-->",b"",raw,flags=re.S))
    tree = ET.fromstring(re.sub(rb'"([A-Za-z_][A-Za-z0-9_]*=)',rb'" \1',text))
    steered = axle == "Front"
    required = (tree.find(".//Locator[@Id='S']") is not None
                and all(tree.find(f".//{part}") is not None for part in ("Chassis","Upright")))
    if (tree.tag != "Suspension" or tree.get("Steered","").casefold() != str(steered).lower() or not required
            or steered and tree.find("Kingpin") is None
            or not 0 < float(tree.get("HalfTrack","0")) < 2
            or not 0 < float(tree.get("TireRadius","0")) < 1
            or not -1 < float(tree.get("DroopTravel","0")) < 0 < float(tree.get("BumpTravel","0")) < 1):
        raise ValueError(f"Native {axle} suspension XML lacks compatible travel or steering fixtures")
    return {"xml_sha256":hash_bytes(raw).hexdigest(),"steered":steered,"spindle_locator":"S"}


def prepare_package(build, output, crypto, original_db, original_manifest, original_en=None, *, library=None):
    """Stage and independently roundtrip native envelopes, leaving previews non-deployable."""
    build, output = build.resolve(strict=True), output.absolute()
    crypto = crypto.resolve(strict=True)
    if current(crypto) != CRYPTO_SHA:
        raise ValueError("Unexpected local FH4 envelope tool")
    if output.exists():
        raise ValueError(f"Output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    validation_path = build / "validation.json"
    from .fh4_validation import suspension_path, verify_integration

    validation = verify_integration(build)
    database = read_json(build / "database-graph.json")["insert_only_proof"]
    hashes = validation["payload_hashes"]
    if current(build / "gamedb.sqlite") != hashes["database"] or current(build / "zipmanifest.xml") != hashes["manifest"]:
        raise ValueError("Compile and deployment payload hashes disagree")
    presentation = read_json(build / "presentation.json")
    receipts = ([build / "build.json"] if (build / "build.json").is_file()
                else sorted((build / "cars").glob("*/build.json")))
    cars = [(path.parent, read_json(path)) for path in receipts]
    if not cars or sorted(r["identity"]["car_id"] for _, r in cars) != sorted(
            r["car_id"] for r in validation["cars"]):
        raise ValueError("Incomplete package car inventory")
    baseline_mount = {r["baseline_manifest_sha256"] for _, r in cars}
    preimage, = baseline_mount
    files = []
    shared_suspension = {}

    def invoke(*args):
        result = subprocess.run([str(crypto), *map(str, args)], capture_output=True, timeout=120)
        if result.returncode:
            raise ValueError(f"Local envelope tool failed during {args[0]}")

    def stage_file(folder, relative, path, before, after):
        target = native_path(folder, relative)
        sync_replace(target, path, None, after)
        files.append({"path": relative, "before": before, "after": after})

    with tempfile.TemporaryDirectory(prefix="wrap-", dir=output.parent) as tmp:
        root = Path(tmp)
        if validation["state"] == "offline-trial-eligible":
            if library is None or current(library) != validation["shared_materials_sha256"]:
                raise ValueError("Preparing an offline trial requires its compiled --fh4-materials input")
            game = library.resolve(strict=True).parents[3]
            for car in validation["cars"]:
                readback = {}
                for axle, entry in car["trial_prerequisites"]["suspension_assets"].items():
                    source = game / suspension_path(entry["path"])
                    if current(source) != entry["sha256"]:
                        raise ValueError(f"Unexpected shared native suspension asset: {source}")
                    decoded = root / f'{car["car_id"]}-{axle}-suspension.xml'
                    invoke("decrypt",source,"-o",decoded)
                    readback[axle] = {"protected_sha256":entry["sha256"],
                                      **suspension_xml_evidence(decoded.read_bytes(),axle)}
                shared_suspension[str(car["car_id"])] = readback
        folder = root / "candidate"
        folder.mkdir()
        for tag, plaintext, original, relative, baseline_hash in (
                ("db", build / "gamedb.sqlite", original_db, DB_PATH, database["baseline_sha256"]),
                ("manifest", build / "zipmanifest.xml", original_manifest, MANIFEST_PATH, preimage)):
            baseline = root / f"{tag}-baseline"
            before = current(original)
            invoke("decrypt", original.resolve(), "-o", baseline)
            if current(baseline) != baseline_hash or current(original) != before:
                raise ValueError(f"Original {tag} wrapper does not match the compiled baseline")
            packed = root / f"{tag}-packed"
            restored = root / f"{tag}-readback"
            invoke("encrypt", plaintext, "--original", original.resolve(), "-o", packed)
            invoke("decrypt", packed, "-o", restored)
            if current(plaintext) != current(restored):
                raise ValueError(f"Independent {tag} wrapper readback differs")
            if tag == "db" and current(restored) != database["output_sha256"]:
                raise ValueError("Wrapped database differs from the staged catalogue proof")
            stage_file(folder, relative, packed, before, current(packed))
        for car_folder, receipt in cars:
            name = receipt["archive"]
            stage_file(folder, f"media/cars/{name}", car_folder / name, None, receipt["archive_sha256"])
        if english := presentation["english"]:
            if original_en is None or current(original_en) != english["baseline_sha256"]:
                raise ValueError("The compiled English archive requires its matching stock preimage")
            stage_file(folder, EN_PATH, build / EN_PATH, english["baseline_sha256"], english["archive_sha256"])
        for art in presentation["art"]:
            stage_file(folder, art["path"], build / art["path"], None, art["sha256"])
        shutil.copyfile(validation_path, folder / "validation.json")
        plan = {"executable_sha256": EXE_SHA, "validation_sha256": current(folder / "validation.json"),
                "target": validation["target"], "state": validation["state"], "files": files,
                "wrapper_roundtrip": True, "installable": validation["installable"]}
        if shared_suspension:
            plan["shared_suspension"] = shared_suspension
        check_entries(files)
        write_journal(folder, plan)
        (folder / "journal.json").rename(folder / "deployment.json")
        folder.rename(output)
    return plan
