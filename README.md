# BTRSpriteStudio

Blender-based converter that renders rigged 3D models with animations as
16-direction isometric sprite folders for the ARPG project, plus
**BTRSpriteStudio** - a minimal raylib GUI (Convert / Queue / Viewer /
Deploy).

## The workflow

You bring the model - already rigged, already animated (e.g. rig + animate
on [Mixamo](https://www.mixamo.com), download each animation as its own
FBX). The tool just converts: it renders every animation as its own clip
folder, using the game's naming convention. No procedural animation, no
rigging, no bone mapping.

```powershell
# 1. Mixamo: upload model, rig, download animations one FBX each.
#    NAME EACH DOWNLOAD like the game clip it should be, e.g.:
#    armed_idle.fbx, armed_walk.fbx, armed_attack.fbx, default_idle.fbx,
#    default_walk.fbx, special_death.fbx, special_select.fbx
# 2. put the animation FBX files in a folder (next to or without the
#    base rigged FBX) and run:
.\run.ps1 --input "C:\path\to\model_or_folder" --category "playable character" --name warrior
#    --input accepts a single model file OR a folder: the first file is the
#    character, every further file contributes its animation (renamed to
#    the file stem).
# 3. add --deploy "C:\repos\bound-to-rot\assets" to copy straight into the
#    game, or use the Deploy screen in the GUI.
# or open the GUI:
.\studio.ps1
```

The GUI Convert screen wraps the same flow (file picker, name, category,
frames/px sliders, optional deploy checkbox).

## Output layout (matches the game exactly)

```
output/<category>/<name>/<name>_<clip>/<DIR>/<name>_<clip>_<DIR>_<angle>_<frame>.png   (animated)
output/<category>/<name>/<DIR>/<name>_<DIR>_<angle>_0.png                              (static prop)
```

- 16 compass directions (E, NEE, NE, ... at 22.5 degree steps), ortho
  camera pitch 30 deg (matches the game's 2:1 floor-tile diamond ratio,
  64x32; use --pitch 47.4 for the legacy Lords-of-Pain look)
- `--frames N` samples N frames evenly across each animation (default 8;
  0 = every frame). Name clips to match what the game loader expects
  (see `core/game.py` `_build_load_steps` for frame counts/fps)
- formats: `.glb`, `.gltf`, `.fbx`, `.obj`
- textures found next to the model (or in `skins/`, `textures/`) are
  applied automatically to untextured materials

## Options

```
.\run.ps1 --input <file-or-folder> [--out <dir>] [--category <cat>] [--name <n>]
          [--frames 8] [--char-px 50] [--size 256] [--pitch 30]
          [--dirs E,S] [--clips walk,attack] [--shadow]
          [--deploy <game assets root>]
```

| Flag | Meaning |
|---|---|
| `--char-px N` | frame so the character renders ~N px tall (default 50; 0 = bbox fit) |
| `--frames 0` | render every frame of each animation instead of sampling 8 |
| `--dirs / --clips` | restrict rendering to specific directions / animations |
| `--shadow` | bake the soft blob shadow into the sprite (default off; the game engine draws its own entity shadows) |
| `--deploy <root>` | after rendering, copy `output/<category>/<name>/` into `<root>/<category>/<name>/` |

## Supporting scripts

- `blend2asset.py` + `convert.ps1` - convert legacy .blend sources (piacenti
  style, or the Flare project's art sources) into clean GLB/FBX models that
  can be uploaded to Mixamo for rigging/animation, with material/texture
  fixes, armature preservation and rigid weapon binding.
- `model_io.py` - shared Blender-side import/bbox/texture utilities.
- `legacy/` - the old procedural pipeline (Rigify auto-rigging, procedural
  animation clips, manifest/recipe packages) kept for reference; the knight
  and skeleton sprite sets in the game were produced with it.

## Game conventions

Sprites are read by `bound-to-rot` (`core/game.py`): a sprite set for
`assets/<category>/<name>/` expects one folder per clip named
`<name>_<clip>`, each containing 16 direction folders with
`<name>_<clip>_<DIR>_<angle>_<frame>.png` PNGs. The converter emits this
verbatim, so deployment is a straight folder copy.
