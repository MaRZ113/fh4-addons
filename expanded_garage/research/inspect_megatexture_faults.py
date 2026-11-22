"""Recover MegaTexture fault frames from local FH4 Steam 1.478.564.0 dumps."""

import argparse
from hashlib import sha256
from mmap import ACCESS_READ, mmap
from pathlib import Path
import struct

from expanded_garage.fh4_installer import EXE_SHA
from expanded_garage.vehicle_ir import write_json


GRID_LOOKUP = 0x46827E7
GRID_TILE_FIELD = 0x46827F4
TILE_NAMES_READ = 0x467FE60
GRID_RETURN = 0x468284F
GRID_ORIGIN = -24
GRID_WIDTH = GRID_HEIGHT = 48
TILE_DIVISOR = 1023
REGISTERS = 'rax rcx rdx rbx rsp rbp rsi rdi r8 r9 r10 r11 r12 r13 r14 r15 rip'.split()


def unpack(data, fmt, offset=0):
    return struct.unpack_from('<' + fmt, data, offset)


class MiniDump:
    def __init__(self, raw, executable):
        self.raw = raw
        if raw[:4] != b'MDMP' or sha256(executable).hexdigest() != EXE_SHA:
            raise ValueError('Expected a Windows minidump and the pinned FH4 executable')
        count, directory = unpack(raw, 'II', 8)
        self.streams = {kind: pos for kind, _, pos in
                        (unpack(raw, 'III', directory + 12 * i) for i in range(count)) if kind}
        modules = self.streams[4]
        count, = unpack(raw, 'I', modules)
        for i in range(count):
            base, size, checksum, stamp, name_at = unpack(raw, 'QIIII', modules + 4 + 108 * i)
            name_size, = unpack(raw, 'I', name_at)
            name = raw[name_at + 4:name_at + 4 + name_size].decode('utf-16-le')
            if name.casefold().endswith('\\forzahorizon4.exe'):
                break
        else:
            raise ValueError('FH4 module is absent')
        pe, = unpack(executable, 'I', 60)
        expected_size, _, expected_checksum = unpack(executable, 'III', pe + 24 + 56)
        expected_stamp, = unpack(executable, 'I', pe + 8)
        if (size, checksum, stamp) != (expected_size, expected_checksum, expected_stamp):
            raise ValueError('The dump does not identify the pinned FH4 module')
        self.base = base
        exc = self.streams[6]
        self.thread, = unpack(raw, 'I', exc)
        self.exception, = unpack(raw, 'I', exc + 8)
        self.pc, = unpack(raw, 'Q', exc + 24)
        self.access, self.fault = unpack(raw, 'QQ', exc + 40)
        _, context = unpack(raw, 'II', exc + 160)
        self.registers = dict(zip(REGISTERS, unpack(raw, '17Q', context + 120)))
        if self.registers['rip'] != self.pc or self.exception != 0xC0000005 or self.access != 0:
            raise ValueError('Expected a consistent AMD64 read-AV context')
        self.rva = self.pc - base
        self.ranges = []
        if 5 in self.streams:
            memory = self.streams[5]
            count, = unpack(raw, 'I', memory)
            self.ranges.extend(unpack(raw, 'QII', memory + 4 + i * 16) for i in range(count))
        if 9 in self.streams:
            memory = self.streams[9]
            count, start = unpack(raw, 'QQ', memory)
            for i in range(count):
                address, length = unpack(raw, 'QQ', memory + 16 + 16 * i)
                self.ranges.append((address, length, start))
                start += length

    def memory(self, address, size):
        for start, length, position in self.ranges:
            delta = address - start
            if 0 <= delta <= length - size:
                return self.raw[position + delta:position + delta + size]
        return None

    def require(self, address, fmt):
        data = self.memory(address, struct.calcsize('<' + fmt))
        if data is None:
            raise ValueError(f'Missing stack memory at {address:#x}')
        return unpack(data, fmt)

    def memory_region(self, address):
        if address is None or 16 not in self.streams:
            return None
        start = self.streams[16]
        header, stride, count = unpack(self.raw, 'IIQ', start)
        if header < 16 or stride < 48:
            raise ValueError('Unexpected MINIDUMP_MEMORY_INFO_LIST layout')
        for i in range(count):
            base, allocation, _, _, size, state, protect, kind, _ = unpack(
                self.raw, 'QQIIQIIII', start + header + stride * i)
            if base <= address < base + size:
                return {'base': hex(base), 'allocation_base': hex(allocation), 'region_size': size,
                        'state': hex(state), 'protection': hex(protect), 'type': hex(kind)}
        return None


def grid_rectangle(coordinates):
    # SAR followed by the native sign-adjusted IDIV is floor division.
    return tuple((coordinate >> 7) // TILE_DIVISOR for coordinate in coordinates)


def grid_index(row, column, origin_row=GRID_ORIGIN, origin_column=GRID_ORIGIN, width=GRID_WIDTH):
    return (row - origin_row) * width + column - origin_column


def inspect(dump):
    r = dump.registers
    rsp = r['rsp']
    tile = table = None
    tile_field = None
    if dump.rva in (GRID_LOOKUP, GRID_TILE_FIELD):
        grid, frame = r['rsi'], r['rbp']
        ring = r['rdi']
        table = r['rax']
        measured_index = struct.unpack('<q', struct.pack('<Q', r['rcx']))[0]
        if dump.rva == GRID_LOOKUP:
            if dump.memory(dump.pc, 4) != bytes.fromhex('4c8b2cc8') or table + 8 * measured_index != dump.fault:
                raise ValueError('The captured grid registers do not reproduce the fault address')
            returns = (0x1E8, 0x258, 0x318, 0x498, 0x4C8)
        else:
            if (dump.memory(dump.pc, 9) != bytes.fromhex('4380bc2ce800000000')
                    or r['r12'] + r['r13'] + 0xE8 != dump.fault):
                raise ValueError('The captured tile record does not reproduce the fault address')
            tile_field = {'computed_slot_address': hex(table + 8 * measured_index),
                          'tile_pointer': hex(r['r13']), 'flag_selector': r['r12']}
            returns = (0x1E8, 0x2A8, 0x428, 0x458)
    elif dump.rva == TILE_NAMES_READ and dump.memory(dump.pc, 4) == bytes.fromhex('49630424'):
        if r['r12'] != 0x10 or dump.fault != r['r12']:
            raise ValueError('The captured registers do not show the null tile-name buffer')
        # Parent RBP and RSI are pushed before the 0x1280-byte child allocation.
        entry = rsp + 0x12A8
        frame, = dump.require(entry - 8, 'Q')
        grid, = dump.require(entry - 16, 'Q')
        saved_tile, = dump.require(entry - 32, 'Q')
        return_pc, = dump.require(entry, 'Q')
        if frame != entry + 0x108 or saved_tile != r['rsi'] or return_pc != dump.base + GRID_RETURN:
            raise ValueError('The tile-name caller does not match the pinned grid frame')
        ring, = dump.require(rsp + 0x1270, 'Q')
        tile = {'address': hex(r['rsi']), 'file_name_buffer_inferred': '0x0', 'request_flag': r['r13']}
        measured_index = None
        returns = (0x12A8, 0x1498, 0x1508, 0x15C8, 0x1748, 0x1778)
    else:
        raise ValueError(f'Unexpected exception instruction at {dump.rva:#x}')

    encoded = dump.require(frame - 0x44, '4i')
    x_min, y_min, x_max, y_max = grid_rectangle(encoded)
    xh, xl, yh = dump.require(frame - 0x50, '3i')
    yl, = dump.require(frame + 0x110, 'i')
    if (x_min, y_min, x_max, y_max) != (xl, yl, xh, yh):
        raise ValueError('The recovered input does not reproduce the recorded tile rectangle')
    captured_grid = dump.memory(grid + 0x60, 24)
    fields = unpack(captured_grid, 'Qiiii') if captured_grid is not None else None
    pointer = fields[0] if fields else table
    ox, oy, width, height = fields[1:] if fields else (GRID_ORIGIN, GRID_ORIGIN, GRID_WIDTH, GRID_HEIGHT)
    modeled_index = grid_index(y_min, x_min, oy, ox, width)
    if measured_index is not None and measured_index != modeled_index:
        raise ValueError('The inferred grid dimensions do not reproduce the recorded index')
    if fields and tile and pointer:
        slot = dump.memory(pointer + 8 * modeled_index, 8)
        tile['pointer_at_computed_slot'] = hex(unpack(slot, 'Q')[0]) if slot is not None else None
        tile_bytes = dump.memory(r['rsi'] + 0xD8, 8)
        tile['captured_file_name_buffer'] = hex(unpack(tile_bytes, 'Q')[0]) if tile_bytes is not None else None

    chain = [dump.require(rsp + offset, 'Q')[0] - dump.base for offset in returns]
    expected = [0x4684608, 0x46824DE, 0x467D531, 0x467FDBA, 0x3325EBA]
    if tile:
        expected.insert(0, GRID_RETURN)
    elif tile_field:
        expected.pop(0)
    if chain != expected:
        raise ValueError(f'Return sites do not match the recovered worker path: {[hex(x) for x in chain]}')
    ring_scale_mask = dump.memory(ring + 0x90, 8)
    ring_origins = dump.memory(ring + 0xD0, 16)
    return {'exe_sha256': EXE_SHA, 'dump_sha256': sha256(dump.raw).hexdigest(),
            'thread_id': dump.thread, 'exception': hex(dump.exception), 'fault_rva': hex(dump.rva),
            'fault_read_address': hex(dump.fault), 'instruction_bytes': dump.memory(dump.pc, 4).hex(),
            'return_rvas': [hex(x) for x in chain], 'encoded_rectangle': encoded,
            'tile_rectangle': [x_min, y_min, x_max, y_max], 'grid_address': hex(grid),
            'table_address': hex(pointer) if pointer else None, 'grid_fields_captured': fields is not None,
            'grid_dimensions_basis': 'dump fields' if fields else 'MegaTexture constructor 0x4672dd0',
            'grid_origins': [ox, oy], 'grid_dimensions': [width, height], 'grid_capacity': width * height,
            'measured_index': measured_index, 'modeled_first_index': modeled_index,
            'modeled_first_slot_in_bounds': 0 <= modeled_index < width * height,
            'ring_address': hex(ring), 'ring_scale_and_mask': unpack(ring_scale_mask, 'ii') if ring_scale_mask else None,
            'ring_origins_and_wrap': unpack(ring_origins, '4i') if ring_origins else None,
            'memory_regions': {'grid': dump.memory_region(grid), 'ring': dump.memory_region(ring),
                               'table': dump.memory_region(pointer), 'fault': dump.memory_region(dump.fault)},
            'tile_field': tile_field,
            'tile_name_parser': tile, 'memory_info_list_present': 16 in dump.streams,
            'upstream_asset_identified': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dump', type=Path, action='append', required=True)
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    executable = args.exe.read_bytes()
    reports = []
    for path in args.dump:
        with path.open('rb') as file, mmap(file.fileno(), 0, access=ACCESS_READ) as data:
            reports.append(inspect(MiniDump(data, executable)))
    write_json(args.out, reports)
