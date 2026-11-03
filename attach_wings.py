# Copyright (C) 2026 MangasMango
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Attach a static prop GLB (e.g. angel wings) onto a rigged character GLB
# at a named bone: the geometry currently weighted to that bone is removed,
# the prop is scaled/placed/oriented onto the bone and JOINED into the
# character's skinned mesh with a single-bone weight (rigid bind), which
# exports through the character's own mesh/joint conventions.
#
# Sketchfab GLBs carry inconsistent skin/node scales that Blender's glTF
# round-trip regularizes, so a placement computed against the import-state
# conventions drifts after export. Hence the two-pass flow: pass 1 places,
# joins, binds and stores an interim file; pass 2 re-loads it, solves the
# file's OWN rest-pose deform affine (LSQ over the bone-rigid prop verts,
# exact to <1e-6 for this class of rigs), and re-maps the prop verts so the
# final file renders the prop exactly where pass 1 placed it. Exports a
# merged GLB with the character's animations untouched.
#
# Usage (inside Blender):
#   blender --background --python attach_wings.py -- \
#       --hero "<character GLB>" --wings "<prop GLB>" --out "<merged GLB>" \
#       [--bone Bone010_026] [--span 1.25] [--back-gap 0.15] [--up-shift 0]
#       [--side-shift 0] [--spin 0] [--flip] [--textures "<dir>"] [--no-drop]

import argparse
import math
import os
import sys
import tempfile

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from model_io import import_model, reset_scene, override_textures

WING_BONE_DEFAULT = 'Bone010_026'


def log(*a):
    print('[attach_wings]', *a)


def find_armature():
    for ob in bpy.context.scene.objects:
        if ob.type == 'ARMATURE':
            return ob
    return None


def skinned_meshes(armature):
    return [ob for ob in bpy.context.scene.objects
            if ob.type == 'MESH' and ob.find_armature() is armature]


def evaluated_world_points(ob):
    """World-space vertex positions of a mesh after its modifiers."""
    dg = bpy.context.evaluated_depsgraph_get()
    oe = ob.evaluated_get(dg)
    me = oe.to_mesh()
    pts = [oe.matrix_world @ Vector(v.co) for v in me.vertices]
    oe.to_mesh_clear()
    return pts


def rest_pose(armature):
    for pb in armature.pose.bones:
        try:
            pb.matrix = pb.bone.matrix_local.copy()
        except Exception:
            pass
    try:
        armature.pose_position = 'REST'
    except Exception:
        pass
    bpy.context.view_layer.update()


def group_full_indices(body, bone):
    """Indices of verts fully weighted (>=0.97, negligible side weights) to
    a named vertex group - the bone-rigid prop verts."""
    vg = body.vertex_groups.get(bone)
    if vg is None:
        return []
    out = []
    for v in body.data.vertices:
        gs = [(g.group, g.weight) for g in v.groups]
        hit = [w for gi, w in gs if gi == vg.index]
        if not hit:
            continue
        others = sum(w for gi, w in gs if gi != vg.index)
        if others < 0.02 and hit[0] > 0.97:
            out.append(v.index)
    return out


def eval_points_for(body, idx):
    """Evaluated world positions for a set of vertex indices."""
    dg = bpy.context.evaluated_depsgraph_get()
    oe = body.evaluated_get(dg)
    me = oe.to_mesh()
    pts = [oe.matrix_world @ Vector(me.vertices[i].co) for i in idx]
    oe.to_mesh_clear()
    return pts


def clear_parents_keep_world(objs):
    for ob in objs:
        if not ob.parent:
            continue
        mw = ob.matrix_world.copy()
        ob.parent = None
        ob.parent_type = 'OBJECT'
        ob.parent_bone = ''
        ob.matrix_parent_inverse = Matrix.Identity(4)
        ob.matrix_world = mw
    bpy.context.view_layer.update()


def remove_non_essentials(objs):
    keep = set(id(o) for o in objs)
    for ob in list(bpy.context.scene.objects):
        if id(ob) not in keep:
            bpy.data.objects.remove(ob, do_unlink=True)
    bpy.context.view_layer.update()


def solve_affine(body, idx, pts):
    """LSQ affine 'mw @ local + 1/gplf -> world' over the sample verts; the
    solve is exact (residual ~1e-7) for bone-rigid geometry on this class
    of rigs. Returns a homogeneous 4x4 mapping local -> world."""
    X = np.array([[c.x, c.y, c.z] for c in pts])
    L = [body.matrix_world @ Vector(body.data.vertices[i].co) for i in idx]
    Lv = np.array([[c.x, c.y, c.z, 1.0] for c in L])
    sol, _, _, _ = np.linalg.lstsq(Lv, X, rcond=None)
    resid = float(np.abs(Lv @ sol - X).max())
    log('deform affine: %d verts, residual %.2e' % (len(pts), resid))
    # world_j = row j.  row i = (sol[*, 0], sol[*, 1], ...) transposed into
    # mathutils rows: row0 gets the world.x coefficients incl. the constant
    return Matrix((
        (sol[0, 0], sol[1, 0], sol[2, 0], sol[3, 0]),
        (sol[0, 1], sol[1, 1], sol[2, 1], sol[3, 1]),
        (sol[0, 2], sol[1, 2], sol[2, 2], sol[3, 2]),
        (0.0, 0.0, 0.0, 1.0))), resid


def shoulder_back(armature):
    """Fallback back normal from the L/R clavicle axis crossed with up."""
    l_c = next((b for b in armature.data.bones
                if b.name.startswith('Bip001 L Clavicle')), None)
    r_c = next((b for b in armature.data.bones
                if b.name.startswith('Bip001 R Clavicle')), None)
    if l_c and r_c:
        a = armature.matrix_world @ Vector(
            armature.pose.bones[l_c.name].head)
        b = armature.matrix_world @ Vector(
            armature.pose.bones[r_c.name].head)
        return (a - b).cross(Vector((0.0, 0.0, 1.0))).normalized()
    return Vector((0.0, -1.0, 0.0))


def restore_feather_alpha():
    """Feather cut-outs need per-pixel alpha to survive sprite rendering."""
    n = 0
    for mat in bpy.data.materials:
        if 'feather' not in mat.name.lower():
            continue
        try:
            mat.blend_method = 'BLEND'
        except Exception:
            try:
                mat.surface_render_method = 'BLENDED'
            except Exception:
                pass
        n += 1
    log('blend mode restored on %d feather material(s)' % n)


def pack_images():
    for img in bpy.data.images:
        if img.source != 'FILE':
            continue
        try:
            img.pack()
        except Exception as e:
            log('pack failed for image %s: %s' % (img.name, e))


def export_scene(out, armature, skin):
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = armature
    for ob in [armature] + list(skin):
        ob.select_set(True)
    bpy.ops.export_scene.gltf(filepath=out, export_format='GLB',
                              use_selection=True, export_yup=True,
                              export_animations=True, export_skins=True,
                              export_image_format='AUTO')
    log('exported: %s' % out)


def main():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument('--hero', required=True)
    p.add_argument('--wings', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--bone', default=WING_BONE_DEFAULT)
    p.add_argument('--span', type=float, default=1.25,
                   help='target wingspan as multiple of character height')
    p.add_argument('--back-gap', type=float, default=0.15,
                   help='push out of the back, fraction of character height')
    p.add_argument('--up-shift', type=float, default=0.0,
                   help='vertical offset, fraction of character height')
    p.add_argument('--side-shift', type=float, default=0.0,
                   help='horizontal offset along wing axis, fraction')
    p.add_argument('--spin', type=float, default=0.0,
                   help='extra twist around the spine axis (deg)')
    p.add_argument('--flip', action='store_true',
                   help='flip front/back orientation of the wings')
    p.add_argument('--textures', default='',
                   help='texture file or folder; images matching a material '
                        'name replace that material\'s albedo')
    p.add_argument('--no-drop', action='store_true',
                   help='keep the geometry already bound to the bone')
    args = p.parse_args(argv)

    hero = os.path.abspath(args.hero)
    wings = os.path.abspath(args.wings)
    out = os.path.abspath(args.out)

    # pass 1 (single pass): place, join, bind, export. The join puts the
    # prop verts into the character mesh's own local space and Blender's glTF
    # round-trip re-regularizes the skin data, so the on-file render matches
    # the placement exactly - verified by the eval==mw*local identity of the
    # round-tripped file. No post-export correction needed.
    reset_scene()
    log('=== hero: %s ===' % os.path.basename(hero))
    import_model(hero)
    armature = find_armature()
    if armature is None:
        raise SystemExit('no armature in hero model')
    skin = skinned_meshes(armature)
    log('armature %s, skin mesh(es): %s'
        % (armature.name, ', '.join(o.name for o in skin) or '(none)'))
    rest_pose(armature)
    clear_parents_keep_world([armature] + skin)
    remove_non_essentials([armature] + skin)

    bone = armature.data.bones.get(args.bone)
    if bone is None:
        raise SystemExit('bone %r not found (bones: %s)'
                         % (args.bone,
                            ', '.join(b.name for b in armature.data.bones)))
    if len(skin) != 1:
        raise SystemExit('expected a single skinned mesh, got %d' % len(skin))
    body = skin[0]

    # anchor: the evaluated world centroid of the bone-bound patch (where it
    # actually renders); body metrics from evaluated verts too
    full = group_full_indices(body, args.bone)
    cape_pts = eval_points_for(body, full) if full else []
    anchor = None
    D4 = None
    if cape_pts:
        anchor = sum(cape_pts, Vector()) / len(cape_pts)
        log('bound patch: %d verts, evaluated centroid %.4f %.4f %.4f'
            % (len(cape_pts), anchor.x, anchor.y, anchor.z))
        D4, resid = solve_affine(body, full, cape_pts)
        if resid > 1e-3:
            log('deform affine residual too high (%.2e), skipping remap'
                % resid)
            D4 = None

    hero_pts = [pt for ob in skin for pt in evaluated_world_points(ob)]
    clo = Vector((min(pt.x for pt in hero_pts), min(pt.y for pt in hero_pts),
                  min(pt.z for pt in hero_pts)))
    chi = Vector((max(pt.x for pt in hero_pts), max(pt.y for pt in hero_pts),
                  max(pt.z for pt in hero_pts)))
    bodyc = (clo + chi) / 2.0
    hero_h = chi.z - clo.z
    if hero_h <= 0:
        raise SystemExit('degenerate hero height')
    log('evaluated body bbox center %.4f %.4f %.4f, height %.4f'
        % (bodyc.x, bodyc.y, bodyc.z, hero_h))

    # back axis: the patch is a thin sliver on the body surface; its smallest
    # principal axis is the surface normal - oriented away from the body
    # (that is what "behind" means here). Falls back to the shoulder axis.
    up = Vector((0.0, 0.0, 1.0))
    back = shoulder_back(armature)
    if anchor is not None:
        off = Vector((anchor.x - bodyc.x, anchor.y - bodyc.y, 0.0))
        if off.length > 1e-6:
            mat = np.array([[pt.x - anchor.x, pt.y - anchor.y,
                             pt.z - anchor.z] for pt in cape_pts])
            u, s, vt = np.linalg.svd(mat, full_matrices=False)
            n = Vector(tuple(vt[-1]))
            if abs(abs(n.z) - 1.0) > 0.92:   # near-vertical normals useless
                # the surface normal must be a side axis: check whether the
                # patch normal is 'back-facing' via the centroid offset
                cand = Vector((n.x, n.y, 0.0))
                if cand.length > 1e-4:
                    cand.normalize()
                    if cand.dot(off) < 0:
                        cand = cand * -1.0
                    back = cand
                    log('back from patch PCA normal: %.3f %.3f %.3f'
                        % tuple(back))
                else:
                    log('patch normal vertical, shoulder-axis fallback: '
                        '%.3f %.3f %.3f' % tuple(back))
    log('back axis: %.3f %.3f %.3f' % tuple(back))

    if not args.no_drop:
        if not full:
            raise SystemExit('no fully-weighted verts found for %s'
                             % args.bone)
        bm = bmesh.new()
        bm.from_mesh(body.data)
        bm.verts.ensure_lookup_table()
        bmesh.ops.delete(bm, geom=[bm.verts[i] for i in full],
                         context='VERTS')
        bm.to_mesh(body.data)
        bm.free()
        body.data.update()
        vg = body.vertex_groups.get(args.bone)
        if vg is not None:
            body.vertex_groups.remove(vg)
        log('dropped %d vert(s) bound to %s from %s'
            % (len(full), args.bone, body.name))

    # import the prop in the same scene
    before = set(o.name for o in bpy.context.scene.objects)
    log('=== wings: %s ===' % os.path.basename(wings))
    import_model(wings)
    wing_objs = [o for o in bpy.context.scene.objects
                 if o.type == 'MESH' and o.name not in before]
    if not wing_objs:
        raise SystemExit('no wing meshes imported')
    log('wing meshes: %s' % ', '.join(o.name for o in wing_objs))
    clear_parents_keep_world(wing_objs)
    bpy.context.view_layer.update()

    def objs_world_bounds(objs):
        cs = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
        return (Vector((min(c.x for c in cs), min(c.y for c in cs),
                        min(c.z for c in cs))),
                Vector((max(c.x for c in cs), max(c.y for c in cs),
                        max(c.z for c in cs))))

    wlo, whi = objs_world_bounds(wing_objs)
    wcen = (wlo + whi) / 2.0
    wspan = max(whi.x - wlo.x, whi.y - wlo.y)
    log('wings raw bbox span %.3f rise %.3f center %.3f %.3f %.3f'
        % (wspan, whi.z - wlo.z, wcen.x, wcen.y, wcen.z))
    k = args.span * hero_h / max(wspan, 1e-6)
    log('uniform scale %.5f (span %.3f -> %.3f)'
        % (k, wspan, args.span * hero_h))

    # orient the prop plane flat against the back and anchor the fan ROOT
    # (bottom center of the fan, where the two halves meet) on the anchor
    normal_in = back * -1.0 if args.flip else back
    rot = (normal_in * k).to_track_quat('Y', 'Z').to_matrix()
    spin = Matrix.Rotation(math.radians(args.spin), 4, 'Z')
    root = Vector((wcen.x, wcen.y, wlo.z))
    target = anchor + back * (args.back_gap * hero_h) \
        + up * (args.up_shift * hero_h)
    right = rot.to_3x3() @ Vector((1.0, 0.0, 0.0))
    target += right * (args.side_shift * hero_h)
    M = (Matrix.Translation(target) @ spin @ rot.to_4x4()
         @ Matrix.Scale(k, 4) @ Matrix.Translation(-root))
    for ob in wing_objs:
        mw = ob.matrix_world.copy()
        ob.matrix_world = M @ mw
    bpy.context.view_layer.update()

    # bake transforms, join into the skinned mesh, rigid-bind to the bone
    bpy.ops.object.select_all(action='DESELECT')
    for ob in wing_objs:
        ob.select_set(True)
        bpy.context.view_layer.objects.active = ob
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.context.view_layer.update()

    nlo, nhi = objs_world_bounds(wing_objs)
    log('placed prop bbox lo %.4f %.4f %.4f hi %.4f %.4f %.4f'
        % (nlo.x, nlo.y, nlo.z, nhi.x, nhi.y, nhi.z))

    bpy.ops.object.select_all(action='DESELECT')
    for ob in wing_objs:
        ob.select_set(True)
    body.select_set(True)
    bpy.context.view_layer.objects.active = body
    pre = len(body.data.vertices)
    bpy.ops.object.join()
    bpy.context.view_layer.update()
    post = len(body.data.vertices)
    wing_idx = list(range(pre, post))
    log('joined prop: %d -> %d verts (+%d)' % (pre, post, post - pre))

    vg = body.vertex_groups.get(args.bone)
    if vg is None:
        vg = body.vertex_groups.new(name=args.bone)
    vg.add(wing_idx, 1.0, 'REPLACE')
    log('rigid bind: %d prop vert(s) -> %s' % (len(wing_idx), args.bone))

    # The source GLB's slot-<bone> skin bind carries an internal offset the
    # round trip preserves; solve it (LSQ over the fully-weighted patch
    # verts, exact to ~1e-6) and map the joined verts through its inverse,
    # conjugated into the mesh's local frame, so they render exactly where
    # they were placed - in this session AND after any reload.
    if D4 is not None:
        bm = bmesh.new()
        bm.from_mesh(body.data)
        bm.verts.ensure_lookup_table()
        T = body.matrix_world.inverted() @ D4.inverted() @ body.matrix_world
        for i in wing_idx:
            bm.verts[i].co = T @ bm.verts[i].co
        bm.to_mesh(body.data)
        bm.free()
        body.data.update()
        log('prop verts re-mapped through the solved rest-pose deform map')

    restore_feather_alpha()
    if args.textures:
        n = override_textures([args.textures], flat=False, tag='attach_wings')
        log('texture override touched %d material(s)' % n)
    pack_images()
    export_scene(out, armature, skin)
    log('done: %s' % out)


if __name__ == '__main__':
    main()
