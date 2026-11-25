"""Label placement: one deterministic engine for the four kinds of label a map carries.

point    a name beside its symbol, in one of eight positions, upper right preferred
area     a name inside its polygon, at the point farthest from the edge, then at others
line     a name along a stream: same-name pieces joined, a smoothed guide, the text set glyph by glyph above it
contour  a figure on its line, the line cut beneath it

Pins (positions David dragged, read from the map's `labels` setting) are placed first and never move. The rest
are placed in priority order, each taking its cheapest position that touches nothing already placed and stays
inside the frame. Everything here is in the frame's reference points; a pin is stored as ground, never as page.

A pin: {page, frame, layer, key, pin: 'place' | 'omit', ...}
  place, point label     offset: [dx, dy] from the symbol to the start of the text's baseline
  place, area or free    at: [lon, lat], the middle of the text
  place, along a line    line: which of the key's joined lines (longest first), along: ground distance from its
                         start to the middle of the text, offset: baseline's distance above the line
  omit                   at: [lon, lat]: no automatic label for this key near that ground (with no `at`, none at all)
  any place pin may carry text: the wording to print, where a line break may be typed
"""
import math
import re

import numpy as np
import shapely
from shapely import STRtree
from shapely.geometry import LineString, Point, box

import text as T

STEP = 1.0            # spacing of the samples along a line, reference points
PAD = 0.6             # half the clear space kept between two labels
EDGE = 2.5            # clear space kept inside the frame's edge
CLUTTER = 4.0         # what a type-size length of avoided linework under a label costs
POINT_ORDER = ('NE', 'SE', 'NW', 'SW', 'E', 'W', 'N', 'S')


def keystr(v):
    if isinstance(v, (float, np.floating)) and float(v).is_integer():
        return str(int(v))
    return str(v)


def slug(s):
    return re.sub(r'[^a-z0-9]+', '-', str(s).lower()).strip('-')


class Label:
    def __init__(self, rule, key, text, kind):
        self.rule, self.key, self.text, self.kind = rule, key, text, kind
        self.layer = rule['layer']
        self.boxes = None           # shapely polygons, padded
        self.glyphs = None          # curved: [(chars, x, y, degrees)]
        self.lines = None           # straight: [(text, x, baseline)]
        self.pinned = False
        self.pin = None             # the pin that placed it
        self.anchor = None          # frame position the label is tied to
        self.centre = None          # frame position of the text's middle
        self.line = self.along = self.along_i = self.offset = None
        self.spacing = 0.0
        self.clusters = self.width = self.guide = None      # curved: its shaped text and the guide it rides
        self._area = None

    def area(self):
        """The ground the label covers, as one polygon."""
        if self._area is None:
            if len(self.boxes) == 1:
                self._area = self.boxes[0]
            else:
                strip = None
                if self.glyphs is not None:      # a curved label: a strip along the tops of its glyphs and back along their feet
                    c = [np.asarray(b.exterior.coords) for b in self.boxes]
                    strip = shapely.Polygon([q[0] for q in c] + [c[-1][1], c[-1][2]] + [q[3] for q in reversed(c)])
                self._area = strip if strip is not None and strip.is_valid else shapely.union_all(np.array(self.boxes, dtype=object))
        return self._area


class Result:
    def __init__(self, frame):
        self.frame = frame
        self.placed, self.unplaced, self.dormant, self.orphans, self.overlapping_pins = [], [], [], [], []
        self.ghosts = []            # labels left off, set where they would have gone, for the workbench to offer
        self.cut = {}               # layer id -> (geometries with gaps under the labels, which of them changed)
        self.guides = {}            # (rule, key, line) -> Guide
        self.ids = {}               # element id -> Label, as last written

    def _text(self, l, eid, preview, ghost=False):
        f = self.frame
        attrs = (f'id="{eid}" class="r{f.rules.index(l.rule)}" data-layer="{esc(l.layer)}" data-key="{esc(l.key)}" data-kind="{l.kind}"'
                 + (' data-pinned="1"' if l.pinned else '') + (' data-ghost="1"' if ghost else ''))
        if l.glyphs is not None:
            if preview and l.guide is not None:
                attrs += (f' data-guide="{self.guide_id(l.guide)}" data-x="{" ".join(fmt(c[1]) for c in l.clusters)}"'
                          f' data-n="{" ".join(str(len(c[0])) for c in l.clusters)}" data-w="{fmt(l.width)}" data-off="{fmt(l.offset)}"')
            chars = ''.join(g[0] for g in l.glyphs)
            xs = ' '.join(fmt(g[1]) for g in l.glyphs for _ in g[0])
            ys = ' '.join(fmt(g[2]) for g in l.glyphs for _ in g[0])
            rs = ' '.join(fmt(g[3]) for g in l.glyphs for _ in g[0])
            return f'<text {attrs} x="{xs}" y="{ys}" rotate="{rs}">{esc(chars)}</text>'
        sp = f' style="letter-spacing:{l.spacing:.2f}px"' if l.spacing else ''
        if len(l.lines) == 1:
            t, x, y = l.lines[0]
            return f'<text {attrs}{sp} x="{fmt(x)}" y="{fmt(y)}">{esc(t)}</text>'
        return f'<text {attrs}{sp}>' + ''.join(f'<tspan x="{fmt(x)}" y="{fmt(y)}">{esc(t)}</tspan>' for t, x, y in l.lines) + '</text>'

    def svg(self, gid, preview=False):
        f = self.frame
        css = [f'#{f.id} .r{i}{{{T.css(r["style"])};font-size:{r["size"]}px;fill:{r["color"]}}}' for i, r in enumerate(f.rules)]
        out = []
        self.ids = {}
        for l in self.placed:
            eid = self._eid(gid, l)
            self.ids[eid] = l
            out.append(self._text(l, eid, preview))
        return f'<g id="{gid}"><style>{"".join(css)}</style>\n' + '\n'.join(out) + '\n</g>'

    def _eid(self, gid, l):
        """A label's element id: its layer and key, and for a name on a line which line and where along it, so the
        other labels keep theirs when one is moved. An edit made against a preview that has since changed then finds
        no such label, and is refused, where a number would have meant another label."""
        eid = f'{gid}-{slug(l.layer)}-{slug(l.key)}'
        if l.glyphs is not None and l.line is not None:
            eid += f'-{l.line}-{round(l.along_i * STEP / self.frame.s)}'      # which line, and how many ground units along it
        elif l.kind == 'free':
            eid += f'-free-{round(l.centre[0])}-{round(l.centre[1])}'
        n = 2
        while eid in self.ids:                 # two keys that spell alike
            eid = f'{eid}-{n}'; n += 1
        return eid

    def guide_id(self, gk):
        return f'{self.frame.id}-guide-{gk[0]}-{slug(gk[1])}-{gk[2]}'

    def guides_svg(self, gid):
        """The guides labels may slide along, where they run through the frame: hidden paths for the workbench."""
        out = []
        for gk, g in self.guides.items():
            d = ''.join('M' + ' '.join(f'{x:.1f},{y:.1f}' for x, y in g.smooth[a:b:2]) for a, b in g.runs() if b - a > 4)
            if d:
                out.append(f'<path id="{self.guide_id(gk)}" d="{d}"/>')
        return f'<g id="{gid}" class="guides">' + ''.join(out) + '</g>'

    def ghosts_svg(self, gid):
        out = []
        for l in self.ghosts:
            eid = self._eid(gid, l)
            self.ids[eid] = l
            out.append(self._text(l, eid, True, ghost=True))
        return f'<g id="{gid}" class="ghosts">' + ''.join(out) + '</g>'


def esc(s):
    import html
    return html.escape(str(s), quote=True)


def fmt(v):
    s = f'{v:.2f}'
    return s.rstrip('0').rstrip('.') if '.' in s else s


class Index:
    """What has been placed, searched through an R-tree rebuilt when it has grown."""
    def __init__(self):
        self.geoms, self.owners, self.tree = [], [], None

    def add(self, geoms, owner):
        self.geoms.extend(geoms)
        self.owners.extend([owner] * len(geoms))
        self.tree = None

    def hits(self, geoms):
        if not self.geoms:
            return []
        if self.tree is None:
            self.tree = STRtree(self.geoms)
        return [self.owners[j] for j in np.unique(self.tree.query(geoms, predicate='intersects')[1])]


# ---------------------------------------------------------------- straight text

def vertical(text, size):
    """How far a line of text reaches above and below its baseline."""
    return 0.72 * size, (0.21 if re.search(r'[gjpqy,;Q]', text) else 0.03) * size


def straight(label, cx, cy, align='centre'):
    """Set a label's text as straight lines. `cx, cy` is the middle of the block (or, with align 'left', the
    start of the first baseline)."""
    r = label.rule
    size, lead = r['size'], r['size'] * r.get('lead', 1.12)
    rows = label.text.split('\n')
    widths = [T.width(t, r['style'], size, label.spacing) for t in rows]
    top, _ = vertical(rows[0], size)
    _, bottom = vertical(rows[-1], size)
    if align == 'left':
        x0, first = cx, cy
        label.centre = (cx + max(widths) / 2, cy - top / 2 + (len(rows) - 1) * lead / 2)
    else:
        block = top + (len(rows) - 1) * lead
        first = cy - block / 2 + top
        x0 = None
        label.centre = (cx, cy)
    label.lines, boxes = [], []
    for i, (t, w) in enumerate(zip(rows, widths)):
        x = x0 if x0 is not None else cx - w / 2
        y = first + i * lead
        a, b = vertical(t, size)
        label.lines.append((t, x, y))
        boxes.append(box(x - PAD, y - a - PAD, x + w + PAD, y + b + PAD))
    label.boxes = boxes
    return label


# ---------------------------------------------------------------- lines

_chain_cache = {}


def chains(frame, L, rule):
    """The joined lines of each key, in ground coordinates, unclipped, longest first: {key: (text, [coords])}."""
    # a rule's own function is known by where it is written, not by the object, which is new at every build
    ident = lambda f: (f.__code__.co_filename, f.__code__.co_firstlineno, f.__code__.co_code) if callable(f) else f
    ck = (str(frame.page.map.path(L['source'])), L['layer'], L['where'], ident(rule['key']), ident(rule['text']), frame.crs.to_wkt())
    if ck in _chain_cache:
        return _chain_cache[ck]
    out = {}
    g = L['data']
    keys = [keystr(k) for k in value_of(g, rule['key'])]
    texts = [str(t) for t in value_of(g, rule['text'])]
    geoms = g.geometry.values
    groups = {}
    for k, t, ge in zip(keys, texts, geoms):
        groups.setdefault(k, [t, []])[1].append(ge)
    for k, (t, ges) in groups.items():
        parts = shapely.get_parts(shapely.get_parts(np.array(ges, dtype=object)))
        parts = [p for p in parts if shapely.get_type_id(p) == 1]
        if not parts:
            continue
        merged = shapely.get_parts(shapely.line_merge(shapely.multilinestrings(parts)))
        cs = [shapely.get_coordinates(m) for m in merged]
        cs.sort(key=lambda c: (-round(float(np.hypot(*np.diff(c, axis=0).T).sum()), 3), float(c[0, 0]), float(c[0, 1])))
        out[k] = (t, cs)
    _chain_cache[ck] = out
    return out


def value_of(g, spec):
    if callable(spec):
        return [spec(r) for r in g.itertuples()]
    return list(g[spec])


def gaussian(a, sigma):
    """Smooth the columns of `a` along its rows, the ends held."""
    if sigma <= 0 or len(a) < 3:
        return a
    n = max(1, int(sigma * 3))
    k = np.exp(-0.5 * (np.arange(-n, n + 1) / sigma) ** 2)
    k /= k.sum()
    p = np.pad(a, ((n, n), (0, 0)), mode='edge')
    return np.column_stack([np.convolve(p[:, i], k, mode='valid') for i in range(a.shape[1])])


class Guide:
    """One joined line in the frame: sampled every STEP, and smoothed to the scale of the text it will carry."""
    def __init__(self, frame, coords, sigma):
        P = frame.to_frame(coords)
        seg = np.hypot(*np.diff(P, axis=0).T)
        cum = np.concatenate([[0], np.cumsum(seg)])
        self.length = float(cum[-1])
        n = max(2, int(self.length / STEP) + 1)
        s = np.arange(n) * STEP
        self.raw = np.column_stack([np.interp(s, cum, P[:, 0]), np.interp(s, cum, P[:, 1])])
        self.smooth = gaussian(self.raw, sigma / STEP)
        d = np.gradient(self.smooth, axis=0)
        self.theta = np.unwrap(np.arctan2(d[:, 1], d[:, 0]))
        t = d / np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-9)[:, None]
        self.side = ((self.raw - self.smooth) * np.column_stack([t[:, 1], -t[:, 0]])).sum(1)   # the raw line's reach to the guide's upper side
        self.dev = np.abs(self.side)
        self.scum = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(self.smooth, axis=0).T))])   # length along the smoothed guide
        m = EDGE + 1
        q = self.smooth
        self.inside = (q[:, 0] > m) & (q[:, 0] < frame.w - m) & (q[:, 1] > m) & (q[:, 1] < frame.h - m)

    def window(self, c, reach):
        """The samples within `reach` of sample `c` along the smoothed guide, as [a, b), or None past an end."""
        a = int(np.searchsorted(self.scum, self.scum[c] - reach, side='right')) - 1
        b = int(np.searchsorted(self.scum, self.scum[c] + reach, side='left')) + 1
        return (a, b) if a >= 0 and b <= len(self.scum) else None

    def runs(self):
        """Stretches of the line inside the frame, as sample ranges [a, b)."""
        idx = np.flatnonzero(np.diff(np.concatenate([[0], self.inside.astype(int), [0]])))
        return list(zip(idx[::2], idx[1::2]))


def set_along(label, guide, centre_i, width, clusters, offset=None, gap=None, max_turn=None, below=False):
    """Set a label's glyphs along a guide, the text's middle at sample `centre_i`. With `offset` the baseline
    sits that far above the guide; with `gap` it clears the raw line's farthest reach to the text's side by that
    much, above it or (`below`) beneath it. Returns False when the line there cannot carry it (too short, or turning too sharply between
    neighbouring glyphs)."""
    size = label.rule['size']
    win = guide.window(int(centre_i), width / 2 + 5)
    if win is None:
        return False
    a, b = win
    sub, side = guide.smooth[a:b], guide.side[a:b]
    if sub[-1, 0] < sub[0, 0]:                       # read left to right
        sub, side = sub[::-1], -side[::-1]
    if offset is None and below:
        offset = -(gap + max(0.0, float((-side).max())) + 0.7 * size)
    elif offset is None:
        offset = gap + max(0.0, float(side.max()))
    t = np.gradient(sub, axis=0)
    t /= np.maximum(np.hypot(t[:, 0], t[:, 1]), 1e-9)[:, None]
    up = np.column_stack([t[:, 1], -t[:, 0]])
    path = sub + up * offset
    cum = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(path, axis=0).T))])
    start = cum[-1] / 2 - width / 2
    if start < 0:
        return False
    at = lambda d: (np.interp(d, cum, path[:, 0]), np.interp(d, cum, path[:, 1]))
    top, bottom = vertical(label.text, size)
    cl = [c for c in clusters]
    xs = np.array([c[1] for c in cl]); adv = np.array([max(c[2], 0.5) for c in cl])
    x0, y0 = np.interp(start + xs, cum, path[:, 0]), np.interp(start + xs, cum, path[:, 1])
    x1, y1 = np.interp(start + xs + adv, cum, path[:, 0]), np.interp(start + xs + adv, cum, path[:, 1])
    ang = np.arctan2(y1 - y0, x1 - x0)
    glyphs = [(c[0], float(a), float(b), float(d)) for c, a, b, d in zip(cl, x0, y0, np.degrees(ang))]
    ink = np.array([bool(c[0].strip()) for c in cl])
    if max_turn is not None and ink.sum() > 1:
        un = np.unwrap(ang[ink])
        if np.abs(np.diff(un)).max() > math.radians(max_turn) or np.abs(np.degrees(un)).max() > 100:
            return False
    c, sn = np.cos(ang[ink]), np.sin(ang[ink])
    w = np.array([cc[2] for cc in cl])[ink]
    px = np.stack([-PAD + 0 * w, w + PAD, w + PAD, -PAD + 0 * w, -PAD + 0 * w], 1)
    py = np.array([-top - PAD, -top - PAD, bottom + PAD, bottom + PAD, -top - PAD])[None, :] + 0 * px
    ring = np.stack([x0[ink][:, None] + px * c[:, None] - py * sn[:, None], y0[ink][:, None] + px * sn[:, None] + py * c[:, None]], -1)
    polys = list(shapely.polygons(ring))
    label.glyphs, label.boxes = glyphs, polys
    label.centre = tuple(float(v) for v in at(cum[-1] / 2))
    label.anchor = tuple(float(v) for v in guide.raw[int(centre_i)])
    label.along_i = int(centre_i)
    label.offset = float(offset)
    label.clusters, label.width = clusters, width
    return True


# ---------------------------------------------------------------- placement

def place(frame):
    res = Result(frame)
    index = Index()
    by_id = {L['id']: L for L in frame.layers}
    inner = (EDGE, EDGE, frame.w - EDGE, frame.h - EDGE)
    pins = frame.page.map.pins(frame.page.n, frame.key)
    used_pins = set()

    def fits_frame(label):
        b = shapely.bounds(np.array(label.boxes, dtype=object))
        return b[:, 0].min() >= inner[0] and b[:, 1].min() >= inner[1] and b[:, 2].max() <= inner[2] and b[:, 3].max() <= inner[3]

    def commit(label):
        index.add(label.boxes, label)
        res.placed.append(label)

    def pins_for(layer, key, kind):
        return [(i, p) for i, p in enumerate(pins) if p.get('layer') == layer and str(p.get('key')) == key and p.get('pin') == kind]

    def omitted(layer, key, xy, reach):
        for i, p in pins_for(layer, key, 'omit'):
            if 'at' not in p:
                return True
            px, py = frame.lonlat_to_frame(*p['at'])
            if math.hypot(px - xy[0], py - xy[1]) <= reach:
                return True
        return False

    def in_frame(xy):
        return 0 <= xy[0] <= frame.w and 0 <= xy[1] <= frame.h

    # symbols no label may cover
    for L in frame.layers:
        if L['kind'] == 'point':
            r = L['radius']
            index.add([box(x - r, y - r, x + r, y + r) for x, y in shapely.get_coordinates(L['geoms'])], None)

    # every rule's features, once
    prepared = []
    for ri, rule in enumerate(frame.rules):
        L = by_id[rule['layer']]
        if rule['kind'] in ('line', 'contour'):
            feats = chains(frame, L, rule)
        else:
            g = L['data']
            feats = {}
            ks = [keystr(k) for k in value_of(g, rule['key'])]
            if rule.get('per_feature'):        # every feature its own label, keyed by the feature as well
                ks = [f'{k}:{i}' for k, i in zip(ks, g.index)]
            for k, t, ge in zip(ks, [str(t) for t in value_of(g, rule['text'])], g.geometry.values):
                feats.setdefault(k, [t, []])[1].append(ge)
        prepared.append((rule, L, feats))

    known = {(rule['layer'], k) for rule, L, feats in prepared for k in feats}
    labelled = {rule['layer'] for rule in frame.rules}       # a pin of a layer whose labels are switched off waits; it is not lost
    for i, p in enumerate(pins):
        if p.get('layer') not in labelled:
            used_pins.add(i)
        elif (p.get('layer'), str(p.get('key'))) not in known:
            res.orphans.append(p)
            used_pins.add(i)

    guides = res.guides

    def guide(rule, key, n, coords):
        gk = (frame.rules.index(rule), key, n)
        if gk not in guides:
            sigma = rule.get('smooth', {'contour': 0.35}.get(rule['kind'], 2.0)) * rule['size']
            from printmap import cached
            guides[gk] = cached(('guide', frame.at, frame.crs.to_wkt(), id(coords), sigma), lambda: Guide(frame, coords, sigma))
        return guides[gk]

    def line_metrics(rule, text, run_len=None):
        size = rule['size']
        spacing = 0.0
        if rule.get('spread') and run_len:
            natural = T.width(text, rule['style'], size)
            n = max(1, len(text) - 1)
            spacing = min(rule.get('max_spacing', 0.9) * size, max(0.0, (run_len * rule.get('spread_fraction', 0.45) - natural) / n))
        clusters, width = T.shape(text, rule['style'], size, spacing or 1e-9)     # a spacing turns ligatures off: one glyph a character
        return clusters, width, spacing

    def clearance(rule, text):
        """How a rule's text sits on its line: (offset, gap), one of them None (see set_along)."""
        m = T.metrics(rule['style'], rule['size'])
        if rule['kind'] == 'contour':
            return -m['cap'] / 2, None
        if rule.get('on_line'):
            return -m['x'] / 2, None
        return None, rule.get('gap', 1.3) + (0.21 * rule['size'] if re.search(r'[gjpqy,;Q]', text) else 0)

    avoid_trees = {}

    def clutter(label):
        """How much of the linework a rule asks to avoid lies under a label, in lengths of its type size."""
        ids = tuple(t for t in label.rule.get('avoid', []) if t in by_id)
        if not ids:
            return 0.0
        if ids not in avoid_trees:          # the avoided layers' lines together, in short pieces, searched as one
            def make():
                pieces = []
                for t in ids:
                    geoms = np.array(by_id[t]['geoms'], dtype=object)
                    lines = shapely.get_parts(shapely.get_parts(shapely.boundary(geoms) if by_id[t]['kind'] == 'fill' else geoms))
                    for line in lines:
                        c = shapely.get_coordinates(line)
                        pieces += [c[i:i + 25] for i in range(0, len(c) - 1, 24)]
                geoms = np.array([shapely.LineString(c) for c in pieces if len(c) > 1], dtype=object)
                return STRtree(geoms), geoms
            from printmap import cached
            avoid_trees[ids] = cached(('avoid', str(frame.page.map.project), frame.at, frame.crs.to_wkt(), ids, tuple(by_id[t]['where'] for t in ids)), make)
        tree, geoms = avoid_trees[ids]
        area = label.area()
        hit = tree.query(area, predicate='intersects')
        if not len(hit):
            return 0.0
        return float(shapely.length(shapely.intersection(geoms[hit], area)).sum()) / label.rule['size']

    # ---- 1. pins, placed first and never moved
    for rule, L, feats in prepared:
        for key, (text, geoms) in feats.items():
            for i, p in pins_for(rule['layer'], key, 'place'):
                used_pins.add(i)
                label = Label(rule, key, p.get('text', text), rule['kind'])
                label.pinned, label.pin = True, p
                ok = False
                if 'along' in p and rule['kind'] in ('line', 'contour'):
                    n = int(p.get('line', 0))
                    if n < len(geoms):
                        g = guide(rule, key, n, geoms[n])
                        ci = int(round(p['along'] * frame.s / STEP))
                        if 0 <= ci < len(g.raw) and in_frame(g.raw[ci]):
                            run = next((b - a for a, b in g.runs() if a <= ci < b), 0) * STEP
                            clusters, width, spacing = line_metrics(rule, label.text.replace('\n', ' '), run)
                            label.spacing, label.line = spacing, n
                            off, gap = clearance(rule, label.text)
                            label.guide = (frame.rules.index(rule), key, n)
                            ok = set_along(label, g, ci, width, clusters, p.get('offset', off), gap)
                elif 'at' in p:
                    xy = frame.lonlat_to_frame(*p['at'])
                    if in_frame(xy):
                        label.kind = 'free' if rule['kind'] in ('line', 'contour') else rule['kind']
                        straight(label, *xy)
                        label.anchor = xy
                        ok = True
                elif 'offset' in p and rule['kind'] == 'point':
                    pt = shapely.get_coordinates(shapely.transform(shapely.centroid(shapely.union_all(np.array(geoms, dtype=object))), frame.to_frame))[0]
                    if in_frame(pt):
                        straight(label, pt[0] + p['offset'][0], pt[1] + p['offset'][1], align='left')
                        label.anchor = (float(pt[0]), float(pt[1]))
                        ok = True
                if not ok:
                    res.dormant.append(p)
                    continue
                for other in index.hits(np.array(label.boxes, dtype=object)):
                    if other is not None:
                        res.overlapping_pins.append((label, other))
                commit(label)

    # ---- 2. the rest, by priority
    order = sorted(range(len(prepared)), key=lambda i: (prepared[i][0]['priority'], i))
    for oi in order:
        rule, L, feats = prepared[oi]
        kind, size = rule['kind'], rule['size']
        same = lambda key: [l for l in res.placed if l.rule is rule and l.key == key]

        if kind in ('point', 'area'):
            from printmap import cached
            sig = repr(sorted((k, str(v)) for k, v in rule.items() if not callable(v)))
            memo = cached(('spots', str(frame.page.map.project), frame.at, frame.crs.to_wkt(), L['id'], L['where'], sig), dict)
            quiet = rule.get('quiet', False)

            def spots_for(key, text, geoms):
                """Where a feature's label may go, cheapest first: (the symbol's place or None, [(cost, xy, align)])."""
                shape = shapely.transform(shapely.union_all(np.array(geoms, dtype=object)), frame.to_frame)
                if kind == 'point':
                    pt = shapely.get_coordinates(shapely.centroid(shape))[0]
                    if not in_frame(pt):
                        return None, []
                    r = L.get('radius', 0) if L['kind'] == 'point' else 0
                    gap = rule.get('gap', 1.4)
                    w = max(T.width(t, rule['style'], size) for t in text.split('\n'))
                    top, bottom = vertical(text, size)
                    xh = T.metrics(rule['style'], size)['x']
                    d = (r + gap) * 0.75
                    spots = {'NE': (pt[0] + d, pt[1] - d - bottom), 'SE': (pt[0] + d, pt[1] + d + top),
                             'NW': (pt[0] - d - w, pt[1] - d - bottom), 'SW': (pt[0] - d - w, pt[1] + d + top),
                             'E': (pt[0] + r + gap, pt[1] + xh / 2), 'W': (pt[0] - r - gap - w, pt[1] + xh / 2),
                             'N': (pt[0] - w / 2, pt[1] - r - gap - bottom), 'S': (pt[0] - w / 2, pt[1] + r + gap + top)}
                    return (float(pt[0]), float(pt[1])), [(i * 1.0, spots[name], 'left') for i, name in enumerate(rule.get('positions', POINT_ORDER))]
                x0, y0, x1, y1 = shapely.bounds(shape)
                w, h = T.width(text, rule['style'], size), size
                if rule.get('inside') and (x1 - x0 < w + 2 or y1 - y0 < h + 2):
                    return None, []
                visible = shapely.intersection(shapely.make_valid(shape), box(*inner))
                polys = [p for p in shapely.get_parts(visible) if shapely.get_type_id(p) == 3]
                if not polys:
                    return None, []
                big = max(polys, key=lambda p: p.area)
                if big.is_empty or big.area < 4:
                    return None, []
                pole = shapely.get_coordinates(shapely.maximum_inscribed_circle(big, tolerance=0.5))[0]
                x0, y0, x1, y1 = big.bounds
                n = 12
                grid = np.array([(x0 + (i + 0.5) * (x1 - x0) / n, y0 + (j + 0.5) * (y1 - y0) / n) for j in range(n) for i in range(n)])
                pts = shapely.points(grid)
                keep = shapely.contains(big, pts)
                dist = shapely.distance(shapely.boundary(big), pts[keep])
                alts = grid[keep][np.argsort(-dist, kind='stable')]
                options = [(0.0, (float(pole[0]), float(pole[1])), 'centre')]
                options += [(0.5 + i * 0.05, (float(c[0]), float(c[1])), 'centre') for i, c in enumerate(alts[:60])]
                if rule.get('inside'):         # only where the whole name lies within its area
                    fits = lambda c: big.contains(box(c[0] - w / 2 - 1, c[1] - h * 0.55, c[0] + w / 2 + 1, c[1] + h * 0.45))
                    options = [o for o in options[:8] if fits(o[1])]
                return None, options

            for key, (text, geoms) in feats.items():
                if pins_for(rule['layer'], key, 'place') or omitted(rule['layer'], key, (0, 0), -1):
                    continue
                if key not in memo:
                    memo[key] = spots_for(key, text, geoms)
                pt, options = memo[key]
                if not options:
                    continue
                label = Label(rule, key, text, kind)
                placed = False
                best = None
                for cost, xy, align in options:
                    if best is not None and cost >= best[0]:
                        break
                    trial = Label(rule, key, text, kind)
                    straight(trial, xy[0], xy[1], align=align)
                    if not fits_frame(trial) or index.hits(np.array(trial.boxes, dtype=object)):
                        continue
                    total = cost + CLUTTER * clutter(trial)
                    if best is None or total < best[0]:
                        trial.anchor = (float(pt[0]), float(pt[1])) if kind == 'point' else xy
                        best = (total, trial)
                if best:
                    label, placed = best[1], True
                if placed:
                    commit(label)
                elif not quiet:
                    res.unplaced.append(f'{rule["layer"]}: {text}')
                    ghost = Label(rule, key, text, kind)
                    straight(ghost, options[0][1][0], options[0][1][1], align=options[0][2])
                    ghost.anchor = (float(pt[0]), float(pt[1])) if kind == 'point' else options[0][1]
                    res.ghosts.append(ghost)
            continue

        # line and contour labels
        on_line = kind == 'contour' or rule.get('on_line')
        max_turn = rule.get('max_turn', 12 if kind == 'contour' else 22)
        repeat = rule.get('repeat', 0)
        apart = rule.get('apart', repeat * 0.55 if repeat else 0)
        near = rule.get('near', 0)               # least distance to any label of this rule, whatever its key
        stride = max(1, int(round(rule.get('stride', 3.0) / STEP)))
        work = []
        for key, (text, cs) in feats.items():
            for n, coords in enumerate(cs):
                bb = frame.to_frame(np.array([coords.min(0), coords.max(0)]))
                if bb[1, 0] < 0 or bb[0, 0] > frame.w or bb[0, 1] < 0 or bb[1, 1] > frame.h:      # y flips: bb[0] is min x, max y
                    continue
                g = guide(rule, key, n, coords)
                for a, b in g.runs():
                    work.append((-(b - a), key, n, a, b, text))
        work.sort(key=lambda w: (w[0], w[1], w[2], w[3]))
        fallback = {}
        # What a window costs and how the text sits in it do not depend on the other labels, so they are kept
        # from one placement to the next (in the workbench, a dropped pin re-places everything)
        from printmap import cached
        sig = repr(sorted((k, str(v)) for k, v in rule.items() if not callable(v))) + repr([t for t in rule.get('avoid', []) if t in by_id])
        memo = cached(('windows', str(frame.page.map.project), frame.at, frame.crs.to_wkt(), L['id'], L['where'], sig), dict)
        for neg, key, n, a, b, text in work:
            g = guide(rule, key, n, feats[key][1][n])
            run_len = (b - a) * STEP
            clusters, width, spacing = line_metrics(rule, text, run_len)
            need = width * (rule.get('room', 3.0 if kind == 'contour' else 1.25))
            if run_len < need:
                continue
            count = max(1, int(round(run_len / repeat))) if repeat else 1
            section = (b - a) / count
            for si in range(count):
                lo, hi = int(a + si * section), int(a + (si + 1) * section)
                mk = ('costs', key, n, a, b, si)
                if mk not in memo:
                    cands, wins = [], []
                    for c in range(lo, hi, stride):
                        win = g.window(c, width / 2 + 3)
                        if win and win[0] >= a and win[1] <= b:
                            cands.append(c)
                            wins.append(win)
                    if not cands:
                        memo[mk] = None
                    # cost of each window: how much the line turns under the text, how far the raw line strays from
                    # the smoothed guide, how steep the text would stand, and how far from the middle of its stretch
                    costs = np.empty(len(cands))
                    for j, (c, (wa, wb)) in enumerate(zip(cands, wins)):
                        th = g.theta[wa:wb]
                        every = max(1, int(round((wb - wa) / max(2.0, width / (size * 0.5)))))     # about one glyph's worth of samples
                        turn = np.abs(np.diff(th[::every]))
                        dev = g.dev[wa:wb].max()
                        steep = abs(math.remainder(th[len(th) // 2], math.pi)) / (math.pi / 2)
                        centre = abs(c - (lo + hi) / 2) / max(1.0, section)
                        costs[j] = 60 * float((turn ** 2).sum()) + 18 * turn.max(initial=0) + 0.6 * dev + 1.2 * steep + 1.5 * centre
                        if dev > rule.get('max_dev', 0.8 if on_line else 9.0):
                            costs[j] = np.inf
                    if cands:
                        memo[mk] = (cands, costs)
                if memo[mk] is None:
                    continue
                cands, costs = memo[mk]
                best, tried = None, 0
                for j in np.argsort(costs, kind='stable'):
                    if not np.isfinite(costs[j]) or (best is not None and costs[j] >= best[0]) or tried >= 60:
                        break
                    c = int(cands[j])
                    xy = g.raw[c]
                    if omitted(rule['layer'], key, xy, width / 2 + 12):
                        continue
                    if apart and any(math.hypot(l.anchor[0] - xy[0], l.anchor[1] - xy[1]) < apart for l in same(key)):
                        continue
                    if near and any(math.hypot(l.anchor[0] - xy[0], l.anchor[1] - xy[1]) < near for l in res.placed if l.rule is rule):
                        continue
                    off, gap = clearance(rule, text)
                    for below in ((False,) if on_line else (False, True)):      # above the line first, then beneath it
                        ck = ('window', key, n, c, below)
                        if ck not in memo:
                            label = Label(rule, key, text, kind)
                            label.spacing, label.line, label.guide = spacing, n, (frame.rules.index(rule), key, n)
                            ok = set_along(label, g, c, width, clusters, off, gap, max_turn, below) and fits_frame(label)
                            memo[ck] = (label, clutter(label)) if ok else None
                        if memo[ck] is None:
                            continue
                        label, mess = memo[ck]
                        label.rule = rule            # the rule as this build declares it
                        fallback.setdefault(key, label)
                        if index.hits(np.array(label.boxes, dtype=object)):
                            continue
                        if mess > rule.get('max_clutter', math.inf):
                            continue
                        tried += 1
                        total = costs[j] + CLUTTER * mess + (0.4 if below else 0)
                        if best is None or total < best[0]:
                            best = (total, label)
                if best:
                    commit(best[1])
        if kind == 'line':                          # a named line in view with no label is reported
            for key, (text, cs) in feats.items():
                seen = any(g.inside.any() for (ri, k, n), g in guides.items() if frame.rules[ri] is rule and k == key)
                if seen and not same(key) and not omitted(rule['layer'], key, (0, 0), -1):
                    res.unplaced.append(f'{rule["layer"]}: {text}')
                    if key in fallback:
                        res.ghosts.append(fallback[key])

    for l in res.placed + res.ghosts:
        if l.along_i is not None:
            l.along = l.along_i * STEP / frame.s
    res.dormant += [p for i, p in enumerate(pins) if i not in used_pins and p.get('pin') == 'place' and p not in res.dormant]

    # ---- 3. no two labels may touch (two pins set on top of each other by hand are reported, not refused)
    tree_geoms, owners = [], []
    for l in res.placed:
        tree_geoms += l.boxes
        owners += [l] * len(l.boxes)
    if tree_geoms:
        tree = STRtree(tree_geoms)
        a, b = tree.query(tree_geoms, predicate='intersects')
        for i, j in zip(a, b):
            if owners[i] is not owners[j] and not (owners[i].pinned and owners[j].pinned):
                raise AssertionError(f'labels overlap: {owners[i].text!r} and {owners[j].text!r}')

    # ---- 4. clear what lies under the labels: a real gap in the lines, never a white box or a halo
    clear, cut_trees = {}, {}
    for l in res.placed:
        targets = list(l.rule.get('clear', []))
        if l.kind == 'contour' and l.layer not in targets:
            targets.append(l.layer)
        for t in targets:
            if t in by_id:
                clear.setdefault(t, []).extend(l.boxes)
    for t, polys in clear.items():
        L = by_id[t]
        geoms = np.array(L['geoms'], dtype=object)
        if not len(geoms):
            continue
        areas = L['kind'] == 'fill' or bool(np.isin(shapely.get_type_id(geoms), (3, 6)).any())
        if areas:                              # an outline is cut as a line; cutting the area would draw a box round the label
            geoms = shapely.boundary(geoms)
        hole = shapely.union_all(shapely.buffer(np.array(polys, dtype=object), 0.5))
        tree = cut_trees.get(t) or cut_trees.setdefault(t, STRtree(geoms))
        hit = tree.query(hole, predicate='intersects')
        geoms[hit] = shapely.difference(geoms[hit], hole)
        res.cut[t] = (geoms, None if areas else hit)
    return res

