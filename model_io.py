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


def rebuild_materials():
    """Rebuild every material as a clean minimal Principled tree:
    diffuse TexImage -> Base Color, normal TexImage -> NormalMap -> Normal.
    Imported FBX/GLTF node soup (duplicate outputs, stale diffuse nodes,
    broken specular links) can shade the whole mesh black otherwise."""
    for mat in bpy.data.materials:
        if not getattr(mat, 'use_nodes', False) or not mat.node_tree:
            continue
        tree = mat.node_tree
        nm_node = next((x for x in tree.nodes if x.type == 'NORMAL_MAP'), None)
        normal = None
        diffuse = None
        if nm_node is not None:
            for lk in nm_node.inputs['Color'].links:
                img = getattr(lk.from_node, 'image', None)
                if img is not None:
                    normal = img
        for x in tree.nodes:
            if x is nm_node or x.type != 'TEX_IMAGE':
                continue
            img = getattr(x, 'image', None)
            if img is None or img.users == 0 or img is normal:
                continue
            if diffuse is None:
                diffuse = img
        # the importer's own Base Color wiring is ground truth for which
        # texture is the albedo (guessing by order picks masks/ORM maps)
        orig = next((x for x in tree.nodes if x.type == 'BSDF_PRINCIPLED'),
                    None)
        if orig is not None:
            for lk in orig.inputs['Base Color'].links:
                img = getattr(lk.from_node, 'image', None)
                if img is not None:
                    diffuse = img
                    break
        if diffuse is None and normal is None:
            continue
        tree.nodes.clear()
        out = tree.nodes.new('ShaderNodeOutputMaterial')
        bsdf = tree.nodes.new('ShaderNodeBsdfPrincipled')
        tree.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
        if diffuse is not None:
            dtex = tree.nodes.new('ShaderNodeTexImage')
            dtex.image = diffuse
            diffuse.colorspace_settings.name = 'sRGB'
            tree.links.new(dtex.outputs['Color'], bsdf.inputs['Base Color'])
        if normal is not None:
            stex = tree.nodes.new('ShaderNodeTexImage')
            stex.image = normal
            normal.colorspace_settings.name = 'Non-Color'
            nm = tree.nodes.new('ShaderNodeNormalMap')
            tree.links.new(stex.outputs['Color'], nm.inputs['Color'])
            tree.links.new(nm.outputs['Normal'], bsdf.inputs['Normal'])
        bsdf.inputs['Metallic'].default_value = 0.0
        bsdf.inputs['Roughness'].default_value = max(0.55, bsdf.inputs['Roughness'].default_value)
        alpha = bsdf.inputs.get('Alpha')
        if alpha is not None and not alpha.is_linked and alpha.default_value == 0.0:
            alpha.default_value = 1.0
        mat.blend_method = 'OPAQUE'
        print('[model_io] rebuilt material: %s' % mat.name)


def import_model(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == '.glb' or ext == '.gltf':
        # NOTE: disable_bone_shape must NOT be passed here - Blender 5.2's
        # glTF importer then builds the armature with an uncompensated unit
        # scale and the whole model imports 100x too small.
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == '.fbx':
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext == '.obj':
        bpy.ops.wm.obj_import(filepath=path)
    else:
        raise ValueError('unsupported format: ' + ext)
    fix_materials()
    rebuild_materials()


def strip_broken_texture_links():
    """FBX/GLTF sources often reference textures that are not embedded and
    have dead absolute paths (Mixamo normal/spec maps, Flare Mac paths).
    A missing image wired into Normal/Specular shades the whole mesh black.
    Strip every link whose texture image has no data and no file."""
    import bpy.path
    for mat in bpy.data.materials:
        if not getattr(mat, 'use_nodes', False) or not mat.node_tree:
            continue
        tree = mat.node_tree
        broken = set()
        for n in tree.nodes:
            img = getattr(n, 'image', None)
            if img is None:
                continue
            if img.has_data:
                continue
            try:
                _ = len(img.pixels)   # force decode of packed data
            except Exception:
                pass
            if img.has_data:
                continue
            try:
                fp = bpy.path.abspath(img.filepath) if img.filepath else ''
                if fp and os.path.isfile(fp):
                    continue
            except Exception:
                pass
            broken.add(n.name)
        if not broken:
            continue
        for lk in list(tree.links):
            if lk.from_node.name in broken or lk.to_node.name in broken:
                tree.links.remove(lk)
        for n in [n for n in tree.nodes
                  if n.type == 'NORMAL_MAP' and not n.inputs['Color'].is_linked]:
            tree.nodes.remove(n)
        print('[model_io] stripped broken texture links in %s' % mat.name)


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
            met = node.inputs.get('Metallic')
            if met is not None and not met.is_linked and met.default_value > 0.2:
                # metallic surfaces mirror the dim stage world -> sprites
                # shade black; keep a hint of glint but stay readable
                met.default_value = 0.15
                print('[model_io] clamped metallic on %s' % mat.name)
    strip_broken_texture_links()


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


def override_textures(texture_paths, flat=True, allow_glow=True,
                      tag='model_io'):
    """Force-replace each material's albedo with an external texture file
    matched by filename stem (cowboy_skinWAR2.png -> material
    cowboy_skinWAR2). Files with no matching material are skipped and
    logged. A "<stem>_e" sibling is wired as a glow layer ONLY when
    allow_glow (some "_e" exports are ID/mask atlases, not emission maps).
    Returns number of materials touched."""
    if not texture_paths:
        return 0
    texs = {}
    for p in texture_paths:
        if os.path.isfile(p) and p.lower().endswith(('.png', '.jpg', '.jpeg')):
            texs[os.path.splitext(os.path.basename(p))[0].lower()] = p
        elif os.path.isdir(p):
            for fn in sorted(os.listdir(p)):
                if fn.startswith('.'):
                    continue
                if fn.lower().endswith(('.png', '.jpg', '.jpeg')):
                    texs[os.path.splitext(fn)[0].lower()] = os.path.join(p, fn)
    if not texs:
        print('[%s] override_textures: no image files found' % tag)
        return 0
    touched = 0
    for mat in bpy.data.materials:
        if not getattr(mat, 'use_nodes', False) or not mat.node_tree:
            continue
        bsdf = next((nd for nd in mat.node_tree.nodes
                     if nd.type == 'BSDF_PRINCIPLED'), None)
        if bsdf is None:
            continue
        stem = mat.name.lower()
        albedo_p = texs.get(stem)
        glow_p = (texs.get(stem + '_e') or texs.get(stem + '_emission')
                  if allow_glow else None)
        if albedo_p is None and glow_p is None:
            continue
        tree = mat.node_tree

        def tex_node(path):
            node = tree.nodes.new('ShaderNodeTexImage')
            node.image = bpy.data.images.load(path)
            node.image.colorspace_settings.name = 'sRGB'
            return node

        albedo_tex = tex_node(albedo_p) if albedo_p else None
        glow_tex = tex_node(glow_p) if glow_p else None
        base = bsdf.inputs['Base Color']
        for lk in list(base.links):
            tree.links.remove(lk)
        base.default_value = (0.0, 0.0, 0.0, 1.0)
        em_col = bsdf.inputs.get('Emission Color')
        em_str = bsdf.inputs.get('Emission Strength')
        for lk in list(em_col.links):
            tree.links.remove(lk)
        if flat:
            # flat mode: everything renders through emission
            if em_str is not None and not em_str.is_linked:
                em_str.default_value = 1.0
            if albedo_tex and glow_tex:
                mix = tree.nodes.new('ShaderNodeMix')
                mix.data_type = 'RGBA'
                mix.blend_type = 'ADD'
                mix.inputs['Factor'].default_value = 1.0
                tree.links.new(albedo_tex.outputs['Color'],
                               mix.inputs[6])
                tree.links.new(glow_tex.outputs['Color'], mix.inputs[7])
                tree.links.new(mix.outputs[2], em_col)
                print('[%s] override %s: albedo+glow -> %s'
                      % (tag, os.path.basename(albedo_p), mat.name))
            elif albedo_tex:
                tree.links.new(albedo_tex.outputs['Color'], em_col)
                print('[%s] override %s: albedo -> %s'
                      % (tag, os.path.basename(albedo_p), mat.name))
            else:
                tree.links.new(glow_tex.outputs['Color'], em_col)
                print('[%s] override %s: glow only -> %s'
                      % (tag, os.path.basename(glow_p), mat.name))
        else:
            if albedo_tex:
                tree.links.new(albedo_tex.outputs['Color'], base)
                base.default_value = (0.8, 0.8, 0.8, 1.0)
            if glow_tex and em_col is not None:
                tree.links.new(glow_tex.outputs['Color'], em_col)
                if em_str is not None and not em_str.is_linked:
                    em_str.default_value = 1.0
            print('[%s] override %s (lit mode) -> %s'
                  % (tag, os.path.basename(albedo_p or glow_p), mat.name))
        touched += 1
    return touched


def safe_name(s):
    import re
    return re.sub(r'[^\w\-.]', '_', s.strip())
