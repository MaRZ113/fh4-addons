"""FM4 engine sound on FH4's granular synth (fh4_audio)."""

import io
import struct
import unittest
import zipfile

import numpy as np

from . import fh4_audio as A


class AdpcmTests(unittest.TestCase):
    def test_encode_decode_roundtrip_is_close(self):
        t = np.arange(128 * 40) / A.RATE
        x = A.to_pcm(0.5 * np.sin(2 * np.pi * 220 * t) + 0.2 * np.sin(2 * np.pi * 1730 * t))
        data = A.adpcm_encode(x)
        self.assertEqual(len(data), 40 * A.BLOCK)
        decoded = A.adpcm_decode(data)
        snr = 10 * np.log10((x.astype(float) ** 2).sum() / ((x - decoded.astype(float)) ** 2).sum())
        self.assertGreater(snr, 30)

    def test_wav_header_matches_the_stock_packs(self):
        blob = A.wav(np.zeros(256, np.int16))
        self.assertEqual(blob[:4], b"RIFF")
        self.assertEqual(struct.unpack_from("<HHIIHH", blob, 20), (2, 1, 48000, 26250, 70, 4))
        # cbSize 32, 128 samples per block, seven coefficient pairs, as in Revolusound Acu_Integra_Eng.
        self.assertEqual(blob[36:56].hex(), "200080000700000100000002" + "00ff0000" + "0000c000")


class PackTests(unittest.TestCase):
    def loops(self):
        out = []
        for rpm in (900, 2000, 4000):
            n = A.resample_loop(np.sin(np.linspace(0, 40 * np.pi, 36000)), 36000)
            out.append((rpm, 0.5 * n))
        return out

    def test_loop_lengths_are_whole_blocks(self):
        self.assertEqual(len(A.resample_loop(np.zeros(37376), 36000)) % A.BLOCK_SAMPLES, 0)

    def test_pack_has_two_configs_sweeps_and_every_named_loop(self):
        blob = A.pack("Exh", "EG_TEST_00", self.loops())
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            names = set(archive.namelist())
            acc = archive.read("Exh_Acc_EG_TEST_00.txt").decode("ascii")
            dec = archive.read("Exh_Dec_EG_TEST_00.txt").decode("ascii")
        self.assertIn("Exh_Acc_EG_TEST_00_Acc_Sweep_GS.dat_ADPCM.wav", names)
        self.assertIn("Exh_Dec_EG_TEST_00_Dec_Sweep_GS.dat_ADPCM.wav", names)
        self.assertTrue(acc.startswith("Emitter,Exh\r\nThrottleLevel,Acc\r\n"))
        self.assertIn("MinRPM,900", acc)
        self.assertIn("Loop,900,Idle_Dec_900_ADPCM.wav,", dec)
        self.assertNotIn("Idle", acc)
        for text in (acc, dec):
            for line in text.splitlines():
                if line.startswith("Loop,"):
                    self.assertIn(line.split(",")[2], names)

    def test_cycles_match_the_community_pack(self):
        self.assertEqual(A.cycles(3311, int(2.608 * A.RATE)), 72)


class MappingTests(unittest.TestCase):
    STOCK = ('﻿<?xml version="1.0" encoding="utf-8"?>\r\n<Engines version="1">\r\n'
             '    <Engine mediaName="ACU_RSXTypeS_02" engineName="ACU_IntegraR_01" />\r\n'
             '    <Engine mediaName="FOR_FiestaST_14" engineName="FOR_FiestaST_14">\r\n'
             '        <GranularEngine granularBank="GS_FOR_Fiesta_81" granularAssets="I4NC_Fiesta" rpmScalar="0.9">\r\n'
             '        </GranularEngine>\r\n    </Engine>\r\n</Engines>\r\n').encode("utf-8")

    def test_entry_goes_before_the_end_and_names_every_pack(self):
        packs = {e: f"EG_X_00_{e}" for _, e in A.MAPPING_TAGS}
        out = A.patched_mapping(self.STOCK, [A.mapping_entry("EG_X_00", "FOR_FiestaST_14", "GS_FOR_Fiesta_81",
                                                             "I4NC_Fiesta", packs)])
        self.assertTrue(out.startswith(b"\xef\xbb\xbf"))
        text = out.decode("utf-8-sig")
        self.assertLess(text.index('mediaName="EG_X_00"'), text.index("</Engines>"))
        self.assertIn("<InternalR>EG_X_00_InR</InternalR>", text)
        self.assertTrue(text.startswith(self.STOCK.decode("utf-8-sig")[:-len("</Engines>\r\n")]))
        with self.assertRaises(ValueError):
            A.patched_mapping(out, [A.mapping_entry("EG_X_00", "a", "b", "c", packs)])
