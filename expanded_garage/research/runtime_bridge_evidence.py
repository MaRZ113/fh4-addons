"""Compare serialized wheel frames and stock tire mount coverage on a local FH4 build."""

from contextlib import closing
from pathlib import Path
from statistics import median
import argparse
import json
import math

from expanded_garage.fh4_db import snapshot
from expanded_garage.fh4_tires import MODEL_FIELDS, TireAssets
from expanded_garage.fh4_vehicle_systems import CORNER_IDS
from expanded_garage.vehicle_ir import write_json, sha256


def compare(db, prior):
    cars = []
    for src in prior["stock_vehicles"]:
        native = {r["name"]:r for r in src["gr2"]["bones"]}
        db_parts = {num:(x,y,z) for num,x,y,z in db.execute(
            "SELECT PartId,PartPosX,PartPosY,PartPosZ FROM CarPartPositions WHERE Ordinal=?",(src["id"],))}
        corners = {}
        for part,label in CORNER_IDS.items():
            model, = [m for m in src["carbin_wheel_placements"]
                      if m["bone_name"]=="spindle"+label and "wheel" in m["path"].casefold()]
            hub = native["hub"+label]["local_m"]
            cockpit = model["placement_m"]
            database = db_parts.get(part)
            corners[label] = {"carbin_model_m":cockpit,"granny_hub_local_m":hub,
                              "game_db_m":database,"model_hub_distance_m":math.dist(cockpit,hub),
                              "db_model_distance_m":math.dist(database,cockpit) if database else None,
                              "db_hub_distance_m":math.dist(database,hub) if database else None}
        cars.append({"archive":src["archive"],"car_id":src["id"],"archive_sha256":src["sha256"],"corners":corners})
    delta = [v["model_hub_distance_m"] for car in cars for v in car["corners"].values()]
    return {"samples":cars,"hub_to_model_range_m":[min(delta),max(delta)],
            "hub_to_model_median_m":median(delta),"runtime_composition":"not observed"}


def tires(db, assets):
    requested = ("a","b","c","WET_b","WET_c","drag")
    return {token: {"asset_readback":assets.resolve(token),"stock_counts": {
        field:db.execute(f'SELECT count(*) FROM List_UpgradeTireCompound WHERE IsStock=1 AND lower("{field}")=?',
                         (token.casefold(),)).fetchone()[0] for field in MODEL_FIELDS}} for token in requested}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--game-db",type=Path,required=True)
    p.add_argument("--materials",type=Path,required=True)
    p.add_argument("--manifest",type=Path,required=True)
    p.add_argument("--stock-rigs",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    args = p.parse_args()
    assets = TireAssets(args.manifest.read_bytes(),args.materials)
    prior = json.loads(args.stock_rigs.read_text(encoding="utf-8"))
    with closing(snapshot(args.game_db)) as db:
        result = {"target_db_sha256":sha256(args.game_db),"stock_rig_receipt_sha256":sha256(args.stock_rigs),
                  "wheel_frames":compare(db,prior),"tire_sides":tires(db,assets)}
    write_json(args.out,result)


if __name__ == "__main__":
    main()
