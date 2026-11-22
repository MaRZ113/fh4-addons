"""Compute source-owned wheel rest, display and unloaded contact coordinates together."""

from .fh4_model import Bone
from .fh4_tires import NativeTireCompiler, dimensions
from .fm4 import rows_for


class WheelTransformCompiler:
    def compile(self, rows, authored):
        car, = rows_for(rows,"Data_Car")
        body, = rows_for(rows,"Data_CarBody")
        sizes = dimensions(car)
        bones = [Bone("<root>",-1),Bone("root",0)]
        corners = {}
        for axle, z_direction in (("Front",1),("Rear",-1)):
            size = NativeTireCompiler.stock_size(sizes,axle)
            radius = size["radius_m"]
            width = size["width_mm"]*.001
            track = body[f"Model{axle}TrackOuter"]-width
            z = -body["BottomCenterWheelbasePosZ"]+z_direction*body["ModelWheelbase"]*.5
            y = radius-body[f"Model{axle}StockRideHeight"]
            if min(radius,track)<=0:
                raise ValueError(f"Invalid {axle} wheel dimensions")
            for side, direction in (("L",-1),("R",1)):
                corner = side+axle[0]
                position = authored.get(corner,(direction*track*.5,y,z))
                if position[0]*direction<=0:
                    raise ValueError(f"Wheel {corner} is on the wrong side of the chassis")
                index = len(bones)
                bones.extend((Bone("hub"+corner,1,position),Bone("spindle"+corner,index)))
                contact = (position[0],position[1]-radius,position[2])
                corners[corner] = {"hub":index,"spindle":index+1,"origin_m": position,"radius_m":radius,
                                   "width_m":width,"unloaded_contact_m":contact,
                                   "model_placement_m":position,"spindle_parent_offset_m":(0.,0.,0.),
                                   "provenance":"FM4 CarPartPositions" if corner in authored else "FM4 axle dimensions"}
        return bones,corners
