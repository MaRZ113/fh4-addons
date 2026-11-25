"""The car's own FM4 engine sound on FH4's granular engine synth.

FH4 picks a car's engine sound in `media/audio/cars/CarEngineEventMapping.xml`:
`<Engine mediaName=… engineName=…>` names the native FMOD event set (shifts, turbo, limiter), and an optional
`<GranularEngine granularBank granularAssets rpmScalar>` names five sample packs
(`<Engine>`, `<Exhaust>`, `<Intake>`, `<InternalL>`, `<InternalR>`) = `audio/enginesynth/pc/<name>.zip`.
Converted cars were absent from the mapping and got the `fallback` engine (NUL_Car_00).

A pack (stock and the Revolusound community packs, which load from `media/stripped/mediaoverride/rc0/audio/
enginesynth/pc/` without zipmanifest entries): mono 48 kHz MS ADPCM WAVs (block 70 bytes, 128 samples), one per
recorded rpm, `Acc_…` for throttle, `Dec_…` off throttle, `Idle_Dec_<rpm>`; two configs `<Emitter>_Acc_<n>.txt`
and `_Dec_` (`Loop,<rpm>,<file>,<engine cycles in the loop>` after common synth settings, CRLF), and a sweep per
config (`<config stem>_Acc_Sweep_GS.dat_ADPCM.wav`).

FM4 (IR `audio` section): the engine tuning `<car>_et.xml` names three harmonic tunings (EngineAmbient,
EngineIntake, Exhaust at upgrade L0); each `_ht.xml` lists loops (`wavebankindex`, `rpm_sample`, `volume`) in an
FSB4 bank of whole-sample XMA loops at 36 kHz. FM4 has no off-throttle recordings: Dec reuses the loops,
low-passed and quieter. Emitters: Eng <- EngineAmbient, Int <- EngineIntake, Exh <- Exhaust (left), InL <- Engine,
InR <- Intake. The FMOD event donor is the native granular car with the same cylinder count and aspiration,
nearest in power (`donor`). EXPANDED_GARAGE_SOUND=off ships no sound.
"""

import io
import os
import re
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MAPPING = "media/audio/cars/CarEngineEventMapping.xml"
PACK_FOLDER = "media/stripped/mediaoverride/rc0/audio/enginesynth/pc/"
VGMSTREAM = ROOT / "tools/ForzaStudioEVO_0.0.7/vgmstream/vgmstream-cli.exe"
RATE = 48000
BLOCK, BLOCK_SAMPLES = 70, 128
ADAPT = np.array([230, 230, 230, 230, 307, 409, 512, 614, 768, 614, 512, 409, 307, 230, 230, 230], np.int64)
COEFFICIENTS = ((256, 0), (512, -256), (0, 0), (192, 64), (240, 0), (460, -208), (392, -232))
# FM4 harmonic tuning -> FH4 emitters.
EMITTERS = (("Eng", "EngineAmbient"), ("Exh", "Exhaust"), ("Int", "EngineIntake"),
            ("InL", "EngineAmbient"), ("InR", "EngineIntake"))
MAPPING_TAGS = (("Engine", "Eng"), ("Exhaust", "Exh"), ("Intake", "Int"), ("InternalL", "InL"), ("InternalR", "InR"))
# Synth settings of the stock/community packs (identical in every sampled config).
SETTINGS = (("LoopRPMRate", "1000"), ("GranularRPMRate", "1500"), ("MaxXFadeRPMRange", "10000"),
            ("RPMBufferZone", "500"), ("RPMSmoothing", "10"), ("GranuleXFade", "0.05"))
TAIL = (("MaxRPM", "10000"), ("ControlValueFactor", "1"), ("LimiterBlendDuration", "200"),
        ("MinRPMForGearSample", "4000"), ("GranularHoldWalkHalfLength", "2"), ("GranularHoldExitThreshold", "4"),
        ("ThrottleVolume", "0,2.2,1,2.2"), ("GearShiftCompleteVolume", "0,1,1000,1"))
DEC_CUTOFF_HZ, DEC_GAIN = 3500.0, 0.7
SWEEP_CHUNK, SWEEP_FADE = 0.35, 0.04
PEAK = 0.89          # -1 dBFS
ZIP_TIME = (2026, 1, 1, 0, 0, 0)


# --- MS ADPCM ----------------------------------------------------------------------------------------------------

def adpcm_encode(pcm):
    """MS ADPCM blocks (70 bytes, 128 samples) for int16 `pcm` whose length is a multiple of 128."""
    x = np.asarray(pcm, np.int64).reshape(-1, BLOCK_SAMPLES)
    count = len(x)
    best_err = np.full(count, np.inf)
    best = [None] * count
    for index, (c1, c2) in enumerate(COEFFICIENTS):
        s1, s2 = x[:, 1].copy(), x[:, 0].copy()
        first = (s1 * c1 + s2 * c2) >> 8
        delta = np.maximum(16, np.abs(x[:, 2] - first) // 4)
        start_delta = delta.copy()
        nibbles = np.zeros((count, BLOCK_SAMPLES - 2), np.int64)
        error = np.zeros(count)
        for i in range(2, BLOCK_SAMPLES):
            predicted = (s1 * c1 + s2 * c2) >> 8
            residual = x[:, i] - predicted
            nib = np.clip(np.floor(residual / delta + 0.5), -8, 7).astype(np.int64)
            decoded = np.clip(predicted + nib * delta, -32768, 32767)
            error += (x[:, i] - decoded) ** 2
            s2, s1 = s1, decoded
            nibbles[:, i - 2] = nib & 15
            delta = np.maximum(16, (ADAPT[nib & 15] * delta) >> 8)
        better = error < best_err
        best_err = np.where(better, error, best_err)
        for k in np.nonzero(better)[0]:
            best[k] = (index, int(start_delta[k]), nibbles[k])
    out = bytearray()
    for k, (index, delta, nibbles) in enumerate(best):
        out += struct.pack("<Bhhh", index, delta, int(x[k, 1]), int(x[k, 0]))
        out += bytes(((nibbles[0::2] << 4) | nibbles[1::2]).astype(np.uint8))
    return bytes(out)


def adpcm_decode(data):
    """int16 samples of MS ADPCM blocks (the encoder's inverse; used by the tests)."""
    out = []
    for offset in range(0, len(data), BLOCK):
        index, delta, s1, s2 = struct.unpack_from("<Bhhh", data, offset)
        c1, c2 = COEFFICIENTS[index]
        samples = [s2, s1]
        for byte in data[offset + 7:offset + BLOCK]:
            for nib in (byte >> 4, byte & 15):
                signed = nib - 16 if nib & 8 else nib
                predicted = (s1 * c1 + s2 * c2) >> 8
                value = max(-32768, min(32767, predicted + signed * delta))
                samples.append(value)
                s2, s1 = s1, value
                delta = max(16, (int(ADAPT[nib]) * delta) >> 8)
        out.extend(samples)
    return np.asarray(out, np.int16)


def wav(pcm):
    """RIFF WAVE, MS ADPCM mono 48 kHz, fmt as the stock packs (cbSize 32, seven coefficient pairs)."""
    data = adpcm_encode(pcm)
    fmt = struct.pack("<HHIIHHHHH", 2, 1, RATE, RATE * BLOCK // BLOCK_SAMPLES, BLOCK, 4, 32, BLOCK_SAMPLES, 7)
    fmt += b"".join(struct.pack("<hh", a, b) for a, b in COEFFICIENTS)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


# --- signal ------------------------------------------------------------------------------------------------------

def resample_loop(x, source_rate):
    """Periodic FFT resampling to 48 kHz; the length is rounded to whole ADPCM blocks so the loop stays seamless."""
    target = max(BLOCK_SAMPLES, int(round(len(x) * RATE / source_rate / BLOCK_SAMPLES)) * BLOCK_SAMPLES)
    spectrum = np.fft.rfft(x)
    out = np.zeros(target // 2 + 1, complex)
    keep = min(len(spectrum), len(out))
    out[:keep] = spectrum[:keep]
    return np.fft.irfft(out, target) * (target / len(x))


def lowpass_loop(x, cutoff):
    spectrum = np.fft.rfft(x)
    freq = np.fft.rfftfreq(len(x), 1 / RATE)
    spectrum *= 1 / (1 + (freq / cutoff) ** 4)
    return np.fft.irfft(spectrum, len(x))


def to_pcm(x):
    return np.clip(np.round(x * 32767), -32768, 32767).astype(np.int16)


def sweep(loops, descending):
    """A continuous run through the loops in rpm order (ascending for Acc, descending for Dec)."""
    chunk, fade = int(SWEEP_CHUNK * RATE), int(SWEEP_FADE * RATE)
    order = sorted(loops, key=lambda item: item[0], reverse=descending)
    out = np.zeros(0)
    ramp = np.linspace(0, 1, fade)
    for _, x in order:
        piece = np.resize(x, chunk + fade)
        if len(out) < fade:
            out = piece
            continue
        out[-fade:] = out[-fade:] * (1 - ramp) + piece[:fade] * ramp
        out = np.concatenate([out, piece[fade:]])
    length = len(out) // BLOCK_SAMPLES * BLOCK_SAMPLES
    return out[:length]


# --- FM4 source ----------------------------------------------------------------------------------------------------

def _children(node, tag):
    return [c for c in node.get("children", ()) if c.get("tag") == tag]


def fm4_tunings(root, ir):
    """{FM4 emission: (bank bytes, [(rpm, wavebank index, volume)], subsongs)} from the IR audio records."""
    records = ir["sections"]["audio"]["data"]["records"]
    by_name = {r["name"].casefold(): r for r in records}
    et = next((r for r in records if r["name"].casefold().endswith("_et.xml")), None)
    if et is None:
        return {}
    out = {}
    for emission in ("EngineAmbient", "EngineIntake", "Exhaust"):
        nodes = _children(et["xml"], emission)
        upgrade = _children(nodes[0], "Upgrade") if nodes else []
        name = upgrade[0]["attributes"].get("L0", "") if upgrade else ""
        ht = by_name.get(name.casefold())
        if ht is None:
            continue
        bank = by_name.get(ht["xml"]["attributes"].get("name", "").casefold())
        if bank is None:
            continue
        group = next((g for g in ht["xml"]["children"] if _children(g, "Loop")), None)
        if group is None:
            continue
        loops = [(int(a["rpm_sample"]), int(a["wavebankindex"]), float(a.get("volume", 1)))
                 for a in (n["attributes"] for n in _children(group, "Loop"))]
        data = (root / "blobs" / bank["provenance"]["asset"]).read_bytes()
        out[emission] = (data, loops, {s["index"]: s for s in bank["subsongs"]})
    return out


def decode_bank(data, indices, vgmstream):
    """{subsong index: float mono samples at the bank rate} through vgmstream-cli (XMA needs its FFmpeg build)."""
    import wave
    out = {}
    with tempfile.TemporaryDirectory(prefix="fm4-audio-") as tmp:
        bank = Path(tmp) / "bank.fsb"
        bank.write_bytes(data)
        for index in indices:
            target = Path(tmp) / f"{index}.wav"
            subprocess.run([str(vgmstream), "-i", "-s", str(index + 1), "-o", str(target), str(bank)],
                           check=True, capture_output=True)
            with wave.open(str(target)) as w:
                frames = np.frombuffer(w.readframes(w.getnframes()), np.int16).reshape(-1, w.getnchannels())
            out[index] = frames[:, 0].astype(np.float64) / 32768
    return out


def emission_loops(data, loops, subsongs, vgmstream):
    """[(rpm, float loop at 48 kHz)] with FM4's per-loop volume, normalised to PEAK."""
    decoded = decode_bank(data, sorted({i for _, i, _ in loops if i in subsongs}), vgmstream)
    top = max(v for _, _, v in loops) or 1.0
    out = []
    for rpm, index, volume in sorted(loops):
        if index not in decoded:
            continue
        rate = subsongs[index]["sample_rate_hz"]
        out.append((rpm, resample_loop(decoded[index], rate) * (volume / top)))
    peak = max((np.abs(x).max() for _, x in out), default=0) or 1.0
    return [(rpm, x * (PEAK / peak)) for rpm, x in out]


# --- pack --------------------------------------------------------------------------------------------------------

def cycles(rpm, samples):
    """Engine cycles (two revolutions) in a loop: Revolusound Acu_Integra_Eng 3311 rpm, 2.608 s -> 72."""
    return max(1, int(round(rpm / 120 * samples / RATE)))


def config(emitter, throttle, rows, minimum):
    lines = [("Emitter", emitter), ("ThrottleLevel", throttle), *SETTINGS, ("MinRPM", str(minimum)), *TAIL]
    text = [f"{k},{v}" for k, v in lines] + [f"Loop,{rpm},{name},{n}" for rpm, name, n in rows]
    return ("\r\n".join(text) + "\r\n").encode("ascii")


def pack(emitter, stem, loops):
    """Zip bytes of one emitter pack from [(rpm, float loop)] (rpm ascending, lowest = idle)."""
    files, acc_rows, dec_rows = {}, [], []
    idle_rpm = loops[0][0]
    dec_loops = []
    for number, (rpm, x) in enumerate(loops, 1):
        if number == 1:
            name = f"Idle_Dec_{rpm}_ADPCM.wav"
        else:
            name = f"Acc_{number - 1:02d}_Acc_{rpm}_ADPCM.wav"
            files[name] = wav(to_pcm(x))
            acc_rows.append((rpm, name, cycles(rpm, len(x))))
            name = f"Dec_{number - 1:02d}_Dec_{rpm}_ADPCM.wav"
        dec = lowpass_loop(x, DEC_CUTOFF_HZ) * DEC_GAIN
        dec_loops.append((rpm, dec))
        files[name] = wav(to_pcm(dec))
        dec_rows.append((rpm, name, cycles(rpm, len(x))))
    if not acc_rows:   # a single recording: it is the whole Acc set as well
        files[f"Acc_01_Acc_{idle_rpm}_ADPCM.wav"] = wav(to_pcm(loops[0][1]))
        acc_rows.append((idle_rpm, f"Acc_01_Acc_{idle_rpm}_ADPCM.wav", cycles(idle_rpm, len(loops[0][1]))))
    for throttle, rows, source, descending in (("Acc", acc_rows, loops[1:] or loops, False),
                                               ("Dec", dec_rows, dec_loops, True)):
        config_name = f"{emitter}_{throttle}_{stem}"
        files[config_name + ".txt"] = config(emitter, throttle, rows, idle_rpm)
        files[f"{config_name}_{throttle}_Sweep_GS.dat_ADPCM.wav"] = wav(to_pcm(sweep(source, descending)))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[name])
    return out.getvalue()


# --- mapping -----------------------------------------------------------------------------------------------------

ENTRY = re.compile(r'<Engine mediaName="([^"]+)" engineName="([^"]+)"\s*>\s*<GranularEngine granularBank="([^"]+)"'
                   r' granularAssets="([^"]+)" rpmScalar="([^"]+)"')


AUDIO_GLOBALS = ROOT / "game/ForzaHorizon4/media/audio/AudioGlobals.xml"


def open_cars(path=AUDIO_GLOBALS):
    """engineNames of AudioGlobals.xml CarsWithNoWipers (racers, buggies, roadsters): never event donors for a
    road car (visual-45 picked the AC Class 10 buggy for the SX4)."""
    if not Path(path).is_file():
        return set()
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    block = re.search(r"<CarsWithNoWipers>(.*?)</CarsWithNoWipers>", text, re.S)
    return {n.casefold() for n in re.findall(r'EngineName="([^"]+)"', block.group(1))} if block else set()


def donor(mapping_text, db, cylinders_id, aspiration_id, power, excluded=frozenset()):
    """(engineName, granularBank, granularAssets) of the native granular car whose engine matches best."""
    rows = {m.casefold(): (c, a, p) for m, c, a, p in db.execute(
        "SELECT MediaName, CylinderID, AspirationID_Stock, EngineGraphingMaxPower FROM Data_Engine")}
    candidates = []
    for media, engine, bank, assets, _ in ENTRY.findall(mapping_text):
        c, a, p = rows.get(media.casefold(), (None, None, None))
        if c is None or engine.casefold() in excluded or media.casefold() in excluded:
            continue
        score = (c != cylinders_id, a != aspiration_id, abs((p or 0) - (power or 0)), media)
        candidates.append((score, (engine, bank, assets)))
    if not candidates:
        raise ValueError("No native granular engine in CarEngineEventMapping.xml")
    return min(candidates)[1]


def mapping_entry(media, engine, bank, assets, packs):
    lines = [f'    <Engine mediaName="{media}" engineName="{engine}">',
             f'        <GranularEngine granularBank="{bank}" granularAssets="{assets}" rpmScalar="1">']
    lines += [f"            <{tag}>{packs[emitter]}</{tag}>" for tag, emitter in MAPPING_TAGS]
    lines += ["        </GranularEngine>", "    </Engine>"]
    return "\r\n".join(lines) + "\r\n"


def patched_mapping(original, entries):
    """The stock mapping with the converted cars' entries before </Engines> (stock bytes otherwise kept)."""
    text = original.decode("utf-8-sig")
    bom = b"\xef\xbb\xbf" if original.startswith(b"\xef\xbb\xbf") else b""
    for media in re.findall(r'<Engine mediaName="([^"]+)"', "".join(entries)):
        if re.search(rf'<Engine mediaName="{re.escape(media)}"', text, re.I):
            raise ValueError(f"CarEngineEventMapping.xml already maps {media}")
    end = text.rindex("</Engines>")
    return bom + (text[:end] + "".join(entries) + text[end:]).encode("utf-8")


def compile_fleet(build, vehicles, baseline_db, compiled_db, mapping_path, vgmstream=None):
    """Write the sound packs and the patched mapping under `build`; return audio.json content (or None)."""
    if os.environ.get("EXPANDED_GARAGE_SOUND") == "off" or mapping_path is None:
        return None
    import sqlite3
    from . import fh4_cache
    from .vehicle_ir import sha256
    tool = Path(vgmstream or os.environ.get("EXPANDED_GARAGE_VGMSTREAM") or VGMSTREAM)
    if not tool.is_file():
        raise ValueError(f"vgmstream-cli is required for FM4 engine sound: {tool}")
    original = Path(mapping_path).read_bytes()
    mapping_text = original.decode("utf-8-sig")
    entries, files, cars = [], [], []
    excluded = open_cars()
    with sqlite3.connect(f"file:{baseline_db}?mode=ro", uri=True) as base, \
            sqlite3.connect(f"file:{compiled_db}?mode=ro", uri=True) as compiled:
        for vehicle in vehicles:
            media = vehicle.identity["media_name"]
            tunings = fm4_tunings(vehicle.root, vehicle.ir)
            if not tunings:
                continue
            engine_row = compiled.execute(
                "SELECT CylinderID, AspirationID_Stock, EngineGraphingMaxPower FROM Data_Engine WHERE MediaName=?",
                (media,)).fetchone()
            if engine_row is None:
                # Electric car: no cylinder/aspiration to match a granular donor; the game's fallback sound for now.
                continue
            engine, bank, assets = donor(mapping_text, base, *engine_row, excluded=excluded)
            packs = {}
            for emitter, emission in EMITTERS:
                source = tunings.get(emission) or next(iter(tunings.values()))
                stem = media if media.startswith("EG_") else f"EG_{media}"
                name = f"{stem}_{emitter}"
                data, loops, subsongs = source
                key = (data, loops, emitter, stem, tool.name)
                payload = fh4_cache.cached_bytes(
                    "fm4-engine-pack", (__name__,), key,
                    lambda: pack(emitter, stem, emission_loops(data, loops, subsongs, tool)))
                relative = PACK_FOLDER + name + ".zip"
                target = build / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                files.append({"path": relative, "sha256": sha256(target)})
                packs[emitter] = name
            entries.append(mapping_entry(media, engine, bank, assets, packs))
            cars.append({"media_name": media, "engine_donor": engine, "granular_bank": bank,
                         "granular_assets": assets, "packs": packs})
    if not entries:
        return None
    target = build / MAPPING
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(patched_mapping(original, entries))
    return {"mapping": {"path": MAPPING, "baseline_sha256": sha256(Path(mapping_path)), "sha256": sha256(target)},
            "packs": files, "cars": cars}


def verify(build, audio):
    """Hashes of the staged sound files and a readable pack layout."""
    from .vehicle_ir import sha256
    for item in [audio["mapping"], *audio["packs"]]:
        if sha256(build / item["path"]) != item["sha256"]:
            raise ValueError(f"Sound file changed: {item['path']}")
    for item in audio["packs"]:
        with zipfile.ZipFile(build / item["path"]) as archive:
            names = archive.namelist()
            configs = [n for n in names if n.endswith(".txt")]
            if len(configs) != 2 or sum("_Sweep_GS.dat_ADPCM.wav" in n for n in names) != 2:
                raise ValueError(f"Incomplete engine sound pack {item['path']}")
            for config_name in configs:
                for line in archive.read(config_name).decode("ascii").splitlines():
                    if line.startswith("Loop,") and line.split(",")[2] not in names:
                        raise ValueError(f"{item['path']}: {config_name} names a missing loop")
