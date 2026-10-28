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
# Convert legacy .blend sources into game-ready GLB/FBX for the sprite
# pipeline: opens each blend headless, sanitizes objects, converts old
# material graphs to Principled BSDF, relinks packed/missing textures,
# joins the picked meshes and exports with textures embedded.
#
# Usage (inside Blender):
#   blender --background --python blend2asset.py -- \
#       --src "<blend file or folder>" [--out "C:\...\3dtoIsometric\models"] \
#       [--category "playable character"] [--name armoredknight] \
#       [--keep NAME1,NAME2] [--drop NAME1,NAME2] [--loose <texture folder>] \
#       [--dry-run] [--report "path.json"]

import argparse
import json
import os
import re
import sys

import bpy

NORMAL_RE = re.compile(r'normal|nmap|nrm', re.I)
SKIP_RE = re.compile(r'rough|spec|metal|occ|height|displace|heights|emition|mask', re.I)


def log(*a):
    print('[blend2asset]', *a)


def count_tris(mesh):
    return sum(len(p.vertices) - 2 for p in mesh.polygons)


def open_blend(path):
    bpy.ops.wm.open_mainfile(filepath=path)
    if bpy.context.view_layer.objects.active is None:
        objs = list(bpy.context.view_layer.objects)
        if objs:
            bpy.context.view_layer.objects.active = objs[0]
    ob = bpy.context.active_object
    if ob is not None and ob.mode != 'OBJECT':
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception as e:
            log('mode_set OBJECT failed: %s' % e)


def image_exists(img):
    if img.packed_file is not None:
        return True
    fp = img.filepath
    if not fp:
        return False
    try:
        return os.path.exists(bpy.path.abspath(fp, library=img.library))
    except Exception:
        return False


def gather_images(mat):
    out = []
    if mat.use_nodes and mat.node_tree:
        for n in mat.node_tree.nodes:
            if n.type == 'TEX_IMAGE' and n.image:
                img = n.image
                out.append({
                    'image': img,
                    'name': img.name,
                    'packed': img.packed_file is not None,
                    'exists': image_exists(img),
                    'filepath': img.filepath or '',
                })
    return out


def inventory():
    objects = []
    for ob in bpy.data.objects:
        e = {
            'name': ob.name,
            'type': ob.type,
            'visible_render': not ob.hide_render,
            'materials': [m.name for m in getattr(ob.data, 'materials', []) if m],
        }
        if ob.type == 'MESH':
            e['tris'] = count_tris(ob.data)
            e['vert_count'] = len(ob.data.vertices)
        objects.append(e)
    materials = []
    for mat in bpy.data.materials:
        materials.append({
            'name': mat.name,
            'use_nodes': bool(getattr(mat, 'use_nodes', False)),
            'diffuse_color': [round(c, 3) for c in getattr(mat, 'diffuse_color', (0, 0, 0, 1))[:3]],
            'images': [{'name': i['name'], 'packed': i['packed'],
                        'exists': i['exists'], 'filepath': i['filepath']}
                       for i in gather_images(mat)],
        })
    return {'objects': objects, 'materials': materials}


def loose_matches(loose_dir):
    out = []
    if not loose_dir or not os.path.isdir(loose_dir):
        return out
    for fn in sorted(os.listdir(loose_dir)):
        base, ext = os.path.splitext(fn)
        if ext.lower() in ('.png', '.jpg', '.jpeg', '.exr'):
            out.append(os.path.join(loose_dir, fn))
    return out


def relink_missing_image(img, loose):
    base = os.path.splitext(os.path.basename(img.filepath or img.name))[0]
    stem = re.sub(r'[^a-z0-9]', '', base.lower())
    best = None
    for p in loose:
        fn_stem = re.sub(r'[^a-z0-9]', '', os.path.splitext(os.path.basename(p))[0].lower())
        if fn_stem and (fn_stem in stem or stem in fn_stem):
            best = p
            if fn_stem == stem:
                break
    if best:
        img.filepath = best
        try:
            img.reload()
        except Exception:
            pass
        return best
    return None


def texture_role(img_name):
    base = os.path.splitext(img_name)[0]
    if NORMAL_RE.search(base):
        return 'normal'
    if re.search(r'metal', base, re.I):
        return 'metallic'
    if re.search(r'rough', base, re.I):
        return 'roughness'
    if SKIP_RE.search(base):
        return 'skip'
    return 'diffuse'


def convert_material(mat, loose):
    """Make the material render correctly in EEVEE with a Principled BSDF:
    one diffuse texture -> Base Color, one normal texture -> Normal,
    everything else dropped. Returns (warnings, fixed)."""
    warnings = []
    fixed = False
    images = gather_images(mat)

    for info in images:
        if not info['exists'] and not info['packed']:
            p = relink_missing_image(info['image'], loose)
            if p:
                info['exists'] = True
                fixed = True
                warnings.append('relinked %s -> %s' % (info['name'], os.path.basename(p)))
            else:
                warnings.append('missing texture: %s' % info['name'])

    if not mat.use_nodes or not mat.node_tree:
        tree = bpy.data.node_groups.new('NT_' + mat.name, 'ShaderNodeTree')
        mat.node_tree = tree
        mat.use_nodes = True
        out = tree.nodes.new('ShaderNodeOutputMaterial')
        bsdf = tree.nodes.new('ShaderNodeBsdfPrincipled')
        tree.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
        color = tuple(mat.diffuse_color[:3]) if hasattr(mat, 'diffuse_color') else (0.5, 0.5, 0.5)
        bsdf.inputs['Base Color'].default_value = (color[0], color[1], color[2], 1.0)
        img_nodes = gather_images(mat)
        for info in img_nodes:
            info['image'].colorspace_settings.name = 'sRGB'
            node = tree.nodes.new('ShaderNodeTexImage')
            node.image = info['image']
            tree.links.new(node.outputs['Color'], bsdf.inputs['Base Color'])
            warnings.append('legacy BI material converted: %s' % mat.name)
            break
        else:
            if not wire_vertex_color(mat, bsdf):
                warnings.append('material has no texture and no vertex '
                                'colors: %s' % mat.name)
        mat.roughness = 0.9
        return warnings, True

    tree = mat.node_tree
    # Legacy 2.7x-era files open with node soup: duplicated OUTPUT_MATERIAL
    # nodes, a stale BSDF_DIFFUSE next to a Principled, dead texture nodes.
    # The glTF exporter fails to convert that and emits a bare default
    # material (white + metallic=1 -> renders black). Rebuild a minimal,
    # deterministic tree instead.
    tree.nodes.clear()
    out = tree.nodes.new('ShaderNodeOutputMaterial')
    bsdf = tree.nodes.new('ShaderNodeBsdfPrincipled')
    tree.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])

    used = []
    for info in images:
        img = info['image']
        if not info['exists']:
            continue
        role = texture_role(info['name'])
        if role == 'skip':
            continue
        slot = {'normal': 'Normal', 'diffuse': 'Base Color',
                'metallic': 'Metallic', 'roughness': 'Roughness'}.get(role)
        if slot is None or slot in used:
            continue
        is_normal = role == 'normal'
        img.colorspace_settings.name = 'Non-Color' if role != 'diffuse' else 'sRGB'
        node = tree.nodes.new('ShaderNodeTexImage')
        node.image = img
        if is_normal:
            nm = tree.nodes.new('ShaderNodeNormalMap')
            tree.links.new(node.outputs['Color'], nm.inputs['Color'])
            tree.links.new(nm.outputs['Normal'], bsdf.inputs['Normal'])
        else:
            if bsdf.inputs[slot].is_linked:
                for lk in list(bsdf.inputs[slot].links):
                    tree.links.remove(lk)
            tree.links.new(node.outputs['Color'], bsdf.inputs[slot])
        used.append(slot)
        fixed = True

    base = bsdf.inputs['Base Color']
    if not base.is_linked:
        if not wire_vertex_color(mat, bsdf):
            color = tuple(mat.diffuse_color[:3]) if hasattr(mat, 'diffuse_color') else (0.5, 0.5, 0.5)
            base.default_value = (color[0], color[1], color[2], 1.0)
    if 'Metallic' in bsdf.inputs:
        bsdf.inputs['Metallic'].default_value = 0.0
    if 'Roughness' in bsdf.inputs:
        bsdf.inputs['Roughness'].default_value = max(0.55, bsdf.inputs['Roughness'].default_value)
    alpha = bsdf.inputs.get('Alpha')
    if alpha is not None and not alpha.is_linked and alpha.default_value == 0.0:
        alpha.default_value = 1.0
    mat.blend_method = 'OPAQUE'
    return warnings, fixed


def pick_meshes(keep, drop):
    keep = [k.lower() for k in keep]
    drop = [d.lower() for d in drop]
    meshes = []
    for ob in bpy.data.objects:
        if ob.type != 'MESH':
            continue
        if drop and any(d in ob.name.lower() for d in drop):
            continue
        if keep and not any(k in ob.name.lower() for k in keep):
            continue
        meshes.append(ob)
    return meshes


def pick_armature(keep, drop, meshes):
    """Flare-style sources: the armature named in --keep travels with the
    meshes (original skinning + weights preserved). Returns None when no
    armature is requested."""
    keep = [k.lower() for k in keep]
    drop = [d.lower() for d in drop]
    for ob in bpy.data.objects:
        if ob.type != 'ARMATURE':
            continue
        if drop and any(d in ob.name.lower() for d in drop):
            continue
        if keep and not any(k in ob.name.lower() for k in keep):
            continue
        return ob
    if not meshes:
        return None
    for ob in meshes:
        par = ob.find_armature()
        if par is not None:
            return par
    return None


def mat_has_vertex_colors(mat):
    for ob in bpy.data.objects:
        if ob.type != 'MESH':
            continue
        me = ob.data
        if me is not None and mat.name in me.materials and len(me.color_attributes):
            return True
    return False


def wire_vertex_color(mat, bsdf):
    for ob in bpy.data.objects:
        if ob.type != 'MESH':
            continue
        me = ob.data
        if me is None or mat.name not in me.materials or not len(me.color_attributes):
            continue
        attr = me.color_attributes.active_color or me.color_attributes[0]
        try:
            node = mat.node_tree.nodes.new('ShaderNodeVertexColor')
            node.layer_name = attr.name
            mat.node_tree.links.new(node.outputs['Color'],
                                    bsdf.inputs['Base Color'])
            log('vertex colors wired: %s <- %s' % (mat.name, attr.name))
            return True
        except Exception as e:
            log('vertex color wiring failed on %s: %s' % (mat.name, e))
            return False
    return False


def unpack_for_export(dst_skins, loose):
    """Make sure every used image has a real file so GLB/FBX embeds it."""
    os.makedirs(dst_skins, exist_ok=True)
    saved, failed = 0, []
    for img in bpy.data.images:
        if img.name.startswith('Render Result') or img.name.startswith('Viewer Node'):
            continue
        if img.name.startswith('BTRGrunge') or img.name.startswith('BTRSpeck'):
            # never write them into skins/ - generate.py's find_texture_images
            # would apply them over every flat material and wash out the palette
            continue
        if img.users == 0:
            continue
        if image_exists(img):
            continue
        if img.packed_file is not None:
            fn = re.sub(r'[^\w\-.]', '_', os.path.splitext(img.name)[0])
            fn += '.png' if not img.packed_file.filepath or img.packed_file.filepath.lower().endswith('.png') else os.path.splitext(img.packed_file.filepath)[1]
            out_path = os.path.join(dst_skins, fn)
            try:
                img.filepath_raw = out_path
                img.filepath = out_path
                img.save()
                img.reload()
                saved += 1
            except Exception as e:
                failed.append('%s: %s' % (img.name, e))
        elif not img.filepath:
            fn = re.sub(r'[^\w\-.]', '_', os.path.splitext(img.name)[0]) + '.png'
            out_path = os.path.join(dst_skins, fn)
            try:
                img.filepath_raw = out_path
                img.filepath = out_path
                img.save()
                img.reload()
                saved += 1
            except Exception as e:
                failed.append('%s: %s' % (img.name, e))
    return saved, failed


def force_rest_pose(armature):
    """Flare sources are often saved mid-animation. Bone-parented children
    must be captured against the REST pose (what the GLB skin binds to),
    or their world transforms end up offset by the pose-vs-rest delta."""
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


def nearest_bone_name(armature, point):
    """Rigid props: bind to the bone segment closest to the mesh center."""
    mw = armature.matrix_world
    best, best_d = None, None
    for pb in armature.pose.bones:
        b = pb.bone
        h = mw @ b.head_local
        t = mw @ b.tail_local
        ab = t - h
        denom = ab.dot(ab)
        if denom < 1e-12:
            d = (point - h).length
        else:
            tt = max(0.0, min(1.0, (point - h).dot(ab) / denom))
            d = (point - (h + ab * tt)).length
        if best_d is None or d < best_d:
            best, best_d = b.name, d
    return best


def bake_rigid_bind(ob, armature, bone_name):
    """Give a rigid child mesh a single-bone vertex group so it exports as
    a proper skinned mesh rigidly following that bone."""
    vg = ob.vertex_groups.get(bone_name)
    if vg is None:
        vg = ob.vertex_groups.new(name=bone_name)
    vg.add(list(range(len(ob.data.vertices))), 1.0, 'REPLACE')
    log('rigid bind baked: %s -> %s' % (ob.name, bone_name))


def grunge_image():
    """Deterministic fine speckle texture multiplied into flat Base Colors,
    so textureless (vertex/flat-color) Flare materials read as surfaces."""
    img = bpy.data.images.get('BTRGrunge')
    if img is not None:
        return img
    import random
    random.seed(7)
    n = 256
    img = bpy.data.images.new('BTRGrunge', n, n)
    px = []
    for _y in range(n):
        for _x in range(n):
            v = 0.70 + 0.30 * random.random()
            px.extend((v, v, v, 1.0))
    img.pixels = px
    return img


def tinted_speckle_image(mat, rgb):
    """Per-material speckle texture with the palette color baked in:
    pixel = color * (0.70..1.00). Exports as a plain TexImage->Base Color
    chain (glTF-friendly, valid texCoord) and keeps the rot-tone palette."""
    import random
    name = 'BTRSpeck_' + re.sub(r'[^\w]', '_', mat.name)
    img = bpy.data.images.get(name)
    if img is not None:
        return img
    random.seed(hash(mat.name) & 0xffff)
    n = 256
    img = bpy.data.images.new(name, n, n)
    px = []
    for _y in range(n):
        for _x in range(n):
            v = 0.70 + 0.30 * random.random()
            px.extend((rgb[0] * v, rgb[1] * v, rgb[2] * v, 1.0))
    img.pixels = px
    return img


def wire_grunge(mat):
    """Give an unlinked Base Color a baked-tint speckle texture."""
    if not mat.use_nodes or not mat.node_tree:
        return False
    bsdf = next((n for n in mat.node_tree.nodes
                 if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is None:
        return False
    base = bsdf.inputs['Base Color']
    if base.is_linked:
        return False
    rgb = tuple(base.default_value)[:3]
    if rgb == (1.0, 1.0, 1.0):
        return False   # untouched material - not part of the palette spec
    tree = mat.node_tree
    tex = tree.nodes.new('ShaderNodeTexImage')
    tex.image = tinted_speckle_image(mat, rgb)
    tree.links.new(tex.outputs['Color'], base)
    return True


def apply_material_colors(spec):
    """--material-color "Mat=R,G,B;Mat2=R,G,B": override a material's Base
    Color after conversion (palette matching / tone themeing)."""
    warnings = []
    for part in spec.split(';'):
        part = part.strip()
        if not part:
            continue
        name, _, rgb = part.partition('=')
        mat = bpy.data.materials.get(name.strip())
        if mat is None:
            warnings.append('material color: no material %r' % name.strip())
            continue
        try:
            vals = [max(0.0, min(1.0, float(v))) for v in rgb.split(',')]
            if len(vals) < 3:
                raise ValueError('need R,G,B')
        except ValueError as e:
            warnings.append('material color: bad spec %r (%s)' % (part, e))
            continue
        if not mat.use_nodes or not mat.node_tree:
            mat.diffuse_color = (vals[0], vals[1], vals[2], 1.0)
            continue
        bsdf = next((n for n in mat.node_tree.nodes
                     if n.type == 'BSDF_PRINCIPLED'), None)
        if bsdf is None:
            warnings.append('material color: no principled in %s' % mat.name)
            continue
        base = bsdf.inputs['Base Color']
        for lk in list(base.links):
            mat.node_tree.links.remove(lk)
        base.default_value = (vals[0], vals[1], vals[2], 1.0)
        warnings.append('material color: %s -> (%.2f, %.2f, %.2f)'
                        % (mat.name, vals[0], vals[1], vals[2]))
    return warnings


def ensure_uvs(meshes):
    """Textureless Flare sources may have no UV map at all; generate one so
    the grunge multiply can be exported with a valid texCoord."""
    fixed = 0
    for ob in meshes:
        if ob.data.uv_layers:
            continue
        bpy.ops.object.select_all(action='DESELECT')
        ob.select_set(True)
        bpy.context.view_layer.objects.active = ob
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        try:
            bpy.ops.uv.smart_project(angle_limit=1.15192, island_margin=0.01)
            fixed += 1
        except Exception as e:
            log('smart_project failed on %s: %s' % (ob.name, e))
        bpy.ops.object.mode_set(mode='OBJECT')
    if fixed:
        log('generated UVs for %d mesh(es)' % fixed)
    return fixed


def sanitize(meshes, armature=None):
    """Clear parents keeping world transform, drop non-mesh leftovers.
    With an armature kept, meshes stay bound to it (bone parents are
    converted to object parents + explicit armature modifiers)."""
    keep = set(id(m) for m in meshes)
    if armature is not None:
        keep.add(id(armature))
    for ob in list(bpy.data.objects):
        if ob.type == 'MESH' and ob in meshes:
            continue
        if armature is not None and ob is armature:
            continue
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
        except Exception:
            pass
    for ob in meshes:
        ob.hide_render = False
        ob.hide_viewport = False
        if armature is not None:
            skinned = any(v.groups for v in ob.data.vertices)
            bone = getattr(ob, 'parent_bone', '')
            if skinned:
                pass   # already bound via vertex groups
            else:
                if bone and bone in armature.data.bones:
                    target = bone
                else:
                    me = ob.matrix_world
                    center = sum(
                        (me @ Vector(c) for c in ob.bound_box), Vector()) / 8.0
                    target = nearest_bone_name(armature, center)
                bake_rigid_bind(ob, armature, target)
            if ob.parent is armature and not getattr(ob, 'parent_bone', ''):
                pass   # already cleanly bound via vertex groups
            else:
                mw = ob.matrix_world.copy()
                ob.parent = armature
                ob.parent_type = 'OBJECT'
                ob.parent_bone = ''
                ob.matrix_parent_inverse = armature.matrix_world.inverted()
                ob.matrix_world = mw
                mod = next((m for m in ob.modifiers if m.type == 'ARMATURE'), None)
                if mod is None:
                    mod = ob.modifiers.new('Armature', 'ARMATURE')
                mod.object = armature
        elif ob.parent:
            mw = ob.matrix_world.copy()
            ob.parent = None
            ob.matrix_world = mw
    bpy.context.view_layer.update()
    return meshes


def join_and_origin(meshes, name):
    meshes = sorted(meshes, key=lambda o: -count_tris(o.data))
    bpy.ops.object.select_all(action='DESELECT')
    for ob in meshes:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    pts = [obj.matrix_world @ __import__('mathutils').Vector(c) for c in obj.bound_box]
    cx = sum(p.x for p in pts) / len(pts)
    cy = sum(p.y for p in pts) / len(pts)
    minz = min(p.z for p in pts)
    obj.location.x -= cx
    obj.location.y -= cy
    obj.location.z -= minz
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    obj.name = name
    bpy.data.objects[name].data.name = name
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    return obj


def export_model(obj, out_path, embed_fbx_path=None, extra=None):
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    for ob in extra or []:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = obj
    if out_path.lower().endswith('.glb'):
        try:
            bpy.ops.export_scene.gltf(filepath=out_path, export_format='GLB',
                                      use_selection=True, export_yup=True)
            return out_path
        except Exception as e:
            log('GLB export failed (%s), falling back to FBX' % e)
    bpy.ops.export_scene.fbx(filepath=embed_fbx_path or out_path, use_selection=True,
                             embed_textures=True, path_mode='COPY', add_leaf_bones=False,
                             apply_unit_scale=True, apply_scale_options='FBX_SCALE_ALL')
    return embed_fbx_path or out_path


def process_blend(path, args):
    log('=== %s ===' % os.path.basename(path))
    open_blend(path)
    report = {
        'source': path,
        'objects': [], 'materials': [],
        'picked': [], 'export': None, 'warnings': [],
    }
    report['objects'] = [{k: v for k, v in ob.items()} for ob in inventory()['objects']]
    mats_inv = inventory()['materials']
    report['materials'] = mats_inv

    keep = args.keep.split(',') if args.keep else []
    drop = args.drop.split(',') if args.drop else []
    meshes = pick_meshes(keep, drop)
    armature = pick_armature(keep, drop, meshes)
    report['picked'] = [o.name for o in meshes]
    if armature is not None:
        report['armature'] = armature.name
        force_rest_pose(armature)
        log('armature flow: keeping %s (original skinning preserved, '
            'rest pose forced)' % armature.name)
    if not meshes:
        report['warnings'].append('no mesh objects matched the pick')
        return report

    if args.dry_run:
        return report

    name = args.name or re.sub(r'[^\w\-]', '_', os.path.splitext(os.path.basename(path))[0])
    out_dir = os.path.join(args.out, args.category, name)
    dst_skins = os.path.join(out_dir, 'skins')
    os.makedirs(out_dir, exist_ok=True)

    loose = loose_matches(args.loose or os.path.dirname(path))
    for mat in bpy.data.materials:
        w, _ = convert_material(mat, loose)
        report['warnings'].extend(w)
    unpack_for_export(dst_skins, loose)

    meshes = sanitize(meshes, armature)
    ensure_uvs(meshes)
    report['warnings'].extend(apply_material_colors(args.material_color))
    if args.grunge:
        if any(ob.data.uv_layers for ob in meshes):
            wired = []
            for mat in bpy.data.materials:
                if wire_grunge(mat):
                    wired.append(mat.name)
            if wired:
                report['warnings'].append('grunge wired: %s' % ', '.join(wired))
        else:
            report['warnings'].append('grunge skipped: meshes have no UV map')
    if armature is not None:
        # origin at feet + centered on mesh mass: offset the ARMATURE so
        # parented meshes follow; strays get the same offset applied.
        from mathutils import Vector
        pts = []
        for ob in meshes:
            for c in ob.bound_box:
                pts.append(ob.matrix_world @ Vector(c))
        cx = sum(p.x for p in pts) / len(pts)
        cy = sum(p.y for p in pts) / len(pts)
        minz = min(p.z for p in pts)
        armature.location.x -= cx
        armature.location.y -= cy
        armature.location.z -= minz
        bpy.context.view_layer.update()
        for ob in meshes:
            if ob.parent is not armature:
                ob.location.x -= cx
                ob.location.y -= cy
                ob.location.z -= minz
        bpy.context.view_layer.update()
        glb = os.path.join(out_dir, name + '.glb')
        saved_path = export_model(armature, glb,
                                  os.path.join(out_dir, name + '.fbx'),
                                  extra=meshes)
        tris = sum(count_tris(o.data) for o in meshes)
    else:
        obj = join_and_origin(meshes, name)
        glb = os.path.join(out_dir, name + '.glb')
        saved_path = export_model(obj, glb, os.path.join(out_dir, name + '.fbx'))
        tris = count_tris(obj.data)
    report['export'] = {
        'path': saved_path,
        'tris': tris,
        'skins_dir': dst_skins,
        'glb': glb,
    }
    report['warnings'].append('exported %d tris -> %s' % (tris, saved_path))
    log('exported %d tris -> %s' % (tris, saved_path))
    return report


def main():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument('--src', required=True)
    p.add_argument('--out', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models'))
    p.add_argument('--category', default='playable character')
    p.add_argument('--name', default='')
    p.add_argument('--keep', default='')
    p.add_argument('--drop', default='')
    p.add_argument('--loose', default='')
    p.add_argument('--material-color', default='',
                   help='overrides "Mat=R,G,B;Mat2=R,G,B" applied to '
                        'Principled Base Color after conversion')
    p.add_argument('--grunge', action='store_true',
                   help='multiply a procedural speckle texture into every '
                        'untextured Base Color')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--report', default='')
    args = p.parse_args(argv)

    src = args.src
    if os.path.isdir(src):
        blends = [os.path.join(src, f) for f in sorted(os.listdir(src))
                  if f.lower().endswith('.blend')]
        blends = [b for b in blends
                  if not re.search(r'\.blend\d+$', b.lower())
                  and '(' not in os.path.basename(b)]
    else:
        blends = [src]

    reports = []
    for b in blends:
        try:
            r = process_blend(b, args)
        except Exception as e:
            r = {'source': b, 'error': str(e)}
        reports.append(r)
        log('done: %s' % os.path.basename(b))

    report_path = args.report or os.path.join(args.out, 'blend2asset_report.json')
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(reports, f, indent=2, default=str)
    log('report -> %s' % report_path)


main()
