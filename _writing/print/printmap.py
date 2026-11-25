"""The print-map engine: a map's `Scripts/map.py` says what the map is, this turns it into pages.

Python does the geography. Each page is one static HTML document sized in inches; its map frames are inline SVG.
Lengths inside a frame, and every type size and line weight a map declares, are in reference points: points on a
letter or tabloid page, scaled up together on a display page (`Page.k`), so one set of settings serves every size.

A map.py holds NAME, SETTINGS (declared with the helpers below) and `pages(m)`, which builds its pages on `m`.
"""
import html as _html
import json
import math
import re
from datetime import date
from pathlib import Path

import numpy as np
import pyogrio
import pyproj
import shapely
from shapely.geometry import box

import text as T

# ---------------------------------------------------------------- settings a map declares

def _setting(type_, key, label, group, value, effect, **more):
    return {'key': key, 'label': label, 'group': group, 'type': type_, **more, 'value': value, 'effect': effect}


def number(key, label, group, value, min, max, step, effect='paint'):
    return _setting('number', key, label, group, value, effect, min=min, max=max, step=step)


def color(key, label, group, value, effect='paint'):
    return _setting('color', key, label, group, value, effect)


def text(key, label, group, value):
    return _setting('text', key, label, group, value, 'structure')


def legend(key='legend', value=None):
    """The legend, edited whole in the workbench."""
    return _setting('legend', key, 'Legend', 'Text', value or [], 'structure')


def choice(key, label, group, value, options, effect='geometry'):
    return _setting('choice', key, label, group, value, effect, options=options)


def toggle(key, label, group, value, effect='structure'):
    return _setting('toggle', key, label, group, value, effect)


def view(key, label, group, center, width_km):
    """A frame's extent: the longitude and latitude at its centre and the ground width it shows."""
    return _setting('view', key, label, group, {'center': center, 'width_km': width_km}, 'geometry')


def labels(key='labels'):
    """The label pins, edited on the map itself."""
    return _setting('labels', key, 'Label Positions', 'Labels', [], 'structure')


PAGES = {'Letter Landscape': (11, 8.5), 'Letter Portrait': (8.5, 11), 'Tabloid Landscape': (17, 11),
         'Tabloid Portrait': (11, 17), 'Display 36 × 24': (36, 24), 'Display 24 × 36': (24, 36)}
INK, STONE, PLUM = '#22282B', '#6B7075', '#5E4B8B'


class Var:
    """A paint setting used in a style: written as a CSS variable, so the workbench can change it in place."""
    def __init__(self, key, value):
        self.key, self.value = key, value

    def __str__(self):
        return f'var(--{self.key.replace(".", "-")})'


class BuildError(Exception):
    pass


def esc(s):
    return _html.escape(str(s), quote=True)


def fmt(v):
    """A coordinate to a hundredth of a point."""
    s = f'{v:.2f}'
    return s.rstrip('0').rstrip('.') if '.' in s else s


# ---------------------------------------------------------------- reading data

_read_cache = {}


def read_layer(path, layer):
    """A GeoPackage layer, opened read-only and immutable so no -wal or -shm file appears beside it."""
    k = (str(path), layer)
    if k not in _read_cache:
        _read_cache[k] = pyogrio.read_dataframe(str(path), layer=layer, IMMUTABLE='YES', NOLOCK='YES')
    return _read_cache[k]


def layer_citation(path, layer):
    """The layer's citation: its description in the GeoPackage's contents table. First line is the credit as
    printed; the full citation may follow beneath."""
    import sqlite3
    con = sqlite3.connect(f'{Path(path).resolve().as_uri()}?immutable=1', uri=True)
    try:
        row = con.execute('select description from gpkg_contents where table_name = ?', (layer,)).fetchone()
    finally:
        con.close()
    return (row[0] or '').strip() if row else ''


def raster_citation(path):
    """A raster's citation: its image description, or a sidecar `<file>.citation.txt` (a Blender render)."""
    side = Path(str(path) + '.citation.txt')
    if side.exists():
        return side.read_text().strip()
    import rasterio
    with rasterio.open(path) as r:
        tags = r.tags()
    return (tags.get('TIFFTAG_IMAGEDESCRIPTION') or tags.get('citation') or '').strip()


# ---------------------------------------------------------------- the map, its pages and frames

CACHE = {}        # stages kept between builds in one process (the workbench session), each under what it read


def cached(key, make):
    if key not in CACHE:
        CACHE[key] = make()
    return CACHE[key]


class Map:
    """`preview` builds the page for the workbench: label guides and the labels left off are included (hidden),
    labels carry what dragging needs, and a layer switched off by a paint toggle stays in the page, unseen."""
    def __init__(self, project, declared, saved=None, today=None, dpi=350, margin=0.0, fonts=None, labels=True,
                 preview=False, raster_base=''):
        self.project = Path(project)
        self.settings = [dict(s) for s in declared]
        saved = {s['key']: s['value'] for s in (saved or [])}
        for s in self.settings:
            if s['key'] in saved:
                s['value'] = saved[s['key']]
        self.values = {s['key']: s['value'] for s in self.settings}
        self.today = today or date.today().isoformat()
        self.dpi, self.margin, self.labels, self.preview, self.raster_base = dpi, margin, labels, preview, raster_base
        self.fonts = fonts or (T.FONTS.as_uri() + '/')
        self.pages = []

    def __getitem__(self, key):
        return self.values[key]

    def var(self, key):
        return Var(key, self.values[key])

    def color(self, c):
        """A colour written as itself, or the name of a colour setting, which then follows that setting."""
        return self.var(c) if c in self.values else c

    def page(self, size, title='', subtitle='', credits='', keys=('title', 'subtitle', 'credits')):
        """A page. `keys` names the text settings its title, subtitle and credits come from, so they can be typed on it."""
        p = Page(self, len(self.pages) + 1, size, title, subtitle, credits)
        p.keys = keys
        self.pages.append(p)
        return p

    def path(self, source):
        return self.project / source

    def pins(self, page, frame):
        key = next((s['key'] for s in self.settings if s['type'] == 'labels'), None)
        return [p for p in (self.values.get(key) or []) if p.get('page', 1) == page and p.get('frame', 'main') == frame]

    def css_vars(self):
        out = []
        for s in self.settings:
            if s['effect'] != 'paint':
                continue
            name = '--' + s['key'].replace('.', '-')
            if s['type'] in ('number', 'color'):
                out.append(f'{name}:{s["value"]};')
            elif s['type'] == 'toggle':
                out.append(f'{name}:{"inline" if s["value"] else "none"};')
        return ''.join(out)


class Page:
    """One page. Left alone, it is laid out by arithmetic from its size: title block, one main frame (with a
    legend column beside it when the map has a legend), and beneath it the scale bar, credits and north arrow.
    A map with its own arrangement calls `place` with where each piece goes and gives each frame its rectangle;
    every position is in reference points from the page's top left."""
    def __init__(self, m, n, size, title, subtitle, credits):
        self.map, self.n, self.size = m, n, size
        self.w_in, self.h_in = PAGES[size]
        self.W, self.H = self.w_in * 72, self.h_in * 72
        self.k = max(1.0, min(self.W, self.H) / 792)          # reference points to page points
        self.title, self.subtitle, self.credits = title, subtitle, credits
        self.frames, self.notes = [], []
        self._legend = None
        self.L = None             # where each piece goes
        self.custom = False
        self.id = f'p{n}'

    # the standard layout, in reference points (multiply by k for the page)
    MARGIN, TITLE_TOP, SUB_TOP, RULE_Y, FRAME_TOP, FOOT_GAP, BOTTOM = 34, 19, 46.5, 68, 76.5, 10, 30
    CREDIT_SIZE, CREDIT_LEAD, BAR_FRACTION = 6.5, 9.5, 0.26

    def legend(self, groups, width=130, gap=14, rect=None, columns=1, swatch=(10.5, 6.6), size=7, lead=9.6, split='group'):
        """The legend: groups, each an optional heading and its items, each a label with a swatch (fill, dot or
        line; a line solid, dashed or dotted) in a colour or a colour setting's key, and an optional code. In the
        standard layout it is a column `width` wide to the right of the frame (call it before `frame`); with
        `rect` (x, y, w, h) it fills that rectangle in `columns`, whole groups to a column (`split='group'`) or
        item by item (`split='item'`)."""
        self._legend = dict(groups=groups or [], width=width, gap=gap, rect=rect, columns=columns, swatch=swatch, size=size, lead=lead, split=split)

    def place(self, **where):
        """A layout of the map's own: title=(x, y), subtitle=(x, y), rule=(x, y, w), credits=(x, y, w),
        scale=[(frame key, x, y, greatest width, 'mi' or 'km', segments)], north=(frame key, right, top),
        title_size, subtitle_size. A piece not named is not drawn."""
        self.custom = True
        self.L = {**(self.L or {}), **where}

    def note(self, text, x, y, style='sans', size=7, color=STONE, lead=None, width=None):
        """A line or paragraph of text set on the page at (x, y), wrapped to `width` when given."""
        self.notes.append(dict(text=text, x=x, y=y, style=style, size=size, color=color, lead=lead or size * 1.36, width=width))

    def _layout(self):
        k = self.k
        Wr, Hr = self.W / k, self.H / k
        full = Wr - 2 * self.MARGIN
        lg = self._legend
        fw = full - (lg['width'] + lg['gap'] if lg and lg['groups'] else 0)
        x0 = self.MARGIN
        self.L = {'title': (x0, self.TITLE_TOP), 'subtitle': (x0, self.SUB_TOP), 'rule': (x0, self.RULE_Y, full),
                  'credits': (x0 + full * 0.37, 0, full * 0.63 - 40)}
        lines = self._credit_lines()
        self.foot_h = max(34, len(lines) * self.CREDIT_LEAD + 2)
        fh = Hr - self.FRAME_TOP - self.FOOT_GAP - self.foot_h - self.BOTTOM
        fy2 = self.FRAME_TOP + fh + self.FOOT_GAP
        self.L.update(frame=(x0, self.FRAME_TOP, fw, fh), credits=(x0 + full * 0.37, fy2, full * 0.63 - 40),
                      scale=[('main', x0, fy2, fw * self.BAR_FRACTION, 'mi', 4)], north=('main', x0 + full, fy2))
        if lg and lg['groups']:
            lg['rect'] = (x0 + fw + lg['gap'], self.FRAME_TOP, lg['width'], fh)

    def frame(self, crs, view, key='main', rect=None, border=True):
        if rect is None:
            if self.L is None or 'frame' not in self.L:
                self._layout()
            rect = self.L['frame']
        f = Frame(self, key, rect, crs, view)
        f.border = border
        self.frames.append(f)
        return f

    def image(self, source, rect, key='main', citation=True):
        """A frame holding a picture (a Blender render) in place of ground: positions in it run 0 to 1 across and
        down, and labels are pinned by those."""
        f = ImageFrame(self, key, rect, source, citation)
        self.frames.append(f)
        return f

    def sources(self):
        """The printed credit of every layer drawn on this page, in drawing order, each once."""
        out = []
        for f in self.frames:
            for name, cite in f.citations:
                if not cite:
                    raise BuildError(f'{name} has no citation. A drawn layer must carry one: a GeoPackage layer in its '
                                     'description (gpkg_contents.description), a raster in its image description or a '
                                     '.citation.txt beside it.')
                line = cite.splitlines()[0].strip()
                if line not in out:
                    out.append(line)
        return out

    def _credit_text(self, with_sources=True):
        s = self.credits.replace('{date}', self.map.today)
        if '{sources}' in s:
            s = s.replace('{sources}', '  ·  '.join(self.sources()) if with_sources else '')
        return s

    def _wrap(self, text, style, size, width, items=False):
        """Text wrapped to a width with the page's own font metrics. With `items`, a line breaks between its items
        (set apart by a middle dot) before it breaks inside one."""
        fits = lambda s: T.width(s, style, size) <= width
        lines = []
        for para in text.split('\n'):
            cur = ''
            for item in ([i.strip() for i in para.split('·') if i.strip()] if items else [para]):
                trial = f'{cur}  ·  {item}' if cur else item
                if items and fits(trial):
                    cur = trial
                    continue
                if cur:
                    lines.append(cur)
                cur = ''
                for word in item.split():
                    trial = f'{cur} {word}' if cur else word
                    if cur and not fits(trial):
                        lines.append(cur)
                        cur = word
                    else:
                        cur = trial
            lines.append(cur)
        return [l for l in lines if l] if items else lines

    def _credit_lines(self):
        return self._wrap(self._credit_text(with_sources=bool(self.frames)), 'serif', self.CREDIT_SIZE, self.L['credits'][2], items=True)

    def _legend_svg(self):
        lg = self._legend
        m, k = self.map, self.k
        x, y0, width, height = lg['rect']
        cols = lg['columns']
        gutter = 16
        cw = (width - gutter * (cols - 1)) / cols
        size, lead, (sw, sh) = lg['size'], lg['lead'], lg['swatch']
        blocks = []          # (height, [svg with y from 0], starts a group)
        for g in lg['groups']:
            coded = any(it.get('code') for it in g.get('items', []))
            tx = sw + 5.5 + (size * 3.3 if coded else 0)
            first = True
            if g.get('heading'):
                blocks.append((size + 6.5, [f'<text x="0" y="{fmt(size + 1)}" style="{T.css("sans-semibold")};font-size:{size + 0.5}px;fill:{INK}">{esc(g["heading"])}</text>'], True, True))
                first = False
            for it in g.get('items', []):
                c = m.color(it.get('color', INK))
                shape = it.get('shape', 'fill')
                rows = [r for r in self._wrap(str(it.get('label', '')), 'sans', size, cw - tx)] or ['']
                h = max(sh + 2.6, len(rows) * lead) + 1.2
                mid = min(sh / 2 + 1.3, h / 2)
                base = mid + size * 0.34
                out = []
                if shape == 'dot':
                    out.append(f'<circle cx="{fmt(sw / 2)}" cy="{fmt(mid)}" r="{fmt(min(sw, sh) * 0.35)}" style="fill:{c}" stroke="#fff" stroke-width="0.4"/>')
                elif shape == 'line':
                    dash = {'dashed': ' stroke-dasharray="3 1.6"', 'dotted': ' stroke-dasharray="0.1 2.2" stroke-linecap="round"'}.get(it.get('dash'), '')
                    out.append(f'<path d="M0,{fmt(mid)} H{fmt(sw)}" fill="none" stroke-width="{it.get("weight", 1.1)}"{dash} style="stroke:{c}"/>')
                elif shape == 'ring':
                    out.append(f'<circle cx="{fmt(sw / 2)}" cy="{fmt(mid)}" r="2" fill="#fff" stroke-width="0.8" style="stroke:{c}"/>')
                else:
                    out.append(f'<rect x="0" y="{fmt(mid - sh / 2)}" width="{fmt(sw)}" height="{fmt(sh)}" style="fill:{c}" stroke="{STONE}" stroke-width="{0.25 if sh > 8 else 0}"/>')
                if it.get('code'):
                    out.append(f'<text x="{fmt(sw + 5.5)}" y="{fmt(base)}" style="{T.css("sans-semibold" if sh > 8 else "sans")};font-size:{size}px;fill:{INK if sh > 8 else STONE}">{esc(it["code"])}</text>')
                for i, r in enumerate(rows):
                    out.append(f'<text x="{fmt(tx)}" y="{fmt(base + i * lead)}" style="{T.css("sans")};font-size:{size}px;fill:{INK}">{esc(r)}</text>')
                blocks.append((h, out, first, False))
                first = False
        # flow the blocks into the columns
        if lg['split'] == 'group':
            units, cur = [], []
            for b in blocks:
                if b[2] and cur:
                    units.append(cur)
                    cur = []
                cur.append(b)
            if cur:
                units.append(cur)
        else:
            units = [[b] for b in blocks]
        out, col, y = [], 0, 0.0
        per_col = sum(b[0] for b in blocks) / cols          # whole groups go to the column their middle falls in
        for u in units:
            uh = sum(b[0] for b in u)
            over = y + uh > per_col + 0.01 if lg['split'] == 'item' else y + uh / 2 > per_col
            if y > 0 and (y + uh > height + 0.01 or over) and col < cols - 1:
                col, y = col + 1, 0.0
            if u[0][2] and u[0][3] and y > 0:
                y += 6.5                       # air above a heading
            for h, svg, _, _ in u:
                out.append(f'<g transform="translate({fmt(col * (cw + gutter))} {fmt(y)})">' + ''.join(svg) + '</g>')
                y += h
            if y > height + 0.01:
                raise BuildError('the legend does not fit its place on the page; shorten it')
        return (f'<svg id="{self.id}-legend" xmlns="http://www.w3.org/2000/svg" style="left:{x * k:.2f}pt;top:{y0 * k:.2f}pt;'
                f'width:{width * k:.2f}pt;height:{height * k:.2f}pt" viewBox="0 0 {fmt(width)} {fmt(height)}">' + ''.join(out) + '</svg>')

    def render(self, raster_dir):
        """The page in pieces: (head, parts), parts a list of (id, html) in order, the id None for the fixed
        markup between the pieces that can change. Rasters are written into `raster_dir`."""
        k = self.k
        m = self.map
        L = self.L
        i = self.id
        pt = lambda v: f'{v * k:.2f}pt'
        self.credit_lines = self._credit_lines() if 'credits' in L else []     # now that every layer is known
        if not self.custom and max(34, len(self.credit_lines) * self.CREDIT_LEAD + 2) > self.foot_h + 0.01:
            raise BuildError('the credits need more lines than the page was laid out for; shorten them or the sources')
        parts = []
        if 'title' in L:
            ts = L.get('title_size', 18)
            parts.append((f'{i}-title', f'<div id="{i}-title" class="title" data-setting="{self.keys[0]}" style="left:{pt(L["title"][0])};top:{pt(L["title"][1])};font-size:{pt(ts)};line-height:{pt(ts * 1.33)}">{esc(self.title)}</div>'))
        if 'subtitle' in L:
            ss = L.get('subtitle_size', 9.5)
            parts.append((f'{i}-subtitle', f'<div id="{i}-subtitle" class="subtitle" data-setting="{self.keys[1]}" style="left:{pt(L["subtitle"][0])};top:{pt(L["subtitle"][1])};font-size:{pt(ss)};line-height:{pt(ss * 1.47)}">{esc(self.subtitle)}</div>'))
        if 'rule' in L:
            parts.append((None, f'<div class="rule" style="left:{pt(L["rule"][0])};top:{pt(L["rule"][1])};width:{pt(L["rule"][2])};height:{pt(1)}"></div>'))
        for f in self.frames:
            parts += f.parts(raster_dir)
        by_key = {f.key: f for f in self.frames}
        if self._legend and self._legend['groups'] and self._legend['rect']:
            parts.append((f'{i}-legend', self._legend_svg()))
        for n, (fk, x, y, max_w, unit, segs) in enumerate(L.get('scale', [])):
            f = by_key[fk]
            parts.append((f'{f.id}-scale-{unit}', f.scale_bar_svg(x, y, max_w, unit, segs)))
        if 'credits' in L:
            cx, cy, cwid = L['credits']
            parts.append((f'{i}-credits', f'<div id="{i}-credits" class="credits" data-setting="{self.keys[2]}" style="left:{pt(cx)};top:{pt(cy)};width:{pt(cwid + 40)};'
                          f'font-size:{pt(self.CREDIT_SIZE)};line-height:{pt(self.CREDIT_LEAD)}">' + '<br>'.join(esc(l) for l in self.credit_lines) + '</div>'))
        if 'north' in L:
            parts.append((f'{by_key[L["north"][0]].id}-north', by_key[L['north'][0]].north_arrow_svg(L['north'][1], L['north'][2])))
        for n, t in enumerate(self.notes):
            rows = self._wrap(t['text'], t['style'], t['size'], t['width']) if t['width'] else t['text'].split('\n')
            parts.append((f'{i}-note-{n + 1}', f'<div id="{i}-note-{n + 1}" class="note" style="{T.css(t["style"])};color:{t["color"]};left:{pt(t["x"])};top:{pt(t["y"])};'
                          f'font-size:{pt(t["size"])};line-height:{pt(t["lead"])}">' + '<br>'.join(esc(r) for r in rows) + '</div>'))
        head = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{esc(self.title)}</title>
<style>
{T.font_css(m.fonts)}
@page{{size:{self.w_in}in {self.h_in}in;margin:0}}
html,body{{margin:0;padding:0}}
body{{width:{self.w_in}in;height:{self.h_in}in;position:relative;overflow:hidden;background:#fff;{m.css_vars()}}}
body>div,body>svg{{position:absolute}}
.title{{{T.css('serif-semibold')};color:{INK};white-space:pre}}
.subtitle{{{T.css('serif')};color:{STONE};white-space:pre}}
.credits{{{T.css('serif')};color:{STONE};white-space:pre}}
.note{{white-space:pre}}
.rule{{background:{PLUM}}}
svg text{{white-space:pre;text-rendering:geometricPrecision;font-variant-ligatures:none;font-kerning:normal}}
svg .fill{{fill-rule:evenodd}}
svg .line{{fill:none;stroke-linejoin:round;stroke-linecap:round}}
svg .guides{{display:none}}
svg .ghosts{{display:none;opacity:0.4}}
</style></head>
<body>
'''
        return head, parts

    def html(self, raster_dir):
        """The page as one static HTML document."""
        head, parts = self.render(raster_dir)
        return head + '\n'.join(h for _, h in parts) + '\n</body></html>\n'


class Frame:
    """A rectangle on the page showing ground: a projection and a view. Inside, lengths are reference points."""
    def __init__(self, page, key, rect, crs, view):
        self.page, self.key, self.view = page, key, view
        self.x, self.y, self.w, self.h = rect
        self.crs = pyproj.CRS.from_user_input(crs)
        self.to_ground, self.to_lonlat = cached(('proj', self.crs.to_wkt()), lambda: (
            pyproj.Transformer.from_crs('EPSG:4326', self.crs, always_xy=True), pyproj.Transformer.from_crs(self.crs, 'EPSG:4326', always_xy=True)))
        cx, cy = self.to_ground.transform(*view['center'])
        gw = view['width_km'] * 1000.0
        self.s = self.w / gw                                   # reference points per ground unit
        gh = self.h / self.s
        self.gx0, self.gy1 = cx - gw / 2, cy + gh / 2
        self.ground = (self.gx0, self.gy1 - gh, self.gx0 + gw, self.gy1)
        self.id = f'{page.id}-{key}'
        self.at = (round(self.gx0, 3), round(self.gy1, 3), round(self.s, 12), round(self.w, 4), round(self.h, 4), page.map.margin)   # what a cached stage depends on
        self.layers, self.rules, self.citations, self.notes = [], [], [], []
        self.label_result = None
        self.border = True

    # ground <-> frame
    def to_frame(self, xy):
        xy = np.asarray(xy, dtype='float64')
        return np.column_stack([(xy[:, 0] - self.gx0) * self.s, (self.gy1 - xy[:, 1]) * self.s])

    def lonlat_to_frame(self, lon, lat):
        x, y = self.to_ground.transform(lon, lat)
        return (x - self.gx0) * self.s, (self.gy1 - y) * self.s

    def frame_to_lonlat(self, u, v):
        return self.to_lonlat.transform(self.gx0 + u / self.s, self.gy1 - v / self.s)

    def _ground_geoms(self, source, layer, where):
        """A layer's features in the frame's projection, with their attributes."""
        def make():
            g = read_layer(self.page.map.path(source), layer)
            if where:
                g = g.query(where)
            if g.crs is not None and not g.crs.equals(self.crs):
                g = g.to_crs(self.crs)
            return g
        return cached(('ground', str(self.page.map.path(source)), layer, where, self.crs.to_wkt()), make)

    def _to_frame_geoms(self, simple, clip_pad=2.0):
        """Simplified ground geometries clipped to the frame (and the margin of ground kept for panning), in frame
        units."""
        m = self.page.map
        gx0, gy0, gx1, gy1 = self.ground
        px, py = clip_pad / self.s + m.margin * (gx1 - gx0), clip_pad / self.s + m.margin * (gy1 - gy0)
        clipped = shapely.clip_by_rect(simple, gx0 - px, gy0 - py, gx1 + px, gy1 + py)
        clipped = clipped[~shapely.is_empty(clipped)]
        return shapely.transform(clipped, self.to_frame)

    # layers
    def _add(self, kind, id_, source, layer, where, show, style, cite=True, **more):
        m = self.page.map
        if isinstance(show, Var):              # a paint toggle: in the workbench the layer stays in the page, shown or not
            if not show.value and not m.preview:
                return None
            style = {**style, 'display': show}
        elif show is False:
            return None
        g = self._ground_geoms(source, layer, where)
        invert = more.pop('invert', False)
        L = {'kind': kind, 'id': id_, 'source': source, 'layer': layer, 'where': where, 'style': style, 'data': g, **more}
        key = ('frame', str(m.path(source)), layer, where, self.crs.to_wkt(), self.at, m.preview, invert)

        # Simplified once for the scale, to a tolerance too small to see, and before it is cut to the frame: what
        # lies in the frame is then the same whatever ground is kept around it, so the preview's labels are the print's
        def simplify():
            out = shapely.simplify(g.geometry.values, 0.04 / self.s, preserve_topology=False)
            out = out[~shapely.is_empty(out)]
            if invert:               # everything but the layer: a veil over the ground outside it
                gx0, gy0, gx1, gy1 = self.ground
                far = 3 * max(gx1 - gx0, gy1 - gy0)
                out = np.array([shapely.difference(box(gx0 - far, gy0 - far, gx1 + far, gy1 + far), shapely.union_all(shapely.make_valid(out)))], dtype=object)
            return out

        def make():
            geoms = self._to_frame_geoms(cached(('simple', *key[1:5], round(self.s, 12), invert and self.at), simplify))
            return geoms, ([] if kind in ('data', 'point') else [self.path_one(x) for x in geoms])
        L['geoms'], L['d'] = cached(key, make)
        self.layers.append(L)
        if cite:
            self.citations.append((f'{Path(source).name}, layer {layer}', cached(('cite', str(m.path(source)), layer), lambda: layer_citation(m.path(source), layer))))
        return L

    def fill(self, id, source, layer, where=None, show=True, fill='none', stroke='none', weight=0, blend=None, opacity=None, dash=None, invert=False, cite=True):
        """Areas. With `invert`, the fill covers everything but the layer's areas."""
        return self._add('fill', id, source, layer, where, show, dict(fill=fill, stroke=stroke, weight=weight, blend=blend, opacity=opacity, dash=dash), cite=cite, invert=invert)

    def line(self, id, source, layer, where=None, show=True, stroke=INK, weight=0.3, dash=None, opacity=None, blend=None):
        return self._add('line', id, source, layer, where, show, dict(stroke=stroke, weight=weight, dash=dash, opacity=opacity, blend=blend))

    def point(self, id, source, layer, where=None, show=True, radius=1.5, fill=INK, stroke='none', weight=0):
        return self._add('point', id, source, layer, where, show, dict(fill=fill, stroke=stroke, weight=weight), radius=radius)

    def data(self, id, source, layer, where=None, show=True):
        """Features that are labelled but not drawn (a river's centreline through its own water)."""
        return self._add('data', id, source, layer, where, show, {})

    def relief(self, dem, show=True, **opts):
        """Shaded and tinted relief computed from an elevation raster, drawn beneath everything after it."""
        if show is False:
            return
        m = self.page.map
        self.layers.append({'kind': 'relief', 'id': 'relief', 'dem': dem, 'opts': opts})
        self.citations.append((Path(dem).name, cached(('cite', str(m.path(dem))), lambda: raster_citation(m.path(dem)))))

    def label(self, layer, text, kind, style='sans', size=7, color=INK, priority=5, key=None, show=True, **opts):
        """A label rule for a drawn layer. `text` and `key` are field names (or functions of a feature's row);
        the key must stay the same for a feature from build to build, since pins are saved under it."""
        if show is False or not any(L['id'] == layer for L in self.layers):
            return
        self.rules.append({'layer': layer, 'text': text, 'kind': kind, 'style': style, 'size': size, 'color': color,
                           'priority': priority, 'key': key or text, **opts})

    def note(self, text, at, style='sans', size=7, color=STONE, rotate=0, spacing=0):
        """Lettering set on the map by hand: text centred at a longitude and latitude, turned counter-clockwise by
        the given degrees and letterspaced. It is drawn as given; labels do not move for it."""
        x, y = self.lonlat_to_frame(*at)
        w = T.width(text, style, size, spacing)
        self.notes.append(f'<text x="{fmt(x - w / 2)}" y="{fmt(y + size * 0.33)}" transform="rotate({-rotate} {fmt(x)} {fmt(y)})" '
                          f'style="{T.css(style)};font-size:{size}px;fill:{color};letter-spacing:{spacing}px">{esc(text)}</text>')

    def place_labels(self):
        if self.label_result is None and self.rules and self.page.map.labels:
            import labels
            self.label_result = labels.place(self)
        return self.label_result

    # drawing
    def _css(self, L):
        s, id_ = L['style'], f'#{self.id}-{L["id"]}'
        d = []
        if L['kind'] == 'line':
            d += [f'stroke:{s["stroke"]}', f'stroke-width:{s["weight"]}']
        else:
            d += [f'fill:{s["fill"]}', f'stroke:{s["stroke"]}', f'stroke-width:{s["weight"]}']
        if s.get('dash'):
            d.append(f'stroke-dasharray:{" ".join(str(v) for v in s["dash"])}')
            d.append('stroke-linecap:butt')
        if s.get('opacity') is not None:
            d.append(f'opacity:{s["opacity"]}')
        if s.get('blend'):
            d.append(f'mix-blend-mode:{s["blend"]}')
        if s.get('display') is not None:
            d.append(f'display:{s["display"]}')
        return f'{id_}{{{";".join(d)}}}'

    @staticmethod
    def path_one(geom):
        """One geometry as SVG path data: every line and ring a subpath."""
        d = []
        for part in shapely.get_parts(geom):
            t = shapely.get_type_id(part)
            if t == 3:
                rings = [part.exterior, *part.interiors]
            elif t in (1, 2):
                rings = [part]
            else:
                continue
            for r in rings:
                c = np.round(shapely.get_coordinates(r), 2)
                if len(c) < 2:
                    continue
                keep = np.ones(len(c), bool)
                keep[1:] = np.any(c[1:] != c[:-1], axis=1)          # points that rounding made the same
                c = c[keep]
                if len(c) < 2:
                    continue
                d.append('M' + ' '.join([f'{a:g},{b:g}' for a, b in c.tolist()]) + ('Z' if t == 3 else ''))
        return ''.join(d)

    def parts(self, raster_dir):
        """The frame in pieces, (id, html) in order: its opening, each layer, the labels, its closing."""
        k = self.page.k
        m = self.page.map
        res = self.place_labels()
        css, out = [], []
        for L in self.layers:
            gid = f'{self.id}-{L["id"]}'
            if L['kind'] == 'relief':
                import relief
                key = ('relief', str(m.path(L['dem'])), self.crs.to_wkt(), self.at, k, m.dpi, repr(sorted(L['opts'].items())), str(raster_dir))
                tiles = cached(key, lambda: relief.tiles(self, m.path(L['dem']), Path(raster_dir), gid, m.dpi, margin=m.margin, **L['opts']))
                out.append((gid, f'<g id="{gid}">' + ''.join(
                    f'<image href="{m.raster_base}{f}" x="{fmt(x)}" y="{fmt(y)}" width="{w:.4f}" height="{h:.4f}" preserveAspectRatio="none"/>'
                    for f, x, y, w, h in tiles) + '</g>'))
                continue
            if L['kind'] == 'data':
                continue
            if L['kind'] == 'picture':         # copied beside the page, under a name that changes when the picture does
                import hashlib, shutil
                src = m.path(L['source'])
                name = f'{gid}-{hashlib.sha1(src.read_bytes()).hexdigest()[:10]}{src.suffix}'
                if not (Path(raster_dir) / name).exists():
                    shutil.copyfile(src, Path(raster_dir) / name)
                out.append((gid, f'<image id="{gid}" href="{m.raster_base}{name}" x="0" y="0" width="{self.w:.3f}" height="{self.h:.3f}" preserveAspectRatio="none"/>'))
                continue
            css.append(self._css(L))
            if L['kind'] == 'point':
                pts = shapely.get_coordinates(L['geoms'])
                out.append((gid, f'<g id="{gid}">' + ''.join(f'<circle cx="{fmt(x)}" cy="{fmt(y)}" r="{L["radius"]}"/>' for x, y in pts) + '</g>'))
                continue
            d = L['d']
            if res and L['id'] in res.cut:
                geoms, hit = res.cut[L['id']]
                if hit is None:
                    d = [self.path_one(x) for x in geoms]
                else:
                    d = list(d)
                    for j in hit:
                        d[j] = self.path_one(geoms[j])
            out.append((gid, f'<path id="{gid}" class="{L["kind"]}" d="{"".join(d)}"/>'))
        if res:
            out.append((f'{self.id}-labels', res.svg(f'{self.id}-labels', m.preview)))
            if m.preview:
                out.append((f'{self.id}-guides', res.guides_svg(f'{self.id}-guides')))
                out.append((f'{self.id}-ghosts', res.ghosts_svg(f'{self.id}-ghosts')))
        w, h = f'{self.w:.3f}', f'{self.h:.3f}'
        opening = (f'<svg id="{self.id}" xmlns="http://www.w3.org/2000/svg" style="left:{self.x * k:.2f}pt;top:{self.y * k:.2f}pt;'
                   f'width:{self.w * k:.2f}pt;height:{self.h * k:.2f}pt;overflow:visible" viewBox="0 0 {w} {h}">\n'
                   f'<defs><clipPath id="{self.id}-clip"><rect width="{w}" height="{h}"/></clipPath></defs>\n'
                   f'<g clip-path="url(#{self.id}-clip)"><g id="{self.id}-content">')
        if self.notes:
            out.append((f'{self.id}-notes', f'<g id="{self.id}-notes">' + ''.join(self.notes) + '</g>'))
        closing = '</g></g>\n' + (f'<rect width="{w}" height="{h}" fill="none" stroke="{INK}" stroke-width="0.5"/>\n' if self.border else '') + '</svg>'
        return [(None, opening), (f'{self.id}-style', f'<style id="{self.id}-style">{"".join(css)}</style>'), *out, (None, closing)]

    # furniture
    def metres_per_unit(self):
        """Ground metres to one reference point, along the frame's middle, from geodesic distance."""
        gx0, gy0, gx1, gy1 = self.ground
        a = self.to_lonlat.transform(gx0, (gy0 + gy1) / 2)
        b = self.to_lonlat.transform(gx1, (gy0 + gy1) / 2)
        return pyproj.Geod(ellps='WGS84').inv(a[0], a[1], b[0], b[1])[2] / self.w

    def scale_bar_svg(self, x, y, max_w, unit='mi', segments=4):
        """A bar in miles or kilometres: alternating segments of a round length, no wider than asked."""
        k = self.page.k
        per = {'mi': 1609.344, 'km': 1000.0}[unit] / self.metres_per_unit()
        seg = next((s for s in (500, 250, 200, 100, 50, 25, 20, 10, 5, 2.5, 2, 1, 0.5, 0.25, 0.2, 0.1, 0.05) if segments * s * per <= max_w), 0.05)
        sw = seg * per
        num = lambda v: f'{v:g}'
        parts = []
        for i in range(segments):
            parts.append(f'<rect x="{fmt(i * sw)}" y="11.5" width="{fmt(sw)}" height="4" fill="{INK if i % 2 == 0 else "#fff"}" stroke="{INK}" stroke-width="0.5"/>')
        for i in range(segments + 1):
            label = num(i * seg) + (f' {unit}' if i == segments else '')
            anchor_x = i * sw - T.width(num(i * seg), 'sans', 7) / 2
            parts.append(f'<text x="{fmt(anchor_x)}" y="8" style="{T.css("sans")};font-size:7px;fill:{INK}">{label}</text>')
        w = segments * sw + 30
        return (f'<svg id="{self.id}-scale-{unit}" xmlns="http://www.w3.org/2000/svg" style="left:{(x - 6) * k:.2f}pt;top:{y * k:.2f}pt;width:{w * k:.2f}pt;height:{20 * k:.2f}pt;overflow:visible" '
                f'viewBox="-6 0 {w:.2f} 20">' + ''.join(parts) + '</svg>')

    def north_angle(self):
        """Degrees clockwise from the top of the page to true north at the frame's centre."""
        gx0, gy0, gx1, gy1 = self.ground
        lon, lat = self.to_lonlat.transform((gx0 + gx1) / 2, (gy0 + gy1) / 2)
        x1, y1 = self.to_ground.transform(lon, lat)
        x2, y2 = self.to_ground.transform(lon, lat + 0.01)
        return math.degrees(math.atan2(x2 - x1, y2 - y1))

    def north_arrow_svg(self, right, top, h=16):
        """A small, fine arrow to true north: a hairline stem under a slim head, its right edge and top given."""
        k = self.page.k
        w = 8
        return (f'<svg id="{self.id}-north" xmlns="http://www.w3.org/2000/svg" style="left:{(right - w) * k:.2f}pt;top:{top * k:.2f}pt;width:{w * k:.2f}pt;height:{h * k:.2f}pt;overflow:visible" '
                f'viewBox="-4 -8 8 16"><g transform="rotate({self.north_angle():.2f})"><path d="M0,-2 L0,8" fill="none" stroke="{INK}" stroke-width="0.5"/>'
                f'<path d="M0,-8 L2.1,-1.2 L0,-2.6 L-2.1,-1.2Z" fill="{INK}"/></g></svg>')


class _Plain:
    """Stands where a projection would for a frame that shows a picture."""
    def to_wkt(self):
        return 'picture'


class ImageFrame(Frame):
    """A rectangle on the page holding a picture in place of ground. Positions in it run 0 to 1 across and down;
    lettering, lines and labels are given in those, and a label's pin is kept in them."""
    def __init__(self, page, key, rect, source, citation=True):
        self.page, self.key, self.view = page, key, None
        self.x, self.y, self.w, self.h = rect
        self.crs, self.s = _Plain(), 1.0
        self.id = f'{page.id}-{key}'
        self.at = ('picture', str(source), tuple(round(v, 4) for v in rect))
        self.layers, self.rules, self.citations, self.notes = [], [], [], []
        self.label_result, self.border = None, False
        m = page.map
        self.layers.append({'kind': 'picture', 'id': 'picture', 'source': source})
        if citation:
            self.citations.append((Path(source).name, raster_citation(m.path(source))))

    def to_frame(self, xy):
        return np.asarray(xy, dtype='float64') * [self.w, self.h]

    def lonlat_to_frame(self, u, v):
        return u * self.w, v * self.h

    def frame_to_lonlat(self, x, y):
        return x / self.w, y / self.h

    def shapes(self, id, kind, geoms, **style):
        """Lines, areas or points drawn over the picture, given as shapely geometries in its 0-to-1 positions.
        For points, `rows` (a dict of columns) gives each its attributes, to be labelled from."""
        import geopandas as gpd
        rows = style.pop('rows', None)
        g = gpd.GeoDataFrame(rows or {}, geometry=list(geoms))
        L = {'kind': kind, 'id': id, 'source': None, 'layer': id, 'where': None, 'data': g, 'radius': style.pop('radius', 0),
             'style': {'fill': 'none', 'stroke': 'none', 'weight': 0, **style}}
        L['geoms'] = shapely.transform(np.array(g.geometry.values, dtype=object), self.to_frame)
        L['d'] = [] if kind in ('data', 'point') else [self.path_one(x) for x in L['geoms']]
        self.layers.append(L)
        return L


def load_settings(scripts):
    f = Path(scripts) / 'settings.json'
    return json.loads(f.read_text())['settings'] if f.exists() else []


def settings_file(name, settings):
    """The settings file's contents: the Basin's shape, a list of {key, label, group, type, value}."""
    return json.dumps({'about': f'Settings for {name}, read by its build (Scripts/map.py, through the print engine in the davalerio.com '
                                'repo, _writing/print) and tuned in the workbench. Each entry declares one setting: its kind, range and value.',
                       'settings': settings}, indent=1, ensure_ascii=False) + '\n'
