"""Native completeness and road-car policies for the FH4 Data_Car record."""

from collections import Counter
from statistics import median

from .database_graph import ident
from .fh4_dynamics import finite_between


CAMERA = ("IsCameraCollidable","UseBoxCameraCollision")
TCS = ("AssistsTCSControlSpeed","AssistsTCSFullEffectFricDiff","AssistsTCSFullSteerSlipScaleRWD",
       "AssistsTCSFullSteerSlipScaleFWD","AssistsTCSFullSteerSlipScaleAWD")
WHEEL_MASS_LEVELS = (1.0,.9,.8,.7,.6)


def wheel_load_contract(mass, level):
    if not finite_between(mass,1,300) or type(level) is not int or not 0 <= level < len(WHEEL_MASS_LEVELS):
        raise ValueError("Invalid List_Wheels.Mass/MassLevel for native wheel selection")
    return {"Mass":mass,"MassLevel":level,"relative_mass_factor":WHEEL_MASS_LEVELS[level]}


class NativeDataCarContract:
    def __init__(self, db, *, imported=()):
        self.db = db
        columns = [row[1] for row in db.execute("PRAGMA table_info(Data_Car)")]
        condition = f' WHERE Id NOT IN ({",".join("?" for _ in imported)})' if imported else ""
        counts = db.execute("SELECT count(*),"+",".join(f"count({ident(c)})" for c in columns)
                            +" FROM Data_Car"+condition,tuple(imported)).fetchone()
        self.samples = counts[0]
        if not self.samples:
            raise ValueError("Native Data_Car contract requires a populated FH4 baseline")
        self.required = tuple(c for c,n in zip(columns,tuple(counts)[1:],strict=True) if n==self.samples)

    def audit(self, row):
        values = dict(row)
        missing = [field for field in self.required if values.get(field) is None]
        if missing:
            raise ValueError(f'Data_Car {values.get("Id")} is missing required native fields: {", ".join(missing)}')
        return {"baseline_rows":self.samples,"required_fields":list(self.required)}

    @staticmethod
    def mode(rows, fields, description, support=.65):
        counts = Counter(tuple(r[f] for f in fields) for r in rows)
        values,count = counts.most_common(1)[0]
        if count < 4 or count < support*len(rows) or None in values:
            raise ValueError(f"Insufficient native support for {description}")
        return dict(zip(fields,values,strict=True)), {"rule":description,"support":count,"samples":len(rows)}

    def road_fields(self, src, bucket, families):
        original = src.record("Data_Car")
        # Road cars (1) and race cars (3: FH4 ships 71, e.g. FER_FXX_05, PAG_ZondaR_10) are supported; the
        # catalogue cohort is the bucket's road cars plus those of the source's own type.
        if original["CarTypeID"] not in (1, 3) or bucket is None:
            raise ValueError("Source needs a supported road-car body and catalogue classification")
        catalogue = self.db.execute("""SELECT c.* FROM Data_Car c JOIN Data_Car_Buckets k ON k.CarId=c.Id
                                       WHERE c.CarTypeID IN (1, ?) AND c.ContentId<>99 AND k.CarBucket=? ORDER BY c.Id""",
                                    (original["CarTypeID"], bucket)).fetchall()
        components = {part["name"].casefold() for model in src.ir["sections"]["geometry"]["data"]["models"]
                      if model["role"].startswith("exterior") for part in model["components"]}
        flags = ("IsOpenCockpit","IsOpenWheel")
        # Closed body: windscreen plus a rear screen or both front side panes (mid-engine GUM_ApolloS_10 has an
        # engine cover where the rear screen would be).
        closed = {"body", "glassf"} <= components and ("glassr" in components or {"glasslf", "glassrf"} <= components)
        if not closed or not any(p.startswith("bumperf") for p in components):
            raise ValueError("Source body closure and wheel coverage need a native classification")
        body,body_evidence = self.mode(catalogue,flags,"Closed FM4 body/window groups and matching native catalogue class")
        if tuple(body.values()) != (0,0):
            raise ValueError("Native body cohort conflicts with the source closed road body")
        road = self.db.execute("SELECT * FROM Data_Car WHERE CarTypeID=1 AND IsDrivable=1 ORDER BY Id").fetchall()
        camera,camera_evidence = self.mode(road,CAMERA,"Native road-car camera collision flag pair",.95)
        assist,assist_evidence = self.mode([r for r in road if r["DriveTypeID"]==original["DriveTypeID"]],
                                          TCS,"Native full TCS tuple in the same drive class",.58)
        if len(catalogue)<5:
            raise ValueError("Insufficient native catalogue cohort for road rating and puddle profiles")
        pi = original["PerformanceIndex"]
        ride = (original["FrontStockRideHeight"]+original["RearStockRideHeight"])/2
        mass = original["CurbWeight"]

        def distance(row):
            height = (row["FrontStockRideHeight"]+row["RearStockRideHeight"])/2
            return ((row["PerformanceIndex"]-pi)/.15)**2+((height-ride)/.075)**2+((row["CurbWeight"]-mass)/6)**2

        peers = sorted(catalogue,key=lambda r:(distance(r),r["Id"]))[:9]
        ratings = {f:median(r[f] for r in peers) for f in
                   ("MaxUpgradeWizardPerfRating","MinOffRoadPerfRating","PuddleScale")}
        if not all(finite_between(v,0,2) for v in ratings.values()):
            raise ValueError("Target-only road handling or puddle projection is outside its observed range")
        # Use a native family selected within the target catalogue taxonomy.
        family_names = {v:k for k,v in families.items()}
        family_count = Counter(r["FamilyBodyID"] for r in peers if r["FamilyBodyID"] in family_names
                               and family_names[r["FamilyBodyID"]] != "none")
        family = family_count.most_common(1)[0][0] if family_count else families["none"]
        flags = {"DoNotAllowRemovalFromGarage":0,"NotAvailableInAuctionHouse":0}
        return {**body,**camera,**assist,**ratings,**flags,"FamilyBodyID":family}, {
            "body":body_evidence,"camera":camera_evidence,"tcs":assist_evidence,
            "rating_and_puddle_projection":{"rule":"Native catalogue-conditioned medians, nearest PI, ride height and mass",
                                            "cohort_car_ids":[r["Id"] for r in peers]},
            "family":{"target":family_names[family],"target_support":family_count[family],
                      "source_id_in_fm4_taxonomy":original["FamilyBodyID"]},
            "ownership_flags":"Ordinary removable catalogue vehicle; single-player trial registers no auction service",
        }
