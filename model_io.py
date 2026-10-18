# Copyright (C) 2026 Stamatis Valis
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Shared Blender-side model I/O used by generate.py and inspect_model.py:
# scene reset, model import, material fixes, texture discovery/application,
# world-space bounding boxes.

import os

import bpy
from mathutils import Vector

MODEL_EXTS = ('.glb', '.gltf', '.fbx', '.obj')


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
                print('[model_io] fixed zero-alpha material: %s' % mat.name)


def world_bbox(objects):
    pts = []
    for ob in objects:
        for corner in ob.bound_box:
            pts.append(ob.matrix_world @ Vector(corner))
    if not pts:
        return Vector((0, 0, 0)), Vector((0, 0, 0))
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


def apply_textures(texture_paths, tag='model_io'):
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
        print('[%s]   applied texture %s -> material %s'
              % (tag, os.path.basename(pick), mat.name))
    return touched


def safe_name(s):
    import re
    return re.sub(r'[^\w\-.]', '_', s.strip())
