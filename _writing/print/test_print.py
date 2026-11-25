"""Tests of the print engine on a small made-up map: uv run test_print.py

It makes a project in a temporary folder (towns, a lake, a river, contours, all invented), and checks: all four
kinds of label are placed and none overlap; two builds are identical; a layer without a citation stops the
build; pins hold their ground when the view shifts by a fifth and the page goes from letter to tabloid; and a
print map follows its web map.
"""
import json, math, sqlite3, sys, tempfile
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, Point, Polygon

sys.path.insert(0, str(Path(__file__).parent))
import build
import printmap as pm
import labels as LB

MAP = '''
from printmap import number, color, text, choice, toggle, view, labels, legend, PAGES
NAME = 'Test'
SETTINGS = [choice('page', 'Page', 'Page', 'Letter Landscape', list(PAGES)), view('view', 'Extent', 'Page', [-103.6, 48.15], 24),
            text('title', 'Title', 'Text', 'Test Map'), text('subtitle', 'Subtitle', 'Text', 'Four Kinds of Label'),
            text('credits', 'Credits', 'Text', 'Made-up data  ·  {date}\\n{sources}'),
            color('water', 'Water', 'Water', '#b7d2e6'), number('contour.weight', 'Contour Weight', 'Contours', 0.5, 0, 2, 0.05),
            toggle('contours.show', 'Contours', 'Contours', True, 'paint'),
            legend(value=[{'heading': 'Water', 'items': [{'color': 'water', 'label': 'Lake'}, {'shape': 'line', 'color': '#4a7fae', 'label': 'River'}]},
                          {'items': [{'shape': 'dot', 'color': '#22282B', 'label': 'Town'}, {'shape': 'line', 'dash': 'dashed', 'color': '#8a6646', 'code': 'c', 'label': 'Contour, every 100 feet of elevation above the sea'}]}]),
            labels()]
def pages(m):
    p = m.page(m['page'], m['title'], m['subtitle'], m['credits'])
    p.legend(m['legend'])
    f = p.frame('EPSG:32613', m['view'])
    f.fill('lakes', 'test.gpkg', 'lakes', fill=m.var('water'), stroke='#4a7fae', weight=0.3)
    f.line('contours', 'test.gpkg', 'contours', show=m.var('contours.show'), stroke='#8a6646', weight=m.var('contour.weight'))
    f.line('rivers', 'test.gpkg', 'rivers', stroke='#4a7fae', weight=0.7)
    f.point('towns', 'test.gpkg', 'towns', radius=1.6)
    f.label('towns', 'name', 'point', style='sans-semibold', size=8, priority=1)
    f.label('lakes', 'name', 'area', style='sans-italic', size=8, color='#2f5f8a', priority=2)
    f.label('rivers', 'name', 'line', style='sans-italic', size=7, color='#2f5f8a', priority=3, repeat=250)
    f.label('contours', lambda r: f'{r.elev:.0f}', 'contour', key='elev', size=5.5, color='#7a5a3c', priority=4, repeat=300)
'''


def project(folder, cite=True):
    """Invented features around 603000 E, 5333000 N in UTM zone 13N."""
    folder = Path(folder); (folder / 'Scripts').mkdir(parents=True, exist_ok=True)
    (folder / 'Scripts' / 'map.py').write_text(MAP)
    cx, cy = 603000, 5333000
    g = folder / 'test.gpkg'
    towns = [('Alder', -6000, 3000), ('Birch', -5200, 3300), ('Cedar', 2500, 4000), ('Dogwood', 7000, -3500),
             ('Elm', 1500, -1500), ('Fir', 5000, 2500), ('Grove', 5600, 2700), ('Hazel', -8000, -5000)]
    gpd.GeoDataFrame({'name': [t[0] for t in towns]}, geometry=[Point(cx + t[1], cy + t[2]) for t in towns], crs=32613).to_file(g, layer='towns')
    a = np.linspace(0, 2 * math.pi, 60)
    lake = lambda x, y, r: Polygon(np.column_stack([cx + x + r * 1.6 * np.cos(a), cy + y + r * np.sin(a) * (1 + 0.2 * np.cos(3 * a))]))
    gpd.GeoDataFrame({'name': ['Long Lake', 'Round Lake']}, geometry=[lake(-3000, -3500, 1400), lake(6000, 5500, 900)], crs=32613).to_file(g, layer='lakes')
    x = np.linspace(-14000, 14000, 400)
    river = np.column_stack([cx + x, cy + 800 + 1800 * np.sin(x / 4500) + 120 * np.sin(x / 300)])
    half = len(river) // 2                                      # in two pieces, to be joined by name
    gpd.GeoDataFrame({'name': ['Crooked River', 'Crooked River']}, geometry=[LineString(river[:half + 1]), LineString(river[half:])], crs=32613).to_file(g, layer='rivers')
    lines, elev = [], []
    for i, e in enumerate((2000, 2100, 2200)):
        lines.append(LineString(np.column_stack([cx + x, cy - 4800 - i * 700 + 500 * np.cos(x / 5000 + i)]))); elev.append(float(e))
    gpd.GeoDataFrame({'elev': elev}, geometry=lines, crs=32613).to_file(g, layer='contours')
    if cite:
        con = sqlite3.connect(g)
        con.execute("update gpkg_contents set description = 'Test: invented ' || table_name")
        con.commit(); con.close()
    return folder


def more(tmp, proj):
    """Smaller things that once went wrong."""
    import copy, session, text as T
    # a blank line in a label's text; the same layer labelled and drawn; credits that do not print their sources
    assert T.shape('', 'sans', 7) == ((), 0.0)
    m = build.load(proj)
    f = m.pages[0].frames[0]
    f.data('rivers-again', 'test.gpkg', 'rivers'); f.line('rivers-drawn', 'test.gpkg', 'rivers')
    assert 'id="p1-main-rivers-drawn" class="line" d="M' in m.pages[0].html(tmp), 'a layer drawn after being only labelled lost its paths'
    bare = project(tmp / 'bare2', cite=False)
    try:
        build.load(bare, saved=[{'key': 'credits', 'value': 'No sources named'}]).pages[0].html(tmp)
        raise AssertionError('a map with no citations built once its credits stopped printing them')
    except pm.BuildError:
        print('citations are checked whether or not the credits print them')
    # the footer takes the height the credits need, and the frame what is left
    short = build.load(proj); short.pages[0].html(tmp)
    long = build.load(proj, saved=[{'key': 'credits', 'value': '\n'.join(['A line of credits'] * 7) + '\n{sources}'}]); long.pages[0].html(tmp)
    assert long.pages[0].frames[0].h < short.pages[0].frames[0].h - 30 and len(long.pages[0].credit_lines) == 8
    print(f'eight lines of credits: the frame gives way, {short.pages[0].frames[0].h:.0f} to {long.pages[0].frames[0].h:.0f} points tall')
    # the session: one label reset leaves the others of its name; a label group switched off does not orphan its pins;
    # reopening forgets what was read; panning does not pile up
    s = session.Session(proj, '/f/', '/r/', 'test'); s.handle({'op': 'open'})
    ids = sorted(i for i in s.pages[1][2].frames[0].label_result.ids if 'crooked-river' in i)
    assert len(ids) >= 2
    for i in ids[:2]:
        g = s.pages[1][2].frames[0]; l = g.label_result.ids[i]
        r = s.handle({'op': 'pin', 'action': 'move', 'label': i, 'x': l.anchor[0] + 25, 'y': l.anchor[1], 'rev': s.rev})
    pinned = sorted(i for i, l in s.pages[1][2].frames[0].label_result.ids.items() if l.pinned)
    assert len(pinned) == 2 and len(r['labels']) == 4, (pinned, r['labels'])
    assert s.handle({'op': 'pin', 'action': 'reset', 'label': pinned[0], 'rev': s.rev - 1}).get('outdated'), 'an edit made on an older preview was acted on'
    r = s.handle({'op': 'pin', 'action': 'reset', 'label': pinned[0], 'rev': s.rev})
    assert len(r['labels']) == 2 and sum(l.pinned for l in s.pages[1][2].frames[0].label_result.ids.values()) == 1
    print('double-click resets the one label: the other pinned label of its name stays')
    s.values[s.labels_key] = s.values[s.labels_key] + [{'page': 1, 'frame': 'main', 'layer': 'no-such-rule', 'key': 'x', 'pin': 'place', 'at': [-103.6, 48.15]}]
    assert not s.apply()['notes']['orphans']
    print('a pin of a layer with no label rule in force waits, and is not called lost')
    assert pm._read_cache and LB._chain_cache
    s.handle({'op': 'open'})
    import resource
    v = copy.deepcopy(s.values['view']); sizes = []
    for i in range(30):
        v['center'][0] += 0.004
        s.handle({'op': 'set', 'values': {'view': copy.deepcopy(v)}, 'rev': s.rev})
        sizes.append(len(pm.CACHE))
    assert max(sizes[5:]) == min(sizes[5:]), sizes
    print(f'thirty pans: the cache holds {sizes[-1]} stages throughout; preview pictures kept: {len(list(s.cache.iterdir()))}')


def ground(frame, xy):
    return np.array(frame.to_ground.transform(*frame.frame_to_lonlat(*xy)))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        proj = project(Path(tmp) / 'cited')

        # 1. every kind is placed; the engine itself asserts no two labels overlap
        m = build.load(proj)
        html1 = m.pages[0].html(Path(tmp))
        res = m.pages[0].frames[0].label_result
        kinds = {l.kind for l in res.placed}
        assert kinds == {'point', 'area', 'line', 'contour'}, kinds
        assert not res.unplaced, res.unplaced
        print(f'four kinds placed: {len(res.placed)} labels, none overlapping, none left off')

        # 2. the same map built twice is the same page, byte for byte
        html2 = build.load(proj).pages[0].html(Path(tmp))
        assert html1 == html2
        print('two builds identical')

        # 3. a drawn layer with no citation stops the build
        bare = project(Path(tmp) / 'bare', cite=False)
        try:
            build.load(bare).pages[0].html(Path(tmp))
            raise AssertionError('a map with no citations built')
        except pm.BuildError as e:
            print('no citation, no build:', str(e)[:60] + '…')

        # 4. pins: one of each kind, an omit, one out of frame, one whose feature is gone
        f0 = m.pages[0].frames[0]
        river = next(l for l in res.placed if l.kind == 'line')
        lonlat = lambda x, y: list(pm.pyproj.Transformer.from_crs(32613, 4326, always_xy=True).transform(x, y))
        pins = [
            {'layer': 'towns', 'key': 'Elm', 'pin': 'place', 'offset': [-30.0, 12.0]},
            {'layer': 'lakes', 'key': 'Long Lake', 'pin': 'place', 'at': lonlat(603000 - 2500, 5333000 - 3900), 'text': 'Long\nLake'},
            {'layer': 'rivers', 'key': 'Crooked River', 'pin': 'place', 'line': 0, 'along': 14000.0 + 2600.0, 'offset': 4.0},
            {'layer': 'rivers', 'key': 'Crooked River', 'pin': 'omit', 'at': list(f0.frame_to_lonlat(*river.anchor))},
            {'layer': 'contours', 'key': '2100', 'pin': 'place', 'line': 0, 'along': 14000.0 + 1500.0},
            {'layer': 'contours', 'key': '2200', 'pin': 'place', 'at': lonlat(603000 + 3000, 5333000 - 3000)},      # dragged off its line: free
            {'layer': 'towns', 'key': 'Alder', 'pin': 'omit'},
            {'layer': 'towns', 'key': 'Hazel', 'pin': 'place', 'offset': [4.0, 4.0]},                                # leaves the frame in the second view
            {'layer': 'towns', 'key': 'Nowhere', 'pin': 'place', 'offset': [4.0, 4.0]},                              # no such feature
        ]

        def built(page, center, width):
            saved = [{'key': 'labels', 'value': pins}, {'key': 'page', 'value': page}, {'key': 'view', 'value': {'center': center, 'width_km': width}}]
            mm = build.load(proj, saved=saved)
            mm.pages[0].html(Path(tmp))
            return mm.pages[0].frames[0]

        fa = built('Letter Landscape', [-103.6, 48.15], 24)
        shifted = list(fa.frame_to_lonlat(fa.w * 0.5 + fa.w / 5, fa.h * 0.5))          # the view moved east by a fifth of its width
        fb = built('Tabloid Landscape', shifted, 24)
        for name, f in (('letter', fa), ('tabloid, shifted a fifth', fb)):
            r = f.label_result
            pinned = [l for l in r.placed if l.pinned]
            assert not any(l.key == 'Alder' for l in r.placed), 'an omitted label was placed'
            assert [p['key'] for p in r.orphans] == ['Nowhere'], r.orphans
            print(f'{name}: {len(pinned)} pinned labels placed, {len(r.dormant)} dormant, {len(r.orphans)} without a feature, {len(r.unplaced)} left off')
        assert not fa.label_result.dormant and [p['key'] for p in fb.label_result.dormant] == ['Hazel']

        def find(f, layer, key):
            return next(l for l in f.label_result.placed if l.pinned and l.layer == layer and l.key == key)

        worst = 0.0
        for layer, key in (('towns', 'Elm'), ('lakes', 'Long Lake'), ('rivers', 'Crooked River'), ('contours', '2100'), ('contours', '2200')):
            la, lb = find(fa, layer, key), find(fb, layer, key)
            d = float(np.hypot(*(ground(fa, la.anchor) - ground(fb, lb.anchor))))
            limit = 0.5 / fa.s + 0.5 / fb.s + 0.01           # a line pin lands on the nearest sample, half a point either way
            assert d <= limit, (layer, key, d, limit)
            worst = max(worst, d)
            print(f'  {layer} {key}: {la.kind}, on the same ground within {d:.1f} m (a point on the page is {1 / fa.s:.0f} m on letter, {1 / fb.s:.0f} m on tabloid)')
        elm_a, elm_b = find(fa, 'towns', 'Elm'), find(fb, 'towns', 'Elm')
        for l in (elm_a, elm_b):
            off = (l.lines[0][1] - l.anchor[0], l.lines[0][2] - l.anchor[1])
            assert abs(off[0] + 30) < 1e-6 and abs(off[1] - 12) < 1e-6, off
        assert find(fa, 'lakes', 'Long Lake').text == 'Long\nLake' and len(find(fa, 'lakes', 'Long Lake').lines) == 2
        assert find(fb, 'contours', '2200').kind == 'free'
        # the omit pin: no automatic river label near the place it names
        ox, oy = fa.lonlat_to_frame(*pins[3]['at'])
        assert not any(l.kind == 'line' and not l.pinned and math.hypot(l.anchor[0] - ox, l.anchor[1] - oy) < 20 for l in fa.label_result.placed)
        print(f'pins hold their ground: worst {worst:.1f} m; point offset kept in points; typed line break kept; omit pins honoured')
        more(Path(tmp), proj)
        following(proj)
    print('ALL TESTS PASSED')


def following(proj):
    """A print map that follows a web map: its values come from the web map until print changes them, every web layer
    has a switch and must be drawn, a layer switched off takes its legend items with it, and the settings file holds
    only what print has changed."""
    fake = {'name': 'fake', 'palette': {'--map-water': '#6F8FA6'},
            'def': {'title': 'Web Title', 'credits': '[Data](https://example.com) one.\n\nTwo.',
                    'legend': [{'heading': 'Water', 'items': [{'color': '--map-water', 'label': 'Lake'}, {'shape': 'dot', 'color': '#112233', 'label': 'Big town'}]}]},
            'layers': [{'id': 'lakes', 'paint': {'fill-color': '--map-water'}}, {'id': 'rivers', 'paint': {'line-color': '--map-water'}},
                       {'id': 'towns', 'paint': {'circle-color': ['match', ['get', 'k'], 'big', '#112233', '#445566']}}]}
    fake['def']['layers'] = fake['layers']
    real, pm.load_web = pm.load_web, lambda name: fake
    try:
        W = pm.web
        decl = [pm.text('title', 'Title', 'Text', W.title), pm.text('credits', 'Credits', 'Text', W.credits('tail')),
                pm.color('water', 'Water', 'Water', W.paint('lakes')), pm.color('town', 'Town', 'Towns', W.paint('towns', 'big')),
                pm.color('small', 'Small Town', 'Towns', W.paint('towns')), pm.legend(value=W.legend), pm.number('w', 'Weight', 'Lines', 1, 0, 2, 0.1)]
        m = pm.Map(proj, decl, [], web='fake')
        assert m['title'] == 'Web Title' and m['credits'] == 'Data one.  ·  Two.\ntail', (m['title'], m['credits'])
        assert (m['water'], m['town'], m['small']) == ('#6F8FA6', '#112233', '#445566')
        assert [it['color'] for it in m['legend'][0]['items']] == ['water', 'town']       # follows print's colours
        assert [s['key'] for s in m.settings if s['key'].startswith('layer.')] == ['layer.lakes', 'layer.rivers', 'layer.towns']
        m = pm.Map(proj, decl, [{'key': 'water', 'value': '#000000'}, {'key': 'town', 'value': '#112233'}, {'key': 'layer.lakes', 'value': False}], web='fake')
        assert m['water'] == '#000000' and next(s for s in m.settings if s['key'] == 'water')['web'] == '#6F8FA6'
        kept = [s['key'] for s in json.loads(pm.settings_file('Test', m.settings))['settings']]
        assert 'water' in kept and 'town' not in kept and 'title' not in kept and 'w' in kept and 'layer.lakes' in kept, kept
        assert all('web' not in s for s in json.loads(pm.settings_file('Test', m.settings))['settings'])
        items = lambda m: [it['label'] for g in m.shown_legend(m['legend']) for it in g['items']]
        assert items(m) == ['Lake', 'Big town']                     # the rivers still use the water colour
        m.values['layer.rivers'] = False
        assert items(m) == ['Big town']
        m.layer('lakes'); m.layer('towns')
        try:
            m.check_web()
            raise AssertionError('an undrawn web layer was let through')
        except pm.BuildError as e:
            assert 'rivers' in str(e)
        m.layer('rivers'); m.check_web()
        try:
            pm.Map(proj, decl, [])
            raise AssertionError('values from no web map were let through')
        except pm.BuildError:
            pass
    finally:
        pm.load_web = real
    print('following a web map: its values until print changes them; a switch per layer, each drawn; legend and settings file follow')


if __name__ == '__main__':
    main()
