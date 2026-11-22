"""Native first-selection catalogue and mounted thumbnail regression tests."""

from contextlib import closing
from io import BytesIO
from pathlib import Path
import sqlite3
import struct
from tempfile import TemporaryDirectory
import unittest

from PIL import Image

from .fh4_data_car import NativeDataCarContract, wheel_load_contract
from .fh4_db import snapshot
from .fh4_db_compiler import Source, Vehicle, compile_database
from .fh4_thumbnail import audit_thumbnail, thumbnail_catalogue_path, thumbnail_swatch
from .research.inspect_megatexture_faults import grid_index, grid_rectangle
from .research.megatexture_producer_contract import I32_MIN, aligned_ring_origin, cvttss2si, world_axis
from .test_native_runtime import BASELINE, CONTROLS, FIXTURES, STOCK
from .vehicle_ir import load


class LoadContractTests(unittest.TestCase):
    def test_megatexture_producer_distinguishes_nan_from_exact_int_min(self):
        self.assertEqual(cvttss2si(float("nan")), (I32_MIN, True))
        self.assertEqual(cvttss2si(float("inf")), (I32_MIN, True))
        self.assertEqual(cvttss2si(float(I32_MIN)), (I32_MIN, False))
        self.assertEqual(cvttss2si(float(1 << 31)), (I32_MIN, True))
        self.assertEqual(world_axis(-2753.763916015625), (-704275, False))
        self.assertEqual(world_axis(float("nan")), (I32_MIN, True))
        self.assertEqual(aligned_ring_origin(I32_MIN, 4, 2048), 2147482624)
        self.assertEqual(aligned_ring_origin(-705204, 4, 2048), -706228)

    def test_megatexture_fault_rectangles_reproduce_native_indexing(self):
        recorded_rectangles = (
            ((-2147483648, 2147379968, -2147419648, 2147380224), (-16401, 16399, -16400, 16399), 771927),
            ((-2147483648, -2147483648, -2147389696, -2147483136), (-16401, -16401, -16400, -16401), -802473),
        )
        for encoded, expected, slot in recorded_rectangles:
            self.assertEqual(grid_rectangle(encoded), expected)
            self.assertEqual(grid_index(expected[1], expected[0]), slot)
        self.assertEqual(grid_rectangle((-130945, -130944, -1, 0)), (-2, -1, -1, 0))
        self.assertEqual(grid_index(-24, -24), 0)
        self.assertEqual(grid_index(23, 23), 2303)

    def test_null_native_fields_and_wheel_mass_sentinel_are_rejected(self):
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("CREATE TABLE Data_Car (Id INTEGER PRIMARY KEY, RequiredField REAL, OptionalField REAL)")
            db.executemany("INSERT INTO Data_Car VALUES (?,?,?)",((1,1,None),(2,2,0)))
            contract = NativeDataCarContract(db)
            self.assertEqual(contract.required,("Id","RequiredField"))
            with self.assertRaisesRegex(ValueError,"RequiredField"):
                contract.audit({"Id":12000,"RequiredField":None})
        for value in (None,-1,5,0x7fffffff):
            with self.assertRaisesRegex(ValueError,"MassLevel"):
                wheel_load_contract(41.5,value)
        self.assertEqual(wheel_load_contract(41.5,0)["relative_mass_factor"],1.0)

    def test_native_swatch_decodes_as_bc7_with_alpha(self):
        width,height = (8,12)
        source = Image.new("RGBA",(8,8),(34,76,54,255))
        native = thumbnail_swatch(source,(width,height))
        audit_thumbnail(native,(width,height))
        dds = (b"DDS "+struct.pack("<7I",124,0x81007,height,width,len(native)-140,0,1)+bytes(44)
               +struct.pack("<II4s10I",32,4,b"DX10",0,0,0,0,0,4096,0,0,0,0)
               +struct.pack("<5I",98,3,0,1,0)+native[140:])
        with Image.open(BytesIO(dds)) as decoded:
            self.assertEqual(decoded.size,(width,height))
            self.assertEqual(decoded.getpixel((4,5)),(34,76,54,254))
            self.assertEqual(decoded.getpixel((0,0)),(0,0,0,0))

    @unittest.skipUnless(BASELINE.is_file() and all((CONTROLS/c/"ir/vehicle.json").is_file() for c,*_ in FIXTURES),
                         "Requires the local FH4 baseline and preserved FM4 IRs")
    def test_three_real_cars_have_no_stock_mandatory_data_car_nulls(self):
        vehicles = []
        for name,car_id,*_ in FIXTURES:
            root = CONTROLS/name/"ir"
            vehicles.append(Vehicle(root,load(root),{"car_id":car_id,"body_id":car_id*1000,"media_name":"EG_"+name}))
        with TemporaryDirectory() as work, closing(snapshot(BASELINE)) as base:
            path = Path(work)
            compile_database(BASELINE,vehicles,path,strings=STOCK/"stripped/stringtables/EN.zip")
            contract = NativeDataCarContract(base)
            with closing(snapshot(path/"gamedb.sqlite")) as stage:
                for vehicle in vehicles:
                    car_id = vehicle.identity["car_id"]
                    car = stage.execute("SELECT * FROM Data_Car WHERE Id=?",(car_id,)).fetchone()
                    contract.audit(car)
                    wheel = stage.execute("SELECT Mass,MassLevel FROM List_Wheels WHERE Id=?",
                                          (car["StockWheelID"],)).fetchone()
                    original = Source(vehicle.root,vehicle.ir).record("List_Wheels")
                    self.assertEqual(wheel_load_contract(*wheel),wheel_load_contract(original["Mass"],0))
                    self.assertEqual(car["Thumbnail"],thumbnail_catalogue_path(car_id))
