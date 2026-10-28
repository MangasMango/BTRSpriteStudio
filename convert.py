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
# Convert a rigged model with animations into 16-direction isometric
# sprite folders. You bring the model (e.g. Mixamo FBX with animations,
# one animation per file in a folder, or one GLB with several actions);
# this script renders every animation as its own clip folder using the
# game's naming convention. No procedural animation, no rig fitting.
#
# Usage (inside Blender):
#   blender --background --python convert.py -- \
#       --input "<model file OR folder of FBX/GLB files>" \
#       [--out "<output root>"] [--category "enemy"] [--name skeleton] \
#       [--frames 8] [--char-px 50] [--size 256] [--pitch 30] \
#       [--dirs N,S] [--clips walk,attack] [--deploy "<game assets root>"]

import argparse
import math
import os
import re
import shutil
import sys
import tempfile

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from model_io import (reset_scene, import_model, eval_world_bbox,
                      world_bbox, safe_name)

COMPASS = [
    ('E', 0.0), ('NEE', 22.5), ('NE', 45.0), ('NNE', 67.5),
    ('N', 90.0), ('NNW', 112.5), ('NW', 135.0), ('NWW', 157.5),
    ('W', 180.0), ('SWW', 202.5), ('SW', 225.0), ('SSW', 247.5),
    ('S', 270.0), ('SSE', 292.5), ('SE', 315.0), ('SEE', 337.5),
]


def log(*a):
    print('[convert]', *a)


def angle_format(angle):
    return '%.1f' % angle


def place_camera(cam, angle_deg, pitch_deg):
    """Folder D contains the character FACING screen-direction D, seen from
    the fixed south-above camera (matches the game's DIR_ANGLE/MOVE_DIR:
    S = front, N = back, E = right-facing profile). Key/fill lights are
    repositioned relative to the camera so every direction is front-lit."""
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


def add_shadow(radius, strength=0.5):
    mat = bpy.data.materials.new('ShadowMat')
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.inputs['Base Color'].default_value = (0.0, 0.0, 0.0, 1.0)
    bsdf.inputs['Roughness'].default_value = 1.0
    n = 128
    img = bpy.data.images.new('ShadowBlob', n, n, alpha=True)
    px = []
    for y in range(n):
        for x in range(n):
            dx = (x + 0.5) / n * 2.0 - 1.0
            dy = (y + 0.5) / n * 2.0 - 1.0
            r = math.sqrt(dx * dx + dy * dy)
            a = max(0.0, 1.0 - r)
            a = a * a * (3.0 - 2.0 * a)
            px.extend((0.0, 0.0, 0.0, a))
    img.pixels = px
    tex = nt.nodes.new('ShaderNodeTexImage')
    tex.image = img
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


def flat_materials():
    """Convert every Principled material to emission-only so sprites render
    as flat unlit albedo. The game darkens/tints sprites in code (sprite
    light system), so no directional light may be baked into the frames."""
    for mat in bpy.data.materials:
        if not getattr(mat, 'use_nodes', False) or not mat.node_tree:
            continue
        tree = mat.node_tree
        bsdf = next((n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED'),
                    None)
        if bsdf is None:
            continue
        base = bsdf.inputs['Base Color']
        em_col = bsdf.inputs.get('Emission Color')
        em_str = bsdf.inputs.get('Emission Strength')
        if em_col is None or em_str is None:
            log('flat: %s has no emission inputs, skipped' % mat.name)
            continue
        if base.is_linked:
            for lk in list(base.links):
                tree.links.new(lk.from_node.outputs[lk.from_socket.identifier],
                               em_col)
            for lk in list(base.links):
                tree.links.remove(lk)
        else:
            em_col.default_value = tuple(base.default_value)
        em_str.default_value = 1.0
        base.default_value = (0.0, 0.0, 0.0, 1.0)
        for sock in ('Normal',):
            for lk in list(bsdf.inputs[sock].links):
                tree.links.remove(lk)
        log('flat: %s -> emission albedo' % mat.name)


def setup_stage(size, pitch, padding, yaw, shadow=True, char_px=50.0,
                flat=True):
    """Center the imported model at the origin (feet down), build the ortho
    camera. Frames by target on-screen character height (char_px) so sprites
    match an existing asset set. Flat mode builds no lights: materials are
    emission-only and the game's sprite lighting does the shading."""
    meshes = [ob for ob in bpy.context.scene.objects if ob.type == 'MESH']
    if not meshes:
        return None, 0.0

    rig = bpy.data.objects.new('RIG', None)
    bpy.context.scene.collection.objects.link(rig)
    for ob in [o for o in bpy.context.scene.objects if o.parent is None
               and o is not rig]:
        ob.parent = rig
    rig.rotation_euler = (0.0, 0.0, math.radians(yaw))
    bpy.context.view_layer.update()

    lo, hi = eval_world_bbox(meshes)
    center = (lo + hi) / 2.0
    rig.location = Vector((-center.x, -center.y, -lo.z))
    bpy.context.view_layer.update()

    dims = hi - lo
    if char_px > 0 and dims.z > 0:
        horizontal = max(dims.x, dims.y)
        projected = (dims.z * math.cos(math.radians(pitch))
                     + horizontal * math.sin(math.radians(pitch)))
        half = projected * size / (2.0 * char_px)
        rig.location.z -= 0.18 * dims.z
        log('char-px framing: dims=(%.3f, %.3f, %.3f) half=%.3f'
            % (dims.x, dims.y, dims.z, half))
    else:
        half = dims.length * 0.5 * (1.0 + padding)

    if shadow:
        add_shadow(radius=max(dims.x, dims.y) * 0.6)

    dist = half * 10.0
    cam_data = bpy.data.cameras.new('Cam')
    cam_data.type = 'ORTHO'
    cam_data.ortho_scale = half * 2.0
    cam_data.clip_start = dist * 0.01
    cam_data.clip_end = dist * 4.0
    cam = bpy.data.objects.new('Cam', cam_data)
    bpy.context.scene.collection.objects.link(cam)
    cam['dist'] = dist
    place_camera(cam, 0.0, pitch)

    if flat:
        return cam, half

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


def render_to(sc, cam, path):
    sc.camera = cam
    sc.render.filepath = path
    try:
        bpy.ops.render.render(write_still=True)
    except Exception as e:
        log('render failed (%s), retrying with Cycles' % e)
        sc.render.engine = 'CYCLES'
        sc.cycles.samples = 16
        sc.cycles.device = 'CPU'
        bpy.ops.render.render(write_still=True)


def apply_light_gain(gain, flat=True):
    """Flat mode: scale every material's Emission Strength. Baked-shade
    mode: scale every light + the world background. (1 = raw)"""
    if flat:
        for mat in bpy.data.materials:
            if not getattr(mat, 'use_nodes', False) or not mat.node_tree:
                continue
            bsdf = next((n for n in mat.node_tree.nodes
                         if n.type == 'BSDF_PRINCIPLED'), None)
            if bsdf is None:
                continue
            em_str = bsdf.inputs.get('Emission Strength')
            if em_str is not None and not em_str.is_linked:
                em_str.default_value = em_str.default_value * gain
        return
    for ob in bpy.context.scene.objects:
        if ob.type == 'LIGHT':
            ob.data.energy = ob.data.energy * gain
    world = bpy.context.scene.world
    if world is not None and world.use_nodes:
        for n in world.node_tree.nodes:
            if n.type == 'BACKGROUND':
                n.inputs[1].default_value = n.inputs[1].default_value * gain


def measure_lum(sc, cam, probe_path):
    """Render one frame and return the average opaque luminance 0-255."""
    render_to(sc, cam, probe_path)
    try:
        img = bpy.data.images.load(probe_path)
    except Exception as e:
        log('probe render unreadable (%s), gain stays 1.0' % e)
        return None
    w, h = img.size
    px = list(img.pixels)
    total = 0.0
    n = 0
    # RGBA, row-major bottom-up; sample a decimated grid for speed
    step = max(1, (w * h) // 20000)
    for i in range(0, w * h, step):
        a = px[i * 4 + 3]
        if a > 0.16:
            total += (px[i * 4] + px[i * 4 + 1] + px[i * 4 + 2]) / 3.0
            n += 1
    bpy.data.images.remove(img)
    if os.path.isfile(probe_path):
        os.remove(probe_path)
    if n == 0:
        return None
    return total / n * 255.0


def auto_exposure(sc, cam, target_lum, flat=True):
    """Measure a probe frame and scale the emission strengths (flat) or the
    lights (baked-shade) until the character's average luminance lands on
    target. Iterative: the AgX view transform compresses shadows, so one
    linear gain step does not hit the target."""
    probe = os.path.join(tempfile.gettempdir(), 'iso_probe_lum.png')
    total_gain = 1.0
    lum = None
    for attempt in range(8):
        lum = measure_lum(sc, cam, probe)
        if lum is None or lum <= 1.0:
            log('auto-exposure: probe unusable, keeping raw lights')
            return
        if abs(lum - target_lum) <= max(3.0, target_lum * 0.1):
            break
        step = (target_lum / lum) ** 0.7
        step = max(0.5, min(2.0, step))
        if abs(step - 1.0) < 0.02:
            break
        total_gain *= step
        if total_gain < 0.12 or total_gain > 2.5:
            log('auto-exposure: gain limit %.2f reached, stopping' % total_gain)
            break
        apply_light_gain(step, flat)
        log('auto-exposure pass %d: lum %.1f, gain step %.2f'
            % (attempt, lum, step))
    log('auto-exposure: final lum %.1f (target %.1f, total gain %.2f)'
        % (lum, target_lum, total_gain))


def bind_action(ob, act):
    ad = ob.animation_data_create()
    ad.action = act
    if getattr(ad, 'action_slot', None) is None:
        slots = list(getattr(act, 'slots', []))
        if slots:
            ad.action_slot = slots[0]


def action_fcurves(act):
    """All fcurves of a slotted action (Blender 4.4+ API)."""
    out = []
    for layer in act.layers:
        for strip in layer.strips:
            for cbag in strip.channelbags:
                out.extend(cbag.fcurves)
    return out


def normalize_orientation():
    """Sketchfab/GLB imports can arrive lying down (root node rotations).
    If the mesh height (z) is not the dominant axis, stand the model up:
    try +/-90-degree X rotations and keep the one that maximizes z."""
    meshes = [ob for ob in bpy.context.scene.objects if ob.type == 'MESH']
    if not meshes:
        return
    def zspan():
        lo, hi = eval_world_bbox(meshes)
        return hi.z - lo.z, hi - lo
    z, d = zspan()
    if z >= 0.9 * max(d.x, d.y):
        return
    roots = [ob for ob in bpy.context.scene.objects if ob.parent is None]
    best = None
    for deg in (math.radians(-90.0), math.radians(90.0)):
        rot = Matrix.Rotation(deg, 4, 'X')
        for ob in roots:
            ob.matrix_world = rot @ ob.matrix_world
        bpy.context.view_layer.update()
        z, d = zspan()
        if best is None or z > best[0]:
            best = (z, deg, tuple(d))
        if deg == math.radians(-90.0):
            undo = rot.inverted()
            for ob in roots:
                ob.matrix_world = undo @ ob.matrix_world
        bpy.context.view_layer.update()
    if best is not None:
        rot = Matrix.Rotation(best[1], 4, 'X')
        for ob in roots:
            ob.matrix_world = rot @ ob.matrix_world
        bpy.context.view_layer.update()
        log('orientation normalized (%.1f deg X): dims=(%.3f, %.3f, %.3f)'
            % (math.degrees(best[1]), best[2][0], best[2][1], best[2][2]))


def strip_root_motion(act, armature):
    """Locomotion clips can bake travel into the root bone. Flatten the two
    ground-plane location channels of every topmost-location-animated bone
    (all its ancestors are location-static: Mixamo Hips, Biped Bip001 root
    under a wrapper joint, ...) to their first keyframe so the clip plays in
    place; the channel that maps to world-vertical (bob, jump, fall) is
    kept. Deeper animated bones (thighs, arms) are left alone."""
    fcs = [fc for fc in action_fcurves(act)
           if fc.data_path.startswith('pose.bones[')
           and '].location' in fc.data_path]
    if not fcs:
        return
    by_bone = {}
    for fc in fcs:
        bone = fc.data_path.split('"')[1]
        by_bone.setdefault(bone, {})[fc.array_index] = fc
    animated = set(by_bone)

    def has_anim_ancestor(pb):
        p = pb.parent
        while p is not None:
            if p.name in animated:
                return True
            p = p.parent
        return False

    for bone_name, chans in by_bone.items():
        pb = armature.pose.bones.get(bone_name)
        if pb is None or sorted(chans) != [0, 1, 2]:
            continue
        is_root = (pb.parent is None
                   or not has_anim_ancestor(pb)
                   or any(t in bone_name.lower()
                          for t in ('hips', 'root')))
        if not is_root:
            continue
        # world axis of each local location channel: moving the bone by e_i
        # displaces it in world space by (armMW @ parentM).linear @ e_i
        if pb.parent is not None:
            base = armature.matrix_world @ pb.parent.matrix
        else:
            base = armature.matrix_world
        dirs = []
        for i in range(3):
            d = base.to_3x3() @ Vector([1.0 if j == i else 0.0
                                        for j in range(3)])
            dirs.append(d.normalized())
        vert = max(range(3), key=lambda i: abs(dirs[i].z))
        first = {i: chans[i].keyframe_points[0].co[1] for i in chans}
        travel = 0.0
        for i in chans:
            if i == vert:
                continue
            vals = [k.co[1] for k in chans[i].keyframe_points]
            travel = max(travel, max(vals) - min(vals))
            for k in chans[i].keyframe_points:
                k.co[1] = first[i]
            chans[i].keyframe_points.update()
        log('root motion flattened on %s (vertical channel %d, travel '
            'removed %.2f)' % (bone_name, vert, travel))


def collect_actions():
    """All animation actions with a real frame span, sanitized names.
    Mixamo's own rest action (Armature|mixamo.com|Layer0) is junk; actions
    with broken (non-ASCII) names - some Sketchfab GLBs - become clip_NN."""
    out = []
    broken = 0
    for act in bpy.data.actions:
        if 'mixamo.com' in act.name:
            continue
        if act.frame_range[1] > act.frame_range[0]:
            if not act.name.isascii():
                broken += 1
                act.name = 'clip_%02d' % broken
            out.append(act)
    out.sort(key=lambda a: a.name)
    return out


def merge_anim_files(files):
    """Folder mode: the first model file is the character; each further
    file contributes its action (renamed to the file stem). Same-skeleton
    transfers need no retargeting (Mixamo exports share bone names)."""
    for path in files[1:]:
        stem = safe_name(os.path.splitext(os.path.basename(path))[0])
        known = set(a.name for a in bpy.data.actions)
        before = set(ob.name for ob in bpy.context.scene.objects)
        try:
            import_model(path)
        except Exception as e:
            log('anim %s import failed: %s' % (os.path.basename(path), e))
            continue
        new_names = set(ob.name for ob in bpy.context.scene.objects) - before
        fresh = [a for a in bpy.data.actions if a.name not in known
                 and a.frame_range[1] > a.frame_range[0]]
        for obj_name in new_names:
            ob = bpy.data.objects.get(obj_name)
            if ob is not None:
                bpy.data.objects.remove(ob, do_unlink=True)
        if not fresh:
            log('anim %s: no action found, skipped' % os.path.basename(path))
            continue
        act = max(fresh, key=lambda a: a.frame_range[1] - a.frame_range[0])
        for a in fresh:
            if a is not act:
                bpy.data.actions.remove(a)
        act.name = stem
        act.use_fake_user = True
        log('merged animation %s (frames %.0f..%.0f)'
            % (stem, act.frame_range[0], act.frame_range[1]))


def main():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument('--input', required=True,
                   help='model file (FBX/GLB with actions) or folder '
                        '(first file = character, rest = animations)')
    p.add_argument('--out', default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'output'))
    p.add_argument('--category', default='character')
    p.add_argument('--name', default='')
    p.add_argument('--frames', type=int, default=8,
                   help='frames sampled per clip (0 = every frame)')
    p.add_argument('--char-px', type=float, default=50.0,
                   help='on-screen character height in px (0 = bbox fit)')
    p.add_argument('--size', type=int, default=256)
    p.add_argument('--pitch', type=float, default=30.0,
                   help='camera elevation; 30 deg = the game floor tiles '
                        '2:1 diamond ratio')
    p.add_argument('--padding', type=float, default=0.15)
    p.add_argument('--yaw', type=float, default=0.0)
    p.add_argument('--shadow', action='store_true')
    p.add_argument('--dirs', default='',
                   help='comma list of compass dirs to render (default all)')
    p.add_argument('--drop', default='',
                   help='comma list: skip mesh objects whose name contains '
                        'these substrings (helper/junk geometry)')
    p.add_argument('--clips', default='',
                   help='comma list of action names to render (default all)')
    p.add_argument('--rename', default='',
                   help='comma list of src=dst mappings; dst1+dst2 renders '
                        'the source action under several clip names '
                        '(e.g. run=walk)')
    p.add_argument('--target-lum', type=float, default=100.0,
                   help='auto-exposure target for average character '
                        'luminance 0-255; default 100 = neutral raw albedo '
                        '(the game light system darkens in code); '
                        '0 = keep raw lights')
    p.add_argument('--no-autobright', action='store_true',
                   help='disable auto-exposure entirely')
    p.add_argument('--baked-shade', action='store_true',
                   help='render with the legacy directional key/fill lights '
                        'baked into the sprites (default: flat unlit '
                        'albedo, the game light system shades in code)')
    p.add_argument('--deploy', default='',
                   help='game assets root; copies output/<category>/<name> '
                        'there after rendering')
    args = p.parse_args(argv)

    src = os.path.abspath(args.input)
    if os.path.isdir(src):
        files = [os.path.join(src, f) for f in sorted(os.listdir(src))
                 if f.lower().endswith(('.fbx', '.glb', '.gltf', '.obj'))
                 and not f.startswith('.')]
        if not files:
            raise SystemExit('no model files in %s' % src)
    else:
        files = [src]

    name = safe_name(args.name
                     or os.path.splitext(os.path.basename(files[0]))[0])
    dirs = COMPASS
    if args.dirs:
        wanted = {d.strip().upper() for d in args.dirs.split(',') if d.strip()}
        dirs = [d for d in COMPASS if d[0] in wanted]

    reset_scene()
    log('=== %s ===' % os.path.basename(files[0]))
    import_model(files[0])
    if not args.baked_shade:
        flat_materials()
    if len(files) > 1:
        merge_anim_files(files)

    if args.drop:
        drops = [d.strip().lower() for d in args.drop.split(',') if d.strip()]
        for ob in [o for o in bpy.context.scene.objects if o.type == 'MESH']:
            if any(d in ob.name.lower() for d in drops):
                log('dropping junk mesh %s' % ob.name)
                bpy.data.objects.remove(ob, do_unlink=True)

    normalize_orientation()

    actions = collect_actions()
    if args.clips:
        wanted = {c.strip() for c in args.clips.split(',') if c.strip()}
        actions = [a for a in actions if a.name in wanted]

    rename_map = {}
    if args.rename:
        for m in args.rename.split(','):
            m = m.strip()
            if '=' not in m:
                continue
            src, _, dsts = m.partition('=')
            rename_map[src.strip()] = [safe_name(d.strip())
                                       for d in dsts.split('+') if d.strip()]
    if rename_map:
        for act in list(actions):
            targets = next((dsts for src, dsts in rename_map.items()
                            if act.name == src
                            or act.name.endswith('|' + src)), None)
            if not targets:
                continue
            act.name = targets[0]
            for extra in targets[1:]:
                dup = act.copy()
                dup.name = extra
                dup.use_fake_user = True
                actions.append(dup)
        log('renamed clips: %s' % ', '.join(a.name for a in actions))

    log('%d action(s): %s' % (len(actions),
                              ', '.join(a.name for a in actions) or '(none)'))

    meshes = [ob for ob in bpy.context.scene.objects if ob.type == 'MESH']
    armature = next((ob.find_armature() for ob in meshes
                     if ob.find_armature()), None)
    if actions and armature is not None:
        stage_act = next((a for a in actions if 'idle' in a.name.lower()),
                         actions[0])
        bind_action(armature, stage_act)
        bpy.context.scene.frame_set(int(round(stage_act.frame_range[0])))
        bpy.context.view_layer.update()
        log('staging pose: %s (avoid T-pose framing)' % stage_act.name)

    cam, _half = setup_stage(args.size, args.pitch, args.padding, args.yaw,
                             shadow=args.shadow, char_px=args.char_px,
                             flat=not args.baked_shade)
    if cam is None:
        raise SystemExit('no mesh objects in the model')
    sc = setup_render(args.size)

    if args.target_lum > 0 and not args.no_autobright:
        place_camera(cam, dirs[0][1], args.pitch)
        auto_exposure(sc, cam, args.target_lum, flat=not args.baked_shade)

    out_root = os.path.join(args.out, args.category)
    os.makedirs(out_root, exist_ok=True)
    total = 0

    if not actions or armature is None:
        if actions and armature is None:
            log('actions found but no armature - rendering static')
        for dname, angle in dirs:
            place_camera(cam, angle, args.pitch)
            path = os.path.join(out_root, name, dname,
                                '%s_%s_%s_0.png' % (name, dname,
                                                    angle_format(angle)))
            render_to(sc, cam, path)
            total += 1
        log('static: %d direction(s)' % len(dirs))
    else:
        for act in actions:
            clip = safe_name(act.name)
            render_name = '%s_%s' % (name, clip)
            fr_start, fr_end = act.frame_range
            dur = fr_end - fr_start
            n = (int(dur) + 1) if args.frames <= 0 else max(1, args.frames)
            bind_action(armature, act)
            strip_root_motion(act, armature)
            bpy.context.view_layer.update()
            clip_dir = os.path.join(out_root, name, render_name)
            os.makedirs(clip_dir, exist_ok=True)
            for f in range(n):
                frame = (fr_start if dur <= 0 else
                         fr_start + f * dur / (n - 1)) if n > 1 else fr_start
                sc.frame_set(int(round(frame)))
                for dname, angle in dirs:
                    place_camera(cam, angle, args.pitch)
                    path = os.path.join(clip_dir, dname,
                                        '%s_%s_%s_%d.png' % (render_name,
                                                             dname,
                                                             angle_format(angle),
                                                             f))
                    render_to(sc, cam, path)
            total += n * len(dirs)
            log('clip %s: %d frame(s) x %d direction(s)' % (clip, n, len(dirs)))

    if args.deploy:
        dest = os.path.join(args.deploy, args.category, name)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copytree(os.path.join(out_root, name), dest,
                        dirs_exist_ok=True)
        log('deployed to %s' % dest)

    log('done: %d PNG(s) under %s' % (total, out_root))


if __name__ == '__main__':
    main()
