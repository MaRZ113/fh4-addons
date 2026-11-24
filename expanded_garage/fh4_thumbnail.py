"""Build the two mounted FH4 BC7 thumbnail archives for an owned car."""

from hashlib import sha256
from io import BytesIO
import struct
from zipfile import ZipFile

import numpy as np
from PIL import Image, ImageChops, ImageOps

from . import fh4_archive, fh4_model


FLAVORS = (("Big",(816,459)),("Small",(224,126)))
TXCH = struct.Struct("<II16sIIIHBB4I2I3I3I")
UI_TEXTURE = bytes.fromhex("00000000000000000000000000000001")
WEIGHTS = np.array((0,4,9,13,17,21,26,30,34,38,43,47,51,55,60,64))


def thumbnail_catalogue_path(car_id):
    return f"game:\\Media\\Stripped\\RC{car_id}\\Thumbnail_{car_id}.png"


def thumbnail_archives(car_id):
    return (f"media/stripped/RC{car_id}.zip",f"media/stripped/RC{car_id}HiRes.zip")


def bc7_pixels(im):
    """Encode one BC7 mode-6 block per 4×4 tile using RGBA endpoint projection."""
    width,height = im.size
    rgba = np.asarray(im,dtype=np.int32)
    blocks = np.pad(rgba,((0,-height%4),(0,-width%4),(0,0)),mode="edge")
    blocks = blocks.reshape((height+3)//4,4,(width+3)//4,4,4).transpose(0,2,1,3,4).reshape(-1,16,4)
    rows = np.arange(len(blocks))
    energy = blocks @ np.array([2,4,1,5])
    ends = np.stack((blocks[rows,energy.argmin(axis=1)],blocks[rows,energy.argmax(axis=1)]),axis=1)
    pbits = (np.sum(ends&1,axis=2)>=2).astype(np.int32)
    ends = np.clip((ends-pbits[:,:,None]+1)//2,0,127)
    decoded = ends*2+pbits[:,:,None]
    delta = decoded[:,1]-decoded[:,0]
    blend = np.clip(64*np.einsum("bpc,bc->bp",blocks-decoded[:,:1],delta)/
                    np.maximum(1,np.sum(delta*delta,axis=1,keepdims=True)),0,64)
    indices = np.searchsorted((WEIGHTS[:-1]+WEIGHTS[1:])/2,blend)
    flip = indices[:,0]>=8
    indices[flip] = 15-indices[flip]
    ends[flip] = ends[flip,::-1]
    pbits[flip] = pbits[flip,::-1]
    out = bytearray()
    for channels,bits,selectors in zip(ends.transpose(0,2,1).reshape(-1,8),pbits,indices,strict=True):
        low = 64 | sum(int(value)<<(7+7*i) for i,value in enumerate(channels)) | int(bits[0])<<63
        # The anchor uses three bits. All sixteen selectors after it shift once.
        high = int(bits[1]) | (int(selectors[0])<<1) | sum(
            int(value)<<(4*i) for i,value in enumerate(selectors[1:],1))
        out.extend(struct.pack("<QQ",low,high))
    return bytes(out)


# Native framing in the dealership tiles (owner, icons.png: Sunbeam Lotus, Racemo, Gurkha beside the SX4): the
# car spans ~72-74 % of the frame width and ~46-50 % of its height, wheels resting at ~90 % of the height. The
# visual-50 frame (88 % / 80 %) let FM4's taller three-quarter views fill the tile up into the title.
FRAME_WIDTH, FRAME_HEIGHT, FRAME_BOTTOM = 0.74, 0.50, 0.90


def main_view(source):
    """The largest car view of an FM4 thumbnail. FM4 thumbnails (768x288) hold a three-quarter view and a small
    side profile at the right edge; shrunk whole into FH4's frame, the car sat small and off-centre with the
    profile beside it in the dealership (owner, visual-49). Views are split at fully transparent columns."""
    alpha = np.asarray(source.getchannel("A")) > 8
    columns = alpha.any(axis=0)
    runs, start = [], None
    for x, filled in enumerate(list(columns) + [False]):
        if filled and start is None:
            start = x
        elif not filled and start is not None:
            runs.append((int(alpha[:, start:x].sum()), start, x))
            start = None
    if not runs:
        return source
    _, left, right = max(runs)
    rows = np.nonzero(alpha[:, left:right].any(axis=1))[0]
    return source.crop((left, int(rows[0]), right, int(rows[-1]) + 1))


def thumbnail_swatch(source, size):
    view = main_view(source)
    scale = min(size[0] * FRAME_WIDTH / view.width, size[1] * FRAME_HEIGHT / view.height)
    scaled = view.resize((max(1, round(view.width * scale)), max(1, round(view.height * scale))),
                         Image.Resampling.LANCZOS)
    target = Image.new("RGBA",size)
    target.paste(scaled,((size[0]-scaled.width)//2, round(size[1] * FRAME_BOTTOM) - scaled.height))
    red,green,blue,alpha = target.split()
    target = Image.merge("RGBA",tuple(ImageChops.multiply(channel,alpha) for channel in (red,green,blue))+(alpha,))
    payload = bc7_pixels(target)
    header = TXCH.pack(56,80,UI_TEXTURE,*size,1,1,1,2,0,0,0,0,64,68,9,76,0xffffffff,
                       len(payload),0,0xffffffff)
    return fh4_model.bundle([fh4_model.Chunk("TXCB",(0,0),payload,(fh4_model.metadata("TXCH",header),))])


def audit_thumbnail(data, size):
    width,height = size
    length = ((width+3)//4)*((height+3)//4)*16
    if len(data)!=140+length:
        raise ValueError("Incorrect native BC7 thumbnail size")
    if (fh4_model.HEADER.unpack_from(data)!=(fh4_model.tag("Grub"),1,1,0,140,len(data),1)
            or fh4_model.BLOB.unpack_from(data,20)!=(fh4_model.tag("TXCB"),0,0,1,44,140,length,length)
            or fh4_model.METADATA.unpack_from(data,44)!=(fh4_model.tag("TXCH"),TXCH.size<<4,8)
            or TXCH.unpack_from(data,52)!=(56,80,UI_TEXTURE,width,height,1,1,1,2,0,0,0,0,64,68,9,
                                          76,0xffffffff,length,0,0xffffffff)):
        raise ValueError("Incorrect native thumbnail texture contract")


def audit_archive(blob,car_id,hires):
    directory = fh4_archive.verify(blob)
    factor = 2 if hires else 1
    variants = {f"Thumbnail_{car_id}_{name}.swatchbin":(width*factor,height*factor)
                for name,(width,height) in FLAVORS}
    with ZipFile(BytesIO(blob)) as archive:
        if set(archive.namelist()) != variants.keys():
            raise ValueError("Native BC7 archive needs the owned Big and Small swatches")
        for name,size in variants.items():
            audit_thumbnail(archive.read(name),size)
    return vars(directory)


def write_thumbnails(root,car_id,source_asset,width,height,rgba):
    im = Image.frombytes("RGBA",(width,height),bytes(rgba))
    records = []
    for hires,path in enumerate(thumbnail_archives(car_id)):
        factor = 2 if hires else 1
        members = {f"Thumbnail_{car_id}_{name}.swatchbin":thumbnail_swatch(im,(w*factor,h*factor))
                   for name,(w,h) in FLAVORS}
        payload,_ = fh4_archive.pack(members)
        directory = audit_archive(payload,car_id,hires)
        target = root/path
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(payload)
        records.append({"path":path,"sha256":sha256(payload).hexdigest(),"source":source_asset,
                        "kind":"thumbnail_archive","car_id":car_id,"hires":bool(hires),"directory":directory})
    return records
