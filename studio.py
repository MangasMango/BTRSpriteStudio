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
# BTRSpriteStudio - raylib GUI for the 3dtoIsometric pipeline (see
# .ai/studio-spec.md): browse manifest characters/clips/dirs, play clips,
# 16-dir grid, compare against reference sets, queue Blender renders,
# filtered deploy into the game assets, contract health checks.
#
# Launch:  uv run --with pyray python studio.py

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import queue as _queue
from collections import deque

import pyray as rl

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST_DIR = os.path.join(HERE, 'manifests')
OUTPUT_DIR = os.path.join(HERE, 'output')
GAME_ASSETS = os.path.normpath(os.path.join(HERE, '..', 'bound-to-rot',
                                            'assets'))
GENERATOR = os.path.join(HERE, 'generate.py')
BLENDER_GLOB = r'C:\Program Files\Blender Foundation\*\blender.exe'

DIRS = ['E', 'NEE', 'NE', 'NNE', 'N', 'NNW', 'NW', 'NWW',
        'W', 'SWW', 'SW', 'SSW', 'S', 'SSE', 'SE', 'SEE']
DIR_ANGLE = {
    'E': 0.0, 'NEE': 22.5, 'NE': 45.0, 'NNE': 67.5,
    'N': 90.0, 'NNW': 112.5, 'NW': 135.0, 'NWW': 157.5,
    'W': 180.0, 'SWW': 202.5, 'SW': 225.0, 'SSW': 247.5,
    'S': 270.0, 'SSE': 292.5, 'SE': 315.0, 'SEE': 337.5,
}
FRAME_RE = re.compile(
    r'^(?P<base>.+)_(?P<dir>NWW|SWW|NNE|NNW|SSE|SSW|SEE|NEE|'
    r'NE|NW|SE|SW|N|S|E|W)_(?P<angle>\d+\.\d+)_(?P<frame>\d+)\.png$')

SCREENS = [('VIEW', 'Viewer'), ('GRID', 'Grid'), ('COMPARE', 'Compare'),
           ('CREATE', 'Create'), ('QUEUE', 'Queue'), ('DEPLOY', 'Deploy'),
           ('HEALTH', 'Health')]

CATEGORIES = ['playable character', 'enemy', 'none-playable character',
              'boss']

COL_BG      = rl.Color(24, 24, 28, 255)
COL_PANEL   = rl.Color(34, 34, 40, 255)
COL_LINE    = rl.Color(60, 60, 70, 255)
COL_TEXT    = rl.Color(220, 220, 225, 255)
COL_DIM     = rl.Color(140, 140, 150, 255)
COL_SEL     = rl.Color(70, 110, 170, 255)
COL_OK      = rl.Color(90, 190, 110, 255)
COL_WARN    = rl.Color(230, 180, 70, 255)
COL_BAD     = rl.Color(220, 90, 90, 255)
COL_ACCENT  = rl.Color(90, 160, 230, 255)

# Alagard pixel font - only renders correctly at its native pixel sizes;
# every UI text size is snapped up to the nearest legal size.
FONT_PATH = os.path.join(HERE, 'font', 'alagard.ttf')
FONT_SIZES = (12, 18, 24, 36, 48, 60, 72)
_fonts = {}
_font_ok = os.path.isfile(FONT_PATH)


def snap_font_size(px):
    if not _font_ok:
        return px
    return min(FONT_SIZES, key=lambda s: (abs(s - px), s))


def get_font(px):
    size = snap_font_size(px)
    f = _fonts.get(size)
    if f is None:
        f = rl.load_font_ex(FONT_PATH.encode(), size, rl.ffi.NULL, 0)
        rl.set_texture_filter(f.texture, rl.TEXTURE_FILTER_POINT)
        _fonts[size] = f
    return f


def draw_text(text, x, y, size, color):
    if _font_ok:
        rl.draw_text_ex(get_font(size), str(text), rl.Vector2(float(x),
                                                              float(y)),
                        float(snap_font_size(size)), 1.0, color)
    else:
        rl.draw_text(str(text), x, y, size, color)


def find_blender():
    import glob as _glob
    hits = sorted(_glob.glob(BLENDER_GLOB))
    return hits[-1] if hits else None


def load_manifests():
    out = {}
    if os.path.isdir(MANIFEST_DIR):
        for fn in sorted(os.listdir(MANIFEST_DIR)):
            if not fn.endswith('.json'):
                continue
            with open(os.path.join(MANIFEST_DIR, fn)) as fh:
                mf = json.load(fh)
            out[mf['character']] = mf
    return out


def clip_dir_for(char, clip, root, category=None):
    """Find a rendered clip folder in a layout root.

    fighter layout: <root>/<category>/<char>/<char>_<clip>
    iso layout:     <root>/<category>/<char>_<clip>
    game assets:    <root>/<deploy>/<char>_<clip>
    """
    cands = []
    if category:
        cands.append(os.path.join(root, category, char, '%s_%s' % (char, clip)))
    cands.append(os.path.join(root, char, '%s_%s' % (char, clip)))
    cands.append(os.path.join(root, '%s_%s' % (char, clip)))
    for c in cands:
        if os.path.isdir(c):
            return c
    return None


def frames_in(clip_path, dir_name):
    """Sorted frame PNG paths for one direction folder (by frame index)."""
    if not clip_path:
        return []
    d = os.path.join(clip_path, dir_name)
    if not os.path.isdir(d):
        return []
    rows = []
    for fn in os.listdir(d):
        m = FRAME_RE.match(fn)
        if m:
            rows.append((int(m.group('frame')), os.path.join(d, fn)))
    rows.sort()
    return [p for _i, p in rows]


def token_ok(fn, char, clip, dir_name):
    m = FRAME_RE.match(fn)
    if not m:
        return False
    return (m.group('base') == '%s_%s' % (char, clip)
            and m.group('dir') == dir_name
            and m.group('angle') == '%.1f' % DIR_ANGLE[dir_name])


def _newest_mtime(root):
    newest = 0.0
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            try:
                newest = max(newest,
                             os.path.getmtime(os.path.join(dirpath, fn)))
            except OSError:
                pass
    return newest


class TexCache:
    def __init__(self, cap=512):
        self.cap = cap
        self._d = {}

    def get(self, path):
        t = self._d.get(path)
        if t is not None:
            return t
        t = rl.load_texture(path) if os.path.exists(path) else None
        self._d[path] = t
        if len(self._d) > self.cap:
            for k in list(self._d)[:len(self._d) - self.cap]:
                old = self._d.pop(k)
                if old:
                    rl.unload_texture(old)
        return t

    def invalidate(self):
        for t in self._d.values():
            if t:
                rl.unload_texture(t)
        self._d.clear()


class RenderJob:
    def __init__(self, char, dirs, recipe=None, deploy_root=None):
        self.char = char
        self.dirs = dirs
        self.recipe = recipe
        self.deploy_root = deploy_root
        self.status = 'queued'
        self.log = deque(maxlen=400)
        self.proc = None


class JobRunner:
    """Runs queued Blender jobs sequentially; pumps stdout in a thread."""

    def __init__(self):
        self.jobs = []
        self.log = deque(maxlen=800)
        self.thread = None

    def add(self, job):
        self.jobs.append(job)
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def _run(self):
        blender = find_blender()
        if not blender:
            self.log.append('blender.exe not found under '
                            'C:\\Program Files\\Blender Foundation')
            for j in self.jobs:
                j.status = 'failed'
            return
        while True:
            job = next((j for j in self.jobs if j.status == 'queued'), None)
            if job is None:
                return
            job.status = 'running'
            if job.recipe:
                argv = [blender, '--background', '--python', GENERATOR, '--',
                        '--recipe', job.recipe, '--output', OUTPUT_DIR]
                if job.deploy_root:
                    argv += ['--deploy', job.deploy_root]
            else:
                argv = [blender, '--background', '--python', GENERATOR, '--',
                        '--models', os.path.join(HERE, 'models'),
                        '--output', OUTPUT_DIR,
                        '--manifests', MANIFEST_DIR,
                        '--characters', job.char]
                if job.dirs:
                    argv += ['--dirs', job.dirs]
            self.log.append('START %s%s dirs=%s'
                            % (job.char or os.path.basename(job.recipe),
                               ' [recipe]' if job.recipe else '',
                               job.dirs or 'all'))
            try:
                job.proc = subprocess.Popen(
                    argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding='utf-8', errors='replace',
                    creationflags=subprocess.CREATE_NO_WINDOW)
                for line in job.proc.stdout:
                    job.log.append(line.rstrip())
                    self.log.append(line.rstrip())
                job.proc.wait()
                job.status = ('done' if job.proc.returncode == 0
                              else 'failed(%d)' % job.proc.returncode)
            except Exception as e:
                job.status = 'failed'
                self.log.append('ERROR %s' % e)
            self.log.append('END %s -> %s'
                            % (job.char or os.path.basename(job.recipe or ''),
                               job.status))


class App:
    def __init__(self):
        self.tex = TexCache()
        self.runner = JobRunner()
        self.reload_manifests()
        self.screen = 'VIEW'
        self.sel_char = None
        self.sel_clip = None
        self.sel_dir = 'S'
        self.playing = True
        self.anim_t = 0.0
        self.fps = 8.0
        self.zoom = 2.0
        self.pan = [0.0, 0.0]
        self.onion = False
        self.bbox = False
        self.blink = False
        self.ref_source = 1
        self.queue_dirs = ''
        self.deploy_sel = set()
        self.deploy_log = []
        self.health = None
        self._panning = False
        self.upload_path = ''
        self.upload_report = None
        self.upload_preview_tex = None
        self.inspect_thread = None
        self.inspect_log = deque(maxlen=300)
        self.name_buf = ''
        self.name_focus = False
        self.cat_idx = 0
        self.create_deploy = True
        self.create_pkgs = set()
        self.create_msg = []

    # â”€â”€ data â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def reload_manifests(self):
        self.manifests = load_manifests()
        self.chars = sorted(self.manifests)
        self.roots = [('output', OUTPUT_DIR, None)]
        if os.path.isdir(GAME_ASSETS):
            self.roots.insert(0, ('game', GAME_ASSETS, 'deploy'))
            bak = os.path.join(GAME_ASSETS, 'playable character',
                               'knight_original_backup')
            if os.path.isdir(bak):
                self.roots.insert(1, ('backup',
                                      os.path.join(GAME_ASSETS,
                                                   'playable character'),
                                      None))

    def manifest_clip(self, char, clip):
        mf = self.manifests.get(char) or {}
        return next((c for c in mf.get('clips', [])
                     if c['clip'] == clip), None)

    def expected_frames(self, char, clip):
        c = self.manifest_clip(char, clip)
        return c.get('frames') if c else None

    def clip_path(self, char, clip, source=0):
        """source: 0=output, 1=game assets, 2=backup pack."""
        mf = self.manifests.get(char) or {}
        if source == 0:
            return clip_dir_for(char, clip, OUTPUT_DIR,
                                mf.get('category'))
        if source == 1 and mf.get('deploy'):
            return clip_dir_for(char, clip,
                                os.path.join(GAME_ASSETS, mf['deploy']))
        if source == 2:
            return clip_dir_for(char, clip, GAME_ASSETS)
        return None

    def select(self, char, clip=None):
        self.sel_char = char
        if clip is None:
            clips = self.manifests.get(char, {}).get('clips', [])
            clip = clips[0]['clip'] if clips else None
        self.sel_clip = clip
        c = self.manifest_clip(char, clip)
        self.fps = (c or {}).get('fps') or 8.0
        self.anim_t = 0.0
        self.zoom = 2.0
        self.pan = [0.0, 0.0]
        self.tex.invalidate()

    # â”€â”€ screens â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def viewer_paths(self):
        out = []
        for src in (0, 1, 2):
            p = self.clip_path(self.sel_char, self.sel_clip, src)
            if p:
                out.append((src, p))
        return out

    # â”€â”€ create flow â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def browse_file(self):
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            path = filedialog.askopenfilename(
                title='Upload a character model',
                filetypes=[('Models', '*.fbx *.glb *.gltf *.obj'),
                           ('All files', '*.*')])
            root.destroy()
        except Exception as e:
            self.create_msg = ['file dialog failed: %s' % e]
            return
        if path:
            self.upload_path = path
            self.upload_report = None
            self.upload_preview_tex = None
            if not self.name_buf:
                self.name_buf = os.path.splitext(os.path.basename(path))[0]

    def start_inspect(self):
        if not self.upload_path or (self.inspect_thread
                                    and self.inspect_thread.is_alive()):
            return
        import tempfile
        out_dir = os.path.join(tempfile.gettempdir(), 'sprite_studio')
        os.makedirs(out_dir, exist_ok=True)
        json_out = os.path.join(out_dir, 'inspect_report.json')
        blender = find_blender()
        if not blender:
            self.create_msg = ['blender.exe not found']
            return
        argv = [blender, '--background', '--python',
                os.path.join(HERE, 'inspect_model.py'), '--',
                '--input', self.upload_path, '--json', json_out,
                '--preview', out_dir]
        self.create_msg = ['inspecting...']
        self.inspect_log.clear()

        def work():
            try:
                proc = subprocess.Popen(
                    argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding='utf-8', errors='replace',
                    creationflags=subprocess.CREATE_NO_WINDOW)
                for line in proc.stdout:
                    self.inspect_log.append(line.rstrip())
                proc.wait()
                if os.path.isfile(json_out):
                    with open(json_out) as fh:
                        rep = json.load(fh)
                    self.upload_report = rep
                    pv = rep.get('preview')
                    if pv and os.path.isfile(pv):
                        self.upload_preview_tex = self.tex.get(pv)
                    self.create_msg = []
                else:
                    self.create_msg = ['inspect failed - see log below']
            except Exception as e:
                self.create_msg = ['inspect error: %s' % e]
        self.inspect_thread = threading.Thread(target=work, daemon=True)
        self.inspect_thread.start()

    def write_recipes(self):
        import tempfile
        if not (self.upload_report and self.upload_report.get('ok')
                and self.name_buf and self.create_pkgs):
            self.create_msg = ['need: valid rig, a name, at least one '
                               'package']
            return
        out_dir = os.path.join(HERE, 'recipes')
        os.makedirs(out_dir, exist_ok=True)
        cat = CATEGORIES[self.cat_idx]
        paths = []
        for pkg in sorted(self.create_pkgs):
            mf = self.manifests[pkg]
            rec = {'character': self.name_buf, 'category': cat,
                   'model': os.path.abspath(self.upload_path),
                   'package': pkg}
            if self.create_deploy:
                rec['deploy'] = '%s/%s' % (cat.replace(os.sep, '/'),
                                           self.name_buf)
            path = os.path.join(out_dir,
                                '%s_%s.json' % (self.name_buf, pkg))
            with open(path, 'w') as fh:
                json.dump(rec, fh, indent=2)
            paths.append(path)
            self.runner.add(RenderJob('', '', recipe=path,
                                      deploy_root=GAME_ASSETS
                                      if self.create_deploy else None))
        self.create_msg = ['queued %d export(s): %s'
                           % (len(paths),
                              ', '.join(os.path.basename(p)
                                        for p in paths))]

    def draw_create(self, x, y, w, h):
        col_w = 420
        # â”€â”€ upload column â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if self._button(rl.Rectangle(x, y, 110, 28), 'Upload FBX'):
            self.browse_file()
        if self.upload_path and self._button(
                rl.Rectangle(x + 120, y, 110, 28), 'Inspect rig'):
            self.start_inspect()
        draw_text(self.upload_path[-52:] or '(no file)',
                     x, y + 34, 13, COL_TEXT if self.upload_path else COL_DIM)
        # report summary
        ry = y + 56
        rep = self.upload_report
        if rep:
            verdict = 'RIGGED - mappable' if rep.get('ok') \
                else 'REJECTED'
            draw_text('%s  (%d bones, %d mesh(es))'
                         % (verdict,
                            (rep.get('armature') or {}).get('bone_count', 0),
                            len(rep.get('meshes', []))),
                         x, ry, 14, COL_OK if rep.get('ok') else COL_BAD)
            ry += 20
            mp = rep.get('mapping') or {}
            if not mp.get('ok'):
                draw_text((mp.get('error') or rep.get('error')
                              or '')[:70], x, ry, 13, COL_BAD)
                ry += 18
            acts = rep.get('actions', [])
            draw_text('native actions: %d | textures: %d | upright: %s'
                         % (len(acts), len(rep.get('textures', [])),
                            (rep.get('rest') or {}).get('upright')),
                         x, ry, 13, COL_DIM)
            ry += 20
            if self.upload_preview_tex:
                k = min(180 / 256.0, 1.6)
                rl.draw_texture_pro(
                    self.upload_preview_tex,
                    rl.Rectangle(0, 0, 256, 256),
                    rl.Rectangle(x, ry, 256 * k, 256 * k),
                    rl.Vector2(0, 0), 0.0, rl.WHITE)
        elif self.inspect_thread and self.inspect_thread.is_alive():
            for i, ln in enumerate(list(self.inspect_log)[-8:]):
                draw_text(ln[:64], x, ry + i * 16, 12, COL_DIM)
        for i, ln in enumerate(self.create_msg):
            draw_text(ln[:70], x, ry + 200 + i * 16, 13, COL_WARN)
        # â”€â”€ creation column â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        cx = x + col_w
        draw_text('name:', cx, y + 6, 14, COL_TEXT)
        box = rl.Rectangle(cx + 50, y + 2, 220, 24)
        rl.draw_rectangle_rec(box, COL_BG if not self.name_focus
                              else rl.Color(20, 26, 38, 255))
        rl.draw_rectangle_lines_ex(box, 1, COL_ACCENT if self.name_focus
                                   else COL_LINE)
        draw_text(self.name_buf or '<click, type name>', cx + 58,
                     y + 7, 14, COL_TEXT if self.name_buf else COL_DIM)
        if self._clicked(box):
            self.name_focus = True
        elif rl.is_mouse_button_pressed(rl.MOUSE_LEFT_BUTTON):
            self.name_focus = False
        if self.name_focus:
            ch = rl.get_char_pressed()
            while ch:
                if 32 <= ch < 127 and len(self.name_buf) < 28:
                    self.name_buf += chr(ch)
                ch = rl.get_char_pressed()
            if rl.is_key_pressed(rl.KEY_BACKSPACE):
                self.name_buf = self.name_buf[:-1]
        ry2 = y + 40
        draw_text('category:', cx, ry2 + 4, 14, COL_TEXT)
        if self._button(rl.Rectangle(cx + 70, ry2, 190, 24),
                        CATEGORIES[self.cat_idx]):
            self.cat_idx = (self.cat_idx + 1) % len(CATEGORIES)
        draw_text('deploy to game assets:', cx, ry2 + 36, 14, COL_TEXT)
        hit = rl.Rectangle(cx + 210, ry2 + 34, 18, 18)
        rl.draw_rectangle_rec(hit, COL_PANEL)
        rl.draw_rectangle_lines_ex(hit, 1, COL_LINE)
        if self.create_deploy:
            rl.draw_rectangle(cx + 214, ry2 + 38, 10, 10, COL_OK)
        if self._clicked(hit):
            self.create_deploy = not self.create_deploy
        # package picker
        py = ry2 + 66
        draw_text('ANIMATION PACKAGE(S) - one export per tick, '
                     'verbatim, no mixing', cx, py, 13, COL_DIM)
        py += 22
        for pkg in self.chars:
            mf = self.manifests[pkg]
            if any(c.get('source') == 'fbx-action'
                   for c in mf.get('clips', [])):
                continue   # native-action packages need retargeting - skip
            hit = rl.Rectangle(cx, py, 18, 18)
            rl.draw_rectangle_rec(hit, COL_PANEL)
            rl.draw_rectangle_lines_ex(hit, 1, COL_LINE)
            if pkg in self.create_pkgs:
                rl.draw_rectangle(cx + 4, py + 4, 10, 10, COL_OK)
            if self._clicked(hit):
                self.create_pkgs ^= {pkg}
            gaps = mf.get('gaps') or []
            note = ' (GAP: +%d missing clips)' % len(gaps) if gaps else ''
            draw_text('%s  %d clip(s)%s'
                         % (pkg, len(mf['clips']), note),
                         cx + 26, py, 14, COL_WARN if gaps else COL_TEXT)
            py += 24
        if self._button(rl.Rectangle(cx, py + 10, 200, 28),
                        'Create & queue render'):
            self.write_recipes()
        if self.create_msg:
            my = py + 46
            for ln in self.create_msg:
                draw_text(ln[:70], cx, my, 13, COL_DIM)
                my += 16

    def draw_viewer(self, x, y, w, h, clip_path, reference=None, tag=None):
        frames = frames_in(clip_path, self.sel_dir)
        rl.draw_rectangle(x, y, w, h, COL_PANEL)
        if not frames:
            draw_text('(no frames - queue a render)', x + 16, y + h // 2,
                         16, COL_DIM)
            return
        if self.playing:
            self.anim_t += rl.get_frame_time()
        fps = max(0.5, self.fps)
        idx = int(self.anim_t * fps) % len(frames)
        tex = self.tex.get(frames[idx])
        if self.onion and len(frames) > 1:
            gt = self.tex.get(frames[(idx - 1) % len(frames)])
            self._blit(gt, x, y, w, h, tint=rl.Color(255, 255, 255, 90))
        self._blit(tex, x, y, w, h)
        if self.bbox:
            self._bbox_overlay(frames[idx], x, y, w, h)
        draw_text('%s  %s  frame %d/%d  %s'
                     % (os.path.basename(clip_path), self.sel_dir,
                        idx + 1, len(frames), tag or ''),
                     x + 10, y + 6, 14, COL_TEXT)
        if reference:
            rframes = frames_in(reference, self.sel_dir)
            if rframes:
                ridx = min(idx, len(rframes) - 1)
                rtex = self.tex.get(rframes[ridx])
                if self.blink and (int(self.anim_t * 2) % 2) == 0:
                    self._blit(rtex, x, y, w, h)
                else:
                    self._blit(rtex, x + w // 2, y, w // 2, h // 2)
                draw_text('REF', x + 10, y + h - 24, 14, COL_WARN)

    def _blit(self, tex, x, y, w, h, tint=None):
        if not tex:
            return
        k = self.zoom
        dw, dh = tex.width * k, tex.height * k
        cx = x + w / 2 - dw / 2 + self.pan[0]
        cy = y + h / 2 - dh / 2 + self.pan[1]
        rl.draw_texture_pro(tex,
                            rl.Rectangle(0, 0, tex.width, tex.height),
                            rl.Rectangle(cx, cy, dw, dh),
                            rl.Vector2(0, 0), 0.0, tint or rl.WHITE)

    def _bbox_overlay(self, path, x, y, w, h):
        try:
            from PIL import Image
        except ImportError:
            return
        im = Image.open(path).convert('RGBA')
        box = im.getbbox()
        if not box:
            return
        k = self.zoom
        cx = x + w / 2 + self.pan[0]
        cy = y + h / 2 + self.pan[1]
        rx = cx + (box[0] - 128) * k
        ry = cy + (box[1] - 128) * k
        rw = (box[2] - box[0]) * k
        rh = (box[3] - box[1]) * k
        rl.draw_rectangle_lines(int(rx), int(ry), int(rw), int(rh), COL_ACCENT)
        draw_text('%dx%d' % (rw, rh), int(rx), int(ry - 16), 12, COL_ACCENT)

    def draw_grid(self, x, y, w, h):
        path = self.clip_path(self.sel_char, self.sel_clip, 0) \
            or self.clip_path(self.sel_char, self.sel_clip, 1)
        rl.draw_rectangle(x, y, w, h, COL_PANEL)
        if not path:
            draw_text('(nothing rendered for this clip)', x + 16,
                         y + h // 2, 16, COL_DIM)
            return
        cols = 4
        cw, ch = (w - 20) // cols, 150
        for i, d in enumerate(DIRS):
            r, c = divmod(i, cols)
            cell_x, cell_y = x + 10 + c * cw, y + 30 + r * ch
            frames = frames_in(path, d)
            if frames:
                tex = self.tex.get(frames[0])
                if tex:
                    s = min((cw - 12) / tex.width, (ch - 28) / tex.height)
                    rl.draw_texture_pro(
                        tex, rl.Rectangle(0, 0, tex.width, tex.height),
                        rl.Rectangle(cell_x + (cw - tex.width * s) / 2,
                                     cell_y + (ch - 34 - tex.height * s) / 2,
                                     tex.width * s, tex.height * s),
                        rl.Vector2(0, 0), 0.0, rl.WHITE)
            hit = rl.Rectangle(cell_x, cell_y, cw - 8, ch - 8)
            rl.draw_rectangle_lines_ex(hit, 1, COL_LINE)
            if self._clicked(hit):
                self.sel_dir = d
                self.screen = 'VIEW'
                self.anim_t = 0.0
            color = COL_SEL if d == self.sel_dir else COL_TEXT
            draw_text(d, cell_x + 6, cell_y + ch - 26, 14, color)
            if frames:
                mark = self.expected_frames(self.sel_char, self.sel_clip)
                ok = mark is None or len(frames) >= mark
                draw_text('%d%s' % (len(frames), '' if ok else '!'),
                             cell_x + cw - 40, cell_y + ch - 26, 14,
                             COL_OK if ok else COL_BAD)
            else:
                draw_text('-', cell_x + cw - 40, cell_y + ch - 26, 14,
                             COL_DIM)
        draw_text('Grid: click a cell to open it in the viewer',
                     x + 10, y + 8, 14, COL_DIM)

    def draw_compare(self, x, y, w, h):
        self_path = self.clip_path(self.sel_char, self.sel_clip, 0)
        ref_src = self.ref_source if self.ref_source != 0 else 1
        ref_path = self.clip_path(self.sel_char, self.sel_clip, ref_src)
        if self.blink and ref_path and (int(self.anim_t * 2) % 2):
            self.draw_viewer(x, y, w, h, ref_path, tag='REF')
            return
        half = w // 2
        draw_text('OURS', x + 10, y + 8, 16, COL_ACCENT)
        draw_text('REFERENCE', x + half + 10, y + 8, 16, COL_WARN)
        if self_path:
            self.draw_viewer(x, y + 28, half, h - 28, self_path, tag='OURS')
        else:
            rl.draw_rectangle(x, y + 28, half, h - 28, COL_PANEL)
            draw_text('(no output)', x + 16, y + 28 + h // 2, 16, COL_DIM)
        if ref_path:
            self.draw_viewer(x + half, y + 28, half, h - 28, ref_path,
                             tag='REF')
        else:
            rl.draw_rectangle(x + half, y + 28, half, h - 28, COL_PANEL)
            draw_text('(no reference)', x + half + 16, y + 28 + h // 2,
                         16, COL_DIM)

    def draw_queue(self, x, y, w, h):
        draw_text('Queue: renders one Blender job at a time '
                     '(manifest mode, output/)', x + 10, y + 8, 14, COL_DIM)
        y0 = y + 30
        for job in self.runner.jobs[-12:]:
            col = {'queued': COL_DIM, 'running': COL_ACCENT,
                   'done': COL_OK}.get(job.status, COL_BAD)
            rl.draw_rectangle(x + 10, y0, w - 20, 26,
                              COL_PANEL if job.status != 'running'
                              else rl.Color(40, 50, 66, 255))
            label = job.char or 'recipe:%s' % os.path.basename(job.recipe)
            draw_text('%s  dirs=%s  %s' % (label, job.dirs or 'all',
                                              job.status),
                         x + 18, y0 + 5, 14, col)
            y0 += 30
        if self._button(rl.Rectangle(x + 10, y0 + 6, 220, 28),
                        'Queue: %s (all dirs)' % self.sel_char):
            self.runner.add(RenderJob(self.sel_char, ''))
        if self._button(rl.Rectangle(x + 240, y0 + 6, 240, 28),
                        'Queue: %s (S only)' % self.sel_char):
            self.runner.add(RenderJob(self.sel_char, 'S'))
        ly = y0 + 44
        rl.draw_rectangle(x + 10, ly, w - 20, h - (ly - y) - 10, COL_BG)
        lines = list(self.runner.log)[-40:]
        for i, ln in enumerate(lines):
            draw_text(ln[:150], x + 16, ly + 8 + i * 16, 13, COL_DIM)

    def draw_deploy(self, x, y, w, h):
        draw_text('Deploy: copy output -> game assets, ONLY selected '
                     'characters', x + 10, y + 8, 14, COL_DIM)
        y0 = y + 32
        for char in self.chars:
            mf = self.manifests[char]
            if not mf.get('deploy'):
                draw_text('%-16s (no deploy target)' % char,
                             x + 18, y0, 14, COL_DIM)
                y0 += 24
                continue
            hit = rl.Rectangle(x + 10, y0, 18, 18)
            rl.draw_rectangle_rec(hit, COL_PANEL)
            rl.draw_rectangle_lines_ex(hit, 1, COL_LINE)
            if char in self.deploy_sel:
                rl.draw_rectangle(x + 14, y0 + 4, 10, 10, COL_OK)
            if self._clicked(hit):
                self.deploy_sel ^= {char}
            draw_text('%-16s -> assets/%s' % (char, mf['deploy']),
                         x + 36, y0, 14, COL_TEXT)
            y0 += 24
        if self._button(rl.Rectangle(x + 10, y0 + 10, 130, 28), 'Dry run'):
            self.deploy_log = self._deploy(dry=True)
        if self._button(rl.Rectangle(x + 150, y0 + 10, 130, 28),
                        'Deploy now'):
            self.deploy_log = self._deploy(dry=False)
        y0 += 52
        for ln in self.deploy_log[-30:]:
            draw_text(ln[:140], x + 14, y0, 13, COL_DIM)
            y0 += 16

    def _deploy(self, dry):
        out = []
        copied = added = updated = 0
        for char in sorted(self.deploy_sel):
            mf = self.manifests[char]
            src_root = os.path.join(OUTPUT_DIR, mf['category'], char)
            if not os.path.isdir(src_root):
                out.append('%s: nothing rendered yet' % char)
                continue
            dest_root = os.path.join(GAME_ASSETS, mf['deploy'])
            n_add = n_upd = 0
            for dirpath, _dirs, files in os.walk(src_root):
                rel = os.path.relpath(dirpath, src_root)
                for fn in files:
                    src = os.path.join(dirpath, fn)
                    dst = os.path.join(dest_root, rel, fn)
                    if not os.path.exists(dst):
                        n_add += 1
                    elif (os.path.getmtime(src) != os.path.getmtime(dst)
                          or os.path.getsize(src) != os.path.getsize(dst)):
                        n_upd += 1
            out.append('%s: %d new, %d updated -> %s'
                       % (char, n_add, n_upd, dest_root))
            if not dry:
                for dirpath, _dirs, files in os.walk(src_root):
                    rel = os.path.relpath(dirpath, src_root)
                    os.makedirs(os.path.join(dest_root, rel), exist_ok=True)
                    for fn in files:
                        shutil.copy2(os.path.join(dirpath, fn),
                                     os.path.join(dest_root, rel, fn))
                copied += n_add + n_upd
                added += n_add
                updated += n_upd
        out.append('%s done: %d copied (%d new, %d updated)'
                   % ('DRY' if dry else 'DEPLOY', copied, added, updated))
        return out

    def compute_health(self):
        rows = []
        for char in self.chars:
            mf = self.manifests[char]
            model = mf.get('model')
            model_path = os.path.join(HERE, model) if model else None
            if model and not os.path.isfile(model_path):
                rows.append((char, 'NO_MODEL', model))
            elif not model:
                rows.append((char, 'NO_MODEL', 'no rig available'))
            for c in mf.get('clips', []):
                clip = c['clip']
                exp = c.get('frames')
                if (c.get('runtime') == 'unreferenced'
                        or c.get('fps') is None):
                    rows.append((char, 'UNREFERENCED', clip))
                if exp is None:
                    continue
                path = clip_dir_for(char, clip, OUTPUT_DIR,
                                    mf.get('category'))
                if path:
                    if model and (os.path.getmtime(model_path)
                                  > _newest_mtime(path)):
                        rows.append((char, 'STALE',
                                     '%s (model newer than render)' % clip))
                    for d in DIRS:
                        frames = frames_in(path, d)
                        if len(frames) < exp:
                            rows.append((char, 'MISSING_FRAMES',
                                         '%s/%s %d<%d'
                                         % (clip, d, len(frames), exp)))
                            break
                        bad = [f for f in frames
                               if not token_ok(os.path.basename(f), char,
                                               clip, d)]
                        if bad:
                            rows.append((char, 'TOKEN_MISMATCH',
                                         '%s/%s %s' % (clip, d,
                                                       len(bad))))
                            break
                else:
                    rows.append((char, 'MISSING_RENDER', clip))
        return rows

    def draw_health(self, x, y, w, h):
        if self._button(rl.Rectangle(x + 10, y + 8, 90, 26), 'Rescan'):
            self.health = None
        if self.health is None:
            self.health = self.compute_health()
        rows = self.health
        col_map = {'NO_MODEL': COL_WARN, 'MISSING_FRAMES': COL_BAD,
                   'TOKEN_MISMATCH': COL_BAD, 'STALE': COL_WARN,
                   'UNREFERENCED': COL_DIM, 'MISSING_RENDER': COL_WARN}
        draw_text('%d finding(s)' % len(rows), x + 110, y + 14, 14,
                     COL_OK if not rows else COL_TEXT)
        y0 = y + 44
        for char, kind, detail in rows[:60]:
            draw_text('%-16s %-16s %s' % (char, kind, detail),
                         x + 14, y0, 13, col_map.get(kind, COL_TEXT))
            y0 += 17

    # â”€â”€ chrome â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def draw_rail(self, w):
        x, y = 0, 0
        rail_w = 230
        rl.draw_rectangle(x, y, rail_w, rl.get_screen_height(), COL_PANEL)
        for i, (key, label) in enumerate(SCREENS):
            hit = rl.Rectangle(x + 8, 8 + i * 30, rail_w - 16, 26)
            active = self.screen == key
            if active:
                rl.draw_rectangle_rec(hit, COL_SEL)
            draw_text(label, x + 18, 12 + i * 30, 15,
                         rl.WHITE if active else COL_TEXT)
            if self._clicked(hit):
                self.screen = key
                if key == 'HEALTH':
                    self.health = None
        y = 8 + len(SCREENS) * 30 + 12
        draw_text('CHARACTER', x + 12, y, 12, COL_DIM)
        y += 18
        for char in self.chars:
            mf = self.manifests[char]
            has_model = bool(mf.get('model'))
            col = COL_TEXT if has_model else COL_WARN
            hit = rl.Rectangle(x + 8, y, rail_w - 16, 22)
            if char == self.sel_char:
                rl.draw_rectangle_rec(hit, rl.Color(50, 50, 60, 255))
            draw_text(('  ' if has_model else '! ') + char,
                         x + 14, y + 2, 14, col)
            if self._clicked(hit):
                self.select(char)
            y += 24
        y += 8
        draw_text('CLIP', x + 12, y, 12, COL_DIM)
        y += 18
        for c in self.manifests.get(self.sel_char, {}).get('clips', []):
            hit = rl.Rectangle(x + 24, y, rail_w - 40, 22)
            if c['clip'] == self.sel_clip:
                rl.draw_rectangle_rec(hit, rl.Color(50, 50, 60, 255))
            draw_text(c['clip'], x + 30, y + 2, 14, COL_TEXT)
            if self._clicked(hit):
                self.select(self.sel_char, c['clip'])
            y += 24
        y += 8
        draw_text('DIR', x + 12, y, 12, COL_DIM)
        y += 18
        for d in DIRS:
            hit = rl.Rectangle(x + 24 + (DIRS.index(d) // 8) * 100,
                               y + (DIRS.index(d) % 8) * 22, 44, 20)
            active = d == self.sel_dir
            rl.draw_rectangle_rec(hit, COL_SEL if active else COL_BG)
            draw_text(d, x + 32 + (DIRS.index(d) // 8) * 100,
                         y + 2 + (DIRS.index(d) % 8) * 22, 13,
                         rl.WHITE if active else COL_TEXT)
            if self._clicked(hit):
                self.sel_dir = d
                self.anim_t = 0.0
        return rail_w

    def draw_viewer_controls(self, x, y):
        if self._button(rl.Rectangle(x, y, 70, 26),
                        'pause' if self.playing else 'play'):
            self.playing = not self.playing
        frames = frames_in(self.clip_path(self.sel_char, self.sel_clip, 0)
                           or self.clip_path(self.sel_char, self.sel_clip, 1),
                           self.sel_dir)
        if frames:
            idx = int(self.anim_t * max(0.5, self.fps)) % len(frames)
            m = rl.Rectangle(x + 80, y + 6, 220, 16)
            rl.draw_rectangle_rec(m, COL_BG)
            rl.draw_rectangle_rec(m, COL_LINE)
            pos = m.x + (m.width - 4) * idx / max(1, len(frames) - 1)
            rl.draw_rectangle(int(pos), int(m.y), 4, 16, COL_ACCENT)
            if rl.is_mouse_button_down(rl.MOUSE_LEFT_BUTTON) \
                    and self._hover(m):
                t = (rl.get_mouse_position().x - m.x) / m.width
                self.playing = False
                self.anim_t = t * len(frames) / max(0.5, self.fps)
        draw_text('fps %.1f' % self.fps, x + 320, y + 4, 14, COL_TEXT)
        self.fps = self._slider(rl.Rectangle(x + 380, y + 6, 120, 16),
                                self.fps, 0.5, 16.0)

    def run(self, selftest=False):
        rl.init_window(1280, 800, b'BTRSpriteStudio')
        rl.set_target_fps(30)
        if not self.chars:
            self.select('knight')
        else:
            # open on the first character that has rendered output, else the
            # first manifest character
            pick = next((c for c in self.chars
                         if any(self.clip_path(c, cl['clip'], 0)
                                for cl in self.manifests[c]['clips'])),
                        self.chars[0])
            self.select(pick)
        stages = ['VIEW', 'GRID', 'COMPARE', 'CREATE', 'DEPLOY', 'HEALTH']
        frame_i = 0
        while not rl.window_should_close():
            frame_i += 1
            if selftest:
                self.screen = stages[min((frame_i - 1) // 10,
                                         len(stages) - 1)]
            else:
                self._shortcuts()
            rl.begin_drawing()
            rl.clear_background(COL_BG)
            rail_w = self.draw_rail(230)
            x = rail_w + 10
            w = rl.get_screen_width() - x - 10
            h = rl.get_screen_height() - 50
            if self.screen == 'VIEW':
                self.draw_viewer_controls(x, 6)
                paths = self.viewer_paths()
                if paths:
                    src, path = paths[0]
                    ref = self.clip_path(self.sel_char, self.sel_clip,
                                         self.ref_source if src != 1 else 0)
                    self.draw_viewer(x, 40, w, h - 40, path,
                                     reference=ref if ref != path else None)
                else:
                    rl.draw_rectangle(x, 40, w, h - 40, COL_PANEL)
                    draw_text('(nothing rendered - Queue screen)',
                                 x + 16, 100, 16, COL_DIM)
            elif self.screen == 'GRID':
                self.draw_grid(x, 40, w, h - 40)
            elif self.screen == 'COMPARE':
                self.draw_compare(x, 40, w, h - 40)
            elif self.screen == 'CREATE':
                self.draw_create(x, 40, w, h - 40)
            elif self.screen == 'QUEUE':
                self.draw_queue(x, 40, w, h - 40)
            elif self.screen == 'DEPLOY':
                self.draw_deploy(x, 40, w, h - 40)
            elif self.screen == 'HEALTH':
                self.draw_health(x, 40, w, h - 40)
            draw_text('[space] play  [arrows] frame  [G]rid  [C]ompare  '
                         '[N]ew character  [R]ender  [D]eploy  [F1] health  '
                         '[O]nion  [B]link  [H] bbox  [wheel] zoom',
                         x, rl.get_screen_height() - 24, 12, COL_DIM)
            rl.end_drawing()
            if selftest and frame_i % 10 == 0:
                rl.take_screenshot(
                    ('studio_shot_%d_%s.png' % (frame_i, self.screen))
                    .encode())
            if selftest and frame_i >= len(stages) * 10:
                break
        rl.close_window()

    # â”€â”€ input helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def _shortcuts(self):
        if rl.is_key_pressed(rl.KEY_SPACE):
            self.playing = not self.playing
        if rl.is_key_pressed(rl.KEY_RIGHT):
            self.anim_t += 1 / max(0.5, self.fps)
            self.playing = False
        if rl.is_key_pressed(rl.KEY_LEFT):
            self.anim_t -= 1 / max(0.5, self.fps)
            self.playing = False
        for key, screen in ((rl.KEY_G, 'GRID'), (rl.KEY_C, 'COMPARE'),
                            (rl.KEY_N, 'CREATE'), (rl.KEY_R, 'QUEUE'),
                            (rl.KEY_D, 'DEPLOY')):
            if rl.is_key_pressed(key):
                self.screen = screen
        if rl.is_key_pressed(rl.KEY_F1):
            self.screen = 'HEALTH'
            self.health = None
        if rl.is_key_pressed(rl.KEY_O):
            self.onion = not self.onion
        if rl.is_key_pressed(rl.KEY_B):
            self.blink = not self.blink
        if rl.is_key_pressed(rl.KEY_H):
            self.bbox = not self.bbox
        wheel = rl.get_mouse_wheel_move()
        if wheel:
            if self.screen in ('VIEW', 'COMPARE') \
                    and rl.get_mouse_position().x > 240:
                self.zoom = max(0.25, min(16.0, self.zoom * (1.15 if wheel
                                                             > 0 else 1 / 1.15)))
            elif self.screen == 'VIEW':
                self.anim_t += wheel / max(0.5, self.fps)
                self.playing = False
        mp = rl.get_mouse_position()
        if rl.is_mouse_button_pressed(rl.MOUSE_RIGHT_BUTTON):
            self._panning = True
        if rl.is_mouse_button_released(rl.MOUSE_RIGHT_BUTTON):
            self._panning = False
        if self._panning and mp.x > 240 and self.screen in ('VIEW',
                                                           'COMPARE'):
            self.pan[0] += rl.get_mouse_delta().x
            self.pan[1] += rl.get_mouse_delta().y

    def _hover(self, rect):
        mp = rl.get_mouse_position()
        return (rect.x <= mp.x <= rect.x + rect.width
                and rect.y <= mp.y <= rect.y + rect.height)

    def _clicked(self, rect):
        return self._hover(rect) and rl.is_mouse_button_pressed(
            rl.MOUSE_LEFT_BUTTON)

    def _button(self, rect, label):
        hov = self._hover(rect)
        rl.draw_rectangle_rec(rect, COL_SEL if hov else
                              rl.Color(46, 46, 54, 255))
        rl.draw_rectangle_lines_ex(rect, 1, COL_LINE)
        draw_text(label, int(rect.x + 8), int(rect.y + 6), 14, rl.WHITE)
        return self._clicked(rect)

    def _slider(self, rect, value, lo, hi):
        rl.draw_rectangle_rec(rect, COL_BG)
        rl.draw_rectangle_rec(rect, COL_LINE)
        t = (value - lo) / (hi - lo)
        rl.draw_rectangle(int(rect.x), int(rect.y),
                          int(rect.width * t), int(rect.height), COL_ACCENT)
        if rl.is_mouse_button_down(rl.MOUSE_LEFT_BUTTON) and self._hover(
                rl.Rectangle(rect.x - 10, rect.y - 10, rect.width + 20,
                             rect.height + 20)):
            t = max(0.0, min(1.0, (rl.get_mouse_position().x - rect.x)
                             / rect.width))
            value = lo + t * (hi - lo)
        return value


def main():
    App().run(selftest='--selftest' in sys.argv)


if __name__ == '__main__':
    main()
