"""Compare FH4 Carbin closure and inspect emitted Modelbin bounds and UV transforms."""

import argparse
from pathlib import Path
import struct
from zipfile import ZipFile

import numpy as np

from expanded_garage import fh4, fh4_model
from expanded_garage.vehicle_ir import sha256, write_json


AUXILIARY = ('aovolumetexture.bin', 'ikanchorbones.xml', 'lights.bin', 'lightpresets.bin',
             'manufacturercolors.bin', 'scene/proxylod.modelbin')


def scene_archive(path, reader):
    with ZipFile(path) as archive:
        names = {name.casefold(): name for name in archive.namelist()}
        carbin, = [name for name in names if name.endswith('.carbin')]
        scene = reader(archive.read(names[carbin])).scene()
        models = [model for part in scene['fixed'] for model in part['models']]
        models += [model for group in scene['groups'] for part in group['upgrades'] for model in part['models']]
        models += [binding['model'] for group in scene['groups'] for binding in group['shared']]
        model_paths = {model['path'].casefold() for model in models}
        auxiliary = {}
        for resource in AUXILIARY:
            if resource in names:
                auxiliary[resource] = {'bytes': archive.getinfo(names[resource]).file_size,
                                       'explicit_model_path': any(p.endswith(resource.replace('/', '\\')) for p in model_paths)}
            else:
                auxiliary[resource] = None
        prefix = fh4.virtual(scene['media_name'], '')
        references = model_paths | {scene['skeleton'].casefold()}
        missing = sorted(p for p in references if p.startswith(prefix)
                         and p[len(prefix):].replace('\\', '/') not in names)
        return {'archive': path.name, 'sha256': sha256(path), 'entries': len(names), 'carbin': carbin,
                'media_name': scene['media_name'], 'ordinal': scene['ordinal'], 'skeleton': scene['skeleton'],
                'fixed_part_types': [part['type'] for part in scene['fixed']],
                'upgrade_part_types': [group['type'] for group in scene['groups']],
                'carbin_ao_references': len(scene['ao_references']), 'missing_local_model_paths': missing,
                'external_model_paths': sorted(p for p in references if not p.startswith(prefix)),
                'auxiliary': auxiliary, 'model_versions': scene['model_versions']}


def mesh_metadata(data):
    count = fh4_model.HEADER.unpack_from(data)[-1]
    for i in range(count):
        tag, _, _, fields, metadata, offset, size, _ = fh4_model.BLOB.unpack_from(
            data, fh4_model.HEADER.size + i * fh4_model.BLOB.size)
        if tag not in (fh4_model.tag('Mesh'), fh4_model.tag('Modl')):
            continue
        box = None
        for field in range(fields):
            start = metadata + field * fh4_model.METADATA.size
            name, flags, delta = fh4_model.METADATA.unpack_from(data, start)
            if name == fh4_model.tag('BBox') and flags >> 4 == 24:
                box = np.asarray(struct.unpack_from('<6f', data, start + delta)).reshape(2, 3)
        yield tag, offset, size, box


def audit_geometry(path, contract, geometry, library):
    totals = {'render_meshes': 0, 'shadow_meshes': 0, 'model_bounds_checked': 0,
              'materials': set(), 'bounds': [np.full(3, np.inf), np.full(3, -np.inf)],
              'uv_endpoint_bounds': [np.full(2, np.inf), np.full(2, -np.inf)]}
    with ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.casefold().endswith('.modelbin'):
                continue
            data = archive.read(name)
            for chunk in geometry.blobs(data):
                if chunk['tag'] == 'MatI':
                    totals['materials'].add(fh4.verify_material(data, chunk, geometry))
            for tag, offset, size, box in mesh_metadata(data):
                if box is None or not np.isfinite(box).all() or np.any(box[0] > box[1]):
                    raise ValueError(f'{path.name}:{name} has an invalid BBox')
                if tag == fh4_model.tag('Modl'):
                    totals['model_bounds_checked'] += 1
                    if not name.endswith('_skeleton.modelbin'):
                        totals['bounds'][0] = np.minimum(totals['bounds'][0], box[0])
                        totals['bounds'][1] = np.maximum(totals['bounds'][1], box[1])
                    continue
                uv, span, bias = np.split(np.asarray(struct.unpack_from('<28f', data, offset + size - 112)), (20, 24))
                if any(not np.isfinite(values).all() for values in (uv, span, bias)) or np.any(span[:3] <= 0):
                    raise ValueError(f'{path.name}:{name} has an invalid position or UV transform')
                slots, = struct.unpack_from('<i', data, offset + 59)
                if slots == 1:
                    totals['shadow_meshes'] += 1
                    continue
                uv = uv.reshape(5, 4)
                totals['render_meshes'] += 1
                endpoints = np.stack((uv[:2, ::2], uv[:2, ::2] + uv[:2, 1::2]))
                totals['uv_endpoint_bounds'][0] = np.minimum(totals['uv_endpoint_bounds'][0], endpoints.min(axis=(0, 1)))
                totals['uv_endpoint_bounds'][1] = np.maximum(totals['uv_endpoint_bounds'][1], endpoints.max(axis=(0, 1)))
    with ZipFile(library) as shared:
        available = {name.casefold() for name in shared.namelist()}
    totals['unresolved_shared_materials'] = sorted(totals['materials'] - available)
    totals['materials'] = sorted(totals['materials'])
    for key in ('bounds', 'uv_endpoint_bounds'):
        totals[key] = [entry.tolist() for entry in totals[key]]
    contract['generated_geometry'] = totals


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control', type=Path, action='append', required=True)
    parser.add_argument('--generated', type=Path, action='append', required=True)
    parser.add_argument('--materials', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    reader, geometry = fh4.previous_readers()
    controls = [scene_archive(path, reader) for path in args.control]
    generated = [scene_archive(path, reader) for path in args.generated]
    for path, contract in zip(args.generated, generated, strict=True):
        audit_geometry(path, contract, geometry, args.materials)
    write_json(args.out, {'native_controls': controls, 'generated': generated,
                         'scope': 'Archive and binary readback. Runtime reads, requiredness and shader execution are unobserved.'})
