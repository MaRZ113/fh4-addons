"""Resolve FM4 tire purposes to checked, shared FH4 physical and scene models."""

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from . import fh4_archive


MODEL_FIELDS = ("TireModelName", "WetTireModelName", "SnowTireModelName")
AXLE_FIELDS = ("TireWidthMM", "TireAspect", "WheelDiameterIN")
WIDTH_TABLES = {f"List_UpgradeCarBodyTireWidth{axle}": axle for axle in ("Front", "Rear")}
# Physical families and unbranded tread presentations observed together in the target catalogue.
FAMILIES = {
    "highperformance": ("modernhighperformance", (("a", "a", "a"), ("wet_b",)*3)),
    "ultrahighperformance": ("modernultrahighperformance", (("a",)*3, ("wet_b",)*3, ("b",)*3)),
    "maxperformance": ("maxperformance", (("a",)*3, ("wet_b",)*3, ("b",)*3, ("wet_c",)*3)),
    "dotcompetition": ("dotcompetition", (("b",)*3,)),
    "racingslick": ("racingslick", (("c", "c", "wet_c"), ("c", "wet_c", "wet_c"))),
    "dragtire": ("drag", (("drag",)*3,)),
}
MOUNT_PREFIX = "game:\\media\\cars\\_library\\scene\\tires\\"


def family_name(label):
    label = re.sub(r"^\d+_", "", label.split(" (", 1)[0])
    return re.sub(r"[^a-z]", "", label.casefold())


def dimensions(car):
    result = {axle: tuple(car[axle + field] for field in AXLE_FIELDS) for axle in ("Front", "Rear")}
    for width, aspect, rim in result.values():
        if not (100 <= width <= 650 and 20 <= aspect <= 100 and 10 <= rim <= 30):
            raise ValueError("Unsupported tire dimensions in mm, percent and inches")
    return result


class UnknownTire(ValueError):
    """A source tire has no verified, supported target mapping."""


@dataclass(frozen=True)
class TireSelection:
    fields: dict
    source_family: str
    target_family: str
    stock_sample_count: int
    shared_keys: tuple


class NativeTireCompiler:
    def __init__(self, db):
        self.db = db
        self.compounds = {family_name(r["DisplayName"]): r for r in db.execute("SELECT * FROM List_TireCompound")}
        columns = ", ".join("c." + axle + field for axle in ("Front", "Rear") for field in AXLE_FIELDS)
        self.native_examples = db.execute(
            f"SELECT u.TireCompoundID, u.IsStock, {', '.join('u.' + f for f in MODEL_FIELDS)}, "
            f"{columns} FROM List_UpgradeTireCompound u JOIN Data_Car c ON c.Id=u.Ordinal "
            "WHERE c.CarTypeID=1 AND c.Year>=1985 AND c.Id<12000"
        ).fetchall()

    @staticmethod
    def stock_size(catalogue, axle):
        width, aspect, rim = catalogue[axle]
        return {"width_mm": width, "aspect_percent": aspect, "rim_in": rim,
                "radius_m": rim * 0.0127 + width * aspect / 100000}

    def select(self, source, row):
        matches = source.raw_records("List_TireCompound", "TireCompoundID", row["TireCompoundID"])
        if len(matches) != 1:
            raise UnknownTire(f"Unknown FM4 tire compound {row['TireCompoundID']}")
        name = matches[0]["record"]["DisplayName"]
        car = source.record("Data_Car")
        family = FAMILIES.get(family_name(name))
        if family is None or car["CarTypeID"] != 1 or car["Year"] < 1985:
            raise UnknownTire(f"No road-car tire rule for {name} in this vehicle class or era")
        compound = self.compounds.get(family[0])
        if compound is None or compound["IsOffroad"] != 0:
            raise UnknownTire(f"Missing compatible asphalt physical family {family[0]}")
        sizes = dimensions(car)
        requested = (*sizes["Front"], *sizes["Rear"])
        # One native analogue must cover both axles. A set of unrelated per-axle extrema does not establish fit.
        tolerance = (40, 20, 4) * 2
        candidates = {}
        for r in self.native_examples:
            values = tuple(r)[5:]
            tokens = tuple((r[f] or "").casefold() for f in MODEL_FIELDS)
            if r["TireCompoundID"] != compound["TireCompoundID"] or tokens not in family[1]:
                continue
            if not all(isinstance(x, (int, float)) for x in values):
                continue
            deltas = tuple(abs(a-b) / limit for a, b, limit in zip(requested, values, tolerance, strict=True))
            if max(deltas) > 1:
                continue
            candidates.setdefault(tokens, []).append((r["IsStock"], sum(deltas), tuple(r[f] for f in MODEL_FIELDS)))
        eligible = [(0 if stock == row["IsStock"] else 1, score, family[1].index(tokens), values)
                    for tokens, examples in candidates.items() for stock, score, values in examples]
        if not eligible:
            raise UnknownTire(f"No compatible native front/rear tire profile for {name} and {requested}")
        _, _, preference, models = min(eligible)
        fields = {"TireCompoundID": compound["TireCompoundID"], **dict(zip(MODEL_FIELDS, models, strict=True))}
        return TireSelection(fields, name, compound["DisplayName"],
                             sum(r[0] == 1 for r in candidates[family[1][preference]]),
                             self.curve_keys(compound))

    def curve_keys(self, compound):
        links = []
        for field in compound.keys():
            if not field.endswith("ID") and "ID_" not in field:
                continue
            if field.startswith("FrictionMultiCurve"):
                table, key = "List_TireFrictionMultiCurve", "FrictionMultiCurveID"
            elif field.startswith("AffectCurve"):
                table, key = "List_TireAffectCurve", "AffectCurveID"
            else:
                continue
            value = compound[field]
            if value is None or value < 0:
                raise UnknownTire(f"Unresolved physical curve {field} in {compound['DisplayName']}")
            if value == 0 and table == "List_TireAffectCurve":
                continue  # Native sentinel for optional modulation.
            link = self.db.execute(f"SELECT * FROM {table} WHERE {key}=?", (value,)).fetchone()
            if link is None:
                raise UnknownTire(f"Missing physical curve {table}/{value}")
            links.append((table, value))
            if table == "List_TireFrictionMultiCurve":
                for index in (0, 1):
                    leaf = link[f"TireFrictionCurveID{index}"]
                    if self.db.execute("SELECT 1 FROM List_TireFrictionCurve WHERE FrictionCurveID=?",
                                       (leaf,)).fetchone() is None:
                        raise UnknownTire(f"Missing load-dependent tire friction curve {leaf}")
                    links.append(("List_TireFrictionCurve", leaf))
        return tuple(dict.fromkeys(links))


class TireAssets:
    """Resolve and validate effective left/right models in the supplied native install."""

    def __init__(self, manifest, materials):
        self.mounts = {row["path"].casefold(): row for row in (entry.attrib for entry in ET.fromstring(manifest))}
        self.files = {p.name.casefold(): p for p in (materials.parent / "scene" / "tires").glob("*.zip")}
        self.cache = {}

    def resolve(self, token):
        token = token.casefold()
        if token in self.cache:
            return self.cache[token]
        if not re.fullmatch(r"[a-z0-9_]+", token):
            raise UnknownTire(f"Invalid target tire model token {token!r}")
        found = {}
        for prefix in ("tire_", "tirer_"):
            filename = prefix + token + ".zip"
            mount = self.mounts.get(MOUNT_PREFIX + filename)
            path = self.files.get(filename)
            if path is None or mount is None:
                if path is not None or mount is not None:
                    raise UnknownTire(f"Physical tire resource and mount disagree for {filename}")
                continue
            blob = path.read_bytes()
            directory = fh4_archive.verify(blob)
            if tuple(int(mount[k]) for k in ("dirstart", "dirsize", "direntries")) != (
                    directory.start, directory.size, directory.entries):
                raise UnknownTire(f"Wrong tire resource mount extent for {filename}")
            with ZipFile(BytesIO(blob)) as archive:
                for name in archive.namelist():
                    for side in ("l", "r"):
                        if name.casefold() == f"tire{side}_{token}.modelbin":
                            with archive.open(name) as member:
                                if member.read(4) != b"burG":
                                    raise UnknownTire(f"Shared tire member {name} is missing its native Modelbin header")
                            found.setdefault(side, []).append({"mount": mount["path"], "member": name,
                                                               "archive_sha256": sha256(blob).hexdigest()})
        if "l" not in found:
            raise UnknownTire(f"Shared tire model {token} has no left base Modelbin")
        result = {"left": found["l"], "right": found.get("r", []),
                  "has_distinct_right_resource": "r" in found}
        self.cache[token] = result
        return result
