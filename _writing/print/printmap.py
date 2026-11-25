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


def choice(key, label, group, value, options, effect='geometry'):
    return _setting('choice', key, label, group, value, effect, options=options)


def toggle(key, label, group, value, effect='paint'):
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

class Map:
    def __init__(self, project, declared, saved=None, today=None, dpi=350, margin=0.0, fonts=None, labels=True):
        self.project = Path(project)
        self.settings = [dict(s) for s in declared]
        saved = {s['key']: s['value'] for s in (saved or [])}
        for s in self.settings:
            if s['key'] in saved:
                s['value'] = saved[s['key']]
        self.values = {s['key']: s['value'] for s in self.settings}
        self.today = today or date.today().isoformat()
        self.dpi, self.margin, self.labels = dpi, margin, labels
        self.fonts = fonts or (T.FONTS.as_uri() + '/')
        self.pages = []

    def __getitem__(self, key):
        return self.values[key]

    def var(self, key):
        return Var(key, self.values[key])

    def page(self, size, title='', subtitle='', credits=''):
        p = Page(self, len(self.pages) + 1, size, title, subtitle, credits)
        self.pages.append(p)
        return p

    def path(self, source):
        return self.project / source

    def pins(self, page, frame):
        key = next((s['key'] for s in self.settings if s['type'] == 'labels'), None)
        return [p for p in (self.values.get(key) or []) if p.get('page', 1) == page and p.get('frame', 'main') == frame]


class Page:
    """One page, laid out by arithmetic from its size: title block, one main frame, and beneath it the scale bar,
    credits and north arrow."""
    def __init__(self, m, n, size, title, subtitle, credits):
        self.map, self.n, self.size = m, n, size
        self.w_in, self.h_in = PAGES[size]
        self.W, self.H = self.w_in * 72, self.h_in * 72
        self.k = max(1.0, min(self.W, self.H) / 792)          # reference points to page points
        self.title, self.subtitle, self.credits = title, subtitle, credits
        self.frames = []
        self.id = f'p{n}'

    # the layout, in reference points (multiply by k for the page)
    MARGIN, TITLE_TOP, SUB_TOP, RULE_Y, FRAME_TOP, FOOT_GAP, BOTTOM = 34, 19, 46.5, 68, 76.5, 10, 30
    CREDIT_SIZE, CREDIT_LEAD, BAR_FRACTION = 6.5, 9.5, 0.26

    def _layout(self):
        k = self.k
        Wr, Hr = self.W / k, self.H / k
        fw = Wr - 2 * self.MARGIN
        self.credit_x = self.MARGIN + fw * 0.37
        self.credit_w = fw * 0.63 - 40
        self.credit_lines = self._credit_lines()
        self.foot_h = max(34, len(self.credit_lines) * self.CREDIT_LEAD + 2)
        fh = Hr - self.FRAME_TOP - self.FOOT_GAP - self.foot_h - self.BOTTOM
        self.rect = (self.MARGIN, self.FRAME_TOP, fw, fh)      # the main frame, reference points

    def frame(self, crs, view, key='main'):
        if not hasattr(self, 'rect'):
            self._layout()
        f = Frame(self, key, self.rect, crs, view)
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

    def _credit_lines(self):
        """Credits wrapped to their column with the page's own font metrics, so the footer's height is known.
        A line breaks between its items (set apart by a middle dot) before it breaks inside one."""
        fits = lambda s: T.width(s, 'serif', self.CREDIT_SIZE) <= self.credit_w
        lines = []
        for para in self._credit_text(with_sources=bool(self.frames)).split('\n'):
            cur = ''
            for item in [i.strip() for i in para.split('·') if i.strip()]:
                trial = f'{cur}  ·  {item}' if cur else item
                if fits(trial):
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
            if cur:
                lines.append(cur)
        return lines

    def html(self, raster_dir, raster_base=''):
        """The page as one static HTML document. Rasters are written into `raster_dir`."""
        k = self.k
        self.credit_lines = self._credit_lines()     # now that every layer is known
        need = max(34, len(self.credit_lines) * self.CREDIT_LEAD + 2)
        if need > self.foot_h + 0.01:
            raise BuildError('the credits need more lines than the page was laid out for; shorten them or the sources')
        m = self.map
        css_vars = ''.join(f'--{s["key"].replace(".", "-")}:{s["value"]};' for s in m.settings
                           if s['type'] in ('number', 'color') and s['effect'] == 'paint')
        pt = lambda v: f'{v * k:.2f}pt'
        x0 = self.MARGIN
        fx, fy, fw, fh = self.rect
        body = [
            f'<div id="{self.id}-title" class="title" style="left:{pt(x0)};top:{pt(self.TITLE_TOP)};font-size:{pt(18)};line-height:{pt(24)}">{esc(self.title)}</div>',
            f'<div id="{self.id}-subtitle" class="subtitle" style="left:{pt(x0)};top:{pt(self.SUB_TOP)};font-size:{pt(9.5)};line-height:{pt(14)}">{esc(self.subtitle)}</div>',
            f'<div class="rule" style="left:{pt(x0)};top:{pt(self.RULE_Y)};width:{pt(fw)};height:{pt(1)}"></div>',
        ]
        for f in self.frames:
            body.append(f.svg(raster_dir, raster_base))
        main = self.frames[0]
        fy2 = fy + fh + self.FOOT_GAP
        body.append(main.scale_bar_svg(x0, fy2, fw * self.BAR_FRACTION))
        body.append(f'<div id="{self.id}-credits" class="credits" style="left:{pt(self.credit_x)};top:{pt(fy2)};width:{pt(self.credit_w + 40)};'
                    f'font-size:{pt(self.CREDIT_SIZE)};line-height:{pt(self.CREDIT_LEAD)}">' + '<br>'.join(esc(l) for l in self.credit_lines) + '</div>')
        body.append(main.north_arrow_svg(x0 + fw - 20, fy2 + 1, 20, 31))
        return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{esc(self.title)}</title>
<style>
{T.font_css(m.fonts)}
@page{{size:{self.w_in}in {self.h_in}in;margin:0}}
html,body{{margin:0;padding:0}}
body{{width:{self.w_in}in;height:{self.h_in}in;position:relative;overflow:hidden;background:#fff;{css_vars}}}
body>div,body>svg{{position:absolute}}
.title{{{T.css('serif-semibold')};color:{INK};white-space:pre}}
.subtitle{{{T.css('serif')};color:{STONE};white-space:pre}}
.credits{{{T.css('serif')};color:{STONE};white-space:pre}}
.rule{{background:{PLUM}}}
svg text{{white-space:pre;text-rendering:geometricPrecision;font-variant-ligatures:none;font-kerning:normal}}
svg .fill{{fill-rule:evenodd}}
svg .line{{fill:none;stroke-linejoin:round;stroke-linecap:round}}
</style></head>
<body>
{chr(10).join(body)}
</body></html>
'''


class Frame:
    """A rectangle on the page showing ground: a projection and a view. Inside, lengths are reference points."""
    def __init__(self, page, key, rect, crs, view):
        self.page, self.key = page, key
        self.x, self.y, self.w, self.h = rect
        self.crs = pyproj.CRS.from_user_input(crs)
        self.to_ground = pyproj.Transformer.from_crs('EPSG:4326', self.crs, always_xy=True)
        self.to_lonlat = pyproj.Transformer.from_crs(self.crs, 'EPSG:4326', always_xy=True)
        cx, cy = self.to_ground.transform(*view['center'])
        gw = view['width_km'] * 1000.0
        self.s = self.w / gw                                   # reference points per ground unit
        gh = self.h / self.s
        self.gx0, self.gy1 = cx - gw / 2, cy + gh / 2
        self.ground = (self.gx0, self.gy1 - gh, self.gx0 + gw, self.gy1)
        self.id = f'{page.id}-{key}'
        self.layers, self.rules, self.citations = [], [], []
        self.label_result = None

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
        g = read_layer(self.page.map.path(source), layer)
        if where:
            g = g.query(where)
        if g.crs is not None and not g.crs.equals(self.crs):
            g = g.to_crs(self.crs)
        return g

    def _to_frame_geoms(self, geoms, clip_pad=2.0, tolerance=0.04):
        """Ground geometries clipped to the frame (and the margin of ground kept for panning), in frame units,
        simplified to a tolerance too small to see."""
        pad = clip_pad / self.s + self.page.map.margin * (self.ground[2] - self.ground[0])
        gx0, gy0, gx1, gy1 = self.ground
        clipped = shapely.clip_by_rect(geoms, gx0 - pad, gy0 - pad, gx1 + pad, gy1 + pad)
        clipped = clipped[~shapely.is_empty(clipped)]
        clipped = shapely.simplify(clipped, tolerance / self.s, preserve_topology=False)
        return shapely.transform(clipped, self.to_frame)

    # layers
    def _add(self, kind, id_, source, layer, where, show, style, cite=True, **more):
        if show is False:
            return None
        g = self._ground_geoms(source, layer, where)
        L = {'kind': kind, 'id': id_, 'source': source, 'layer': layer, 'where': where, 'style': style, 'data': g, **more}
        L['geoms'] = self._to_frame_geoms(g.geometry.values)
        self.layers.append(L)
        if cite:
            self.citations.append((f'{Path(source).name}, layer {layer}', layer_citation(self.page.map.path(source), layer)))
        return L

    def fill(self, id, source, layer, where=None, show=True, fill='none', stroke='none', weight=0, blend=None, opacity=None, dash=None):
        return self._add('fill', id, source, layer, where, show, dict(fill=fill, stroke=stroke, weight=weight, blend=blend, opacity=opacity, dash=dash))

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
        self.layers.append({'kind': 'relief', 'id': 'relief', 'dem': dem, 'opts': opts})
        self.citations.append((Path(dem).name, raster_citation(self.page.map.path(dem))))

    def label(self, layer, text, kind, style='sans', size=7, color=INK, priority=5, key=None, show=True, **opts):
        """A label rule for a drawn layer. `text` and `key` are field names (or functions of a feature's row);
        the key must stay the same for a feature from build to build, since pins are saved under it."""
        if show is False or not any(L['id'] == layer for L in self.layers):
            return
        self.rules.append({'layer': layer, 'text': text, 'kind': kind, 'style': style, 'size': size, 'color': color,
                           'priority': priority, 'key': key or text, **opts})

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
        return f'{id_}{{{";".join(d)}}}'

    @staticmethod
    def path_d(geoms):
        """Geometries as one SVG path: every line and ring a subpath."""
        d = []
        for part in shapely.get_parts(shapely.get_parts(geoms)) if len(geoms) else []:
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
                d.append('M' + ' '.join(f'{fmt(a)},{fmt(b)}' for a, b in c) + ('Z' if t == 3 else ''))
        return ''.join(d)

    def svg(self, raster_dir, raster_base=''):
        k = self.page.k
        res = self.place_labels()
        css, groups = [], []
        for L in self.layers:
            gid = f'{self.id}-{L["id"]}'
            if L['kind'] == 'relief':
                import relief
                tiles = relief.tiles(self, self.page.map.path(L['dem']), Path(raster_dir), gid, self.page.map.dpi, **L['opts'])
                groups.append(f'<g id="{gid}">' + ''.join(
                    f'<image href="{raster_base}{f}" x="{fmt(x)}" y="{fmt(y)}" width="{w:.4f}" height="{h:.4f}" preserveAspectRatio="none"/>'
                    for f, x, y, w, h in tiles) + '</g>')
                continue
            if L['kind'] == 'data':
                continue
            css.append(self._css(L))
            geoms = res.cut.get(L['id'], L['geoms']) if res else L['geoms']
            if L['kind'] == 'point':
                pts = shapely.get_coordinates(geoms)
                groups.append(f'<g id="{gid}">' + ''.join(f'<circle cx="{fmt(x)}" cy="{fmt(y)}" r="{L["radius"]}"/>' for x, y in pts) + '</g>')
            else:
                groups.append(f'<path id="{gid}" class="{L["kind"]}" d="{self.path_d(geoms)}"/>')
        label_svg = res.svg(f'{self.id}-labels') if res else ''
        return (f'<svg id="{self.id}" xmlns="http://www.w3.org/2000/svg" style="left:{self.x * k:.2f}pt;top:{self.y * k:.2f}pt;'
                f'width:{self.w * k:.2f}pt;height:{self.h * k:.2f}pt;overflow:visible" viewBox="0 0 {self.w:.3f} {self.h:.3f}">\n'
                f'<style>{"".join(css)}</style>\n<defs><clipPath id="{self.id}-clip"><rect width="{self.w:.3f}" height="{self.h:.3f}"/></clipPath></defs>\n'
                f'<g clip-path="url(#{self.id}-clip)">\n' + '\n'.join(groups) + f'\n{label_svg}\n</g>\n'
                f'<rect width="{self.w:.3f}" height="{self.h:.3f}" fill="none" stroke="{INK}" stroke-width="0.5"/>\n</svg>')

    # furniture
    def metres_per_unit(self):
        """Ground metres to one reference point, along the frame's middle, from geodesic distance."""
        gx0, gy0, gx1, gy1 = self.ground
        a = self.to_lonlat.transform(gx0, (gy0 + gy1) / 2)
        b = self.to_lonlat.transform(gx1, (gy0 + gy1) / 2)
        return pyproj.Geod(ellps='WGS84').inv(a[0], a[1], b[0], b[1])[2] / self.w

    def scale_bar_svg(self, x, y, max_w):
        """A bar in miles: four alternating segments of a round length, at most `max_w` wide."""
        k = self.page.k
        per_mile = 1609.344 / self.metres_per_unit()
        seg = next((s for s in (500, 250, 200, 100, 50, 25, 20, 10, 5, 2.5, 2, 1, 0.5, 0.25, 0.2, 0.1, 0.05) if 4 * s * per_mile <= max_w), 0.05)
        sw = seg * per_mile
        num = lambda v: f'{v:g}'
        parts = []
        for i in range(4):
            parts.append(f'<rect x="{fmt(i * sw)}" y="11.5" width="{fmt(sw)}" height="4" fill="{INK if i % 2 == 0 else "#fff"}" stroke="{INK}" stroke-width="0.5"/>')
        for i in range(5):
            label = num(i * seg) + (' mi' if i == 4 else '')
            anchor_x = i * sw - T.width(num(i * seg), 'sans', 7) / 2
            parts.append(f'<text x="{fmt(anchor_x)}" y="8" style="{T.css("sans")};font-size:7px;fill:{INK}">{label}</text>')
        w = 4 * sw + 30
        return (f'<svg id="{self.id}-scale" xmlns="http://www.w3.org/2000/svg" style="left:{(x - 6) * k:.2f}pt;top:{y * k:.2f}pt;width:{w * k:.2f}pt;height:{20 * k:.2f}pt;overflow:visible" '
                f'viewBox="-6 0 {w:.2f} 20">' + ''.join(parts) + '</svg>')

    def north_angle(self):
        """Degrees clockwise from the top of the page to true north at the frame's centre."""
        gx0, gy0, gx1, gy1 = self.ground
        lon, lat = self.to_lonlat.transform((gx0 + gx1) / 2, (gy0 + gy1) / 2)
        x1, y1 = self.to_ground.transform(lon, lat)
        x2, y2 = self.to_ground.transform(lon, lat + 0.01)
        return math.degrees(math.atan2(x2 - x1, y2 - y1))

    def north_arrow_svg(self, x, y, w, h):
        k = self.page.k
        return (f'<svg id="{self.id}-north" xmlns="http://www.w3.org/2000/svg" style="left:{x * k:.2f}pt;top:{y * k:.2f}pt;width:{w * k:.2f}pt;height:{h * k:.2f}pt;overflow:visible" '
                f'viewBox="-10 -15.5 20 31"><path transform="rotate({self.north_angle():.2f})" d="M0,-15.5 L10,15.5 L0,8.5 L-10,15.5Z" fill="{INK}"/></svg>')


def load_settings(scripts):
    f = Path(scripts) / 'settings.json'
    return json.loads(f.read_text())['settings'] if f.exists() else []


def settings_file(name, settings):
    """The settings file's contents: the Basin's shape, a list of {key, label, group, type, value}."""
    return json.dumps({'about': f'Settings for {name}, read by its build (Scripts/map.py, through the print engine in the davalerio.com '
                                'repo, _writing/print) and tuned in the workbench. Each entry declares one setting: its kind, range and value.',
                       'settings': settings}, indent=1, ensure_ascii=False) + '\n'
