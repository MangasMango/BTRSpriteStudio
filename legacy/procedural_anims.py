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
# Procedural generation of bound-to-rot animation clips.
#
# Packages (see .ai/packages-spec.md): every existing character's clip set is
# an atomic, transplantable package - any clip authored here can be rendered
# on any mappable rig. Clips are authored as world-space bone directions
# (see rig_map.Rig) so any rest pose / bone roll works. The fighter package
# (see .ai/spec.md Â§5) is the reference implementation.
#
# Conventions (model at import rest, facing like characterMedium at yaw 0):
#   forward = -Y, character left = +X, up = +Z.

import math

import bpy

import rig_map

FIGHTER_CLIPS = [
    'default_idle',
    'default_walk',
    'armed_idle',
    'armed_walk',
    'armed_attack',
    'special_select',
    'special_death',
]

PACKAGE_CLIPS = {
    'fighter': FIGHTER_CLIPS,
    'knight': FIGHTER_CLIPS,
    'warrior': FIGHTER_CLIPS,
    'skeleton': ['default_idle', 'default_walk', 'default_attack',
                 'special_death'],
    'slime': ['default_idle', 'default_walk', 'special_death'],
    'subservient': ['special_working', 'special_worship'],
    'demonlord': ['default_attack1', 'default_attack2', 'default_idle',
                  'default_walk', 'special_death', 'special_intro',
                  'special_laugh'],
}

# Clips a package lacks vs a game-complete set (health/UI flag; verbatim
# export keeps the gap, .ai/packages-spec.md Â§3).
PACKAGE_GAPS = {
    'skeleton': [],
    'slime': ['armed_idle', 'armed_walk', 'armed_attack', 'special_select'],
    'subservient': ['default_idle', 'default_walk', 'armed_idle',
                    'armed_walk', 'armed_attack', 'special_select',
                    'special_death'],
    'demonlord': ['special_select'],
}

FWD = (0.0, -1.0, 0.0)
BACK = (0.0, 1.0, 0.0)
UP = (0.0, 0.0, 1.0)
DOWN = (0.0, 0.0, -1.0)
LEFT = (1.0, 0.0, 0.0)
RIGHT = (-1.0, 0.0, 0.0)


def _mix(first, *rest):
    """Weighted direction sum: _mix(dir, dir, w, dir, w, ...)."""
    x, y, z = first
    it = iter(rest)
    for d, w in zip(it, it):
        x += d[0] * w
        y += d[1] * w
        z += d[2] * w
    n = math.sqrt(x * x + y * y + z * z) or 1.0
    return (x / n, y / n, z / n)


def _stance(rig, armed):
    """Relaxed standing pose; armed variant adds weapon-ready guard."""
    rig.aim('Hips', UP)
    rig.aim('Spine', _mix(UP, BACK, 0.05))
    rig.aim('Chest', _mix(UP, BACK, 0.06))
    rig.aim('Neck', _mix(UP, BACK, 0.04))
    rig.aim('Head', UP)
    rig.aim('LeftUpLeg', _mix(DOWN, LEFT, 0.07))
    rig.aim('RightUpLeg', _mix(DOWN, RIGHT, 0.07))
    rig.aim('LeftLeg', _mix(DOWN, LEFT, 0.05))
    rig.aim('RightLeg', _mix(DOWN, RIGHT, 0.05))
    rig.aim('LeftFoot', _mix(FWD, DOWN, 0.55))
    rig.aim('RightFoot', _mix(FWD, DOWN, 0.55))
    rig.aim('LeftArm', _mix(DOWN, LEFT, 0.34, FWD, 0.06))
    rig.aim('LeftForeArm', _mix(DOWN, LEFT, 0.38, FWD, 0.2))
    rig.aim('LeftHand', _mix(DOWN, LEFT, 0.36, FWD, 0.24))
    if armed:
        rig.twist('Spine', -0.10)
        rig.twist('Chest', -0.13)
        rig.aim('RightArm', _mix(DOWN, RIGHT, 0.36, FWD, 0.16))
        rig.aim('RightForeArm', _mix(FWD, RIGHT, 0.16, DOWN, 0.5))
        rig.aim('RightHand', _mix(FWD, RIGHT, 0.15, DOWN, 0.45))
    else:
        rig.aim('RightArm', _mix(DOWN, RIGHT, 0.34, FWD, 0.06))
        rig.aim('RightForeArm', _mix(DOWN, RIGHT, 0.38, FWD, 0.2))
        rig.aim('RightHand', _mix(DOWN, RIGHT, 0.36, FWD, 0.24))


def _walk_frame(rig, height, i, armed):
    ph = 2.0 * math.pi * i / 8.0
    # thigh swing: cos so f0/f4 are the split (contact) frames and f2/f6
    # the passing frames - matches the reference bob (low at pass).
    s = math.cos(ph)
    # body high at contact, low at pass. BOTH knees flex at mid-stance
    # (real walk pattern) - that chain-shortening is what actually shrinks
    # the silhouette; a pure hip nudge just translates it. The shin's BACK
    # weight stays modest: it swings the foot toward/away from the camera,
    # which at pitch cancels the bob in front/back views.
    bob = math.cos(2.0 * ph)
    bend_base = (1.0 - bob) * 0.30
    bend_l = bend_base + max(0.0, math.sin(ph)) * 0.3
    bend_r = bend_base + max(0.0, -math.sin(ph)) * 0.3

    rig.aim('Hips', UP)
    rig.nudge('Hips', (0.0, 0.0, 0.10 * height * bob))
    rig.aim('Spine', _mix(UP, FWD, 0.07))
    rig.aim('Chest', _mix(UP, FWD, 0.06))
    rig.twist('Chest', 0.14 * s)
    rig.aim('Neck', UP)
    rig.aim('Head', _mix(UP, FWD, 0.03))

    rig.aim('LeftUpLeg', _mix(DOWN, FWD, 0.5 * s, LEFT, 0.07))
    rig.aim('RightUpLeg', _mix(DOWN, FWD, -0.5 * s, RIGHT, 0.07))
    rig.aim('LeftLeg', _mix(DOWN, FWD, 0.5 * s, BACK, 0.45 * bend_l, LEFT, 0.05))
    rig.aim('RightLeg', _mix(DOWN, FWD, -0.5 * s, BACK, 0.45 * bend_r, RIGHT, 0.05))
    rig.aim('LeftFoot', _mix(FWD, DOWN, 0.55, UP, 0.3 * bend_l))
    rig.aim('RightFoot', _mix(FWD, DOWN, 0.55, UP, 0.3 * bend_r))

    rig.aim('LeftArm', _mix(DOWN, LEFT, 0.34, FWD, -0.45 * s))
    rig.aim('LeftForeArm', _mix(DOWN, LEFT, 0.36, FWD, -0.3 * s))
    rig.aim('LeftHand', _mix(DOWN, LEFT, 0.35, FWD, -0.26 * s))
    if armed:
        rig.aim('RightArm', _mix(DOWN, RIGHT, 0.36, FWD, 0.16 + 0.08 * s))
        rig.aim('RightForeArm', _mix(FWD, RIGHT, 0.16, DOWN, 0.5))
        rig.aim('RightHand', _mix(FWD, RIGHT, 0.15, DOWN, 0.45))
    else:
        rig.aim('RightArm', _mix(DOWN, RIGHT, 0.34, FWD, 0.5 * s))
        rig.aim('RightForeArm', _mix(DOWN, RIGHT, 0.36, FWD, 0.34 * s))
        rig.aim('RightHand', _mix(DOWN, RIGHT, 0.35, FWD, 0.3 * s))


def _attack_frame(rig, height, i):
    h = height
    if i == 0:
        _stance(rig, True)
        return
    if i == 1:
        # windup: body rises (hips up, tiptoes), weapon arm vertical
        # overhead with the shoulder lifted. On weaponless rigs the
        # silhouette top (hand/head) rises even if total height can't
        # exceed idle by 15% (the reference fighter's sword did that).
        rig.twist('Spine', -0.55)
        rig.twist('Chest', -0.40)
        rig.aim('Hips', UP)
        rig.nudge('Hips', (0.0, 0.02 * h, 0.12 * h))
        rig.aim('Spine', UP)
        rig.aim('Chest', UP)
        rig.aim('Neck', UP)
        rig.aim('Head', UP)
        rig.aim('RightShoulder', _mix(UP, RIGHT, 0.3))
        rig.aim('RightArm', UP)
        rig.aim('RightForeArm', UP)
        rig.aim('RightHand', _mix(UP, BACK, 0.1))
        rig.aim('LeftShoulder', _mix(UP, LEFT, 0.2, FWD, 0.15))
        rig.aim('LeftArm', _mix(DOWN, LEFT, 0.55, FWD, 0.45))
        rig.aim('LeftForeArm', _mix(FWD, LEFT, 0.45, DOWN, 0.45))
        rig.aim('LeftHand', _mix(FWD, DOWN, 0.5))
        rig.aim('LeftUpLeg', _mix(DOWN, FWD, 0.12, LEFT, 0.05))
        rig.aim('RightUpLeg', _mix(DOWN, BACK, 0.15, RIGHT, 0.05))
        rig.aim('LeftLeg', DOWN)
        rig.aim('RightLeg', DOWN)
        rig.aim('LeftFoot', _mix(DOWN, FWD, 0.15))
        rig.aim('RightFoot', _mix(DOWN, BACK, 0.15))
        return
    if i == 2:
        rig.twist('Spine', -0.18)
        rig.twist('Chest', -0.12)
        rig.aim('Hips', UP)
        rig.nudge('Hips', (0.0, 0.005 * h, -0.04 * h))
        rig.aim('Spine', _mix(UP, BACK, 0.06))
        rig.aim('Chest', _mix(UP, BACK, 0.05))
        rig.aim('Head', _mix(UP, FWD, 0.05))
        rig.aim('RightArm', _mix(UP, RIGHT, 0.3, FWD, 0.1))
        rig.aim('RightForeArm', _mix(UP, FWD, 0.25, RIGHT, 0.1))
        rig.aim('RightHand', _mix(UP, FWD, 0.4))
        rig.aim('LeftArm', _mix(DOWN, LEFT, 0.35, FWD, 0.4))
        rig.aim('LeftForeArm', _mix(FWD, LEFT, 0.25, DOWN, 0.65))
        rig.aim('LeftUpLeg', _mix(DOWN, FWD, 0.3, LEFT, 0.08))
        rig.aim('RightUpLeg', _mix(DOWN, BACK, 0.2, RIGHT, 0.08))
        rig.aim('LeftLeg', _mix(DOWN, FWD, 0.1, LEFT, 0.06))
        rig.aim('RightLeg', _mix(DOWN, BACK, 0.05, RIGHT, 0.06))
        rig.aim('LeftFoot', _mix(FWD, DOWN, 0.55))
        rig.aim('RightFoot', _mix(FWD, DOWN, 0.6))
        return
    if i in (3, 4):
        deep = 1.0 if i == 3 else 1.1
        rig.twist('Spine', 0.50)
        rig.twist('Chest', 0.36)
        rig.aim('Hips', UP)
        rig.nudge('Hips', (0.0, -0.01 * h, -0.075 * h * deep))
        rig.aim('Spine', _mix(DOWN, FWD, 0.85))
        rig.aim('Chest', _mix(DOWN, FWD, 0.9))
        rig.aim('Neck', _mix(DOWN, FWD, 0.5))
        rig.aim('Head', _mix(DOWN, FWD, 0.55))
        rig.aim('RightArm', _mix(DOWN, FWD, 0.75, RIGHT, 0.35))
        rig.aim('RightForeArm', _mix(DOWN, FWD, 0.9, RIGHT, 0.2))
        rig.aim('RightHand', _mix(DOWN, FWD, 0.95, RIGHT, 0.15))
        rig.aim('LeftArm', _mix(DOWN, LEFT, 0.4, FWD, 0.5))
        rig.aim('LeftForeArm', _mix(DOWN, FWD, 0.6, LEFT, 0.3))
        rig.aim('LeftUpLeg', _mix(DOWN, FWD, 0.25, LEFT, 0.12))
        rig.aim('RightUpLeg', _mix(DOWN, FWD, 0.15, RIGHT, 0.12))
        rig.aim('LeftLeg', _mix(DOWN, BACK, 0.2, LEFT, 0.1))
        rig.aim('RightLeg', _mix(DOWN, BACK, 0.25, RIGHT, 0.1))
        rig.aim('LeftFoot', _mix(FWD, DOWN, 0.55))
        rig.aim('RightFoot', _mix(FWD, DOWN, 0.6))
        return
    if i == 5:
        rig.twist('Spine', 0.26)
        rig.twist('Chest', 0.18)
        rig.aim('Hips', UP)
        rig.nudge('Hips', (0.0, 0.0, -0.03 * h))
        rig.aim('Spine', _mix(UP, FWD, 0.3))
        rig.aim('Chest', _mix(UP, FWD, 0.28))
        rig.aim('Head', _mix(UP, FWD, 0.15))
        rig.aim('RightArm', _mix(DOWN, RIGHT, 0.4, FWD, 0.4))
        rig.aim('RightForeArm', _mix(DOWN, FWD, 0.6, RIGHT, 0.3))
        rig.aim('RightHand', _mix(DOWN, FWD, 0.7))
        rig.aim('LeftArm', _mix(DOWN, LEFT, 0.35, FWD, 0.25))
        rig.aim('LeftForeArm', _mix(DOWN, LEFT, 0.38, FWD, 0.2))
        rig.aim('LeftUpLeg', _mix(DOWN, FWD, 0.2, LEFT, 0.09))
        rig.aim('RightUpLeg', _mix(DOWN, FWD, 0.1, RIGHT, 0.09))
        rig.aim('LeftLeg', _mix(DOWN, FWD, 0.05, LEFT, 0.06))
        rig.aim('RightLeg', _mix(DOWN, FWD, 0.05, RIGHT, 0.06))
        rig.aim('LeftFoot', _mix(FWD, DOWN, 0.55))
        rig.aim('RightFoot', _mix(FWD, DOWN, 0.55))
        return
    if i == 6:
        rig.twist('Spine', 0.14)
        rig.twist('Chest', 0.10)
        rig.aim('Hips', UP)
        rig.nudge('Hips', (0.0, 0.0, -0.015 * h))
        rig.aim('Spine', _mix(UP, FWD, 0.12))
        rig.aim('Chest', _mix(UP, FWD, 0.1))
        rig.aim('Head', UP)
        rig.aim('RightArm', _mix(DOWN, RIGHT, 0.38, FWD, 0.25))
        rig.aim('RightForeArm', _mix(DOWN, FWD, 0.5, RIGHT, 0.25))
        rig.aim('RightHand', _mix(DOWN, FWD, 0.55))
        rig.aim('LeftArm', _mix(DOWN, LEFT, 0.34, FWD, 0.12))
        rig.aim('LeftForeArm', _mix(DOWN, LEFT, 0.38, FWD, 0.2))
        rig.aim('LeftUpLeg', _mix(DOWN, FWD, 0.1, LEFT, 0.08))
        rig.aim('RightUpLeg', _mix(DOWN, FWD, 0.05, RIGHT, 0.08))
        rig.aim('LeftLeg', _mix(DOWN, LEFT, 0.05))
        rig.aim('RightLeg', _mix(DOWN, RIGHT, 0.05))
        rig.aim('LeftFoot', _mix(FWD, DOWN, 0.55))
        rig.aim('RightFoot', _mix(FWD, DOWN, 0.55))
        return
    _stance(rig, True)


def _select_frame(rig, height, i):
    _stance(rig, True)
    t = (1.0 - math.cos(2.0 * math.pi * i / 8.0)) * 0.5
    rig.nudge('Hips', (0.0, 0.0, 0.014 * height * t))


def _death_frame(rig, height, i):
    h = height
    if i == 0:
        _stance(rig, True)
        return
    if i in (1, 2):
        k = i
        rig.aim('Hips', UP)
        rig.nudge('Hips', (0.0, 0.01 * h * k, -0.012 * h * k))
        rig.aim('Spine', _mix(UP, BACK, 0.22 * k))
        rig.aim('Chest', _mix(UP, BACK, 0.18 * k))
        rig.aim('Head', _mix(UP, BACK, 0.3 * k))
        rig.aim('RightArm', _mix(DOWN, RIGHT, 0.3, UP, 0.25 * k, BACK, 0.3 * k))
        rig.aim('RightForeArm', _mix(FWD, RIGHT, 0.2, UP, 0.3 * k))
        rig.aim('LeftArm', _mix(DOWN, LEFT, 0.3, UP, 0.4 * k, FWD, 0.3 * k))
        rig.aim('LeftForeArm', _mix(UP, LEFT, 0.3, FWD, 0.4))
        buckled = 0.35 * k
        rig.aim('LeftUpLeg', _mix(DOWN, FWD, 0.15, LEFT, 0.08))
        rig.aim('LeftLeg', _mix(DOWN, FWD, buckled, LEFT, 0.06))
        rig.aim('RightUpLeg', _mix(DOWN, RIGHT, 0.08))
        rig.aim('RightLeg', _mix(DOWN, BACK, 0.1, RIGHT, 0.06))
        rig.aim('LeftFoot', _mix(FWD, DOWN, 0.55))
        rig.aim('RightFoot', _mix(FWD, DOWN, 0.6))
        return
    if i == 3:
        rig.aim('Hips', _mix(UP, FWD, 0.25))
        rig.nudge('Hips', (0.0, -0.02 * h, -0.07 * h))
        rig.aim('Spine', _mix(UP, FWD, 0.45))
        rig.aim('Chest', _mix(UP, FWD, 0.5))
        rig.aim('Head', _mix(UP, FWD, 0.35))
        rig.aim('RightArm', _mix(DOWN, RIGHT, 0.5, FWD, 0.4))
        rig.aim('RightForeArm', _mix(FWD, RIGHT, 0.3, DOWN, 0.4))
        rig.aim('LeftArm', _mix(DOWN, LEFT, 0.5, FWD, 0.5))
        rig.aim('LeftForeArm', _mix(FWD, LEFT, 0.3, DOWN, 0.4))
        rig.aim('LeftUpLeg', _mix(DOWN, FWD, 0.3, LEFT, 0.1))
        rig.aim('RightUpLeg', _mix(DOWN, FWD, 0.2, RIGHT, 0.1))
        rig.aim('LeftLeg', _mix(DOWN, BACK, 0.35, LEFT, 0.08))
        rig.aim('RightLeg', _mix(DOWN, BACK, 0.3, RIGHT, 0.08))
        rig.aim('LeftFoot', _mix(FWD, DOWN, 0.55))
        rig.aim('RightFoot', _mix(FWD, DOWN, 0.6))
        return
    if i == 4:
        rig.aim('Hips', _mix(UP, FWD, 0.4))
        rig.nudge('Hips', (0.0, -0.05 * h, -0.17 * h))
        rig.aim('Spine', _mix(UP, FWD, 0.75))
        rig.aim('Chest', _mix(UP, FWD, 0.8))
        rig.aim('Head', _mix(UP, FWD, 0.6))
        rig.aim('RightArm', _mix(UP, RIGHT, 0.6, FWD, 0.3))
        rig.aim('RightForeArm', _mix(UP, FWD, 0.5, RIGHT, 0.3))
        rig.aim('LeftArm', _mix(UP, LEFT, 0.6, FWD, 0.3))
        rig.aim('LeftForeArm', _mix(UP, FWD, 0.5, LEFT, 0.3))
        rig.aim('LeftUpLeg', _mix(DOWN, FWD, 0.35, LEFT, 0.12))
        rig.aim('RightUpLeg', _mix(DOWN, FWD, 0.25, RIGHT, 0.12))
        rig.aim('LeftLeg', _mix(DOWN, BACK, 0.5, LEFT, 0.1))
        rig.aim('RightLeg', _mix(DOWN, BACK, 0.45, RIGHT, 0.1))
        rig.aim('LeftFoot', _mix(FWD, DOWN, 0.4))
        rig.aim('RightFoot', _mix(FWD, DOWN, 0.4))
        return
    if i in (5, 6, 7):
        drop = {5: 0.30, 6: 0.36, 7: 0.37}[i]
        curl = {5: 0.0, 6: 0.5, 7: 1.0}[i]
        rig.aim('Hips', _mix(FWD, DOWN, 2.0))
        rig.nudge('Hips', (0.0, -0.05 * h, -drop * h))
        rig.aim('Spine', _mix(FWD, DOWN, 0.45 - 0.25 * curl))
        rig.aim('Chest', _mix(FWD, DOWN, 0.3 - 0.15 * curl))
        rig.aim('Head', _mix(DOWN, FWD, 0.4, DOWN, 0.6 * curl))
        rig.aim('RightArm', _mix(FWD, DOWN, 1.1, RIGHT, 0.55 - 0.25 * curl))
        rig.aim('RightForeArm', _mix(DOWN, FWD, 0.5 + 0.5 * curl, RIGHT, 0.35))
        rig.aim('LeftArm', _mix(FWD, DOWN, 1.2, LEFT, 0.6 - 0.3 * curl))
        rig.aim('LeftForeArm', _mix(DOWN, FWD, 0.55 + 0.45 * curl, LEFT, 0.35))
        # knees fold forward under the body (compact heap, not sprawled)
        rig.aim('LeftUpLeg', _mix(FWD, DOWN, 1.3 - 0.5 * curl, LEFT, 0.15))
        rig.aim('RightUpLeg', _mix(FWD, DOWN, 1.1 - 0.4 * curl, RIGHT, 0.15))
        rig.aim('LeftLeg', _mix(BACK, DOWN, 0.7, FWD, 0.6 * curl, LEFT, 0.1))
        rig.aim('RightLeg', _mix(BACK, DOWN, 0.6, FWD, 0.55 * curl, RIGHT, 0.1))
        rig.aim('LeftFoot', _mix(BACK, DOWN, 0.5, FWD, 0.4))
        rig.aim('RightFoot', _mix(BACK, DOWN, 0.45, FWD, 0.45))
        return


def _breath_frame(rig, height, i):
    """Breathing idle: subtle chest/hips pulse + head nod (enemy/boss idle)."""
    h = height
    t = 2.0 * math.pi * i / 8.0
    pulse = math.sin(t)
    _stance(rig, False)
    rig.nudge('Hips', (0.0, 0.0, 0.018 * h * pulse))
    rig.twist('Chest', 0.05 * pulse)
    rig.aim('Head', _mix(UP, FWD, 0.04 * (0.5 - 0.5 * pulse)))
    rig.aim('Spine', _mix(UP, BACK, 0.05 + 0.03 * (0.5 + 0.5 * pulse)))


def _swipe_frame(rig, height, i):
    """Two-armed horizontal swipe: windup right -> sweep across -> low
    follow-through -> recover (skeleton attack / demonlord attack2)."""
    h = height
    if i == 0:
        _stance(rig, False)
        return
    if i == 1:
        rig.twist('Spine', -0.35)
        rig.twist('Chest', -0.28)
        rig.aim('Hips', UP)
        rig.nudge('Hips', (0.0, 0.0, -0.02 * h))
        rig.aim('Spine', UP)
        rig.aim('Chest', UP)
        rig.aim('Head', UP)
        rig.aim('LeftArm', _mix(DOWN, RIGHT, 0.45, FWD, 0.2))
        rig.aim('RightArm', _mix(DOWN, RIGHT, 0.5, FWD, 0.25))
        rig.aim('LeftForeArm', _mix(FWD, RIGHT, 0.3, DOWN, 0.5))
        rig.aim('RightForeArm', _mix(FWD, RIGHT, 0.35, DOWN, 0.4))
        rig.aim('LeftHand', _mix(FWD, RIGHT, 0.4, DOWN, 0.3))
        rig.aim('RightHand', _mix(FWD, RIGHT, 0.45, DOWN, 0.3))
        rig.aim('LeftUpLeg', _mix(DOWN, RIGHT, 0.1))
        rig.aim('RightUpLeg', _mix(DOWN, RIGHT, 0.12, BACK, 0.1))
        rig.aim('LeftLeg', DOWN)
        rig.aim('RightLeg', DOWN)
        rig.aim('LeftFoot', _mix(FWD, DOWN, 0.55))
        rig.aim('RightFoot', _mix(FWD, DOWN, 0.6))
        return
    if i in (2, 3):
        deep = 1.0 if i == 2 else 1.15
        rig.twist('Spine', 0.42 * deep)
        rig.twist('Chest', 0.32 * deep)
        rig.aim('Hips', UP)
        rig.nudge('Hips', (0.0, 0.01 * h, -0.04 * h))
        rig.aim('Spine', _mix(UP, FWD, 0.15))
        rig.aim('Chest', _mix(UP, FWD, 0.12))
        rig.aim('Head', _mix(UP, FWD, 0.1))
        rig.aim('LeftArm', _mix(FWD, LEFT, 0.7, DOWN, 0.25))
        rig.aim('RightArm', _mix(FWD, LEFT, 0.65, DOWN, 0.3))
        rig.aim('LeftForeArm', _mix(FWD, LEFT, 0.5, DOWN, 0.35))
        rig.aim('RightForeArm', _mix(FWD, LEFT, 0.45, DOWN, 0.4))
        rig.aim('LeftHand', _mix(FWD, LEFT, 0.55, DOWN, 0.25))
        rig.aim('RightHand', _mix(FWD, LEFT, 0.5, DOWN, 0.3))
        rig.aim('LeftUpLeg', _mix(DOWN, FWD, 0.18, LEFT, 0.08))
        rig.aim('RightUpLeg', _mix(DOWN, BACK, 0.12, RIGHT, 0.08))
        rig.aim('LeftLeg', DOWN)
        rig.aim('RightLeg', DOWN)
        rig.aim('LeftFoot', _mix(FWD, DOWN, 0.55))
        rig.aim('RightFoot', _mix(FWD, DOWN, 0.6))
        return
    if i in (4, 5):
        low = 1.0 if i == 4 else 0.7
        rig.twist('Spine', 0.28 * low)
        rig.twist('Chest', 0.2 * low)
        rig.aim('Hips', UP)
        rig.nudge('Hips', (0.0, 0.0, -0.06 * h * low))
        rig.aim('Spine', _mix(UP, FWD, 0.35 * low))
        rig.aim('Chest', _mix(UP, FWD, 0.3 * low))
        rig.aim('Head', _mix(UP, FWD, 0.2 * low))
        rig.aim('LeftArm', _mix(DOWN, LEFT, 0.5, FWD, 0.3 * low))
        rig.aim('RightArm', _mix(DOWN, LEFT, 0.45, FWD, 0.35 * low))
        rig.aim('LeftForeArm', _mix(DOWN, LEFT, 0.5, FWD, 0.4))
        rig.aim('RightForeArm', _mix(DOWN, LEFT, 0.45, FWD, 0.45))
        rig.aim('LeftHand', _mix(DOWN, FWD, 0.4))
        rig.aim('RightHand', _mix(DOWN, FWD, 0.45))
        rig.aim('LeftUpLeg', _mix(DOWN, FWD, 0.2, LEFT, 0.1))
        rig.aim('RightUpLeg', _mix(DOWN, FWD, 0.1, RIGHT, 0.1))
        rig.aim('LeftLeg', _mix(DOWN, BACK, 0.15, LEFT, 0.05))
        rig.aim('RightLeg', _mix(DOWN, BACK, 0.15, RIGHT, 0.05))
        rig.aim('LeftFoot', _mix(FWD, DOWN, 0.55))
        rig.aim('RightFoot', _mix(FWD, DOWN, 0.55))
        return
    if i == 6:
        _stance(rig, False)
        rig.twist('Spine', 0.1)
        rig.twist('Chest', 0.08)
        return
    _stance(rig, False)


def _work_frame(rig, height, i):
    """Bowing work loop: two deep bend pulses with arms reaching down
    (subservient special_working)."""
    h = height
    t = 2.0 * math.pi * i / 8.0
    bend = 0.5 - 0.5 * math.cos(2.0 * t)      # 0 at f0/f4, 1 at f2/f6
    rig.aim('Hips', UP)
    rig.nudge('Hips', (0.0, 0.0, -0.05 * h * bend))
    rig.aim('Spine', _mix(UP, FWD, 0.15 + 0.55 * bend))
    rig.aim('Chest', _mix(UP, FWD, 0.12 + 0.5 * bend))
    rig.aim('Neck', _mix(UP, FWD, 0.3 * bend))
    rig.aim('Head', _mix(UP, FWD, 0.3 + 0.3 * bend))
    rig.aim('LeftArm', _mix(DOWN, LEFT, 0.25, FWD, 0.4 + 0.45 * bend))
    rig.aim('RightArm', _mix(DOWN, RIGHT, 0.25, FWD, 0.4 + 0.45 * bend))
    rig.aim('LeftForeArm', _mix(DOWN, FWD, 0.5 + 0.3 * bend, LEFT, 0.2))
    rig.aim('RightForeArm', _mix(DOWN, FWD, 0.5 + 0.3 * bend, RIGHT, 0.2))
    rig.aim('LeftHand', _mix(DOWN, FWD, 0.6))
    rig.aim('RightHand', _mix(DOWN, FWD, 0.6))
    rig.aim('LeftUpLeg', _mix(DOWN, LEFT, 0.08))
    rig.aim('RightUpLeg', _mix(DOWN, RIGHT, 0.08))
    rig.aim('LeftLeg', _mix(DOWN, BACK, 0.1 + 0.15 * bend))
    rig.aim('RightLeg', _mix(DOWN, BACK, 0.1 + 0.15 * bend))
    rig.aim('LeftFoot', _mix(FWD, DOWN, 0.55))
    rig.aim('RightFoot', _mix(FWD, DOWN, 0.55))


def _worship_frame(rig, height, i):
    """Lower into a kneel, hold with a slight head-bow pulse (subservient
    special_worship)."""
    h = height
    drop = min(1.0, i / 2.0)                  # kneel over f0-f2, hold after
    t = 2.0 * math.pi * i / 8.0
    hold = 0.5 + 0.5 * math.sin(t) if i > 2 else 0.5
    rig.aim('Hips', _mix(UP, BACK, 0.25 * drop))
    rig.nudge('Hips', (0.0, 0.03 * h * drop, -0.35 * h * drop))
    rig.aim('Spine', _mix(UP, BACK, 0.2 * drop))
    rig.aim('Chest', _mix(UP, BACK, 0.15 * drop))
    rig.aim('Neck', _mix(UP, FWD, 0.25 * drop * hold))
    rig.aim('Head', _mix(UP, FWD, 0.35 * drop * (0.7 + 0.3 * hold)))
    rig.aim('LeftUpLeg', _mix(BACK, DOWN, 0.9 * drop, LEFT, 0.1))
    rig.aim('RightUpLeg', _mix(BACK, DOWN, 0.9 * drop, RIGHT, 0.1))
    rig.aim('LeftLeg', _mix(FWD, DOWN, 1.0 * drop, LEFT, 0.1))
    rig.aim('RightLeg', _mix(FWD, DOWN, 1.0 * drop, RIGHT, 0.1))
    rig.aim('LeftFoot', _mix(BACK, DOWN, 0.5, FWD, 0.4))
    rig.aim('RightFoot', _mix(BACK, DOWN, 0.5, FWD, 0.4))
    rig.aim('LeftArm', _mix(DOWN, LEFT, 0.55, BACK, 0.2 * drop))
    rig.aim('RightArm', _mix(DOWN, RIGHT, 0.55, BACK, 0.2 * drop))
    rig.aim('LeftForeArm', _mix(DOWN, LEFT, 0.4, FWD, 0.3))
    rig.aim('RightForeArm', _mix(DOWN, RIGHT, 0.4, FWD, 0.3))
    rig.aim('LeftHand', _mix(DOWN, FWD, 0.35))
    rig.aim('RightHand', _mix(DOWN, FWD, 0.35))


def _intro_frame(rig, height, i):
    """Boss entrance: crouched -> rise + arms spread V -> chest-puff hold ->
    settle (demonlord special_intro)."""
    h = height
    if i == 0:
        rig.aim('Hips', UP)
        rig.nudge('Hips', (0.0, 0.0, -0.18 * h))
        rig.aim('Spine', _mix(UP, FWD, 0.4))
        rig.aim('Chest', _mix(UP, FWD, 0.35))
        rig.aim('Head', _mix(UP, FWD, 0.45))
        rig.aim('LeftUpLeg', _mix(DOWN, FWD, 0.3, LEFT, 0.2))
        rig.aim('RightUpLeg', _mix(DOWN, BACK, 0.25, RIGHT, 0.2))
        rig.aim('LeftLeg', _mix(DOWN, BACK, 0.35))
        rig.aim('RightLeg', _mix(DOWN, BACK, 0.35))
        rig.aim('LeftFoot', _mix(FWD, DOWN, 0.55))
        rig.aim('RightFoot', _mix(FWD, DOWN, 0.6))
        rig.aim('LeftArm', _mix(DOWN, LEFT, 0.4, FWD, 0.3))
        rig.aim('RightArm', _mix(DOWN, RIGHT, 0.4, FWD, 0.3))
        rig.aim('LeftForeArm', _mix(DOWN, FWD, 0.5, LEFT, 0.3))
        rig.aim('RightForeArm', _mix(DOWN, FWD, 0.5, RIGHT, 0.3))
        return
    if i in (1, 2, 3):
        k = i / 3.0
        rig.aim('Hips', UP)
        rig.nudge('Hips', (0.0, 0.0, -0.18 * h + 0.3 * h * k))
        rig.aim('Spine', _mix(UP, FWD, 0.4 - 0.45 * k))
        rig.aim('Chest', _mix(UP, FWD, 0.35 - 0.4 * k))
        rig.aim('Neck', UP)
        rig.aim('Head', _mix(UP, FWD, 0.45 - 0.55 * k, UP, 0.3 * k))
        rig.aim('LeftUpLeg', _mix(DOWN, LEFT, 0.1))
        rig.aim('RightUpLeg', _mix(DOWN, RIGHT, 0.1))
        rig.aim('LeftLeg', _mix(DOWN, BACK, 0.35 - 0.3 * k))
        rig.aim('RightLeg', _mix(DOWN, BACK, 0.35 - 0.3 * k))
        rig.aim('LeftFoot', _mix(FWD, DOWN, 0.55))
        rig.aim('RightFoot', _mix(FWD, DOWN, 0.55))
        spread = 0.75 * k
        lift = 0.85 * k
        rig.aim('LeftShoulder', _mix(UP, LEFT, 0.3 * k))
        rig.aim('RightShoulder', _mix(UP, RIGHT, 0.3 * k))
        rig.aim('LeftArm', _mix(LEFT, UP, spread))
        rig.aim('RightArm', _mix(RIGHT, UP, spread))
        rig.aim('LeftForeArm', _mix(LEFT, UP, lift))
        rig.aim('RightForeArm', _mix(RIGHT, UP, lift))
        rig.aim('LeftHand', _mix(LEFT, UP, lift))
        rig.aim('RightHand', _mix(RIGHT, UP, lift))
        return
    if i in (4, 5):
        rig.aim('Hips', UP)
        rig.nudge('Hips', (0.0, 0.0, 0.12 * h))
        rig.aim('Spine', _mix(UP, BACK, 0.08))
        rig.aim('Chest', _mix(UP, BACK, 0.12))
        rig.aim('Head', _mix(UP, BACK, 0.2))
        rig.aim('LeftShoulder', _mix(UP, LEFT, 0.3))
        rig.aim('RightShoulder', _mix(UP, RIGHT, 0.3))
        rig.aim('LeftArm', _mix(LEFT, UP, 0.75))
        rig.aim('RightArm', _mix(RIGHT, UP, 0.75))
        rig.aim('LeftForeArm', _mix(LEFT, UP, 0.85))
        rig.aim('RightForeArm', _mix(RIGHT, UP, 0.85))
        rig.aim('LeftHand', _mix(LEFT, UP, 0.85))
        rig.aim('RightHand', _mix(RIGHT, UP, 0.85))
        rig.aim('LeftUpLeg', _mix(DOWN, LEFT, 0.07))
        rig.aim('RightUpLeg', _mix(DOWN, RIGHT, 0.07))
        rig.aim('LeftLeg', DOWN)
        rig.aim('RightLeg', DOWN)
        rig.aim('LeftFoot', _mix(FWD, DOWN, 0.55))
        rig.aim('RightFoot', _mix(FWD, DOWN, 0.55))
        return
    if i in (6, 7):
        settle = 1.0 if i == 6 else 0.4
        rig.aim('Hips', UP)
        rig.nudge('Hips', (0.0, 0.0, 0.04 * h * settle))
        rig.aim('Spine', _mix(UP, BACK, 0.04))
        rig.aim('Chest', _mix(UP, BACK, 0.05))
        rig.aim('Head', _mix(UP, BACK, 0.08 * settle))
        rig.aim('LeftArm', _mix(DOWN, LEFT, 0.34, LEFT, 0.4 * settle))
        rig.aim('RightArm', _mix(DOWN, RIGHT, 0.34, RIGHT, 0.4 * settle))
        rig.aim('LeftForeArm', _mix(DOWN, LEFT, 0.45, FWD, 0.15))
        rig.aim('RightForeArm', _mix(DOWN, RIGHT, 0.45, FWD, 0.15))
        rig.aim('LeftHand', _mix(DOWN, LEFT, 0.4))
        rig.aim('RightHand', _mix(DOWN, RIGHT, 0.4))
        rig.aim('LeftUpLeg', _mix(DOWN, LEFT, 0.07))
        rig.aim('RightUpLeg', _mix(DOWN, RIGHT, 0.07))
        rig.aim('LeftLeg', DOWN)
        rig.aim('RightLeg', DOWN)
        rig.aim('LeftFoot', _mix(FWD, DOWN, 0.55))
        rig.aim('RightFoot', _mix(FWD, DOWN, 0.55))
        return


def _laugh_frame(rig, height, i):
    """Two torso-shake pulses, head thrown back, arms out-bent (demonlord
    special_laugh)."""
    h = height
    t = 2.0 * math.pi * i / 8.0
    pulse = math.cos(2.0 * t) * 0.5 + 0.5     # 1 at f0/f4, 0 at f2/f6
    _stance(rig, False)
    rig.nudge('Hips', (0.0, 0.0, -0.02 * h + 0.05 * h * pulse))
    rig.aim('Hips', UP)
    rig.aim('Spine', _mix(UP, BACK, 0.12 + 0.08 * pulse))
    rig.aim('Chest', _mix(UP, BACK, 0.15 + 0.12 * pulse))
    rig.aim('Neck', _mix(UP, BACK, 0.25))
    rig.aim('Head', _mix(UP, BACK, 0.45 + 0.1 * pulse))
    rig.aim('LeftShoulder', _mix(UP, LEFT, 0.35))
    rig.aim('RightShoulder', _mix(UP, RIGHT, 0.35))
    rig.aim('LeftArm', _mix(LEFT, UP, 0.35, DOWN, 0.2))
    rig.aim('RightArm', _mix(RIGHT, UP, 0.35, DOWN, 0.2))
    rig.aim('LeftForeArm', _mix(LEFT, UP, 0.4, FWD, 0.25))
    rig.aim('RightForeArm', _mix(RIGHT, UP, 0.4, FWD, 0.25))
    rig.aim('LeftHand', _mix(FWD, UP, 0.3))
    rig.aim('RightHand', _mix(FWD, UP, 0.3))


def _heavy_walk(rig, height, i):
    """Demonlord walk: the standard walk beats but arms held out for
    balance and a deeper, slower-feeling bob."""
    h = height
    _walk_frame(rig, h, i, False)
    ph = 2.0 * math.pi * i / 8.0
    bob = math.cos(2.0 * ph)
    rig.nudge('Hips', (0.0, 0.0, 0.05 * h * bob))
    rig.aim('LeftArm', _mix(LEFT, DOWN, 0.45, FWD, -0.2 * math.sin(ph)))
    rig.aim('RightArm', _mix(RIGHT, DOWN, 0.45, FWD, 0.2 * math.sin(ph)))
    rig.aim('LeftForeArm', _mix(LEFT, DOWN, 0.5, FWD, 0.15))
    rig.aim('RightForeArm', _mix(RIGHT, DOWN, 0.5, FWD, 0.15))
    rig.aim('LeftHand', _mix(LEFT, DOWN, 0.4))
    rig.aim('RightHand', _mix(RIGHT, DOWN, 0.4))
    rig.aim('LeftUpLeg', _mix(DOWN, FWD, 0.55 * math.cos(ph), LEFT, 0.12))
    rig.aim('RightUpLeg', _mix(DOWN, FWD, -0.55 * math.cos(ph), RIGHT, 0.12))


def _blob_idle(rig, height, i):
    """Slime approximation, static: crouched tuck (squash shape)."""
    h = height
    rig.aim('Hips', UP)
    rig.nudge('Hips', (0.0, 0.0, -0.3 * h))
    rig.aim('Spine', _mix(UP, FWD, 0.12))
    rig.aim('Chest', _mix(UP, FWD, 0.1))
    rig.aim('Head', UP)
    rig.aim('LeftUpLeg', _mix(DOWN, FWD, 0.85, LEFT, 0.25))
    rig.aim('RightUpLeg', _mix(DOWN, FWD, 0.85, RIGHT, 0.25))
    rig.aim('LeftLeg', _mix(BACK, DOWN, 0.9, LEFT, 0.15))
    rig.aim('RightLeg', _mix(BACK, DOWN, 0.9, RIGHT, 0.15))
    rig.aim('LeftFoot', _mix(FWD, DOWN, 0.55))
    rig.aim('RightFoot', _mix(FWD, DOWN, 0.55))
    rig.aim('LeftArm', _mix(DOWN, LEFT, 0.3, FWD, 0.55))
    rig.aim('RightArm', _mix(DOWN, RIGHT, 0.3, FWD, 0.55))
    rig.aim('LeftForeArm', _mix(DOWN, FWD, 0.75, LEFT, 0.25))
    rig.aim('RightForeArm', _mix(DOWN, FWD, 0.75, RIGHT, 0.25))
    rig.aim('LeftHand', _mix(DOWN, FWD, 0.6))
    rig.aim('RightHand', _mix(DOWN, FWD, 0.6))


def _blob_walk(rig, height, i):
    """Slime approximation: side-sway waddle with squash bob."""
    h = height
    t = 2.0 * math.pi * i / 8.0
    sway = math.sin(t)
    bob = math.cos(2.0 * t)
    _blob_idle(rig, h, i)
    rig.clear()
    rig.aim('Hips', UP)
    rig.nudge('Hips', (0.06 * h * sway, 0.0, -0.3 * h + 0.05 * h * bob))
    rig.twist('Chest', 0.2 * sway)
    rig.twist('Spine', 0.15 * sway)
    rig.aim('Spine', _mix(UP, FWD, 0.12))
    rig.aim('Chest', _mix(UP, FWD, 0.1))
    rig.aim('Neck', UP)
    rig.aim('Head', _mix(UP, RIGHT, -0.1 * sway))
    rig.aim('LeftUpLeg', _mix(DOWN, FWD, 0.85 - 0.15 * sway, LEFT, 0.3))
    rig.aim('RightUpLeg', _mix(DOWN, FWD, 0.85 + 0.15 * sway, RIGHT, 0.3))
    rig.aim('LeftLeg', _mix(BACK, DOWN, 0.9, LEFT, 0.15))
    rig.aim('RightLeg', _mix(BACK, DOWN, 0.9, RIGHT, 0.15))
    rig.aim('LeftFoot', _mix(FWD, DOWN, 0.55, UP, 0.2 + 0.2 * sway))
    rig.aim('RightFoot', _mix(FWD, DOWN, 0.55, UP, 0.2 - 0.2 * sway))
    rig.aim('LeftArm', _mix(DOWN, LEFT, 0.35 + 0.15 * sway, FWD, 0.5))
    rig.aim('RightArm', _mix(DOWN, RIGHT, 0.35 - 0.15 * sway, FWD, 0.5))
    rig.aim('LeftForeArm', _mix(DOWN, FWD, 0.75, LEFT, 0.25))
    rig.aim('RightForeArm', _mix(DOWN, FWD, 0.75, RIGHT, 0.25))
    rig.aim('LeftHand', _mix(DOWN, FWD, 0.6))
    rig.aim('RightHand', _mix(DOWN, FWD, 0.6))


def _blob_death(rig, height, i):
    """Slime approximation: 7-frame progressive deflate into a heap."""
    h = height
    k = i / 6.0                                # 0 at f0, 1 at f6
    squash = 0.02 + 0.28 * k
    fold = 0.85 + 0.4 * k
    rig.aim('Hips', _mix(UP, FWD, 0.3 * k))
    rig.nudge('Hips', (0.0, 0.02 * h * k, -squash * h))
    rig.aim('Spine', _mix(UP, FWD, 0.12 + 0.55 * k))
    rig.aim('Chest', _mix(UP, FWD, 0.1 + 0.6 * k))
    rig.aim('Neck', _mix(UP, FWD, 0.6 * k))
    rig.aim('Head', _mix(UP, FWD, 0.2 + 0.7 * k))
    rig.aim('LeftUpLeg', _mix(DOWN, FWD, fold, LEFT, 0.25))
    rig.aim('RightUpLeg', _mix(DOWN, FWD, fold, RIGHT, 0.25))
    rig.aim('LeftLeg', _mix(BACK, DOWN, fold, LEFT, 0.15))
    rig.aim('RightLeg', _mix(BACK, DOWN, fold, RIGHT, 0.15))
    rig.aim('LeftFoot', _mix(BACK, DOWN, 0.5, FWD, 0.3))
    rig.aim('RightFoot', _mix(BACK, DOWN, 0.5, FWD, 0.3))
    rig.aim('LeftArm', _mix(DOWN, LEFT, 0.3, FWD, 0.55, FWD, 0.4 * k))
    rig.aim('RightArm', _mix(DOWN, RIGHT, 0.3, FWD, 0.55, FWD, 0.4 * k))
    rig.aim('LeftForeArm', _mix(DOWN, FWD, 0.75 + 0.2 * k, LEFT, 0.25))
    rig.aim('RightForeArm', _mix(DOWN, FWD, 0.75 + 0.2 * k, RIGHT, 0.25))
    rig.aim('LeftHand', _mix(DOWN, FWD, 0.6))
    rig.aim('RightHand', _mix(DOWN, FWD, 0.6))


def _finish(arm_obj, clip):
    ad = arm_obj.animation_data
    act = ad.action if ad else None
    if act is None:
        raise RuntimeError('no action generated for clip %s' % clip)
    act.name = clip
    act.use_fake_user = True
    try:
        ad.action = None
        if getattr(ad, 'action_slot', None) is not None:
            ad.action_slot = None
    except Exception:
        pass
    return act


# Registry: (package, clip) first, then shared ('*', clip) authors.
# Value: (frame_author_fn, frame_count). The frame count is authoritative
# for the authored action; manifests must agree with it.
CLIP_AUTHORS = {
    ('*', 'default_idle'): (lambda rig, h, i: _stance(rig, False), 1),
    ('*', 'armed_idle'): (lambda rig, h, i: _stance(rig, True), 1),
    ('*', 'default_walk'): (lambda rig, h, i: _walk_frame(rig, h, i, False), 8),
    ('*', 'armed_walk'): (lambda rig, h, i: _walk_frame(rig, h, i, True), 8),
    ('*', 'armed_attack'): (_attack_frame, 8),
    ('*', 'special_select'): (_select_frame, 8),
    ('*', 'special_death'): (_death_frame, 8),
    ('skeleton', 'default_idle'): (_breath_frame, 8),
    ('skeleton', 'default_walk'): (_heavy_walk, 8),
    ('skeleton', 'default_attack'): (_swipe_frame, 8),
    ('slime', 'default_idle'): (_blob_idle, 1),
    ('slime', 'default_walk'): (_blob_walk, 8),
    ('slime', 'special_death'): (_blob_death, 7),
    ('subservient', 'special_working'): (_work_frame, 8),
    ('subservient', 'special_worship'): (_worship_frame, 8),
    ('demonlord', 'default_attack1'): (_attack_frame, 8),
    ('demonlord', 'default_attack2'): (_swipe_frame, 8),
    ('demonlord', 'default_idle'): (_breath_frame, 8),
    ('demonlord', 'default_walk'): (_heavy_walk, 8),
    ('demonlord', 'special_death'): (_death_frame, 8),
    ('demonlord', 'special_intro'): (_intro_frame, 8),
    ('demonlord', 'special_laugh'): (_laugh_frame, 8),
}


class ClipAuthorError(Exception):
    pass


def resolve_author(package, clip):
    """(fn, frames) for a package clip; '*' entries are shared defaults."""
    key = (package, clip)
    if key not in CLIP_AUTHORS:
        key = ('*', clip)
    if key not in CLIP_AUTHORS:
        raise ClipAuthorError(
            'no author for clip %r (package %r)' % (clip, package))
    return CLIP_AUTHORS[key]


def generate_clips(arm_obj, mapping, clips, package='fighter'):
    """Author the listed clips on arm_obj via CLIP_AUTHORS. Returns the
    clip names actually made, in order (unknown clips raise)."""
    rig = rig_map.Rig(arm_obj, mapping)
    height = rig.height()
    arm_obj.animation_data_create()
    made = []
    for clip in clips:
        fn, frames = resolve_author(package, clip)
        for i in range(frames):
            rig.clear()
            fn(rig, height, i)
            rig.key(i)
        _finish(arm_obj, clip)
        made.append(clip)
        print('[anim] generated %s (%d frame%s)'
              % (clip, frames, '' if frames == 1 else 's'))
    rig.clear()
    bpy.context.view_layer.update()
    return made


def generate_all(arm_obj, mapping):
    """Generate the 7 fighter clips on arm_obj. Returns clip names in order."""
    return generate_clips(arm_obj, mapping, FIGHTER_CLIPS)
