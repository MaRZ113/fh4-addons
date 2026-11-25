"""Extract a native analog-gauge animation graph (GSF) that converted cars reuse byte for byte.

Every native FH4 car graph has the same frame: CharacterRoot holds `blend graph` plus the parameter
nodes GameInputs (AnimationLOD), dynamics and gauges; `blend graph` holds the LODSelect state machine
whose states InRace_NonFocus / InRace_Focus / Autovista are chosen by scalar compares on AnimationLOD,
and the gauges subtree sits under InRace_Focus and Autovista. A minimal graph with the gauges directly
under the root never moved its needles in game, so the converter now ships a native graph and only
rewrites its numbers.

The donor (LOT_2Eleven_09) animates exactly the tachometer and speedometer: Linear Eqn. nodes map the
`tach` (thousand RPM) and `speed` (mph) parameters to unit-relative clip time with time = AValue*input.
A converted car bakes its dial's non-linear calibration into its own clip and sets AValue = 1/max and
the gauges parameter MaxValue = max. Writes expanded_garage/fh4-gauge-graph-v1.json with the donor GSF
and the file offsets of those floats.

    python -m expanded_garage.research.extract_gauge_graph <FH4 media/cars> [--donor LOT_2Eleven_09]
"""

import argparse
import base64
import json
from pathlib import Path
from zipfile import ZipFile

from .granny_reader import G

# Blend Graph input edge name -> gauges parameter name
EDGE_PARAMETERS = {"tachometer": "tach", "speed": "speed"}


def role(g, ref):
    obj = g.deref(ref)
    kind = next(k for k in obj if k.startswith("ContextUID_"))[len("ContextUID_"):]
    return obj, kind, g.deref(obj["nodeToken"])


def children(g, obj):
    container = g.deref(obj.get("containerToken")) if "containerToken" in obj else None
    return [g.deref(c)["Variant"] for c in container["ChildTokens"]] if container else []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cars", type=Path)
    parser.add_argument("--donor", default="LOT_2Eleven_09")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "fh4-gauge-graph-v1.json")
    args = parser.parse_args()
    archive = ZipFile(args.cars / f"{args.donor}.zip")
    member = next(n for n in archive.namelist() if n.lower().endswith(".gsf"))
    data = archive.read(member)
    g = G(data)
    root = g.read(g.root_obj, g.root_type)
    equations, maxima = {}, {}

    def walk(ref, parent=None):
        obj, kind, node = role(g, ref)
        if kind == "linear_equation":
            edge, = (g.deref(e) for e in node["InputEdges"])
            container = g.deref(parent[0]["nodeToken"])
            name = g.deref(container["InputEdges"][edge["OtherNodeEdge"]])["EdgeName"]
            token = obj["linear_equationToken"]
            equations.setdefault(EDGE_PARAMETERS[name], []).append(
                g.field_offset(token[1], token[2], "AValue"))
            if g.deref(token)["BValue"] != 0:
                raise SystemExit("Donor gauge equation has an offset")
        if kind == "parameters" and node["Name"] == "gauges":
            extra = g.deref(obj["parametersToken"])
            for edge, parameter in zip(node["OutputEdges"], extra["Parameters"], strict=True):
                maxima[g.deref(edge)["EdgeName"]] = g.field_offset(parameter[1], parameter[2], "MaxValue")
        for child in children(g, obj):
            walk(child, (obj, kind))

    walk(root["StateMachine"])
    slots = [g.deref(s)["Name"] for s in root["AnimationSlots"]]
    sources = [g.deref(s)["SourceFilename"] for s in g.deref(root["AnimationSets"][0])["SourceFileReferences"]]
    if set(equations) != set(maxima) or any(len(v) != 2 for v in equations.values()):
        raise SystemExit(f"Unexpected donor gauge layout: {equations} {maxima}")
    record = {"format": "fh4-gauge-graph-v1", "donor": {"car": args.donor, "member": member},
              "gsf": base64.b64encode(data).decode("ascii"), "slots": slots, "sources": sources,
              "parameters": {p: {"max_value": maxima[p], "a_value": equations[p]} for p in sorted(maxima)}}
    args.out.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    print(f"{args.out}: {args.donor} slots={slots} parameters={record['parameters']}")


if __name__ == "__main__":
    main()
