# Copyright (C) 2026 Stamatis Valis
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Merge animation-only FBX/GLB files onto a character rig and export one
# GLB with embedded clips. Works when the animation files use the same
# skeleton (bone names) as the character - e.g. Kenney asset packs.
#
# With -Clips fighter, the 7-clip bound-to-rot fighter set is generated
# procedurally on the (auto bone-mapped) rig instead - no anims needed.
#
# Usage:
#   blender --background --python merge_anims.py -- --character CHAR.fbx --anims DIR --out OUT.glb
#   blender --background --python merge_anims.py -- --character CHAR.fbx --clips fighter --out OUT.glb
# or: .\merge.ps1 <character-file> <anims-folder> <out.glb>
#     .\merge.ps1 -Character CHAR.fbx -Clips fighter -Out OUT.glb

import argparse
import os
import re
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import procedural_anims
import rig_map


def parse_args():
    argv = sys.argv
    argv = argv[argv.index('--') + 1:] if '--' in argv else []
    p = argparse.ArgumentParser()
    p.add_argument('--character', required=True)
    p.add_argument('--anims', default='',
                   help='folder of animation-only FBX/GLB files '
                        '(required unless --clips fighter)')
    p.add_argument('--out', required=True)
    p.add_argument('--clips', default='', choices=['', 'fighter'],
                   help='generate a clip set on the rig instead of '
                        'importing --anims')
    p.add_argument('--bone-map', default='',
                   help='explicit bone_map.json overriding auto bone mapping')
    return p.parse_args(argv)


def import_file(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.glb', '.gltf'):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == '.fbx':
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext == '.obj':
        bpy.ops.wm.obj_import(filepath=path)
    else:
        raise ValueError('unsupported format: ' + ext)


def scene_objects():
    return set(ob.name for ob in bpy.context.scene.objects)


def remove_objects(names):
    for name in names:
        ob = bpy.data.objects.get(name)
        if ob is not None:
            bpy.data.objects.remove(ob, do_unlink=True)


def grab_action(pre_import_names, new_object_names, stem):
    """Among the actions created by this import, keep the one with the
    largest frame span (FBX imports create stub actions next to the real
    animation), rename it to the animation file's stem, delete the rest."""
    new_actions = [a for a in bpy.data.actions if a.name not in pre_import_names]
    if not new_actions:
        return None
    best = max(new_actions,
               key=lambda a: a.frame_range[1] - a.frame_range[0])
    for a in new_actions:
        if a is not best:
            bpy.data.actions.remove(a)
    best.name = stem
    best.use_fake_user = True
    return best


def main():
    args = parse_args()
    if not args.clips and not args.anims:
        print('[merge] ERROR: need --anims DIR or --clips fighter')
        sys.exit(1)
    bpy.ops.wm.read_factory_settings(use_empty=True)

    import_file(args.character)
    armatures = [ob for ob in bpy.context.scene.objects
                 if ob.type == 'ARMATURE']
    if armatures:
        print('[merge] character armature(s): %s'
              % ', '.join(ob.data.name for ob in armatures))
    else:
        print('[merge] no armature found')

    merged = []
    if args.clips == 'fighter':
        if not armatures:
            print('[merge] ERROR: fighter clips need a rigged character')
            sys.exit(1)
        arm = armatures[0]
        bone_map = args.bone_map
        if not bone_map:
            model_dir = os.path.dirname(os.path.abspath(args.character))
            stem = os.path.splitext(os.path.basename(args.character))[0]
            for cand in (os.path.join(model_dir, 'bone_map.json'),
                         os.path.join(model_dir, stem + '.json')):
                if os.path.isfile(cand):
                    bone_map = cand
                    break
        try:
            mapping = rig_map.resolve_mapping(arm, bone_map)
        except rig_map.RigMapError as e:
            print('[merge] ERROR: %s' % e)
            sys.exit(1)
        merged = procedural_anims.generate_all(arm, mapping)
    else:
        for fn in sorted(os.listdir(args.anims)):
            if fn.startswith('.') or not fn.lower().endswith(('.fbx', '.glb', '.gltf')):
                continue
            path = os.path.join(args.anims, fn)
            stem = re.sub(r'[^\w\-.]', '_', os.path.splitext(fn)[0].strip())
            before = scene_objects()
            known_actions = set(a.name for a in bpy.data.actions)
            try:
                import_file(path)
            except Exception as e:
                print('[merge] SKIP %s (import failed: %s)' % (fn, e))
                remove_objects(scene_objects() - before)
                continue
            new_names = scene_objects() - before
            act = grab_action(known_actions, new_names, stem)
            remove_objects(new_names)
            if act is None:
                print('[merge] SKIP %s (no action found)' % fn)
                continue
            if armatures:
                dropped = rig_map.strip_missing_fcurves(act, armatures[0])
                if dropped:
                    print('[merge] %s: dropped %d curve(s) for missing bones'
                          % (stem, dropped))
            print('[merge] merged %s -> action "%s" (frames %s)' %
                  (fn, act.name, tuple(round(f, 1) for f in act.frame_range)))
            merged.append(act.name)

    if not merged:
        print('[merge] ERROR: no animations merged, not writing output')
        sys.exit(1)

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=out, export_format='GLB')
    print('[merge] wrote %s with %d clip(s): %s' %
          (out, len(merged), ', '.join(merged)))


main()
