"""Native-complete rows: no NULL where the native catalogue never has one.

FH4 reads catalogue columns into fixed binary records. An absent value is not a
neutral zero: the v1.4.5 aerodynamics fault showed a NULL arriving as
FLT_MAX-derived garbage. Every inserted row therefore receives a native value
for each column that is defined in every baseline row.
"""

from collections import Counter
from statistics import median


def ident(value):
    return '"' + value.replace('"', '""') + '"'


# Columns whose FM4 meaning does not carry over. On 197 cars present in both
# games the FH4 values are re-authored (correlation with FM4 below 0.13, and
# fewer than 5% equal), so the source value is replaced by the native median.
REAUTHORED = {
    "CameraOverrides": ("CamHoodHeightOffset", "CamHoodZOffset",
                        "CamCockpitOffsetX", "CamCockpitOffsetY", "CamCockpitOffsetZ"),
}

# Strategy per column when no source value exists. "mode" is the default.
STRATEGY = {
    ("CameraOverrides", "CamNoWheelCockpitOffsetX"): "median",
    ("CameraOverrides", "CamNoWheelCockpitOffsetY"): "median",
    ("CameraOverrides", "CamNoWheelCockpitOffsetZ"): "median",
    **{("CameraOverrides", c): "median" for c in REAUTHORED["CameraOverrides"]},
}


class NativeDefaults:
    def __init__(self, db):
        self.db = db
        self._stats = {}

    def stats(self, table):
        """Return {column: (never_null, mode, median)} over the baseline rows."""
        if table not in self._stats:
            columns = [c[1] for c in self.db.execute(f"PRAGMA table_info({ident(table)})")]
            rows = self.db.execute(f"SELECT * FROM {ident(table)}").fetchall()
            result = {}
            for i, column in enumerate(columns):
                values = [r[i] for r in rows]
                defined = [v for v in values if v is not None]
                never_null = bool(rows) and len(defined) == len(values)
                mode = Counter(defined).most_common(1)[0][0] if defined else None
                numeric = [v for v in defined if isinstance(v, (int, float))]
                mid = median(numeric) if numeric and len(numeric) == len(defined) else mode
                if isinstance(mid, float):
                    mid = round(mid, 6)
                result[column] = (never_null, mode, mid)
            self._stats[table] = result
        return self._stats[table]

    def required(self, table):
        return {c for c, (never_null, _, _) in self.stats(table).items() if never_null}

    def value(self, table, column):
        _, mode, mid = self.stats(table)[column]
        return mid if STRATEGY.get((table, column), "mode") == "median" else mode

    def complete(self, table, pk, values):
        """Fill required columns; return (values, {column: (reason, value)})."""
        filled = {}
        out = dict(values)
        for column in REAUTHORED.get(table, ()):
            if column in self.stats(table):
                native = self.value(table, column)
                if out.get(column) != native:
                    filled[column] = ("reauthored_native_median", native)
                    out[column] = native
        for column in sorted(self.required(table) - {pk}):
            if out.get(column) is None:
                native = self.value(table, column)
                filled[column] = ("native_" + STRATEGY.get((table, column), "mode"), native)
                out[column] = native
        return out, filled

    def violations(self, table, row):
        """Columns that are NULL in `row` although every baseline row defines them."""
        return sorted(c for c in self.required(table) if c in row.keys() and row[c] is None)
