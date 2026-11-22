"""Reference arithmetic for the observed FH4 terrain coordinate producer."""

from math import isfinite, trunc
import struct


WORLD_TO_RING = 255.75
I32_MIN = -(1 << 31)
I32_MAX = (1 << 31) - 1


def i32(value):
    return ((value - I32_MIN) & 0xFFFFFFFF) + I32_MIN


def cvttss2si(value):
    """Return the signed conversion result and whether SSE marks it invalid."""
    if not isfinite(value):
        return I32_MIN, True
    result = trunc(value)
    return (result, False) if I32_MIN <= result <= I32_MAX else (I32_MIN, True)


def world_axis(value):
    source, = struct.unpack("<f", struct.pack("<f", value))
    try:
        scaled, = struct.unpack("<f", struct.pack("<f", source * WORLD_TO_RING))
    except OverflowError:
        scaled = float("-inf") if source < 0 else float("inf")
    return cvttss2si(scaled)


def aligned_ring_origin(center, spacing, extent):
    """Branch used when the ring moves and accepts the newly requested center."""
    if spacing <= 0 or spacing & (spacing - 1) or extent <= 0 or extent % (2 * spacing):
        raise ValueError("The ring needs power-of-two spacing and an aligned even extent")
    return i32(center - (center & (spacing - 1)) - extent // 2)
