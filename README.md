# 3dtoisometric

Blender-based batch generator that renders 3D models as isometric game
sprites for the ARPG project, plus **Sprite Studio** — a raylib GUI for
viewing, comparing, rendering and deploying those sprite sets.

## Usage

```
# 1. drop models into models/  (subfolder = category, e.g. models/prop/barrel.glb)
# 2. run:
.\run.ps1
# 3. find sprites in output/
# or open the GUI:
.\studio.ps1
```

Output layout matches the game exactly:

```
output/<category>/<model>/<model>_<clip>/<DIR>/<model>_<clip>_<DIR>_<angle>_<frame>.png   (animated)
output/<category>/<model>/<DIR>/<model>_<DIR>_<angle>_0.png                                (static prop)
```

- 16 compass directions (E, NEE, NE, ... at 22.5 deg steps), camera pitch 47.4
  deg (matches the game's 128/174 diamond ratio)
- Animation clips embedded in the model are rendered as frame sequences
  (default 8 frames @ 8 fps); models without clips get one frame per direction
- Formats: `.glb`, `.gltf`, `.fbx`, `.obj`; animation-only files are skipped
- A `manifest.json` describing the batch is written to `output/`

## Options

Pass through run.ps1 after the script args:

```
.\run.ps1 --frames 6 --fps 8 --size 256 --pitch 47.4 --padding 0.15 --yaw 45 --dirs E,N,SE --models <path> --output <path>
```

| Flag | Meaning |
|---|---|
| `--fixed-frame U` | render all models at one world scale: the ortho frame covers U world units (0 = auto-fit each model). Matches the game's asset scale; `--fixed-frame 20` reproduces arpg-project sprite sizes for Kenney-scaled models. Lower = bigger sprites. |
| `--no-shadow` | skip the soft blob shadow under the model |
| `--shadow-scale F` | blob shadow radius multiplier (default 1.0) |
| `--texture <path>` | force a specific texture image (default: auto-detect) |
| `--target fighter` | rigged character → the game's 7-clip set, authored procedurally on the rig (`rig_map.py` maps any humanoid skeleton), fighter layout, no shadow |
| `--char-px N` | frame so the character renders ≈ N px tall (fighter default 38) |
| `--manifests <dir>` | manifest mode: render only manifest characters with their own clip lists (see `manifests/*.json`, `.ai/studio-spec.md`) |
| `--characters A,B` | manifest mode: restrict to these characters |
| `--deploy <root>` | copy the rendered character folder into `<root>/<category>/<name>/` (game assets) |
| `--deploy-only A,B` | deploy filter: only these characters may deploy |

Textures found next to the model (or in `skins/` / `textures/` subfolders)
are applied automatically to untextured materials.

## Game characters (fighter mode)

```
.\run.ps1 --target fighter --deploy "C:\repos\bound-to-rot\assets"
```

Rigged humanoid models are posed procedurally (no animation FBXs needed):
idle/walk/attack/select/death beats measured against the reference pack, on
any mappable rig (Kenney / Mixamo / Unreal / Rigify naming, or a
`bone_map.json` override). **Direction convention** follows the game
(`iso_engine.MOVE_DIR`): S = front, N = back, E = right-facing profile.

## Sprite Studio (raylib GUI)

```
.\studio.ps1          # or --selftest for a headless screenshot run
```

- **Viewer** — play/scrub any clip × direction, fps slider, onion skin,
  zoom/pan, bbox overlay
- **Grid** — all 16 directions of a clip at a glance (direction sanity)
- **Compare** — frame-synced ours vs reference pack, blink toggle
- **Create** — upload an FBX, inspect the rig (mapping verdict + preview),
  pick an animation package, queue render + deploy
- **Queue / Deploy / Health** — Blender render queue with live log; filtered
  deploy with dry-run diff; contract dashboard (missing frames, token
  mismatches, stale renders)

## Animation packages & new characters

Every existing character's clip set is an atomic, transplantable package
(no clip mixing). Create a character from any rigged FBX:

```
.\run.ps1 --upload-model <file.fbx> --package skeleton --name myskeleton `
          --category enemy --deploy "C:\repos\bound-to-rot\assets" --register
```

- `--package` is repeatable → several exports from one upload
- packages available: fighter/knight/warrior (7 clips), skeleton (4),
  slime (3, 7-frame death), subservient (2), demonlord (7) — see
  `manifests/*.json` and [`.ai/packages-spec.md`](.ai/packages-spec.md)
- packages missing core clips (e.g. subservient has no idle/walk) export
  verbatim and carry a `GAP` flag — never auto-filled
- the rig is validated first (`inspect_model.py`): unmappable rigs are
  rejected with the exact missing bones before anything renders

The reproducible form is a recipe JSON
(`{"character", "category", "model", "package"}`, written by the studio):

```
.\run.ps1 --recipe <recipe.json> [--deploy <assets-root>]
```

## Animated characters from separate animation files (Kenney-style packs)

Packs like `kenney_animated-characters-retro` ship the character and each
animation as separate FBX files. Use the `anims/` convention and everything
merges automatically during `.\run.ps1`:

```
models/
  playable character/
    characterMedium/
      characterMedium.fbx     <- character (any supported format)
      anims/
        idle.fbx              <- animation-only files, same skeleton
        run.fbx
        jump.fbx
```

The animations must use the same skeleton (bone names) as the character.
Output: `output/playable character/characterMedium_idle/E/characterMedium_idle_E_0.0_0.png` etc.

To instead bake character + animations into a single GLB:

```
.\merge.ps1 <character-file> <anims-folder> <out.glb>
```

## Notes

- Blender's FBX/GLTF importers sometimes produce materials with alpha 0
  (invisible in renders); the script clamps those to opaque automatically.
- Models are rendered as-is; use `--yaw` if a model faces the wrong way.
- Blender version: any recent LTS with EEVEE Next (falls back to Cycles CPU);
  found automatically under `C:\Program Files\Blender Foundation`.
- Full design docs live in [.ai/spec.md](.ai/spec.md),
  [.ai/studio-spec.md](.ai/studio-spec.md) and
  [.ai/packages-spec.md](.ai/packages-spec.md).

## Contributing

Bug reports and feature suggestions are welcome. Please submit them at
https://github.com/MangasMango/3d-to-isometric/issues

If you wish to contribute code or improve the documentation, please create
an issue, or take an existing issue and ping me on it. It is usually better
to be aligned before submitting a pull request. All help is appreciated!

### Suggesting a feature

If you have an idea for a feature that you're missing, submit a request at
the issue tracker. Try to describe the feature, but don't go into too much
detail. If it gets picked up, we will hash out the details together.

### Reporting bugs

You can submit bug reports at the issue tracker. Be sure to include:

- **3dtoisometric version / commit**: the commit hash of your checkout
- **Operating system and version**: e.g. "Windows 11 64bit"
- **Blender version**: e.g. "Blender 5.2.0 LTS" (see `C:\Program Files\Blender Foundation`)
- **Exact steps to reproduce**: what you ran and what happened, including the
  console output of `run.ps1` / `studio.ps1`. Screenshots or the model file
  that misbehaves help a lot — a reproducible case is often deducible even
  if you cannot fully narrow it down.

### Submitting pull requests

Before you start working on a pull request, please file an issue or ping me
on an existing issue so we can discuss the approach and agree on it — this
significantly reduces churn and review effort later on.

- Split changes into individual commits, each adding a step that compiles
  and works on its own. The history should be clean, as if the work had
  been perfectly planned.
- Don't merge `master` into your PR; rebase your branch onto `master` instead.
- Add verification wherever possible (the pipeline's acceptance tests in
  `.ai/*-spec.md` show the expected style: rendered-output contract checks,
  visual comparisons).
- Respond to review comments promptly.

### AI-assisted contributions

The use of AI-assisted development tools is permitted. Contributors remain
solely responsible for all submitted code, including code generated or
modified with AI assistance. By submitting a contribution, you assert that:

- you have reviewed and understood the contribution,
- you are capable of explaining how it works,
- your contribution complies with this project's license requirements,
- your contribution does not knowingly incorporate incompatible third-party
  code,
- you accept responsibility for defects, security issues, and licensing
  problems in the submitted code.

Please do not submit code that you do not understand.

## License

Copyright (c) 2026 Stamatis Valis. This project is licensed under the
**GNU General Public License v3.0 or greater** — see [LICENSE](LICENSE) for
the full text (`SPDX-License-Identifier: GPL-3.0-or-later`).

The bundled sample assets under `models/playable character/` are from
Kenney's *Animated Characters (Retro)* pack, licensed CC0 1.0 (public domain
dedication).
See https://kenney.nl for the original packs.
