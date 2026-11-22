"""Generic self-describing Granny v7 reader (types from the file's own type tree)."""
import struct
from ..fh4_runtime_rig import TYPE, HEADER_SIZE, SECTION, FIXUP
SIZES = {10: 4, 11: 1, 12: 1, 13: 1, 14: 1, 15: 2, 16: 2, 17: 2, 18: 2, 19: 4, 20: 4, 21: 2, 2: 8, 3: 12, 4: 12, 5: 16, 7: 20, 8: 8, 9: 68, 22: 8}
FMT = {10: "f", 11: "b", 12: "B", 13: "b", 14: "B", 15: "h", 16: "H", 17: "h", 18: "H", 19: "i", 20: "I", 21: "e"}

class G:
    def __init__(self, data, tag=None):
        h = struct.unpack_from("<10I", data, 32)
        self.root_type = (h[5], h[6]); self.root_obj = (h[7], h[8])
        self.s, self.p, self.file_offsets = [], {}, []
        tables = []
        for i in range(h[4]):
            comp, off, size, exp, align, f16, f8, fixes, count, marshal, mcount = SECTION.unpack_from(data, HEADER_SIZE + i * SECTION.size)
            assert comp == 0
            self.s.append(data[off:off + size]); self.file_offsets.append(off); tables.append((fixes, count))
        for i, (fixes, count) in enumerate(tables):
            for k in range(count):
                src, tgt, addr = FIXUP.unpack_from(data, fixes + k * FIXUP.size)
                self.p[i, src] = (tgt, addr)
        self.types = {}
    def ptr(self, sec, off):
        return self.p.get((sec, off))
    def cstr(self, loc):
        if loc is None: return None
        sec, off = loc; d = self.s[sec]; return d[off:d.index(0, off)].decode("latin1")
    def typedef(self, loc):
        if loc in self.types: return self.types[loc]
        sec, off = loc; fields = []
        while True:
            kind, _, _, width, *_ = TYPE.unpack_from(self.s[sec], off)
            if kind == 0: break
            name = self.cstr(self.ptr(sec, off + 4)); ref = self.ptr(sec, off + 12)
            fields.append((kind, name, ref, width)); off += TYPE.size
        self.types[loc] = fields; return fields
    def size(self, tloc):
        return sum(self.fsize(f) for f in self.typedef(tloc))
    def fsize(self, f):
        kind, _, ref, width = f
        if kind == 1: return self.size(ref) * max(width, 1)
        return SIZES[kind] * max(width, 1)
    def read(self, loc, tloc):
        sec, off = loc; out = {}
        for f in self.typedef(tloc):
            kind, name, ref, width = f
            if kind in FMT:
                n = max(width, 1); v = struct.unpack_from("<" + FMT[kind] * n, self.s[sec], off); out[name] = v[0] if n == 1 else v
            elif kind == 8: out[name] = self.cstr(self.ptr(sec, off))
            elif kind == 2: t = self.ptr(sec, off); out[name] = ("ref", t, ref) if t else None
            elif kind == 22: out[name] = None
            elif kind == 5:
                tt = self.ptr(sec, off); obj = self.ptr(sec, off + 8); out[name] = ("ref", obj, tt) if obj else None
            elif kind == 3:
                cnt = struct.unpack_from("<I", self.s[sec], off)[0]; at = self.ptr(sec, off + 4)
                st = self.size(ref) if ref else 0
                out[name] = [("ref", (at[0], at[1] + i * st), ref) for i in range(cnt)] if cnt and at else []
            elif kind == 4:
                cnt = struct.unpack_from("<I", self.s[sec], off)[0]; at = self.ptr(sec, off + 4)
                out[name] = [("ref", self.ptr(at[0], at[1] + 8 * i), ref) for i in range(cnt)] if cnt and at else []
            elif kind == 7:
                tt = self.ptr(sec, off); cnt = struct.unpack_from("<I", self.s[sec], off + 8)[0]; at = self.ptr(sec, off + 12)
                st = self.size(tt) if tt else 0
                out[name] = [("ref", (at[0], at[1] + i * st), tt) for i in range(cnt)] if cnt and at else []
            elif kind == 1: out[name] = ("inline", (sec, off), ref)
            elif kind == 9: out[name] = struct.unpack_from("<I16f", self.s[sec], off)
            else: out[name] = f"<kind {kind}>"
            off += self.fsize(f)
        return out
    def field_offset(self, loc, tloc, name):
        """File offset of a scalar field of the object at `loc` (section, offset) typed `tloc`."""
        sec, off = loc
        for f in self.typedef(tloc):
            if f[1] == name:
                return self.file_offsets[sec] + off
            off += self.fsize(f)
        raise KeyError(name)
    def deref(self, r):
        if not r or r[0] not in ("ref", "inline") or r[1] is None or r[2] is None: return None
        return self.read(r[1], r[2])
