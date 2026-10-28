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
# Auto-rig a humanoid GLB/FBX for the procedural fighter pipeline:
# fits a Rigify humanoid metarig to the mesh, generates the control rig,
# strips it down to deform bones, renames them to the canonical names
# rig_map.py expects, and skins the mesh with distance-based weights
# (robust against hollow/armor topology where Blender's heat solver
# fails). Blender's automatic weights remain available via --weights heat.
#
# Usage (inside Blender):
#   blender --background --python rig_rigify.py -- \
#       --input "models/playable character/armoredknight/armoredknight.glb" \
#       [--out <path.glb>] [--weights distance|heat|envelope] \
#       [--dry-run] [--report path.json]

import argparse
import json
import math
import os
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import model_io
import rig_map


def log(*a):
    print('[rigify]', *a)


# ---------------------------------------------------------------- bbox utils

def mesh_bbox(meshes):
    pts = []
    for ob in meshes:
        if ob.type != 'MESH':
            continue
        for corner in ob.bound_box:
            pts.append(ob.matrix_world @ Vector(corner))
    if not pts:
        raise RuntimeError('no meshes found for bbox')
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts),
                 min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts),
                 max(p.z for p in pts)))
    return lo, hi


def _arm_bounds(arm_obj):
    zs = []
    for pb in arm_obj.pose.bones:
        zs += [pb.bone.head_local.z, pb.bone.tail_local.z]
    return min(zs), max(zs), 0.0


# ---------------------------------------------------------------- rig build

DEF_MAP = {
    'DEF-pelvis.L': 'Hips',
    'DEF-shoulder.L': 'LeftShoulder', 'DEF-shoulder.R': 'RightShoulder',
    'DEF-upper_arm.L': 'LeftArm', 'DEF-upper_arm.R': 'RightArm',
    'DEF-forearm.L': 'LeftForeArm', 'DEF-forearm.R': 'RightForeArm',
    'DEF-hand.L': 'LeftHand', 'DEF-hand.R': 'RightHand',
    'DEF-thigh.L': 'LeftUpLeg', 'DEF-thigh.R': 'RightUpLeg',
    'DEF-shin.L': 'LeftLeg', 'DEF-shin.R': 'RightLeg',
    'DEF-foot.L': 'LeftFoot', 'DEF-foot.R': 'RightFoot',
    'DEF-toe.L': 'LeftToes', 'DEF-toe.R': 'RightToes',
    'DEF-neck': 'Neck', 'DEF-head': 'Head',
}

TORSO_NAMES = ['Spine', 'Chest', 'UpperChest']
TORSO_NECK_IDX = 5


def create_human_metarig():
    """Build the rigify basic humanoid metarig on a fresh armature object."""
    arm = bpy.data.armatures.new('metarig')
    ob = bpy.data.objects.new('metarig', arm)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    from rigify.metarigs.Basic import basic_human
    basic_human.create(ob)
    bpy.ops.object.mode_set(mode='OBJECT')
    return ob


def fit_metarig(met, lo, hi):
    """Uniform-scale + translate the sample metarig to the mesh bounds."""
    bpy.ops.object.select_all(action='DESELECT')
    met.select_set(True)
    bpy.context.view_layer.objects.active = met
    z_lo, z_hi, _ = _arm_bounds(met)
    mh = max(z_hi - z_lo, 1e-6)
    h = max(hi.z - lo.z, 1e-6)
    s = h / mh
    met.scale = (s, s, s)
    bpy.context.view_layer.update()
    z_lo2, _, _ = _arm_bounds(met)
    met.location.z += lo.z - z_lo2
    bpy.context.view_layer.update()
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return met


def strip_and_rename(arm, lo, hi):
    """Reduce a generated rigify rig to the canonical deform skeleton:
    keep ONLY the bones we rename, delete everything else, and re-parent
    orphaned kept bones to their nearest surviving ancestor. Adds a Head
    bone manually (the basic humanoid metarig has none)."""
    bpy.ops.object.mode_set(mode='EDIT')
    ebs = arm.data.edit_bones

    torso = sorted([eb.name for eb in ebs
                    if eb.name.startswith('DEF-spine')],
                   key=lambda n: (len(n), n))
    renames = {}
    for eb in ebs:
        n = eb.name
        if n in DEF_MAP:
            renames[n] = DEF_MAP[n]
        elif n.startswith('DEF-spine'):
            idx = torso.index(n)
            if idx < len(TORSO_NAMES):
                renames[n] = TORSO_NAMES[idx]
            elif idx == TORSO_NECK_IDX:
                renames[n] = 'Neck'

    keep = set(renames) | set(renames.values())
    parents = {}
    for eb in ebs:
        parents[eb.name] = eb.parent.name if eb.parent else None
    for eb in list(ebs):
        if eb.name not in keep:
            ebs.remove(eb)
    for eb in ebs:
        p = parents[eb.name]
        if p is not None and p not in keep:
            eb.parent = nearest_kept_by_name(p, parents, keep, ebs)
    for old, new in renames.items():
        eb = ebs.get(old)
        if eb is not None and old != new:
            eb.name = new
    log('kept %d bones' % len(ebs))

    if ebs.get('Head') is None:
        h = max(hi.z - lo.z, 1e-6)
        cx = (lo.x + hi.x) / 2.0
        cy = (lo.y + hi.y) / 2.0
        neck = ebs.get('Neck')
        head_z = hi.z - 0.10 * h
        top_z = hi.z - 0.005 * h
        eb = ebs.new('Head')
        eb.head = Vector((cx, cy, head_z))
        eb.tail = Vector((cx, cy, top_z))
        if neck is not None:
            eb.parent = neck
        renames['(new)'] = 'Head'
        log('added Head bone manually')

    bpy.ops.object.mode_set(mode='OBJECT')
    return arm


def nearest_kept_by_name(name, parents, keep, ebs):
    seen = set()
    while name is not None and name not in keep:
        if name in seen:
            return None
        seen.add(name)
        name = parents.get(name)
    return ebs.get(name) if name else None


def mesh_landmarks(meshes):
    """Measure body landmarks from the vertex cloud: arm line (z, y, span),
    neck base z, hip line z, thigh x-centers, center y."""
    verts = []
    for ob in meshes:
        if ob.type != 'MESH':
            continue
        m = ob.matrix_world
        verts.extend(m @ v.co for v in ob.data.vertices)
    if not verts:
        raise RuntimeError('no vertices for landmark scan')
    lo = Vector((min(v.x for v in verts), min(v.y for v in verts),
                 min(v.z for v in verts)))
    hi = Vector((max(v.x for v in verts), max(v.y for v in verts),
                 max(v.z for v in verts)))
    h = max(hi.z - lo.z, 1e-6)
    max_x = max(abs(v.x) for v in verts)

    lm = {'lo': lo, 'hi': hi, 'h': h, 'span_x': max_x}

    body = [v for v in verts if 0.35 * h < v.z < 0.75 * h
            and abs(v.x) < 0.35 * max_x]
    if body:
        lm['body_x'] = sum(v.x for v in body) / len(body)
        lm['body_y'] = sum(v.y for v in body) / len(body)
    else:
        lm['body_x'] = (lo.x + hi.x) / 2.0
        lm['body_y'] = (lo.y + hi.y) / 2.0

    arm_verts = [v for v in verts if abs(v.x) > 0.75 * max_x]
    if arm_verts:
        lm['arm_z'] = sum(v.z for v in arm_verts) / len(arm_verts)
        lm['arm_y'] = sum(v.y for v in arm_verts) / len(arm_verts)
    else:
        lm['arm_z'] = 0.80 * h
        lm['arm_y'] = 0.0

    top_band = [v for v in verts if v.z > hi.z - 0.06 * h]
    top_span = max((max(v.x for v in top_band) - min(v.x for v in top_band)),
                   1e-6) if top_band else 0.2 * max_x
    neck_z = hi.z - 0.13 * h
    n = 40
    for i in range(n - 1, 0, -1):
        z0 = lo.z + h * i / n
        band = [v for v in verts if z0 <= v.z < z0 + h / n]
        if not band:
            continue
        span = max(v.x for v in band) - min(v.x for v in band)
        if span > 1.35 * top_span:
            neck_z = z0
            break
    lm['neck_z'] = neck_z

    leg_band = [v for v in verts if v.z < 0.45 * h]
    lx = [v.x for v in leg_band if v.x >= 0]
    rx = [v.x for v in leg_band if v.x < 0]
    lm['leg_x'] = (sum(lx) / len(lx)) if lx else 0.25 * max_x
    lm['leg_x_r'] = (sum(rx) / len(rx)) if rx else -0.25 * max_x
    lm['leg_y'] = sum(v.y for v in leg_band) / len(leg_band) if leg_band else 0.0

    hip_z = 0.52 * h
    for i in range(int(0.60 * n), int(0.25 * n), -1):
        z0 = lo.z + h * i / n
        band = [v for v in verts if z0 <= v.z < z0 + h / n]
        if not band:
            continue
        center = [v for v in band if abs(v.x) < 0.12 * max_x]
        side = len(band) - len(center)
        if center and side > 0 and len(center) > len(band) * 0.4:
            hip_z = z0 + h / n
            break
    lm['hip_z'] = hip_z
    return lm


def translate_rig(arm, lm):
    """Shift every edit bone so the rig centerline sits on the body mass
    (mesh bbox center can be off-body when a cape/train skews the bbox)."""
    dx = lm.get('body_x', 0.0)
    dy = lm.get('body_y', 0.0)
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return
    bpy.ops.object.mode_set(mode='EDIT')
    for eb in arm.data.edit_bones:
        eb.head.x += dx
        eb.head.y += dy
        eb.tail.x += dx
        eb.tail.y += dy
    bpy.ops.object.mode_set(mode='OBJECT')
    log('rig translated to body mass (%.2f, %.2f)' % (dx, dy))


def fit_torso_legs(arm, meshes, lm):
    """Rebuild the torso chain vertically on the body centerline (rigify's
    spine deform bones come out with unusable horizontal directions) and
    place both leg chains on measured leg columns (3-means split on x of
    the low region separates tabard/cape center from the two legs)."""
    import numpy as np
    lo, hi, h = lm['lo'], lm['hi'], lm['h']
    cx = lm.get('body_x', 0.0)
    cy = lm.get('body_y', 0.0)
    arm_z = lm.get('arm_z', 0.80 * h)
    neck_z = lm.get('neck_z', 0.87 * h)
    hip_z = lm.get('hip_z', 0.52 * h)

    verts = []
    for ob in meshes:
        if ob.type != 'MESH':
            continue
        m = ob.matrix_world
        verts.extend(m @ v.co for v in ob.data.vertices)
    low = [v for v in verts if v.z < 0.42 * h]
    legs = []
    if low:
        xs = np.array([v.x for v in low])
        centers = np.array([-0.35 * lm['span_x'], 0.0, 0.35 * lm['span_x']])
        idx = None
        for _ in range(12):
            idx = np.argmin(np.abs(xs[:, None] - centers[None, :]), axis=1)
            for k in range(3):
                sel = xs[idx == k]
                if len(sel):
                    centers[k] = sel.mean()
        clusters = []
        for k in range(3):
            selv = [v for v, kk in zip(low, idx) if kk == k]
            clusters.append({'x': centers[k], 'n': len(selv),
                             'min_z': min(v.z for v in selv),
                             'y': sum(v.y for v in selv) / len(selv)})
        legs = sorted([c for c in clusters if c['min_z'] < 0.2 * h],
                      key=lambda c: c['x'])
        if len(legs) < 2:
            legs = []

    bpy.ops.object.mode_set(mode='EDIT')
    ebs = arm.data.edit_bones

    def put(name, head, tail, parent=None):
        eb = ebs.get(name)
        if eb is None:
            eb = ebs.new(name)
        eb.head = Vector(head)
        eb.tail = Vector(tail)
        if parent:
            p = ebs.get(parent)
            if p is not None:
                eb.parent = p
        return eb

    put('Hips', (cx, cy, hip_z), (cx, cy, hip_z + 0.06 * h))
    put('Spine', (cx, cy, hip_z + 0.05 * h),
        (cx, cy, hip_z + 0.5 * (arm_z - hip_z)), 'Hips')
    put('Chest', (cx, cy, hip_z + 0.5 * (arm_z - hip_z)),
        (cx, cy, hip_z + 0.8 * (arm_z - hip_z)), 'Spine')
    put('UpperChest', (cx, cy, hip_z + 0.8 * (arm_z - hip_z)),
        (cx, cy, arm_z), 'Chest')
    put('Neck', (cx, cy, arm_z), (cx, cy, neck_z), 'UpperChest')
    put('Head', (cx, cy, neck_z), (cx, cy, hi.z - 0.005 * h), 'Neck')

    if legs:
        left, right = legs[-1], legs[0]
        for pre, c in (('Left', left), ('Right', right)):
            hip = Vector((c['x'], c['y'], hip_z))
            knee = Vector((c['x'], c['y'], 0.45 * hip_z))
            ankle = Vector((c['x'], c['y'], 0.07 * h))
            foot = Vector((c['x'], c['y'] - 0.09 * h, 0.005 * h))
            put(pre + 'UpLeg', hip, knee, 'Hips')
            put(pre + 'Leg', knee, ankle, pre + 'UpLeg')
            put(pre + 'Foot', ankle, foot, pre + 'Leg')
            put(pre + 'Toes', foot,
                Vector((c['x'], c['y'] - 0.15 * h, 0.005 * h)), pre + 'Foot')
    bpy.ops.object.mode_set(mode='OBJECT')
    leg_txt = ('L x=%.2f R x=%.2f' % (legs[-1]['x'], legs[0]['x'])) if legs         else 'metarig default'
    log('torso rebuilt vertically + legs on columns (%s, hip=%.2f '
        'arm=%.2f neck=%.2f)' % (leg_txt, hip_z, arm_z, neck_z))


def simple_skeleton(lo, hi):
    """Fallback rig: canonical bones placed at measured proportions."""
    h = hi.z - lo.z
    cx, cy = (lo.x + hi.x) / 2.0, (lo.y + hi.y) / 2.0
    w = max(abs(hi.x - lo.x), 0.02)
    half_arm_x = w * 0.30
    zs = {c: lo.z + f * h for c, f in (
        ('Hips', 0.52), ('Spine', 0.60), ('Chest', 0.68), ('UpperChest', 0.76),
        ('Neck', 0.82), ('Head', 0.88), ('HeadTop', 0.99),
        ('Shoulder', 0.80), ('Hand', 0.50), ('Knee', 0.30), ('Ankle', 0.05))}

    arm = bpy.data.armatures.new('rig')
    ob = bpy.data.objects.new('rig', arm)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode='EDIT')

    def bone(name, head, tail, parent=None):
        eb = arm.edit_bones.new(name)
        eb.head = head
        eb.tail = tail
        if parent:
            eb.parent = arm.edit_bones[parent]
        return eb

    bone('Hips', Vector((cx, cy, zs['Hips'])), Vector((cx, cy, zs['Spine'])))
    bone('Spine', Vector((cx, cy, zs['Spine'])), Vector((cx, cy, zs['Chest'])), 'Hips')
    bone('Chest', Vector((cx, cy, zs['Chest'])), Vector((cx, cy, zs['UpperChest'])), 'Spine')
    bone('UpperChest', Vector((cx, cy, zs['UpperChest'])), Vector((cx, cy, zs['Neck'])), 'Chest')
    bone('Neck', Vector((cx, cy, zs['Neck'])), Vector((cx, cy, zs['Head'])), 'UpperChest')
    bone('Head', Vector((cx, cy, zs['Head'])), Vector((cx, cy, zs['HeadTop'])), 'Neck')
    for side, sign, pre in (('Left', 1, 'Left'), ('Right', -1, 'Right')):
        sh = Vector((cx + sign * half_arm_x, cy, zs['Shoulder']))
        bone(pre + 'Shoulder', Vector((cx, cy, zs['Chest'])), sh, 'UpperChest')
        bone(pre + 'Arm', sh, Vector((cx + sign * half_arm_x * 1.45, cy, zs['Hand'] + 0.02 * h)), pre + 'Shoulder')
        bone(pre + 'ForeArm', Vector((cx + sign * half_arm_x * 1.05, cy, zs['Hand'] + 0.14 * h)),
             Vector((cx + sign * half_arm_x * 1.45, cy, zs['Hand'] + 0.04 * h)), pre + 'Arm')
        bone(pre + 'Hand', Vector((cx + sign * half_arm_x * 1.45, cy, zs['Hand'] + 0.04 * h)),
             Vector((cx + sign * half_arm_x * 1.55, cy, zs['Hand'])), pre + 'ForeArm')
        bone(pre + 'UpLeg', Vector((cx + sign * w * 0.12, cy, zs['Hips'])),
             Vector((cx + sign * w * 0.12, cy, zs['Knee'])), 'Hips')
        bone(pre + 'Leg', Vector((cx + sign * w * 0.12, cy, zs['Knee'])),
             Vector((cx + sign * w * 0.12, cy, zs['Ankle'])), pre + 'UpLeg')
        bone(pre + 'Foot', Vector((cx + sign * w * 0.12, cy, zs['Ankle'])),
             Vector((cx + sign * w * 0.12, cy - 0.06 * h, lo.z)), pre + 'Leg')
    bpy.ops.object.mode_set(mode='OBJECT')
    return ob


# ---------------------------------------------------------------- skinning

def bone_segments_world(arm):
    segs = []
    mw = arm.matrix_world
    for pb in arm.pose.bones:
        b = pb.bone
        h = mw @ b.head_local
        t = mw @ b.tail_local
        segs.append((b.name, h, t))
    return segs


def point_segment_dist(p, a, b):
    ab = b - a
    denom = ab.dot(ab)
    if denom < 1e-12:
        return (p - a).length
    t = max(0.0, min(1.0, (p - a).dot(ab) / denom))
    return (p - (a + ab * t)).length


def hybrid_weights(meshes, arm):
    """Heat-solve automatic weights first (best for connected cloth/armor);
    for every bone that ends up with no usable vertex group, fill it with
    distance weights, then renormalize per vertex."""
    bpy.ops.object.select_all(action='DESELECT')
    for ob in meshes:
        ob.select_set(True)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    try:
        bpy.ops.object.parent_set(type='ARMATURE_AUTO')
    except Exception as e:
        log('heat solve failed entirely (%s) - distance fallback' % e)
        ensure_armature_modifier(meshes, arm)
        distance_weights(meshes, arm)
        return 'distance (heat failed)'
    ensure_armature_modifier(meshes, arm)

    h = max(mesh_bbox(meshes)[1].z - mesh_bbox(meshes)[0].z, 1e-6)
    segs = bone_segments_world(arm)
    filled = []
    for ob in meshes:
        if ob.type != 'MESH':
            continue
        mw = ob.matrix_world
        mesh = ob.data
        gi_names = {g.index: g.name for g in ob.vertex_groups}
        present = {gi_names.get(g.group) for v in mesh.vertices for g in v.groups}
        missing = [s for s in segs if s[0] not in present]
        for name, a, t in missing:
            vg = ob.vertex_groups.get(name) or ob.vertex_groups.new(name=name)
            cnt = 0
            for v in mesh.vertices:
                d = point_segment_dist(mw @ v.co, a, t)
                if d < 0.15 * h:
                    vg.add([v.index], 1.0, 'ADD')
                    cnt += 1
            if cnt:
                filled.append(name)
        for v in mesh.vertices:
            tot = sum(g.weight for g in v.groups)
            if tot > 1e-6 and abs(tot - 1.0) > 1e-4:
                for g in v.groups:
                    g.weight = g.weight / tot
    log('heat missing bones filled by distance: %s' % (filled or 'none'))
    return 'hybrid (heat + %d distance-filled)' % len(filled)


def distance_weights(meshes, arm, power=4.0, max_bones=4):
    """Assign vertex weights by inverse distance to each bone segment.
    Armor-proof (no heat solve), deterministic, normalized per vertex."""
    segs = bone_segments_world(arm)
    mesh_lo, mesh_hi = mesh_bbox(meshes)
    h = max(mesh_hi.z - mesh_lo.z, 1e-6)
    for ob in meshes:
        if ob.type != 'MESH':
            continue
        mw = ob.matrix_world
        mesh = ob.data
        for vg in list(ob.vertex_groups):
            ob.vertex_groups.remove(vg)
        groups = {name: ob.vertex_groups.new(name=name) for name, _, _ in segs}
        for v in mesh.vertices:
            p = mw @ v.co
            dists = [(point_segment_dist(p, a, t), name) for name, a, t in segs]
            dists.sort()
            picks = dists[:max_bones]
            if picks[0][0] > h * 0.5:
                weights = [(picks[0][1], 1.0)]
            else:
                weights = [(name, 1.0 / (d + 0.02) ** power) for d, name in picks]
            total = sum(w for _, w in weights) or 1e-6
            for name, w in weights:
                groups[name].add([v.index], w / total, 'REPLACE')
    return 'distance'


def heat_weights(meshes, arm):
    bpy.ops.object.select_all(action='DESELECT')
    for ob in meshes:
        ob.select_set(True)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.parent_set(type='ARMATURE_AUTO')
    return 'heat'


def envelope_weights(meshes, arm):
    bpy.ops.object.select_all(action='DESELECT')
    for ob in meshes:
        ob.select_set(True)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.parent_set(type='ARMATURE_ENVELOPE')
    return 'envelope'


def cleanup_groups(meshes, arm):
    keep = set(rig_map.CANONICAL)
    for ob in meshes:
        if ob.type != 'MESH':
            continue
        for vg in list(ob.vertex_groups):
            if vg.name not in keep:
                ob.vertex_groups.remove(vg)


def ensure_armature_modifier(meshes, arm):
    for ob in meshes:
        if ob.type != 'MESH':
            continue
        if ob.parent != arm:
            ob.parent = arm
        mod = next((m for m in ob.modifiers if m.type == 'ARMATURE'), None)
        if mod is None:
            mod = ob.modifiers.new('Armature', 'ARMATURE')
        mod.object = arm


def export_rigged(mesh_objs, arm, out_path):
    bpy.ops.object.select_all(action='DESELECT')
    for ob in [arm] + list(mesh_objs):
        ob.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.export_scene.gltf(filepath=out_path, export_format='GLB',
                              use_selection=True, export_yup=True)


def main():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument('--input', required=True)
    p.add_argument('--out', default='')
    p.add_argument('--weights', default='hybrid',
                   choices=['hybrid', 'distance', 'heat', 'envelope'])
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--report', default='')
    args = p.parse_args(argv)

    here = os.path.dirname(os.path.abspath(args.input))
    stem = os.path.splitext(os.path.basename(args.input))[0]
    out_path = args.out or os.path.join(here, stem + '_rigged.glb')
    report = {'input': args.input, 'out': out_path, 'steps': [], 'warnings': []}

    model_io.reset_scene()
    model_io.import_model(args.input)

    meshes = [ob for ob in bpy.context.scene.objects if ob.type == 'MESH']
    armatures = [ob for ob in bpy.context.scene.objects if ob.type == 'ARMATURE']
    report['meshes'] = [o.name for o in meshes]
    report['existing_armature'] = [a.name for a in armatures]

    if armatures:
        log('model already has armature %s - checking mapping only' % armatures[0].name)
        try:
            mapping = rig_map.resolve_mapping(armatures[0])
            report['mapping'] = mapping
            log('mapping OK: %d bones' % len(mapping))
        except rig_map.RigMapError as e:
            report['warnings'].append('mapping failed: %s' % e)
            log('MAPPING FAILED: %s' % e)
        report_path = args.report or os.path.join(here, stem + '_rig_report.json')
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, default=str)
        return

    lo, hi = mesh_bbox(meshes)
    h = hi.z - lo.z
    log('mesh bbox height %.3f' % h)
    report['bbox'] = {'lo': list(lo), 'hi': list(hi)}

    if args.dry_run:
        report_path = args.report or os.path.join(here, stem + '_rig_report.json')
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, default=str)
        return

    arm = None
    try:
        bpy.ops.preferences.addon_enable(module='rigify')
        met = create_human_metarig()
        met = fit_metarig(met, lo, hi)
        bpy.ops.object.select_all(action='DESELECT')
        met.select_set(True)
        bpy.context.view_layer.objects.active = met
        bpy.context.view_layer.update()
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.pose.rigify_generate()
        arms = [ob for ob in bpy.context.scene.objects if ob.type == 'ARMATURE']
        arm = next((a for a in arms if a.name == 'rig'), None)
        if arm is None:
            arm = max(arms, key=lambda a: len(a.data.bones), default=None)
        if arm is None:
            raise RuntimeError('rigify_generate produced no armature')
        for ob in arms:
            if ob is not arm:
                bpy.data.objects.remove(ob, do_unlink=True)
        arm.select_set(True)
        bpy.context.view_layer.objects.active = arm
        bpy.context.view_layer.update()
        arm = strip_and_rename(arm, lo, hi)
        lm = mesh_landmarks(meshes)
        translate_rig(arm, lm)
        fit_torso_legs(arm, meshes, lm)
        report['steps'].append('rigify rig generated + stripped to canonical')
        log('rigify rig generated + stripped to canonical names')
    except Exception as e:
        report['warnings'].append('rigify path failed: %s' % e)
        log('rigify path failed (%s) - using simple skeleton fallback' % e)
        for ob in list(bpy.context.scene.objects):
            if ob.type == 'ARMATURE':
                bpy.data.objects.remove(ob, do_unlink=True)
        arm = simple_skeleton(lo, hi)
        report['steps'].append('simple fallback skeleton')

    if args.weights == 'distance':
        kind = distance_weights(meshes, arm)
    elif args.weights == 'heat':
        kind = heat_weights(meshes, arm)
    elif args.weights == 'hybrid':
        kind = hybrid_weights(meshes, arm)
    else:
        kind = envelope_weights(meshes, arm)
    ensure_armature_modifier(meshes, arm)
    cleanup_groups(meshes, arm)
    report['weights'] = kind
    log('weights: %s' % kind)

    bpy.context.view_layer.update()
    keep_ids = set(id(o) for o in meshes)
    for ob in list(bpy.data.objects):
        if ob.type == 'MESH' and id(ob) not in keep_ids:
            bpy.data.objects.remove(ob, do_unlink=True)
    export_rigged(meshes, arm, out_path)
    report['steps'].append('exported %s' % out_path)
    log('exported %s' % out_path)

    try:
        mapping = rig_map.resolve_mapping(arm)
        report['mapping'] = mapping
        log('final mapping OK: %d bones' % len(mapping))
    except rig_map.RigMapError as e:
        report['warnings'].append('final mapping failed: %s' % e)
        log('FINAL MAPPING FAILED: %s' % e)

    report_path = args.report or os.path.join(here, stem + '_rig_report.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, default=str)
    log('report -> %s' % report_path)


if __name__ == '__main__':
    main()
