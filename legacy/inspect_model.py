# Copyright (C) 2026 MangasMango
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Headless model inspector (see .ai/packages-spec.md Â§4): rig verdict,
# meshes/actions/textures + optional one-frame preview render.
#
#   blender --background --python inspect_model.py -- --input <file> \
#           [--json <out>] [--preview <dir>]
#
# Exit code 0 = rig maps; 1 = no rig / unmappable (report still written).

import argparse
import json
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rig_map
from model_io import (apply_textures, eval_world_bbox, find_texture_images,
                      import_model, reset_scene)
from generate import (get_actions, place_camera, render_to, setup_render,
                      setup_stage)


def parse_args():
    argv = sys.argv
    argv = argv[argv.index('--') + 1:] if '--' in argv else []
    p = argparse.ArgumentParser()
    p.add_argument('--input', required=True)
    p.add_argument('--json', default='')
    p.add_argument('--preview', default='')
    p.add_argument('--pitch', type=float, default=47.4)
    p.add_argument('--yaw', type=float, default=0.0)
    return p.parse_args(argv)


def inspect_file(input_path, json_out='', preview_dir='', pitch=47.4,
                 yaw=0.0):
    reset_scene()
    report = {'input': input_path, 'ok': False, 'error': None}
    try:
        import_model(input_path)
    except Exception as e:
        report['error'] = 'import failed: %s' % e
        _emit(report, json_out)
        return report

    sc = bpy.context.scene
    meshes = [ob for ob in sc.objects if ob.type == 'MESH']
    armatures = [ob for ob in sc.objects if ob.type == 'ARMATURE']
    arm = armatures[0] if armatures else None

    if not meshes:
        report['error'] = 'no mesh objects'
        _emit(report, json_out)
        return report

    lo, hi = eval_world_bbox(meshes)
    dims = (hi - lo)
    report['meshes'] = [
        {'name': ob.name,
         'verts': len(ob.data.vertices),
         'world_dims': [round(v, 4) for v in (hi - lo)]}
        for ob in meshes]
    report['rest'] = {'dims': [round(v, 4) for v in dims],
                      'upright': dims.z >= max(dims.x, dims.y) * 0.9}

    if arm is None:
        report['error'] = ('no armature - model is not rigged '
                           '(procedural packages need a rig)')
        _emit(report, json_out)
        return report

    report['armature'] = {
        'name': arm.name,
        'bone_count': len(arm.data.bones),
        'bones': [b.name for b in arm.data.bones],
    }
    try:
        mapping = rig_map.resolve_mapping(arm, None)
        report['mapping'] = {
            'ok': True, 'error': None, 'canonical': mapping,
            'missing_required': [], 'missing_optional': [
                c for c in rig_map.OPTIONAL if c not in mapping]}
        report['ok'] = True
    except rig_map.RigMapError as e:
        report['mapping'] = {'ok': False, 'error': str(e),
                             'canonical': {}, 'missing_required': [],
                             'missing_optional': []}
        _emit(report, json_out)
        return report

    acts = get_actions(meshes + [arm])
    report['actions'] = [
        {'name': a.name, 'range': list(a.frame_range)} for a in acts]
    report['materials'] = sorted({m.name for ob in meshes
                                  for m in ob.data.materials or []})
    textures = find_texture_images(os.path.dirname(input_path))
    report['textures'] = textures
    report['textured_materials'] = apply_textures(textures)

    if preview_dir:
        _preview(sc, meshes, preview_dir,
                 os.path.splitext(os.path.basename(input_path))[0],
                 pitch, yaw)
        report['preview'] = os.path.join(
            preview_dir, '%s_preview.png'
            % os.path.splitext(os.path.basename(input_path))[0])

    _emit(report, json_out)
    return report


def _preview(sc, meshes, preview_dir, stem, pitch, yaw):
    os.makedirs(preview_dir, exist_ok=True)
    cam, _half = setup_stage(256, pitch, 0.15, yaw, 0.0, shadow=False,
                             shadow_scale=1.0, char_px=38.0)
    if cam is None:
        return
    setup_render(256)
    place_camera(cam, 270.0, pitch)   # S = front view
    render_to(sc, cam, os.path.join(preview_dir, '%s_preview.png' % stem))


def _emit(report, json_out):
    if json_out:
        os.makedirs(os.path.dirname(json_out) or '.', exist_ok=True)
        with open(json_out, 'w') as fh:
            json.dump(report, fh, indent=2)
    print('[inspect] ok=%s error=%s' % (report['ok'], report['error']))
    if report.get('mapping'):
        print('[inspect] mapping: %s' % report['mapping']['ok'])
    for m in report.get('meshes', []):
        print('[inspect] mesh %s dims=%s' % (m['name'], m['world_dims']))
    for a in report.get('actions', []):
        print('[inspect] action %s %s' % (a['name'], a['range']))


def main():
    args = parse_args()
    report = inspect_file(args.input, args.json, args.preview,
                          args.pitch, args.yaw)
    sys.exit(0 if report['ok'] else 1)


if __name__ == '__main__':
    main()
