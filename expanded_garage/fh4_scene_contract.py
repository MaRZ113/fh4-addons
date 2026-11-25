"""Shared FM4 upgrade identification and FH4 v5 scene membership."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Family:
    table: str
    part_type: int
    prefixes: tuple
    first_label: int
    race_label: int | None = None


FAMILIES = (
    Family("List_UpgradeCarBodyFrontBumper", 34, ("bumperf",), 1, 27),
    Family("List_UpgradeCarBodyRearBumper", 35, ("bumperr",), 28),
    Family("List_UpgradeCarBodyHood", 36, ("hood",), 108),
    Family("List_UpgradeCarBodySideSkirt", 37, ("skirtl", "skirtr"), 82),
    Family("List_UpgradeRearWing", 9, ("wing",), 55, 81),
)
VIEW_ONLY = {"speed_min", "speed_max", "speed_mid", "tach_min", "tach_max", "tach_mid",
             "fuel_min", "fuel_max", "fuel_mid", "boost_min", "boost_max", "boost_mid", "cagerace"}


@dataclass(frozen=True)
class Part:
    family: Family
    source_key: int
    native_key: int
    level: int
    stock: bool
    components: tuple
    label: int


class NativeCarSceneContract:
    def __init__(self, ir, body_id):
        self.parts = []
        self.unresolved = []
        self.reserved_components = set()
        self.selected = set()
        models = ir["sections"]["geometry"]["data"]["models"]
        exterior = {c["name"].casefold() for m in models if m["role"].startswith("exterior")
                    for c in m["components"] if any(0 <= d["lod"] <= 5 for d in c["draws"])}
        tables = ir["sections"]["physics"]["data"]["tables"]
        for family in FAMILIES:
            candidates = {prefix + suffix for prefix in family.prefixes
                          for suffix in (*"abcdefghijklmnopqrstuvwxyz", "race")}
            self.reserved_components.update(candidates)
            for entry in tables.get(family.table, []):
                row = entry["record"]
                field = next((v for k, v in row.items() if k.casefold() == "partsstringid"), None)
                if not str(field).isdecimal():
                    self.unresolved.append({"table": family.table, "id": row["Id"], "reason": "invalid FM4 part label"})
                    continue
                label = int(field)
                delta = label - family.first_label
                if family.race_label == label:
                    suffix = "race"
                elif 0 <= delta < 26:
                    suffix = chr(ord("a") + delta)
                else:
                    suffix = None
                components = tuple(prefix + suffix for prefix in family.prefixes
                                   if suffix is not None and prefix + suffix in exterior)
                if not components and not row["IsStock"]:
                    self.unresolved.append({"table": family.table, "id": row["Id"], "label": label,
                                            "reason": "no matching source mesh for this variant"})
                    continue
                # A stock row without a mesh is the bare body (NIS_Leaf_11: no stock wing, race wing optional):
                # it stays the group's stock part with no models, so the upgrade group keeps one stock entry.
                prior = self.selected.intersection(components)
                if prior:
                    raise ValueError(f"Ambiguous FM4 upgrade geometry ownership: {sorted(prior)}")
                self.selected.update(components)
                self.parts.append(Part(family, row["Id"], body_id + row["Id"] % 1000,
                                       row["Level"], bool(row["IsStock"]), components, label))
        # Mesh-less stock parts only where the family has a modelled variant to switch to.
        modelled = {part.family for part in self.parts if part.components}
        for part in [p for p in self.parts if not p.components and p.family not in modelled]:
            self.parts.remove(part)
            self.unresolved.append({"table": part.family.table, "id": part.source_key, "label": part.label,
                                    "reason": "no matching source mesh for this variant"})
        for name in sorted(exterior & (self.reserved_components - self.selected)):
            self.unresolved.append({"component": name, "reason": "no mapped FM4 catalogue variant"})

    def use_component(self, role, name):
        name = name.casefold()
        if name in VIEW_ONLY:
            return False
        if role.startswith("exterior"):
            if name in self.reserved_components:
                return name in self.selected
            if name in {"wheel", "exhaustb", "exhaustlb", "exhaustrb"}:
                return False
        if role == "cockpit" and name in self.reserved_components and name not in self.selected:
            return False
        return True

    def groups(self, models):
        groups = {}
        for part in self.parts:
            owned = [m for m in models if m.kind in ("exterior", "cockpit") and m.component in part.components]
            if sum(m.kind == "exterior" for m in owned) != len(part.components):
                raise ValueError(f"Unbuilt source variants {part.components}")
            groups.setdefault(part.family.part_type, []).append((part, owned))
        for kind, entries in groups.items():
            if sum(part.stock for part, _ in entries) != 1:
                raise ValueError(f"FH4 scene group {kind} lacks one stock part")
        return groups

    def receipt(self):
        return {
            "version": 5,
            "upgrade_format": "v3 shared references",
            "variant_bindings": [
                {"table": p.family.table, "part_type": p.family.part_type, "source_key": p.source_key,
                 "native_key": p.native_key, "source_parts_string_id": p.label, "level": p.level,
                 "stock": p.stock, "components": p.components} for p in self.parts
            ],
            "retained_in_ir": self.unresolved,
            "variant_evidence": "FM4 catalogue part labels reconciled against source component names",
            "runtime_status": "unverified",
        }
