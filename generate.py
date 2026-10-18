# Copyright (C) 2026 Stamatis Valis
# SPDX-License-Identifier: MIT
#
# Blender headless isometric sprite generator.
#
# Scans models/ for 3D assets (GLB / GLTF / FBX / OBJ) and renders each one
# as 256x256 transparent PNGs in the game's sprite convention:
#
#   output/<category>/<model>/<model>_<clip>/<DIR>/<model>_<clip>_<DIR>_<angle>_<frame>.png
#
# Static models (no animation clips) render one frame per direction:
#   output/<category>/<model>/<DIR>/<model>_<DIR>_<angle>_0.png
#
# Usage:
#   blender --background --python generate.py -- [options]
# or just run run.ps1
#
# Options (after --):
#   --models PATH     input folder   (default: models/ next to this script)
#   --output PATH     output folder  (default: output/ next to this script)
#   --size N          output resolution (default 256)
#   --frames N        frames per animation clip (default 8)
#   --fps N           fps used for sampling clips (default 8)
#   --pitch DEG       camera elevation above horizon (default 47.4, matches
#                     the game's diamond ratio 128/174)
#   --padding F       empty margin around the model, 0..0.6 (default 0.15)
#   --yaw DEG         model yaw offset in degrees (default 0)
#   --dirs LIST       comma-separated direction names (default: all 16)

import argparse
import json
import math
import os
import re
import shutil
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import procedural_anims
import rig_map

# Same 16 compass directions the game's iso_engine.py uses.
COMPASS = [
    ('E',   0.0),
    ('NEE', 22.5),
    ('NE',  45.0),
    ('NNE', 67.5),
    ('N',   90.0),
    ('NNW', 112.5),
    ('NW',  135.0),
    ('NWW', 157.5),
    ('W',   180.0),
    ('SWW', 202.5),
    ('SW',  225.0),
    ('SSW', 247.5),
    ('S',   270.0),
    ('SSE', 292.5),
    ('SE',  315.0),
    ('SEE', 337.5),
]

MODEL_EXTS = ('.glb', '.gltf', '.fbx', '.obj')


def parse_args():
    argv = sys.argv
    argv = argv[argv.index('--') + 1:] if '--' in argv else []
    here = os.path.dirname(os.path.abspath(__file__))
    p = argparse.ArgumentParser()
    p.add_argument('--models', default=os.path.join(here, 'models'))
    p.add_argument('--output', default=os.path.join(here, 'output'))
    p.add_argument('--size', type=int, default=256)
    p.add_argument('--frames', type=int, default=8)
    p.add_argument('--fps', type=int, default=8)
    p.add_argument('--pitch', type=float, default=47.4)
    p.add_argument('--padding', type=float, default=0.15)
    p.add_argument('--yaw', type=float, default=0.0)
    p.add_argument('--dirs', default='')
    p.add_argument('--fixed-frame', type=float, default=0.0,
                   help='render all models at one world scale: the ortho '
                        'frame covers this many world units (0 = auto-fit '
                        'each model). Use to match an existing asset set.')
    p.add_argument('--no-shadow', action='store_true',
                   help='skip the blob shadow under the model')
    p.add_argument('--shadow-scale', type=float, default=1.0,
                   help='blob shadow radius multiplier')
    p.add_argument('--texture', default='',
                   help='explicit texture image to apply to untextured '
                        'materials (default: auto-detect near the model)')
    p.add_argument('--target', choices=['iso', 'fighter'], default='iso',
                   help='iso: current sprite pipeline; fighter: generate the '
                        '7-clip bound-to-rot fighter set on the rig and '
                        'render the drop-in fighter layout')
    p.add_argument('--char-px', type=float, default=0.0,
                   help='frame so the character renders ~N px tall '
                        '(fighter mode default: 33)')
    p.add_argument('--bone-map', default='',
                   help='explicit bone_map.json overriding auto bone mapping')
    p.add_argument('--deploy', default='',
                   help='copy the rendered character folder into '
                        '<path>/<category>/<character>/ (e.g. the game assets)')
    return p.parse_args(argv)


def scan_models(root):
    """Yield (category_path, name, filepath, anims_dir) for every model file.

    A model file sitting next to an `anims/` subfolder (animation-only FBX/GLB
    files with the same skeleton) gets those clips merged before rendering.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        # never treat files inside an anims/ folder as renderable models
        if 'anims' in dirpath.replace('\\', '/').split('/'):
            dirnames[:] = []
            continue
        anims_dir = os.path.join(dirpath, 'anims')
        has_anims = os.path.isdir(anims_dir)
        for fn in sorted(filenames):
            if fn.startswith('.') or not fn.lower().endswith(MODEL_EXTS):
                continue
            rel = os.path.relpath(dirpath, root)
            parts = [] if rel == '.' else rel.replace('\\', '/').split('/')
            if has_anims and parts:
                # models/<cat>/<ModelName>/<file> + anims/ : the containing
                # folder is the model name, so drop it from the category.
                parts = parts[:-1]
            category = '/'.join(parts)
            yield (category, os.path.splitext(fn)[0], os.path.join(dirpath, fn),
                   anims_dir if has_anims else None)


def reset_scene():
    """Wipe to an empty scene."""
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_model(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == '.glb' or ext == '.gltf':
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == '.fbx':
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext == '.obj':
        bpy.ops.wm.obj_import(filepath=path)
    else:
        raise ValueError('unsupported format: ' + ext)
    fix_materials()


def fix_materials():
    """FBX/GLTF importers sometimes produce materials with alpha 0, which
    renders fully invisible in EEVEE/Cycles. Clamp those to opaque."""
    for mat in bpy.data.materials:
        if not getattr(mat, 'use_nodes', False) or not mat.node_tree:
            continue
        for node in mat.node_tree.nodes:
            if node.type != 'BSDF_PRINCIPLED':
                continue
            alpha = node.inputs.get('Alpha')
            if alpha is not None and not alpha.is_linked and alpha.default_value == 0.0:
                alpha.default_value = 1.0
                mat.blend_method = 'OPAQUE'
                print('[iso]   fixed zero-alpha material: %s' % mat.name)


def world_bbox(objects):
    pts = []
    for ob in objects:
        for corner in ob.bound_box:
            pts.append(ob.matrix_world @ Vector(corner))
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts),
                 min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts),
                 max(p.z for p in pts)))
    return lo, hi


def eval_world_bbox(objects):
    """World AABB of the EVALUATED meshes (includes armature deformation),
    used for char-px framing where the staged pose matters."""
    dg = bpy.context.evaluated_depsgraph_get()
    pts = []
    for ob in objects:
        if ob.type != 'MESH':
            continue
        ob_eval = ob.evaluated_get(dg)
        for corner in ob_eval.bound_box:
            pts.append(ob_eval.matrix_world @ Vector(corner))
    if not pts:
        return world_bbox(objects)
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts),
                 min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts),
                 max(p.z for p in pts)))
    return lo, hi


def place_camera(cam, angle_deg, pitch_deg):
    """Position the camera for one compass direction.

    Semantics follow the game's DIR_ANGLE/MOVE_DIR (iso_engine.py): folder D
    must contain the character FACING screen-direction D, seen from the
    fixed south-above camera. That is the opposite of the orbit that
    produced the reference packs - the pack convention (N = front view)
    reads as walking backwards in-game and was swapped on 2026-10-18 with
    fix_directions.py, so the generator now bakes the correct convention
    directly.

    With the model facing -Y (toe-bone direction on Kenney imports), the
    old orbit (az = +angle) put the front view in N; orbiting by
    az = -angle reflects that about the E-W axis - E/W stay put, N/S and
    the NE..SEE pairs mirror - putting the front view in S. Correct
    arrangement per game logic:
        S = front, N = back, E = right-facing profile, W = left-facing
        profile, SE/NE = front/back-right 3/4, SW/NW = front/back-left 3/4.

    Key/fill lights are repositioned relative to the camera every call so
    every direction is front-lit.
    """
    dist = cam['dist']
    az = math.radians(-angle_deg)
    pit = math.radians(pitch_deg)
    cam.location = Vector((-math.cos(az) * math.cos(pit) * dist,
                           -math.sin(az) * math.cos(pit) * dist,
                           math.sin(pit) * dist))
    direction = Vector((0.0, 0.0, 0.0)) - cam.location
    cam.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    for name, off, z, r in (('Key', 0.6, 6.0, 5.0),
                            ('Fill', -0.9, 2.0, 5.5)):
        ob = bpy.context.scene.objects.get(name)
        if ob is None:
            continue
        a = az + off
        ob.location = Vector((-math.cos(a) * r, -math.sin(a) * r, z))
        ob.rotation_euler = ob.location.to_track_quat('-Z', 'Y').to_euler()


def make_shadow_image():
    """Procedural radial-gradient blob used as the ground shadow."""
    n = 128
    img = bpy.data.images.new('ShadowBlob', n, n, alpha=True)
    px = []
    for y in range(n):
        for x in range(n):
            dx = (x + 0.5) / n * 2.0 - 1.0
            dy = (y + 0.5) / n * 2.0 - 1.0
            r = math.sqrt(dx * dx + dy * dy)
            a = max(0.0, 1.0 - r)
            a = a * a * (3.0 - 2.0 * a)  # smooth falloff
            px.extend((0.0, 0.0, 0.0, a))
    img.pixels = px
    return img


def add_shadow(radius, strength=0.5):
    """Soft dark ellipse on the ground under the model (like the game's)."""
    mat = bpy.data.materials.new('ShadowMat')
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.inputs['Base Color'].default_value = (0.0, 0.0, 0.0, 1.0)
    bsdf.inputs['Roughness'].default_value = 1.0
    tex = nt.nodes.new('ShaderNodeTexImage')
    tex.image = make_shadow_image()
    nt.links.new(tex.outputs['Alpha'], bsdf.inputs['Alpha'])
    nt.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
    for attempt in ('blend_method', 'surface_render_method'):
        try:
            setattr(mat, attempt, 'BLEND' if attempt == 'blend_method'
                    else 'BLENDED')
        except Exception:
            pass

    bpy.ops.mesh.primitive_plane_add(size=radius * 2.0,
                                     location=(0.0, 0.0, 0.002))
    plane = bpy.context.active_object
    plane.name = 'ShadowBlob'
    plane.data.materials.append(mat)
    try:
        plane.visible_shadow = False
        plane.visible_diffuse = False
        plane.visible_glossy = False
    except Exception:
        pass


def find_texture_images(model_dir):
    """Texture candidates living next to the model or in skins/ textures/."""
    out = []
    for sub in ('', 'skins', 'textures'):
        d = os.path.join(model_dir, sub) if sub else model_dir
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.startswith('.'):
                continue
            if fn.lower().endswith(('.png', '.jpg', '.jpeg')):
                out.append(os.path.join(d, fn))
    return out


def apply_textures(texture_paths):
    """Link the first/best-matching texture image onto every material whose
    Base Color is not already textured. Returns number of materials touched."""
    if not texture_paths:
        return 0
    loaded = {}
    touched = 0
    for mat in bpy.data.materials:
        if not getattr(mat, 'use_nodes', False) or not mat.node_tree:
            continue
        bsdf = next((nd for nd in mat.node_tree.nodes
                     if nd.type == 'BSDF_PRINCIPLED'), None)
        if bsdf is None:
            continue
        base = bsdf.inputs['Base Color']
        if base.is_linked:
            continue
        pick = next((p for p in texture_paths
                     if os.path.splitext(os.path.basename(p))[0].lower()
                     in mat.name.lower()), texture_paths[0])
        if pick not in loaded:
            loaded[pick] = bpy.data.images.load(pick)
        tex = mat.node_tree.nodes.new('ShaderNodeTexImage')
        tex.image = loaded[pick]
        mat.node_tree.links.new(tex.outputs['Color'], base)
        touched += 1
        print('[iso]   applied texture %s -> material %s'
              % (os.path.basename(pick), mat.name))
    return touched


def setup_stage(size, pitch, padding, yaw, fixed_frame=0.0, shadow=True,
                shadow_scale=1.0, char_px=0.0):
    """Center the imported model at the origin (feet down), build camera +
    lights. Returns the camera and the framing half-extent."""
    meshes = [ob for ob in bpy.context.scene.objects if ob.type == 'MESH']
    if not meshes:
        return None, 0.0

    # Parent everything to one rig empty so we can move/rotate it all.
    rig = bpy.data.objects.new('RIG', None)
    bpy.context.scene.collection.objects.link(rig)
    roots = [ob for ob in bpy.context.scene.objects
             if ob.parent is None and ob is not rig]
    for ob in roots:
        ob.parent = rig

    # Rotate first (yaw offset), then measure and center.
    rig.rotation_euler = (0.0, 0.0, math.radians(yaw))
    bpy.context.view_layer.update()

    if char_px > 0:
        # frame against the staged pose (evaluated bounds)
        lo, hi = eval_world_bbox(meshes)
    else:
        lo, hi = world_bbox(meshes)
    center = (lo + hi) / 2.0
    rig.location = Vector((-center.x, -center.y, -lo.z))
    bpy.context.view_layer.update()

    dims = hi - lo
    if char_px > 0 and dims.z > 0:
        # Frame by target on-screen character height: at pitch p a body of
        # height z and horizontal depth d projects to z*cos(p) + d*sin(p)
        # under the ortho camera. Use the larger horizontal extent so the
        # framing is safe for every direction.
        horizontal = max(dims.x, dims.y)
        projected = (dims.z * math.cos(math.radians(pitch))
                     + horizontal * math.sin(math.radians(pitch)))
        half = projected * size / (2.0 * char_px)
        # Reference pack: feet sit ~18% of char height below frame center.
        rig.location.z -= 0.18 * dims.z
        print('[stage] char-px framing: dims=(%.3f, %.3f, %.3f) half=%.3f'
              % (dims.x, dims.y, dims.z, half))
    elif fixed_frame > 0:
        # One world scale for all models (matches an existing asset set).
        half = fixed_frame / 2.0
    else:
        # Frame by bounding sphere so the model fits from EVERY direction.
        # (A max-dimension frame crops side views of T-posed characters:
        # the arms point at the camera and project vertically at 47 deg pitch.)
        half = dims.length * 0.5 * (1.0 + padding)

    if shadow:
        add_shadow(radius=max(dims.x, dims.y) * 0.6 * shadow_scale)

    # Camera: replicate the tool's three.js placement. three.js is Y-up,
    # Blender is Z-up: (x, y, z)_three -> (x, -z, y)_blender.
    dist = half * 10.0
    cam_data = bpy.data.cameras.new('Cam')
    cam_data.type = 'ORTHO'
    cam_data.ortho_scale = half * 2.0
    cam_data.clip_start = dist * 0.01
    cam_data.clip_end = dist * 4.0
    cam = bpy.data.objects.new('Cam', cam_data)
    bpy.context.scene.collection.objects.link(cam)
    cam['dist'] = dist
    place_camera(cam, 0.0, pitch)    # Lights: key + fill suns and a soft world ambient, mirroring the
    # three.js hemisphere + key + fill setup.
    key = bpy.data.lights.new('Key', 'SUN')
    key.energy = 3.0
    key_ob = bpy.data.objects.new('Key', key)
    key_ob.location = (3.0, -4.0, 6.0)
    key_ob.rotation_euler = (Vector((3.0, -4.0, 6.0))
                             .to_track_quat('-Z', 'Y').to_euler())
    bpy.context.scene.collection.objects.link(key_ob)

    fill = bpy.data.lights.new('Fill', 'SUN')
    fill.energy = 1.0
    fill_ob = bpy.data.objects.new('Fill', fill)
    fill_ob.location = (-4.0, 3.0, 2.0)
    fill_ob.rotation_euler = (Vector((-4.0, 3.0, 2.0))
                              .to_track_quat('-Z', 'Y').to_euler())
    bpy.context.scene.collection.objects.link(fill_ob)

    world = bpy.data.worlds.new('World')
    world.use_nodes = True
    bg = world.node_tree.nodes['Background']
    bg.inputs[0].default_value = (0.55, 0.57, 0.60, 1.0)
    bg.inputs[1].default_value = 0.6
    bpy.context.scene.world = world

    return cam, half


def setup_render(size):
    sc = bpy.context.scene
    sc.render.resolution_x = size
    sc.render.resolution_y = size
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = True
    sc.render.image_settings.file_format = 'PNG'
    sc.render.image_settings.color_mode = 'RGBA'
    sc.render.image_settings.color_depth = '8'
    sc.render.fps = 8
    try:
        sc.render.engine = 'BLENDER_EEVEE_NEXT'
    except TypeError:
        sc.render.engine = 'BLENDER_EEVEE'
    return sc


def use_cycles_fallback(sc):
    sc.render.engine = 'CYCLES'
    sc.cycles.samples = 16
    sc.cycles.transparent_max_bounces = 8
    sc.cycles.device = 'CPU'
    print('[iso] falling back to Cycles CPU')


def render_to(sc, cam, path):
    sc.camera = cam
    sc.render.filepath = path
    try:
        bpy.ops.render.render(write_still=True)
    except Exception as e:  # EEVEE headless can fail; retry once with Cycles
        print('[iso] render failed (%s), retrying with Cycles' % e)
        if sc.render.engine != 'CYCLES':
            use_cycles_fallback(sc)
            bpy.ops.render.render(write_still=True)
        else:
            raise


def get_actions(meshes):
    """Collect animation actions on armatures/deformed objects."""
    actions = []
    seen = set()
    for ob in meshes:
        ad = ob.animation_data
        if ad is None or ad.action is None:
            continue
        if ad.action.name not in seen:
            seen.add(ad.action.name)
            actions.append(ad.action)
    # Imported libraries usually register all actions in the blend data.
    for act in bpy.data.actions:
        if act.name not in seen and act.frame_range[1] > act.frame_range[0]:
            seen.add(act.name)
            actions.append(act)
    return actions


def bind_action(ob, act):
    ad = ob.animation_data_create()
    ad.action = act
    if getattr(ad, 'action_slot', None) is None:
        slots = list(getattr(act, 'slots', []))
        if slots:
            ad.action_slot = slots[0]


def safe_name(s):
    return re.sub(r'[^\w\-.]', '_', s.strip())


def pick_new_action(pre_import_names):
    """Among actions created since the snapshot, return the one with the
    largest frame span (FBX imports create stub actions next to the real
    animation) and delete the others."""
    new_actions = [a for a in bpy.data.actions if a.name not in pre_import_names]
    if not new_actions:
        return None
    best = max(new_actions,
               key=lambda a: a.frame_range[1] - a.frame_range[0])
    for a in new_actions:
        if a is not best:
            bpy.data.actions.remove(a)
    return best


def merge_anim_folder(anims_dir):
    """Transfer actions from animation-only FBX/GLB files onto the character
    rig already in the scene (same-skeleton retarget), then remove the
    imported rig/objects. Returns the number of merged clips."""
    count = 0
    for fn in sorted(os.listdir(anims_dir)):
        if fn.startswith('.') or not fn.lower().endswith(('.fbx', '.glb', '.gltf')):
            continue
        path = os.path.join(anims_dir, fn)
        stem = safe_name(os.path.splitext(fn)[0])
        before = set(ob.name for ob in bpy.context.scene.objects)
        known_actions = set(a.name for a in bpy.data.actions)
        try:
            import_model(path)
        except Exception as e:
            print('[iso]   anim %s import failed: %s' % (fn, e))
            continue
        new_names = set(ob.name for ob in bpy.context.scene.objects) - before
        act = pick_new_action(known_actions)
        # remove the imported rig/objects; the action survives in blend data
        for obj_name in new_names:
            ob = bpy.data.objects.get(obj_name)
            if ob is not None:
                bpy.data.objects.remove(ob, do_unlink=True)
        if act is None:
            print('[iso]   anim %s: no action found, skipped' % fn)
            continue
        act.name = stem
        act.use_fake_user = True
        char_arm = next((ob for ob in bpy.context.scene.objects
                         if ob.type == 'ARMATURE'), None)
        if char_arm:
            dropped = rig_map.strip_missing_fcurves(act, char_arm)
            if dropped:
                print('[iso]   anim %s: dropped %d curve(s) for missing bones'
                      % (stem, dropped))
        count += 1
        print('[iso]   merged animation %s (frames %s)' %
              (stem, tuple(round(f, 1) for f in act.frame_range)))
    return count


def fighter_layout_root(out_root, name):
    """fighter: <out>/<category>/<name>/<name>_<clip>/..."""
    return os.path.join(out_root, name)


def deploy_character(char_dir, deploy_root, category, name):
    dest = os.path.join(deploy_root, category, name) if category \
        else os.path.join(deploy_root, name)
    os.makedirs(os.path.dirname(dest) or deploy_root, exist_ok=True)
    shutil.copytree(char_dir, dest, dirs_exist_ok=True)
    print('[iso]   deployed to %s' % dest)


def process_model(args, dirs, category, name, filepath, anims_dir=None):
    fighter = args.target == 'fighter'
    tag = 'fighter' if fighter else 'iso'
    if fighter and category:
        # scan_models keeps a model's own folder in the category unless an
        # anims/ sibling drops it; fighter layout + deploy append the name
        # again, so strip a trailing "<name>" segment to avoid cat/name/name.
        if category == name:
            category = ''
        elif category.endswith('/' + name):
            category = category[:-(len(name) + 1)]
    print('[%s] === %s/%s ===' % (tag, category, name))
    reset_scene()
    try:
        import_model(filepath)
    except Exception as e:
        print('[%s] SKIP (import failed: %s)' % (tag, e))
        return None

    mapping = None
    if fighter:
        armatures = [ob for ob in bpy.context.scene.objects
                     if ob.type == 'ARMATURE']
        if not armatures:
            print('[fighter] SKIP (no armature - fighter mode needs a '
                  'rigged model)')
            return None
        arm = armatures[0]
        bone_map = args.bone_map
        if not bone_map:
            model_dir = os.path.dirname(filepath)
            stem = os.path.splitext(os.path.basename(filepath))[0]
            for cand in (os.path.join(model_dir, 'bone_map.json'),
                         os.path.join(model_dir, stem + '.json')):
                if os.path.isfile(cand):
                    bone_map = cand
                    break
        try:
            mapping = rig_map.resolve_mapping(arm, bone_map)
        except rig_map.RigMapError as e:
            print('[fighter] SKIP (%s)' % e)
            return None
        clips = procedural_anims.generate_all(arm, mapping)
        print('[fighter] generated %d clip(s)' % len(clips))
    elif anims_dir:
        n = merge_anim_folder(anims_dir)
        print('[iso]   merged %d animation(s) from anims/' % n)

    if args.texture:
        tex_paths = [args.texture]
    else:
        tex_paths = find_texture_images(os.path.dirname(filepath))
    applied = apply_textures(tex_paths)
    if applied:
        print('[%s]   textured %d material(s)' % (tag, applied))

    char_px = args.char_px
    if fighter and char_px <= 0:
        # empirical: the projection formula under-reads the alpha bbox on
        # back views (~26px actual at 33) - 38 lands idle in 30-33px,
        # matching the reference pack's 33px character.
        char_px = 38.0
    shadow = (not args.no_shadow) and not fighter
    if fighter:
        # Stage against the posed idle, not the T-pose rest state, so the
        # framing matches the sprites that actually get rendered.
        idle = bpy.data.actions.get('default_idle')
        arm = next((ob for ob in bpy.context.scene.objects
                    if ob.type == 'ARMATURE'), None)
        if idle and arm:
            bind_action(arm, idle)
            bpy.context.scene.frame_set(0)
            bpy.context.view_layer.update()
    cam, half = setup_stage(args.size, args.pitch, args.padding, args.yaw,
                            args.fixed_frame, shadow,
                            args.shadow_scale, char_px)
    if cam is None:
        print('[%s] SKIP (no mesh objects - animation-only file?)' % tag)
        return None
    sc = setup_render(args.size)

    meshes = [ob for ob in sc.objects if ob.type == 'MESH']
    armature = next((ob.find_armature() for ob in meshes
                     if ob.find_armature()), None)
    if fighter:
        actions = [bpy.data.actions[c] for c in procedural_anims.FIGHTER_CLIPS
                   if c in bpy.data.actions]
    else:
        actions = get_actions(meshes) if armature else []

    out_root = os.path.join(args.output, category) if category else args.output
    entries = []

    if not actions:
        # Static prop / tile: one frame per direction.
        for dname, angle in dirs:
            place_camera(cam, angle, args.pitch)
            fname = '%s_%s_%s_0.png' % (name, dname, angle_format(angle))
            path = os.path.join(out_root, name, dname, fname)
            render_to(sc, cam, path)
            entries.append({'clip': None, 'dir': dname, 'frames': 1})
        print('[%s]   static: 16 directions' % tag)
    else:
        for act in actions:
            clip = safe_name(act.name)
            render_name = '%s_%s' % (name, clip)
            fr_start, fr_end = act.frame_range
            dur = fr_end - fr_start
            if armature:
                bind_action(armature, act)
            n = max(1, min(args.frames, int(dur) + 1)) if dur > 0 else 1
            for f in range(n):
                if dur > 0:
                    if fighter and n > 1:
                        # fighter clips are keyed on exact frames 0..N-1
                        frame = fr_start + f * dur / (n - 1)
                    else:
                        frame = fr_start + f * (dur / n)
                    sc.frame_set(int(round(frame)))
                else:
                    sc.frame_set(int(fr_start))
                for dname, angle in dirs:
                    place_camera(cam, angle, args.pitch)
                    fname = '%s_%s_%s_%d.png' % (render_name, dname,
                                                 angle_format(angle), f)
                    if fighter:
                        path = os.path.join(fighter_layout_root(out_root, name),
                                            render_name, dname, fname)
                    else:
                        path = os.path.join(out_root, render_name, dname, fname)
                    render_to(sc, cam, path)
            entries.append({'clip': clip, 'frames': n})
            print('[%s]   clip %s: %d frames x %d directions'
                  % (tag, clip, n, len(dirs)))

    if args.deploy:
        if fighter:
            deploy_character(fighter_layout_root(out_root, name),
                             args.deploy, category, name)
        else:
            print('[iso]   --deploy is a fighter-mode option, ignored')

    result = {'category': category, 'model': name, 'source': filepath,
              'renders': entries}
    if fighter and mapping is not None:
        result['bone_map'] = mapping
    return result


def angle_format(angle):
    return ('%.1f' % angle)


def main():
    args = parse_args()
    dirs = COMPASS
    if args.dirs:
        wanted = set(args.dirs.split(','))
        dirs = [d for d in COMPASS if d[0] in wanted]

    models = list(scan_models(args.models))
    if not models:
        print('[iso] no model files found in %s' % args.models)
        return
    print('[iso] found %d model(s)' % len(models))

    os.makedirs(args.output, exist_ok=True)
    manifest = {'size': args.size, 'frames': args.frames, 'fps': args.fps,
                'pitch': args.pitch, 'target': args.target, 'models': []}
    for i, (category, name, filepath, anims_dir) in enumerate(models, 1):
        print('[%s] (%d/%d)' % (args.target, i, len(models)))
        result = process_model(args, dirs, category, name, filepath, anims_dir)
        if result:
            manifest['models'].append(result)
            clips = [e['clip'] for e in result['renders'] if e['clip']]
            total = sum(e.get('frames', 1) * len(dirs)
                        for e in result['renders'])
            print('[%s] summary %s: %d clip(s), ~%d PNG(s)'
                  % (args.target, name, len(clips), total))

    with open(os.path.join(args.output, 'manifest.json'), 'w') as fh:
        json.dump(manifest, fh, indent=2)
    print('[iso] done. Output in %s' % os.path.abspath(args.output))


main()
