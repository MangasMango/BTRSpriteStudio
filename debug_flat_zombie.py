import bpy, sys
sys.path.insert(0, r"C:\repos\3dtoIsometric")
import convert, model_io
from mathutils import Vector
convert.reset_scene()
model_io.import_model(r"C:\repos\3dtoIsometric\models\enemy\zombie.glb")
print("MATS before flat:")
for mat in bpy.data.materials:
    if not getattr(mat, "use_nodes", False) or not mat.node_tree:
        print("  %s: no nodes" % mat.name); continue
    for n in mat.node_tree.nodes:
        if n.type == "TEX_IMAGE" and n.image:
            img = n.image
            try:
                px = list(img.pixels[0:400])
                avg = sum(px[0::4][:10]) / 10.0
            except Exception as e:
                avg = -1
            print("  %s: TEX %s size=%dx%d has_data=%s avg_r=%.3f" % (mat.name, img.name, img.size[0], img.size[1], img.has_data, avg))
        elif n.type == "BSDF_PRINCIPLED":
            bc = n.inputs["Base Color"]
            print("  %s: PRINCIPLED base linked=%s val=%s" % (mat.name, bc.is_linked, [round(v,3) for v in bc.default_value]))
        elif n.type == "OUTPUT_MATERIAL":
            print("  %s: OUTPUT" % mat.name)
convert.flat_materials()
print("AFTER FLAT:")
for mat in bpy.data.materials:
    if not getattr(mat, "use_nodes", False) or not mat.node_tree:
        continue
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        print("  %s: no principled!" % mat.name); continue
    em_col = bsdf.inputs["Emission Color"]
    em_str = bsdf.inputs["Emission Strength"]
    bc = bsdf.inputs["Base Color"]
    print("  %s: em linked=%s val=%s strength=%.2f base linked=%s" % (mat.name, em_col.is_linked, [round(v,3) for v in em_col.default_value], em_str.default_value, bc.is_linked))
    if em_col.is_linked:
        for lk in em_col.links:
            n = lk.from_node
            img = getattr(n, "image", None)
            print("    from %s img=%s" % (n.type, img.name if img else "-"))
