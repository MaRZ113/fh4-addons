"""Append FH4 v4 localization records and transcode original FM4 artwork."""

from hashlib import sha256
import importlib
from io import BytesIO
from pathlib import Path
import struct
import sys
import uuid
from zipfile import ZipFile
import zlib
import re
import xml.etree.ElementTree as ET

from . import fh4_archive, fh4_model
from .fh4_thumbnail import write_thumbnails
from .vehicle_ir import read_asset, sha256 as file_sha


EN_PATH = "media/stripped/stringtables/EN.zip"
EN_VIRTUAL = "game:\\media\\stripped\\stringtables\\en.zip"
TABLES = {"Data_Car": ("DisplayName", "ModelShort"), "List_CarMake": ("DisplayName", "Profile")}
ROOT = Path(__file__).resolve().parents[1]


def strings_part(index, pool):
    items = []
    next_offset = 0
    for key, offset in struct.iter_unpack("<II", index):
        if offset != next_offset:
            raise ValueError("Unexpected native string offset")
        end = offset
        while end < len(pool) and pool[end:end+2] != b"\0\0":
            end += 2
        if end + 2 > len(pool):
            raise ValueError("Unterminated FH4 UTF-16 string")
        items.append((key, pool[offset:end].decode("utf-16-le")))
        next_offset = end + 2
    if next_offset != len(pool) or len({k for k, _ in items}) != len(items):
        raise ValueError("Unexpected native string pool or duplicate key")
    return items


class StringTable:
    def __init__(self, data, name):
        if data[:2] != b"\0\4" or data[2:132].split(b"\0", 1)[0].decode("ascii") != name:
            raise ValueError(f"Unexpected FH4 localization table {name}")
        start, secondary, primary_size, primary_pool, count = struct.unpack_from("<5I", data, 132)
        split = 152 + count * 8
        if start != 140 or secondary != split + primary_pool or primary_size != secondary - 152:
            raise ValueError("Unrecognized FH4 primary localization section")
        size, name_pool, names = struct.unpack_from("<3I", data, secondary)
        end = secondary + 12 + count * 8
        if names != count or len(data) != end + name_pool or size != 8 * count + name_pool:
            raise ValueError("Unrecognized FH4 secondary localization section")
        self.prefix = data[:132]
        primary = strings_part(data[152:split], data[split:secondary])
        internal = strings_part(data[secondary+12:end], data[end:])
        self.rows = {}
        for (number, text), (check, symbol) in zip(primary, internal, strict=True):
            if number != check:
                raise ValueError("The native localization index tables disagree")
            self.rows[number] = (text, symbol)
        self.original_count = count

    def append(self, symbol, text):
        if "\0" in symbol or "\0" in text:
            raise ValueError("NUL in vehicle display string")
        candidate = zlib.crc32(symbol.encode("utf-8"))
        while candidate in self.rows:
            candidate = (candidate + 1) & 0xffffffff
        self.rows[candidate] = (text, symbol)
        return candidate

    def encode(self):
        pieces = []
        for field in (0, 1):
            index, pool = bytearray(), bytearray()
            for key, values in self.rows.items():
                index.extend(struct.pack("<II", key, len(pool)))
                pool.extend(values[field].encode("utf-16-le") + b"\0\0")
            pieces.append((index, pool))
        (texts, encoded), (names, symbols) = pieces
        size = len(texts) + len(encoded)
        return (self.prefix + struct.pack("<5I", 140, 152 + size, size, len(encoded), len(self.rows))
                + texts + encoded + struct.pack("<3I", len(names) + len(symbols), len(symbols), len(self.rows))
                + names + symbols)


class NativeLocalization:
    def __init__(self, db, archive):
        self.archive = archive
        self.tables = {}
        self.namespaces = {}
        if archive is None:
            return
        with ZipFile(archive) as source:
            for table, fields in TABLES.items():
                self.tables[table] = StringTable(source.read(table + ".str"), table)
                values = {int(row[0][2:]) >> 32 for field in fields
                          for row in db.execute(f'SELECT "{field}" FROM "{table}"')
                          if isinstance(row[0], str) and row[0].startswith("_&")}
                self.namespaces[table], = values

    def encode(self, table, field, alias, text):
        if table not in self.tables:
            return text
        low = self.tables[table].append(f"IDS_{field}_ExpandedGarage_{alias}", text)
        return f"_&{(self.namespaces[table] << 32) | low}"

    def write(self, output):
        if self.archive is None:
            return None
        with ZipFile(self.archive) as source:
            members = {item.filename: source.read(item) for item in source.infolist() if not item.is_dir()}
            original = fh4_archive.Directory(source.start_dir, self.archive.stat().st_size - source.start_dir,
                                             len(source.infolist()))
        for table, edited in self.tables.items():
            name = table + ".str"
            baseline = StringTable(members[name], table)
            members[name] = edited.encode()
            check = StringTable(members[name], table)
            if check.rows != edited.rows or any(check.rows[k] != v for k, v in baseline.rows.items()):
                raise ValueError(f"Localization roundtrip changed existing {table} strings")
        data, directory = fh4_archive.pack(members)
        path = output / EN_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"path": EN_PATH, "baseline_sha256": file_sha(self.archive), "archive_sha256": file_sha(path),
                "directory": vars(directory), "baseline_directory": vars(original),
                "tables": {t: len(v.rows)-v.original_count for t, v in self.tables.items()}}


def update_mount(data, report):
    if report is None:
        return data
    original = ET.fromstring(data)
    selected, = [e for e in original if e.get("path", "").casefold() == EN_VIRTUAL]
    before = report["baseline_directory"]
    after = report["directory"]
    old = selected.attrib.copy()
    updates = {"dirstart": "start", "dirsize": "size", "direntries": "entries"}
    for field, key in updates.items():
        if old[field] != str(before[key]):
            raise ValueError("Baseline English archive geometry disagrees with manifest")
    marker = re.compile(rb'<Zip\b[^<>]*\bpath="' + re.escape(selected.get("path").encode()) + rb'"[^<>]*/>')
    match, = marker.finditer(data)
    line = match[0]
    for field, key in updates.items():
        line = line.replace(f'{field}="{before[key]}"'.encode(), f'{field}="{after[key]}"'.encode())
        selected.set(field, str(after[key]))
    output = data[:match.start()] + line + data[match.end():]
    if [row.attrib for row in ET.fromstring(output)] != [row.attrib for row in original]:
        raise ValueError("Localization archive mount update did not round-trip")
    return output


def thumbnail_virtual(path):
    return "game:\\"+path.replace("/","\\").casefold()


def presentation_mount(data, presentation):
    data = update_mount(data,presentation["english"])
    template = {r.get("path","").casefold():r.attrib for r in ET.fromstring(data)}
    for item in presentation["art"]:
        if item.get("kind")!="thumbnail_archive":
            continue
        native = template[thumbnail_virtual(f'media/stripped/RC0{"HiRes" if item["hires"] else ""}.zip')]
        data = fh4_archive.add_archive_mount(data,thumbnail_virtual(item["path"]),
                    fh4_archive.Directory(**item["directory"]),native["version"],native["priority"])
    return data


def xds_codec(checkout):
    source = (checkout or ROOT / "tools/driversf-forza-master") / "src"
    path = str(source.resolve()) if source.is_dir() else None
    if path:
        sys.path.insert(0, path)
    try:
        return (importlib.import_module("dsfforza.game.gui_assets").read_xds_rgba,
                importlib.import_module("dsfforza.formats.xds").png_bytes)
    except ImportError:
        return None
    finally:
        if path:
            sys.path.remove(path)


def swatch(name, width, height, rgba):
    """Generate a one-slice premultiplied BC3 texture from FM4 texels."""
    from PIL import Image

    straight = Image.frombytes("RGBA", (width, height), bytes(rgba))
    red, green, blue, alpha = straight.split()
    from PIL import ImageChops
    premultiplied = Image.merge("RGBA", tuple(ImageChops.multiply(channel, alpha)
                                              for channel in (red, green, blue)) + (alpha,))
    out = BytesIO()
    premultiplied.save(out, format="DDS", pixel_format="DXT5")
    dds = out.getvalue()
    if dds[84:88] != b"DXT5":
        raise ValueError("BC3 encoder returned an unexpected texture format")
    payload = dds[128:]
    if len(payload) != ((width + 3)//4) * ((height + 3)//4) * 16:
        raise ValueError("BC3 encoder produced an invalid mip size")
    identifier = uuid.uuid5(uuid.NAMESPACE_URL, "urn:fh4-expanded-garage:swatch:" + name.casefold())
    header = struct.pack("<II16sIIIHBB4I2I3I3I", 56, 80, identifier.bytes_le,
                         width, height, 1, 1, 1, 2, 0, 0, 0, 0, 64, 68,
                         2, 76, 0xffffffff, len(payload), 0, 0xffffffff)
    result = fh4_model.bundle([fh4_model.Chunk("TXCB", (0, 0), payload,
                                              (fh4_model.metadata("TXCH", header, 8),))])
    _, _, _, _, _, offset, size, _ = fh4_model.BLOB.unpack_from(result, fh4_model.HEADER.size)
    if size != len(payload) or result[offset:offset+size] != payload:
        raise ValueError("Swatch wire readback failed")
    return result


def generate_art(output, vehicles, manufacturers, checkout=None):
    codec = xds_codec(checkout)
    emitted, missing, brands = [], [], set()
    for vehicle in vehicles:
        car = vehicle.identity["car_id"]
        source = vehicle.ir["sections"]["physics"]["data"]["tables"]["List_CarMake"][0]["record"]
        code = source["ManufacturerCode"]
        textures = vehicle.ir["presentation"]["textures"]
        jobs = [(next((t for t in textures if t["name"].startswith("thumbnails/")), None), None)]
        if code in manufacturers and code not in brands:
            brands.add(code)
            for kind, folder in (("small", "manufacturer_logos_i"), ("large", "manufacturer_logos_lg")):
                jobs.append((next((t for t in textures if f"/{folder}/" in t["name"]), None),
                             f"media/expanded_garage/manufacturers/{code}_{kind}.swatchbin"))
        for resource, target in jobs:
            if codec is None or resource is None:
                missing.append(target or f"Vehicle {car} native thumbnail pair")
                continue
            decode, _ = codec
            digest = resource["provenance"]["asset"]
            try:
                width, height, rgba = decode(read_asset(vehicle.root, vehicle.ir, digest))
                if target is None:
                    emitted.extend(write_thumbnails(output,car,digest,width,height,rgba))
                    continue
                payload = swatch(target,width,height,rgba)
            except (ImportError, OSError, ValueError) as error:
                missing.append(f"{target or f'Vehicle {car} native thumbnail pair'}: {error}")
                continue
            path = output / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            emitted.append({"path": target, "sha256": sha256(payload).hexdigest(),
                            "source": digest, "size": len(payload), "dimensions": (width, height)})
    return {"art": emitted, "missing": missing}
