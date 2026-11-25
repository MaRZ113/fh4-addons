"""Expanded Garage command line."""

import argparse
import json
from pathlib import Path
import sqlite3
import struct
import subprocess
import os
import sys
import tempfile
from zipfile import BadZipFile

from . import fh4, fh4_installer, fh4_physics, fm4, vehicle_ir


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DSF = ROOT / "tools/driversf-forza-master"


def target_args(parser):
    parser.add_argument("--target", required=True, choices=["fh4"])
    parser.add_argument("--fh4-db", required=True, type=Path, help="Pristine decrypted SQLite GameDB")
    parser.add_argument("--fh4-manifest", required=True, type=Path, help="Pristine plaintext zipmanifest.xml")
    parser.add_argument("--fh4-materials", required=True, type=Path, help="Stock media/cars/_library/Materials.zip")
    parser.add_argument("--fh4-strings", type=Path, help="Stock FH4 English string archive (EN.zip)")
    parser.add_argument("--registry", required=True, type=Path, help="Persistent Expanded Garage SQLite ID ledger")


def source_args(parser):
    parser.add_argument("--source", required=True, choices=["fm4"])
    parser.add_argument("--car", required=True, help="Source MediaName")
    parser.add_argument("--fm4-root", required=True, type=Path, help="Extracted FM4 Disc 1 root")
    parser.add_argument("--dlc", type=Path, action="append", default=[], help="Local STFS, payload ZIP or extracted DLC")
    parser.add_argument("--stfs-tool", type=Path, help="Local Extract-STFS.ps1 for raw DLC")
    parser.add_argument("--source-tools", type=Path, default=DEFAULT_DSF, help="Local driversf-forza checkout")


def stage(args):
    output = args.out.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError(f"Output already exists: {output}")
    with tempfile.TemporaryDirectory(prefix=".garage-", dir=output.parent) as work:
        build = Path(work) / "result"
        build.mkdir()
        if args.action in ("import", "extract"):
            ir_path = build if args.action == "extract" else build / "ir"
            ir_path.mkdir(exist_ok=True)
            fm4.build(args.fm4_root.resolve(), [p.resolve() for p in args.dlc], args.car, ir_path,
                      args.source_tools.resolve(), args.stfs_tool.resolve() if args.stfs_tool else None)
        else:
            ir_path = args.ir.resolve()
        ir = vehicle_ir.load(ir_path)
        if args.action != "extract":
            target = build / "fh4" if args.action == "import" else build
            target.mkdir(exist_ok=True)
            identity = fh4.build(ir_path, ir, target, args.registry, args.fh4_db, args.fh4_manifest,
                                 args.fh4_materials, strings=args.fh4_strings, source_tools=args.source_tools)
            state = json.loads((target / "validation.json").read_text(encoding="utf-8"))["state"]
        build.rename(output)
    if args.action == "extract":
        print(f'Vehicle IR {ir["vehicle"]["media_name"]} written to {output}')
    else:
        print(f'{identity["media_name"]} {identity["car_id"]} {state} written to {output}')
        print("See validation.json for installation and runtime status.")


def fleet(args):
    from .fh4_archive import Directory, add_mount
    from .fh4_db_compiler import Vehicle, compile_database
    from .fh4_presentation import presentation_mount
    from .fh4_validation import integrated_report

    out = args.out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        raise ValueError(f"Output already exists: {out}")
    with tempfile.TemporaryDirectory(prefix=".garage-fleet-", dir=out.parent) as temp:
        build = Path(temp) / "fleet"
        build.mkdir()
        choices = [(path.resolve(), vehicle_ir.load(path.resolve())) for path in args.ir]
        names = [ir["vehicle"]["media_name"].casefold() for _, ir in choices]
        if len(set(names)) != len(names):
            raise ValueError("Fleet contains duplicate source MediaNames")
        vehicles, cars = [], []
        mount = args.fh4_manifest.read_bytes()
        for root, ir in sorted(choices, key=lambda item: item[1]["vehicle"]["media_name"].casefold()):
            name = vehicle_ir.media_name(ir["vehicle"]["media_name"])
            folder = build / "cars" / name
            folder.mkdir(parents=True)
            identity = fh4.build(root, ir, folder, args.registry, args.fh4_db,
                                 args.fh4_manifest, args.fh4_materials, catalogue=False)
            receipt = json.loads((folder / "build.json").read_text(encoding="utf-8"))
            mount = add_mount(mount, identity["media_name"], Directory(**receipt["archive_directory"]))
            vehicles.append(Vehicle(root, ir, identity))
            cars.append((folder, receipt))
        database = compile_database(args.fh4_db, vehicles, build, strings=args.fh4_strings,
                                    source_tools=args.source_tools)
        from . import fh4_audio
        audio = fh4_audio.compile_fleet(build, vehicles, args.fh4_db, build / "gamedb.sqlite", args.fh4_audio_mapping)
        if audio:
            vehicle_ir.write_json(build / "audio.json", audio)
        mount = presentation_mount(mount,database["presentation"])
        (build / "zipmanifest.xml").write_bytes(mount)
        report = integrated_report(args.fh4_db, args.fh4_manifest, args.fh4_materials, build, mount, cars)
        vehicle_ir.write_json(build / "validation.json", report)
        build.rename(out)
    print(f"{len(report['cars'])} cars compiled into one native GameDB and ZIP manifest at {out}")
    print(f"Installable: {str(report['installable']).lower()}")


def verify(args):
    import xml.etree.ElementTree as ET

    path = args.bundle.resolve()
    if (path / "validation.json").is_file():
        from .fh4_validation import verify_integration

        report = verify_integration(path, args.fh4_db, args.fh4_manifest, args.fh4_materials)
        print(f"Verified integrated GameDB, mounts and {len(report['cars'])} vehicle archive(s)")
        print(f"Installable: {str(report['installable']).lower()}")
        return
    report = json.loads((path / "build.json").read_text(encoding="utf-8"))
    identity = report["identity"]
    media = vehicle_ir.media_name(identity["media_name"])
    archive = path / f"{media}.zip"
    if vehicle_ir.sha256(archive) != report["archive_sha256"]:
        raise ValueError("Unexpected FH4 archive hash")
    directory = fh4.validate_zip(archive.read_bytes(), media, identity["car_id"])
    if vars(directory) != report["archive_directory"]:
        raise ValueError("Build receipt does not match ZIP directory")
    root = ET.fromstring((path / "zipmanifest.xml").read_bytes())
    entry, = [e for e in root if e.get("path", "").casefold() == fh4.fh4_archive.mount_path(media)]
    if ([entry.get(k) for k in ("dirstart", "dirsize", "direntries")]
            != [str(v) for v in (directory.start, directory.size, directory.entries)]):
        raise ValueError("Manifest directory values differ from ZIP")
    print(f"Verified {media}: {directory.entries} FH4 records, 0x1123, Carbin and Modelbin links")


def deploy(args):
    if args.action == "install":
        receipt = fh4_installer.install(args.host, args.package, offline_test=args.offline_test,
                                        offline_trial=args.offline_trial)
    elif args.action == "preflight":
        receipt = fh4_installer.preflight(args.host, args.package)
    elif args.action == "rollback":
        receipt = fh4_installer.rollback(args.host, args.transaction)
    else:
        receipt = fh4_installer.record_runtime_acceptance(args.host, args.transaction,
                                                          fh4_installer.read_json(args.evidence))
    print(json.dumps(receipt, indent=2))


def prepare(args):
    plan = fh4_installer.prepare_package(args.build, args.out, args.crypto,
                                         args.original_db, args.original_manifest, args.original_en,
                                         library=args.fh4_materials)
    print(f"Staged {len(plan['files'])} verified files at {args.out}")
    print(f"Installable: {str(plan['installable']).lower()}")


def physics(args):
    result = fh4_physics.compare(args.fm4_db, args.fh4_db)
    with args.out.open("x", encoding="utf-8", newline="\n") as stream:
        import json
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(f'Compared {len(result["pairs"])} exact FM4/FH4 stock vehicle matches')


def main(argv=None):
    parser = argparse.ArgumentParser(prog="expanded-garage")
    sub = parser.add_subparsers(dest="action", required=True)
    importer = sub.add_parser("import", help="Extract Vehicle IR and compile an offline FH4 candidate")
    source_args(importer)
    target_args(importer)
    extractor = sub.add_parser("extract", help="Extract a reusable Vehicle IR bundle")
    source_args(extractor)
    compiler = sub.add_parser("compile", help="Compile an offline FH4 candidate from Vehicle IR")
    compiler.add_argument("--ir", required=True, type=Path)
    compiler.add_argument("--source-tools", type=Path, default=DEFAULT_DSF, help="Local XDS decoder checkout")
    target_args(compiler)
    for command in (extractor, importer, compiler):
        command.add_argument("--out", required=True, type=Path, help="New output directory")
        command.set_defaults(run=stage)
    checker = sub.add_parser("verify", help="Check a generated native archive and mount receipt")
    checker.add_argument("bundle", type=Path, help="Directory containing build.json")
    checker.add_argument("--fh4-db", type=Path, help="Baseline SQLite for a full integration re-audit")
    checker.add_argument("--fh4-manifest", type=Path, help="Baseline XML for a full integration re-audit")
    checker.add_argument("--fh4-materials", type=Path, help="Shared material library for a full integration re-audit")
    checker.set_defaults(run=verify)
    study = sub.add_parser("compare-physics", help="Compare physical fields on shared stock cars")
    study.add_argument("--fm4-db", required=True, type=Path)
    study.add_argument("--fh4-db", required=True, type=Path)
    study.add_argument("--out", required=True, type=Path)
    study.set_defaults(run=physics)
    fleet_parser = sub.add_parser("compile-fleet", help="Compile several IR bundles into one native catalogue")
    fleet_parser.add_argument("--ir", action="append", required=True, type=Path)
    fleet_parser.add_argument("--out", required=True, type=Path)
    fleet_parser.add_argument("--source-tools", type=Path, default=DEFAULT_DSF, help="Local XDS decoder checkout")
    fleet_parser.add_argument("--fh4-audio-mapping", type=Path,
                              help="Stock media/audio/cars/CarEngineEventMapping.xml: adds the cars' FM4 engine sound")
    target_args(fleet_parser)
    fleet_parser.set_defaults(run=fleet)
    install = sub.add_parser("install", help="Deploy a qualified native package to a closed offline host")
    install.add_argument("--host", required=True, type=Path)
    install.add_argument("--package", required=True, type=Path)
    mode = install.add_mutually_exclusive_group()
    mode.add_argument("--offline-test", action="store_true", help="Install a legacy drive-ready test package")
    mode.add_argument("--offline-trial", action="store_true", help="Install a statically eligible package on an isolated offline copy")
    install.set_defaults(run=deploy)
    preflight = sub.add_parser("preflight", help="Audit a staged package and offline host without deploying files")
    preflight.add_argument("--host", required=True, type=Path)
    preflight.add_argument("--package", required=True, type=Path)
    preflight.set_defaults(run=deploy)
    rollback = sub.add_parser("rollback", help="Restore the file preimages of one installation")
    rollback.add_argument("--host", required=True, type=Path)
    rollback.add_argument("--transaction", required=True)
    rollback.set_defaults(run=deploy)
    acceptance = sub.add_parser("record-runtime", help="Record owner observations after a successful test and rollback")
    acceptance.add_argument("--host", required=True, type=Path)
    acceptance.add_argument("--transaction", required=True)
    acceptance.add_argument("--evidence", required=True, type=Path)
    acceptance.set_defaults(run=deploy)
    wrapper = sub.add_parser("prepare-package", help="Stage native envelopes and verify byte-exact readback")
    wrapper.add_argument("--build", required=True, type=Path)
    wrapper.add_argument("--out", required=True, type=Path)
    wrapper.add_argument("--crypto", required=True, type=Path)
    wrapper.add_argument("--original-db", required=True, type=Path)
    wrapper.add_argument("--original-manifest", required=True, type=Path)
    wrapper.add_argument("--original-en", type=Path)
    wrapper.add_argument("--fh4-materials", type=Path, help="Matching installed library for offline trial resource readback")
    wrapper.set_defaults(run=prepare)
    args = parser.parse_args(argv)
    try:
        args.run(args)
    except (BadZipFile, ImportError, KeyError, OSError, ValueError, sqlite3.Error, struct.error,
            subprocess.CalledProcessError) as error:
        print(f"expanded-garage: {error}", file=sys.stderr)
        if os.environ.get("EXPANDED_GARAGE_TRACEBACK"):
            import traceback
            traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
