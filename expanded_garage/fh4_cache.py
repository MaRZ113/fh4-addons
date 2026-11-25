"""Content-addressed disk cache for deterministic, slow build steps (BC7 encoding, AO baking).

A key is the SHA-256 of the step name, the source text of the modules that compute it and every input
(bytes, numpy arrays, numbers, strings, nested tuples/lists). Editing an algorithm therefore invalidates
its entries by itself; the output is byte-identical to an uncached run.

Location: EXPANDED_GARAGE_CACHE (a directory), default %LOCALAPPDATA%/expanded-garage/cache (or
~/.cache/expanded-garage). EXPANDED_GARAGE_CACHE=off disables it.
"""

import hashlib
import os
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np


def directory():
    setting = os.environ.get("EXPANDED_GARAGE_CACHE")
    if setting == "off":
        return None
    if setting:
        return Path(setting)
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / ".cache") / "expanded-garage" / "cache"


@lru_cache(maxsize=None)
def _source(module):
    path = getattr(sys.modules[module], "__file__", None)
    return Path(path).read_bytes() if path else module.encode()


def _feed(digest, value):
    if isinstance(value, np.ndarray):
        array = np.ascontiguousarray(value)
        digest.update(b"A" + str(array.dtype).encode() + repr(array.shape).encode())
        digest.update(array.tobytes())
    elif isinstance(value, (bytes, bytearray, memoryview)):
        digest.update(b"B%d:" % len(value) + bytes(value))
    elif isinstance(value, (tuple, list)):
        digest.update(b"L%d:" % len(value))
        for item in value:
            _feed(digest, item)
    elif isinstance(value, dict):
        _feed(digest, sorted(value.items()))
    elif value is None or isinstance(value, (bool, int, float, str, np.generic)):
        digest.update(b"S" + repr(value).encode() + b";")
    else:
        raise TypeError(f"Cannot key a cache entry on {type(value).__name__}")


def key(step, modules, *inputs):
    digest = hashlib.sha256(step.encode() + b"\0")
    for module in modules:
        digest.update(_source(module))
    _feed(digest, inputs)
    return digest.hexdigest()


def cached_bytes(step, modules, inputs, compute):
    """compute() -> bytes, stored under the key of (step, modules' source, inputs)."""
    root = directory()
    if root is None:
        return compute()
    path = root / step / (key(step, modules, *inputs) + ".bin")
    if path.is_file():
        return path.read_bytes()
    data = bytes(compute())
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)
    return data


def cached_array(step, modules, inputs, compute):
    """compute() -> numpy array, cached like cached_bytes (dtype and shape kept)."""
    import io
    def encode():
        buffer = io.BytesIO()
        np.save(buffer, np.asarray(compute()), allow_pickle=False)
        return buffer.getvalue()
    return np.load(io.BytesIO(cached_bytes(step, modules, inputs, encode)), allow_pickle=False)
