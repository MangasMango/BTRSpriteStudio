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
# BTRSpriteStudio - minimal raylib GUI for the simplified converter:
# bring a rigged model with animations (Mixamo FBX, one anim per file in a
# folder, or a GLB with several actions) -> convert.py renders every
# animation as a 16-direction clip folder. Screens: Convert, Queue,
# Viewer, Deploy.
#
# Launch:  uv run --with pyray python studio.py

import os
import re
import shutil
import subprocess
import sys
import threading
from collections import deque

import pyray as rl

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(HERE, 'output')
GAME_ASSETS = os.path.normpath(os.path.join(HERE, '..', 'bound-to-rot',
                                            'assets'))
CONVERTER = os.path.join(HERE, 'convert.py')
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

SCREENS = [('CONVERT', 'Convert'), ('QUEUE', 'Queue'),
           ('VIEW', 'Viewer'), ('DEPLOY', 'Deploy')]

CATEGORIES = ['playable character', 'enemy', 'none-playable character',
              'boss', 'prop']

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


def frames_in(clip_path, dir_name):
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


def scan_output():
    """Registry without manifests: walk output/<category>/<name>/ and
    discover clips from the folder layout itself."""
    chars = []
    if not os.path.isdir(OUTPUT_DIR):
        return chars
    for category in sorted(os.listdir(OUTPUT_DIR)):
        cat_root = os.path.join(OUTPUT_DIR, category)
        if not os.path.isdir(cat_root) or category == 'manifest.json':
            continue
        for name in sorted(os.listdir(cat_root)):
            char_root = os.path.join(cat_root, name)
            if not os.path.isdir(char_root):
                continue
            clips = []
            static = all(d in DIR_ANGLE and
                         os.path.isdir(os.path.join(char_root, d))
                         for d in os.listdir(char_root))
            if static and os.listdir(char_root):
                clips.append({'clip': None, 'path': char_root})
            else:
                for entry in sorted(os.listdir(char_root)):
                    clip_root = os.path.join(char_root, entry)
                    if not os.path.isdir(clip_root):
                        continue
                    if not entry.startswith(name + '_'):
                        continue
                    sub = [d for d in os.listdir(clip_root)
                           if d in DIR_ANGLE and
                           os.path.isdir(os.path.join(clip_root, d))]
                    if sub:
                        clips.append({'clip': entry[len(name) + 1:],
                                      'path': clip_root})
            if clips:
                chars.append({'name': name, 'category': category,
                              'root': char_root, 'clips': clips})
    return chars


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
    def __init__(self, label, argv):
        self.label = label
        self.argv = argv
        self.status = 'queued'
        self.log = deque(maxlen=400)
        self.proc = None


class JobRunner:
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
            self.log.append('START %s' % job.label)
            try:
                job.proc = subprocess.Popen(
                    job.argv, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True,
                    encoding='utf-8', errors='replace',
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
            self.log.append('END %s -> %s' % (job.label, job.status))


class App:
    def __init__(self):
        self.tex = TexCache()
        self.runner = JobRunner()
        self.chars = []
        self.screen = 'CONVERT'
        self.input_path = ''
        self.name_buf = ''
        self.name_focus = False
        self.cat_idx = 0
        self.frames = 8
        self.char_px = 50.0
        self.size = 256
        self.do_deploy = True
        self.msg = []
        self.sel_char = None
        self.sel_clip = None
        self.sel_dir = 'S'
        self.playing = True
        self.anim_t = 0.0
        self.fps = 8.0
        self.zoom = 2.0
        self.pan = [0.0, 0.0]
        self.onion = False
        self.deploy_sel = set()
        self.deploy_log = []
        self._panning = False
        self.reload()

    def reload(self):
        self.chars = scan_output()
        self.tex.invalidate()

    def char_by_name(self, name):
        return next((c for c in self.chars if c['name'] == name), None)

    def select(self, name, clip_key=None):
        ch = self.char_by_name(name)
        if ch is None:
            return
        self.sel_char = name
        clips = ch['clips']
        pick = next((c for c in clips if c['clip'] == clip_key), None) \
            if clip_key is not None else None
        self.sel_clip = (pick or clips[0])['clip']
        self.anim_t = 0.0
        self.zoom = 2.0
        self.pan = [0.0, 0.0]
        self.tex.invalidate()

    def browse_input(self):
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            path = filedialog.askopenfilename(
                title='Pick a rigged model (or one FBX of a folder set)',
                filetypes=[('Models', '*.fbx *.glb *.gltf *.obj'),
                           ('All files', '*.*')])
            root.destroy()
        except Exception as e:
            self.msg = ['file dialog failed: %s' % e]
            return
        if path:
            self.input_path = path
            if not self.name_buf:
                self.name_buf = os.path.splitext(os.path.basename(path))[0]

    def queue_convert(self):
        if not self.input_path:
            self.msg = ['pick a model first']
            return
        if self.do_deploy and not os.path.isdir(GAME_ASSETS):
            self.msg = ['game assets folder not found: %s' % GAME_ASSETS]
            return
        blender = find_blender()
        if not blender:
            self.msg = ['blender.exe not found']
            return
        cat = CATEGORIES[self.cat_idx]
        argv = [blender, '--background', '--python', CONVERTER, '--',
                '--input', self.input_path,
                '--out', OUTPUT_DIR,
                '--category', cat]
        if self.name_buf:
            argv += ['--name', self.name_buf]
        argv += ['--frames', str(self.frames),
                 '--char-px', str(self.char_px),
                 '--size', str(self.size)]
        if self.do_deploy:
            argv += ['--deploy', GAME_ASSETS]
        label = '%s -> %s/%s' % (os.path.basename(self.input_path), cat,
                                 self.name_buf or '(auto)')
        self.runner.add(RenderJob(label, argv))
        self.msg = ['queued: %s' % label]
        self.screen = 'QUEUE'

    def draw_convert(self, x, y, w, h):
        draw_text('Bring a rigged model with animations. Mixamo: download '
                     'each animation', x + 10, y + 8, 14, COL_DIM)
        draw_text('as its own FBX (named like the game clips, e.g. '
                     'armed_idle), put them in', x + 10, y + 26, 14, COL_DIM)
        draw_text('one folder next to the base FBX, and pick the folder '
                     '(or one file).', x + 10, y + 44, 14, COL_DIM)
        if self._button(rl.Rectangle(x + 10, y + 74, 130, 28),
                        'Pick model/folder'):
            self.browse_input()
        draw_text(self.input_path[-64:] or '(no file)',
                     x + 150, y + 82, 13,
                     COL_TEXT if self.input_path else COL_DIM)
        ry = y + 116
        draw_text('name:', x + 10, ry + 6, 14, COL_TEXT)
        box = rl.Rectangle(x + 60, ry, 220, 24)
        rl.draw_rectangle_rec(box, COL_BG if not self.name_focus
                              else rl.Color(20, 26, 38, 255))
        rl.draw_rectangle_lines_ex(box, 1, COL_ACCENT if self.name_focus
                                   else COL_LINE)
        draw_text(self.name_buf or '<auto from file>', x + 68, ry + 5, 14,
                  COL_TEXT if self.name_buf else COL_DIM)
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
        ry += 36
        draw_text('category:', x + 10, ry + 5, 14, COL_TEXT)
        if self._button(rl.Rectangle(x + 80, ry, 220, 24),
                        CATEGORIES[self.cat_idx]):
            self.cat_idx = (self.cat_idx + 1) % len(CATEGORIES)
        ry += 36
        draw_text('frames per clip: %d (0 = all)' % self.frames,
                  x + 10, ry + 5, 14, COL_TEXT)
        self.frames = int(self._slider(rl.Rectangle(x + 170, ry + 6, 130, 16),
                                        self.frames, 0, 16))
        ry += 34
        draw_text('character height px: %d' % self.char_px,
                  x + 10, ry + 5, 14, COL_TEXT)
        self.char_px = self._slider(rl.Rectangle(x + 170, ry + 6, 130, 16),
                                     self.char_px, 16, 128)
        ry += 34
        hit = rl.Rectangle(x + 10, ry, 18, 18)
        rl.draw_rectangle_rec(hit, COL_PANEL)
        rl.draw_rectangle_lines_ex(hit, 1, COL_LINE)
        if self.do_deploy:
            rl.draw_rectangle(x + 14, ry + 4, 10, 10, COL_OK)
        if self._clicked(hit):
            self.do_deploy = not self.do_deploy
        draw_text('deploy into game assets after render', x + 36, ry, 14,
                  COL_TEXT)
        ry += 34
        if self._button(rl.Rectangle(x + 10, ry, 220, 30),
                        'Convert & queue render'):
            self.queue_convert()
        for i, ln in enumerate(self.msg[-6:]):
            draw_text(ln[:76], x + 10, ry + 44 + i * 16, 13, COL_DIM)

    def draw_queue(self, x, y, w, h):
        draw_text('Queue: one Blender job at a time (convert.py)',
                  x + 10, y + 8, 14, COL_DIM)
        y0 = y + 32
        for job in self.runner.jobs[-12:]:
            col = {'queued': COL_DIM, 'running': COL_ACCENT,
                   'done': COL_OK}.get(job.status, COL_BAD)
            rl.draw_rectangle(x + 10, y0, w - 20, 26,
                              COL_PANEL if job.status != 'running'
                              else rl.Color(40, 50, 66, 255))
            draw_text('%s  %s' % (job.label, job.status),
                      x + 18, y0 + 5, 14, col)
            y0 += 30
        if self._button(rl.Rectangle(x + 10, y0 + 8, 130, 26), 'Rescan'):
            self.reload()
            self.msg = []
        ly = y0 + 46
        rl.draw_rectangle(x + 10, ly, w - 20, h - (ly - y) - 10, COL_BG)
        lines = list(self.runner.log)[-40:]
        for i, ln in enumerate(lines):
            draw_text(ln[:150], x + 16, ly + 8 + i * 16, 13, COL_DIM)

    def clip_path(self):
        ch = self.char_by_name(self.sel_char)
        if ch is None:
            return None
        c = next((c for c in ch['clips'] if c['clip'] == self.sel_clip),
                 None)
        return c['path'] if c else None

    def draw_viewer(self, x, y, w, h):
        clip_path = self.clip_path()
        rl.draw_rectangle(x, y, w, h, COL_PANEL)
        frames = frames_in(clip_path, self.sel_dir)
        if not frames:
            draw_text('(no frames - convert a model first)', x + 16,
                      y + h // 2, 16, COL_DIM)
            return
        if self.playing:
            self.anim_t += rl.get_frame_time()
        idx = int(self.anim_t * max(0.5, self.fps)) % len(frames)
        tex = self.tex.get(frames[idx])
        if self.onion and len(frames) > 1:
            gt = self.tex.get(frames[(idx - 1) % len(frames)])
            self._blit(gt, x, y, w, h, tint=rl.Color(255, 255, 255, 90))
        self._blit(tex, x, y, w, h)
        draw_text('%s  %s  frame %d/%d'
                     % (os.path.basename(clip_path), self.sel_dir,
                        idx + 1, len(frames)),
                     x + 10, y + 6, 14, COL_TEXT)

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

    def draw_grid(self, x, y, w, h):
        path = self.clip_path()
        rl.draw_rectangle(x, y, w, h, COL_PANEL)
        if not path:
            draw_text('(nothing rendered)', x + 16, y + h // 2, 16, COL_DIM)
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
            if self._clicked(hit) and frames:
                self.sel_dir = d
                self.screen = 'VIEW'
                self.anim_t = 0.0
            draw_text(d, cell_x + 6, cell_y + ch - 26, 14,
                      COL_SEL if d == self.sel_dir else COL_TEXT)
            draw_text(str(len(frames)) if frames else '-',
                      cell_x + cw - 40, cell_y + ch - 26, 14,
                      COL_TEXT if frames else COL_DIM)
        draw_text('Grid: click a cell to open it in the viewer',
                  x + 10, y + 8, 14, COL_DIM)

    def draw_deploy(self, x, y, w, h):
        draw_text('Deploy: copy a rendered character folder into the game '
                  'assets', x + 10, y + 8, 14, COL_DIM)
        y0 = y + 32
        for ch in self.chars:
            hit = rl.Rectangle(x + 10, y0, 18, 18)
            rl.draw_rectangle_rec(hit, COL_PANEL)
            rl.draw_rectangle_lines_ex(hit, 1, COL_LINE)
            if ch['name'] in self.deploy_sel:
                rl.draw_rectangle(x + 14, y0 + 4, 10, 10, COL_OK)
            if self._clicked(hit):
                self.deploy_sel ^= {ch['name']}
            draw_text('%-18s [%s] %d clip(s)' % (ch['name'], ch['category'],
                                                 len(ch['clips'])),
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
        copied = 0
        for name in sorted(self.deploy_sel):
            ch = self.char_by_name(name)
            if ch is None:
                continue
            dest = os.path.join(GAME_ASSETS, ch['category'], ch['name'])
            if not os.path.isdir(ch['root']):
                out.append('%s: nothing rendered' % name)
                continue
            if dry:
                n = sum(len(os.listdir(os.path.join(dp)))
                        for dp, _dn, _fn in os.walk(ch['root'])
                        if os.path.isdir(dp))
                out.append('%s: would copy %d files -> %s'
                           % (name, n, dest))
                continue
            shutil.copytree(ch['root'], dest, dirs_exist_ok=True)
            n = sum(len(fn) for _dp, _dn, fn in os.walk(dest))
            copied += n
            out.append('%s: deployed %d files -> %s' % (name, n, dest))
        out.append('%s done: %d file(s)'
                   % ('DRY' if dry else 'DEPLOY', copied))
        return out

    def draw_rail(self):
        rail_w = 230
        rl.draw_rectangle(0, 0, rail_w, rl.get_screen_height(), COL_PANEL)
        for i, (key, label) in enumerate(SCREENS):
            hit = rl.Rectangle(8, 8 + i * 30, rail_w - 16, 26)
            active = self.screen == key
            if active:
                rl.draw_rectangle_rec(hit, COL_SEL)
            draw_text(label, 18, 12 + i * 30, 15,
                      rl.WHITE if active else COL_TEXT)
            if self._clicked(hit):
                self.screen = key
        y = 8 + len(SCREENS) * 30 + 12
        draw_text('CHARACTER', 12, y, 12, COL_DIM)
        y += 18
        for ch in self.chars:
            hit = rl.Rectangle(8, y, rail_w - 16, 22)
            if ch['name'] == self.sel_char:
                rl.draw_rectangle_rec(hit, rl.Color(50, 50, 60, 255))
            draw_text(ch['name'], 14, y + 2, 14, COL_TEXT)
            if self._clicked(hit):
                self.select(ch['name'])
            y += 24
        y += 8
        ch = self.char_by_name(self.sel_char)
        if ch:
            draw_text('CLIP', 12, y, 12, COL_DIM)
            y += 18
            for c in ch['clips']:
                hit = rl.Rectangle(24, y, rail_w - 40, 22)
                label = c['clip'] if c['clip'] else '(static)'
                if c['clip'] == self.sel_clip:
                    rl.draw_rectangle_rec(hit, rl.Color(50, 50, 60, 255))
                draw_text(label, 30, y + 2, 14, COL_TEXT)
                if self._clicked(hit):
                    self.select(self.sel_char, c['clip'])
                y += 24
            y += 8
            draw_text('DIR', 12, y, 12, COL_DIM)
            y += 18
            for d in DIRS:
                col_i = DIRS.index(d)
                hit = rl.Rectangle(24 + (col_i // 8) * 100,
                                   y + (col_i % 8) * 22, 44, 20)
                active = d == self.sel_dir
                rl.draw_rectangle_rec(hit, COL_SEL if active else COL_BG)
                draw_text(d, 32 + (col_i // 8) * 100,
                          y + 2 + (col_i % 8) * 22, 13,
                          rl.WHITE if active else COL_TEXT)
                if self._clicked(hit):
                    self.sel_dir = d
                    self.anim_t = 0.0
        return rail_w

    def draw_viewer_controls(self, x, y):
        if self._button(rl.Rectangle(x, y, 70, 26),
                        'pause' if self.playing else 'play'):
            self.playing = not self.playing
        frames = frames_in(self.clip_path(), self.sel_dir)
        if frames:
            idx = int(self.anim_t * max(0.5, self.fps)) % len(frames)
            m = rl.Rectangle(x + 80, y + 6, 220, 16)
            rl.draw_rectangle_rec(m, COL_BG)
            rl.draw_rectangle_lines_ex(m, 1, COL_LINE)
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
        if self.chars:
            self.select(self.chars[0]['name'])
        stages = ['CONVERT', 'QUEUE', 'VIEW', 'DEPLOY']
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
            rail_w = self.draw_rail()
            x = rail_w + 10
            w = rl.get_screen_width() - x - 10
            h = rl.get_screen_height() - 50
            if self.screen == 'CONVERT':
                self.draw_convert(x, 40, w, h - 40)
            elif self.screen == 'QUEUE':
                self.draw_queue(x, 40, w, h - 40)
            elif self.screen == 'VIEW':
                self.draw_viewer_controls(x, 6)
                self.draw_viewer(x, 40, w, h - 40)
            elif self.screen == 'DEPLOY':
                self.draw_deploy(x, 40, w, h - 40)
            draw_text('[space] play  [arrows] frame  [wheel] zoom  '
                      '[G]rid  [V]iewer  [O]nion  [RMB] pan',
                      x, rl.get_screen_height() - 24, 12, COL_DIM)
            rl.end_drawing()
            if selftest and frame_i % 10 == 0:
                rl.take_screenshot(
                    ('studio_shot_%d_%s.png' % (frame_i, self.screen))
                    .encode())
            if selftest and frame_i >= len(stages) * 10:
                break
        rl.close_window()

    def _shortcuts(self):
        if rl.is_key_pressed(rl.KEY_SPACE):
            self.playing = not self.playing
        if rl.is_key_pressed(rl.KEY_RIGHT):
            self.anim_t += 1 / max(0.5, self.fps)
            self.playing = False
        if rl.is_key_pressed(rl.KEY_LEFT):
            self.anim_t -= 1 / max(0.5, self.fps)
            self.playing = False
        if rl.is_key_pressed(rl.KEY_G):
            self.screen = 'VIEW'
        if rl.is_key_pressed(rl.KEY_O):
            self.onion = not self.onion
        wheel = rl.get_mouse_wheel_move()
        if wheel:
            if rl.get_mouse_position().x > 240 and self.screen == 'VIEW':
                self.zoom = max(0.25, min(16.0, self.zoom * (1.15 if wheel
                                                             > 0 else 1 / 1.15)))
        mp = rl.get_mouse_position()
        if rl.is_mouse_button_pressed(rl.MOUSE_RIGHT_BUTTON):
            self._panning = True
        if rl.is_mouse_button_released(rl.MOUSE_RIGHT_BUTTON):
            self._panning = False
        if self._panning and mp.x > 240 and self.screen == 'VIEW':
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
