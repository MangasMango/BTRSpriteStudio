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
# Humanoid bone mapping + rest-pose-agnostic posing helpers.
#
# Resolves an arbitrary input armature onto a canonical bone set
# (Kenney deform names) via: explicit bone_map.json override, common
# naming-convention matchers (Mixamo / Unreal / Rigify / Kenney), then a
# positional/symmetry fallback. Procedural animation authors poses through
# Rig.aim() / Rig.twist() / Rig.nudge(), which express motion as world-space
# directions so any rest pose (T or A) and any bone rolls work.

import json
import os
import re

import bpy
from mathutils import Quaternion, Vector

REQUIRED = [
    'Hips', 'Spine', 'Head',
    'LeftArm', 'LeftForeArm', 'LeftHand',
    'RightArm', 'RightForeArm', 'RightHand',
    'LeftUpLeg', 'LeftLeg', 'LeftFoot',
    'RightUpLeg', 'RightLeg', 'RightFoot',
]

OPTIONAL = [
    'Root', 'Chest', 'UpperChest', 'Neck',
    'LeftShoulder', 'RightShoulder',
    'LeftToes', 'RightToes',
]

CANONICAL = REQUIRED + OPTIONAL


class RigMapError(Exception):
    pass


def _norm(name):
    s = name.lower()
    s = re.sub(r'^[^:]*:', '', s)
    return re.sub(r'[^a-z0-9]', '', s)


CONVENTIONS = {}
for _c, _names in {
    'Hips': ['hips', 'pelvis', 'hip'],
    'Spine': ['spine', 'back'],
    'Chest': ['chest', 'spine1', 'torso'],
    'UpperChest': ['upperchest', 'spine2'],
    'Neck': ['neck'],
    'Head': ['head'],
    'LeftShoulder': ['leftshoulder', 'claviclel', 'lclavicle'],
    'LeftArm': ['leftarm', 'upperarml', 'leftupperarm', 'upperarmleft'],
    'LeftForeArm': ['leftforearm', 'lowerarml', 'leftlowerarm', 'forearml', 'forearmleft'],
    'LeftHand': ['lefthand', 'handl', 'handleft'],
    'LeftUpLeg': ['leftupleg', 'thighl', 'leftthigh', 'uplegl', 'thighleft'],
    'LeftLeg': ['leftleg', 'shinl', 'calfl', 'leftshin', 'legl', 'shinleft'],
    'LeftFoot': ['leftfoot', 'footl', 'footleft'],
    'LeftToes': ['lefttoes', 'lefttoebase', 'balll', 'toesleft'],
    'RightShoulder': ['rightshoulder', 'clavicler', 'rclavicle'],
    'RightArm': ['rightarm', 'upperarmr', 'rightupperarm', 'upperarmright'],
    'RightForeArm': ['rightforearm', 'lowerarmr', 'rightlowerarm', 'forearmr', 'forearmright'],
    'RightHand': ['righthand', 'handr', 'handright'],
    'RightUpLeg': ['rightupleg', 'thighr', 'rightthigh', 'uplegr', 'thighright'],
    'RightLeg': ['rightleg', 'shinr', 'calfr', 'rightshin', 'legr', 'shinright'],
    'RightFoot': ['rightfoot', 'footr', 'footright'],
    'RightToes': ['righttoes', 'righttoebase', 'ballr', 'toesright'],
    'Root': ['root'],
}.items():
    for _n in _names:
        CONVENTIONS[_n] = _c

# positional bands: (z_lo, z_hi) as fraction of skeleton height from feet,
# side: +1 left (+X), -1 right (-X), 0 center. arm-chain bones prefer
# increasing |x| down the chain (arm < forearm < hand).
BANDS = {
    'Hips': (0.42, 0.62, 0, 0),
    'Spine': (0.55, 0.70, 0, 0),
    'Chest': (0.62, 0.78, 0, 0),
    'UpperChest': (0.70, 0.84, 0, 0),
    'Neck': (0.78, 0.90, 0, 0),
    'Head': (0.85, 1.01, 0, 0),
    'LeftShoulder': (0.72, 0.85, 1, 1),
    'LeftArm': (0.70, 0.85, 1, 2),
    'LeftForeArm': (0.55, 0.85, 1, 4),
    'LeftHand': (0.45, 0.85, 1, 6),
    'LeftUpLeg': (0.42, 0.58, 1, 0),
    'LeftLeg': (0.18, 0.44, 1, 0),
    'LeftFoot': (0.02, 0.16, 1, 0),
    'LeftToes': (0.00, 0.07, 1, 0),
}
for _side, _sign in (('Right', -1),):
    for _k, (_lo, _hi, _s, _xp) in list(BANDS.items()):
        if _k.startswith('Left'):
            BANDS[_side + _k[4:]] = (_lo, _hi, _sign, _xp)

_POS_EXCLUDE = re.compile(
    r'(ctrl|ik|roll|heel|pole|target|end|tip|tweak|fk|helper|prop)', re.I)


def _load_override(path):
    if not path or not os.path.isfile(path):
        return {}
    with open(path) as fh:
        data = json.load(fh)
    return {str(k): str(v) for k, v in data.items()}


def _skeleton_bounds(arm_obj):
    zs, xs = [], []
    for pb in arm_obj.pose.bones:
        zs += [pb.bone.head_local.z, pb.bone.tail_local.z]
        xs += [pb.bone.head_local.x, pb.bone.tail_local.x]
    return min(zs), max(zs), max(abs(min(xs)), abs(max(xs)))


def resolve_mapping(arm_obj, override_path=None):
    """Return {canonical: input_bone_name} for arm_obj. Raises RigMapError
    listing every missing required bone if the rig cannot be mapped."""
    bones = list(arm_obj.data.bones)
    by_norm = {}
    for b in bones:
        by_norm.setdefault(_norm(b.name), []).append(b.name)

    mapping = {}
    taken = set()

    def claim(canon, bone_name):
        if canon in mapping or bone_name in taken:
            return False
        mapping[canon] = bone_name
        taken.add(bone_name)
        return True

    for canon, bone_name in _load_override(override_path).items():
        if canon not in CANONICAL:
            print('[rig]   override: unknown canonical "%s" ignored' % canon)
            continue
        if bone_name not in arm_obj.data.bones:
            print('[rig]   override: bone "%s" not in rig, ignored' % bone_name)
            continue
        claim(canon, bone_name)

    for b in bones:
        if b.name in taken:
            continue
        canon = CONVENTIONS.get(_norm(b.name))
        if canon:
            claim(canon, b.name)

    z_lo, z_hi, x_max = _skeleton_bounds(arm_obj)
    height = max(z_hi - z_lo, 1e-6)

    def z_frac(b):
        return ((b.head_local.z + b.tail_local.z) * 0.5 - z_lo) / height

    cands = [b for b in bones
             if b.name not in taken and not _POS_EXCLUDE.search(b.name)]
    scores = []
    for canon in CANONICAL:
        if canon in mapping or canon not in BANDS:
            continue
        lo, hi, side, x_pref = BANDS[canon]
        for b in cands:
            zf = z_frac(b)
            if not (lo <= zf <= hi):
                continue
            if side and abs(b.head_local.x) < 0.02 * max(x_max, 1e-6):
                continue
            if side > 0 and b.head_local.x <= 0:
                continue
            if side < 0 and b.head_local.x >= 0:
                continue
            center = (lo + hi) / 2.0
            scores.append((abs(zf - center) - 0.02 * x_pref, canon, b.name))
    scores.sort()
    used_names = set()
    for _, canon, bname in scores:
        if canon in mapping or bname in used_names:
            continue
        claim(canon, bname)
        used_names.add(bname)

    for canon in CANONICAL:
        src = mapping.get(canon, '-')
        print('[rig]   map %-14s <- %s' % (canon, src))

    missing = [c for c in REQUIRED if c not in mapping]
    if missing:
        raise RigMapError(
            'rig is missing required bones: %s (input bones: %s)'
            % (', '.join(missing),
               ', '.join(b.name for b in bones) or 'none'))
    return mapping


def _all_fcurve_collections(act):
    """Yield every collection that owns fcurves on this action, across
    Blender generations (layered 4.4+/5.x and legacy flat)."""
    if hasattr(act, 'layers'):
        for layer in act.layers:
            for strip in layer.strips:
                for cb in getattr(strip, 'channelbags', []):
                    yield cb.fcurves
    if hasattr(act, 'fcurves'):
        yield act.fcurves


def strip_missing_fcurves(act, arm_obj):
    """Drop fcurves targeting bones that don't exist on arm_obj (e.g. Kenney
    control-bone curves surviving a transplant). Returns removed count."""
    bone_names = set(arm_obj.data.bones.keys())
    pat = re.compile(r'pose\.bones\["([^"]+)"\]')
    removed = 0
    for curves in _all_fcurve_collections(act):
        for fc in list(curves):
            m = pat.match(fc.data_path)
            if m and m.group(1) not in bone_names:
                curves.remove(fc)
                removed += 1
    return removed


class Rig:
    """Pose helper bound to one armature + canonical mapping."""

    def __init__(self, arm_obj, mapping):
        self.arm = arm_obj
        self.map = dict(mapping)
        self.touched = set()
        for pb in arm_obj.pose.bones:
            pb.rotation_mode = 'QUATERNION'

    def pb(self, canonical):
        name = self.map.get(canonical)
        return None if name is None else self.arm.pose.bones.get(name)

    def height(self):
        z_lo, z_hi, _ = _skeleton_bounds(self.arm)
        return max(z_hi - z_lo, 1e-6)

    def clear(self):
        for pb in self.arm.pose.bones:
            pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
            pb.location = (0.0, 0.0, 0.0)

    def _to_armature(self, vec):
        return self.arm.matrix_world.to_3x3().inverted() @ Vector(vec)

    def _fixed_rot(self, pb):
        """Parent-chain rotation (armature space), excluding this bone's own
        pose basis â€” the frame conjugation must use, not the full bone rot."""
        fixed = (pb.matrix @ pb.matrix_basis.inverted()).to_3x3()
        return fixed.to_quaternion()

    def aim(self, canonical, target, weight=1.0):
        pb = self.pb(canonical)
        if pb is None:
            return
        bpy.context.view_layer.update()
        t_arm = self._to_armature(target).normalized()
        cur = (pb.matrix.to_3x3() @ Vector((0.0, 1.0, 0.0))).normalized()
        q_delta_arm = cur.rotation_difference(t_arm)
        q_fixed = self._fixed_rot(pb)
        q_delta_local = q_fixed.inverted() @ q_delta_arm @ q_fixed
        full = pb.rotation_quaternion @ q_delta_local
        pb.rotation_quaternion = pb.rotation_quaternion.slerp(full, weight)
        self.touched.add(pb.name)
        bpy.context.view_layer.update()

    def twist(self, canonical, angle_rad, axis=(0.0, 0.0, 1.0)):
        pb = self.pb(canonical)
        if pb is None:
            return
        bpy.context.view_layer.update()
        ax = self._to_armature(axis).normalized()
        q_delta_arm = Quaternion(ax, angle_rad)
        q_fixed = self._fixed_rot(pb)
        q_delta_local = q_fixed.inverted() @ q_delta_arm @ q_fixed
        pb.rotation_quaternion = pb.rotation_quaternion @ q_delta_local
        self.touched.add(pb.name)
        bpy.context.view_layer.update()

    def nudge(self, canonical, world_offset, weight=1.0):
        pb = self.pb(canonical)
        if pb is None:
            return
        off = self._to_armature(Vector(world_offset)) * weight
        local = pb.bone.matrix_local.to_3x3().inverted() @ off
        pb.location = pb.location + local
        self.touched.add(pb.name)

    def key(self, frame):
        for name in self.touched:
            pb = self.arm.pose.bones[name]
            pb.keyframe_insert('rotation_quaternion', frame=frame)
            pb.keyframe_insert('location', frame=frame)
