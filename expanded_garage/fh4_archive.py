"""FH4 PC ZIP indexing and append-only mount manifest handling."""

from dataclasses import dataclass
from io import BytesIO
import struct
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
import zlib

from .vehicle_ir import media_name, member_path


OFFSET_EXTRA = struct.Struct("<HHI")
EOCD = struct.Struct("<4s4H2IH")


@dataclass(frozen=True)
class Directory:
    start: int
    size: int
    entries: int


class CarZipInfo(ZipInfo):
    def FileHeader(self, zip64=None):
        # On stock PC car archives 0x1123 is present only in the central directory.
        extra, self.extra = self.extra, b""
        try:
            return super().FileHeader(zip64)
        finally:
            self.extra = extra


def validate_members(names):
    folded = set()
    for name in names:
        if name != member_path(name) or name.casefold() in folded:
            raise ValueError(f"Invalid or duplicate FH4 member {name}")
        folded.add(name.casefold())


def directories(blob, archive):
    eocd_at = len(blob) - EOCD.size
    sig, disk, disk_dir, here, count, size, start, comment = EOCD.unpack_from(blob, eocd_at)
    if (sig != b"PK\x05\x06" or disk or disk_dir or comment or count != here
            or count != len(archive.infolist()) or start != archive.start_dir or start + size != eocd_at):
        raise ValueError("Expected a single-disk FH4 ZIP32 with no comment")
    cursor = start
    for entry in archive.infolist():
        if (blob[cursor:cursor + 4] != b"PK\x01\x02"
                or blob[entry.header_offset:entry.header_offset + 4] != b"PK\x03\x04"
                or entry.flag_bits or entry.compress_type not in (0, 8)):
            raise ValueError(f"Unsupported FH4 ZIP record {entry.filename}")
        name, extra, header_comment = struct.unpack_from("<3H", blob, cursor + 28)
        local_name, local_extra = struct.unpack_from("<2H", blob, entry.header_offset + 26)
        filename = entry.filename.encode("ascii")
        extra_start = cursor + 46 + name
        local_name_start = entry.header_offset + 30
        if (filename != blob[cursor + 46:extra_start] or
                filename != blob[local_name_start:local_name_start + local_name] or
                extra != OFFSET_EXTRA.size):
            raise ValueError(f"Unexpected FH4 name or extra data for {entry.filename}")
        payload = local_name_start + local_name + local_extra
        if payload + entry.compress_size > start:
            raise ValueError(f"ZIP payload overlaps directory for {entry.filename}")
        yield entry, extra_start, payload
        cursor = extra_start + extra + header_comment
    if cursor != eocd_at:
        raise ValueError("Unexpected central directory suffix")


def verify(blob):
    with ZipFile(BytesIO(blob)) as archive:
        validate_members(archive.namelist())
        intervals = []
        for entry, extra, payload in directories(blob, archive):
            if OFFSET_EXTRA.unpack_from(blob, extra) != (0x1123, 4, payload):
                raise ValueError(f"Invalid 0x1123 payload offset for {entry.filename}")
            end = payload + entry.compress_size
            intervals.append((entry.header_offset, end))
            decoded = blob[payload:end]
            if entry.compress_type == 8:
                inflater = zlib.decompressobj(-15)
                decoded = inflater.decompress(decoded, entry.file_size + 1)
                if not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
                    raise ValueError(f"Invalid DEFLATE frame for {entry.filename}")
            if len(decoded) != entry.file_size or zlib.crc32(decoded) != entry.CRC:
                raise ValueError(f"FH4 payload CRC or size mismatch for {entry.filename}")
        spans = sorted(intervals)
        if any(a_end > b_start for (_, a_end), (b_start, _) in zip(spans, spans[1:])):
            raise ValueError("Overlapping ZIP local records")
        return Directory(archive.start_dir, len(blob) - archive.start_dir, len(spans))


def pack(members):
    validate_members(members)
    output = BytesIO()
    with ZipFile(output, "w", allowZip64=False) as archive:
        for name, payload in sorted(members.items()):
            item = CarZipInfo(name)
            item.create_system = 0
            item.extra = OFFSET_EXTRA.pack(0x1123, 4, 0)
            archive.writestr(item, payload, compress_type=ZIP_DEFLATED, compresslevel=6)
    data = bytearray(output.getvalue())
    with ZipFile(BytesIO(data)) as archive:
        for _, extra, payload in directories(data, archive):
            OFFSET_EXTRA.pack_into(data, extra, 0x1123, 4, payload)
    result = bytes(data)
    return result, verify(result)


def mount_path(name):
    return f"game:\\media\\cars\\{media_name(name).lower()}.zip"


def add_mount(data, name, directory):
    xml = ET.fromstring(data)
    if xml.tag != "ZipFiles" or xml.get("version") != "3":
        raise ValueError("Expected FH4 plaintext zipmanifest version 3")
    path = mount_path(name)
    mount_classes = {(child.get("version"), child.get("priority")) for child in xml
                     if child.get("path", "").casefold().startswith("game:\\media\\cars\\")
                     and child.get("path", "").count("\\") == 3}
    if len(mount_classes) != 1 or None in next(iter(mount_classes)):
        raise ValueError(f"Ambiguous FH4 base car mount class {sorted(mount_classes, key=repr)}")
    version, priority = next(iter(mount_classes))
    return add_archive_mount(data,path,directory,version,priority)


def add_archive_mount(data,path,directory,version,priority):
    xml = ET.fromstring(data)
    if xml.tag != "ZipFiles" or xml.get("version") != "3":
        raise ValueError("Expected FH4 plaintext zipmanifest version 3")
    if any(child.get("path","").casefold()==path.casefold() for child in xml):
        raise ValueError(f"FH4 archive mount already exists: {path}")
    entry = ET.Element("Zip", version=version, path=path, priority=priority, dirstart=str(directory.start),
                       dirsize=str(directory.size), direntries=str(directory.entries))
    closing = b"</ZipFiles>"
    if data.count(closing) != 1:
        raise ValueError("Unexpected FH4 manifest framing")
    newline = b"\r\n" if b"\r\n" in data else b"\n"
    result = data.replace(closing, b"  " + ET.tostring(entry, encoding="ascii", xml_declaration=False) + newline + closing)
    checked = ET.fromstring(result)
    if checked[-1].attrib != entry.attrib or len(checked) != len(xml) + 1:
        raise ValueError("FH4 manifest failed mount readback")
    return result
