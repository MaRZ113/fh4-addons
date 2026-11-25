"""Content cache: same inputs reuse the stored result, any input change recomputes."""

import os
import tempfile
import unittest

import numpy as np

from . import fh4_cache


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.get("EXPANDED_GARAGE_CACHE")
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["EXPANDED_GARAGE_CACHE"] = self.tmp.name

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("EXPANDED_GARAGE_CACHE", None)
        else:
            os.environ["EXPANDED_GARAGE_CACHE"] = self.saved
        self.tmp.cleanup()

    def test_hit_miss_and_array_round_trip(self):
        calls = []

        def compute(value):
            calls.append(value)
            return np.full((2, 3), value, np.uint8)

        inputs = (np.arange(4.0), "name", 7)
        first = fh4_cache.cached_array("probe", (__name__,), inputs, lambda: compute(1))
        again = fh4_cache.cached_array("probe", (__name__,), inputs, lambda: compute(2))
        self.assertEqual(calls, [1])
        np.testing.assert_array_equal(first, again)
        self.assertEqual(again.dtype, np.uint8)
        changed = (np.arange(4.0) + 1e-9, "name", 7)
        fh4_cache.cached_array("probe", (__name__,), changed, lambda: compute(3))
        self.assertEqual(calls, [1, 3])

    def test_off_always_computes(self):
        os.environ["EXPANDED_GARAGE_CACHE"] = "off"
        calls = []
        for _ in range(2):
            fh4_cache.cached_bytes("probe", (__name__,), (b"x",), lambda: calls.append(1) or b"y")
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
