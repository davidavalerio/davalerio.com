"""The workbench's session with one print map: a long-lived process that keeps the map's data and its built
stages in memory and answers over its pipes, one JSON message a line.

    uv run session.py "<project folder>" <fonts URL base> <files URL base> <editor id>

Each open editor has a session of its own, with its own preview folder, so two tabs on one map do not share
unsaved changes.

Messages in ({"id": n, "op": ...}) and what comes back ({"id": n, ...} or {"id": n, "error": "..."}):
  open                     reads map.py and settings.json afresh; returns the declared settings and the pages
  page {n}                 the page's whole HTML, as last built
  set {values}             changed settings in; out, the pieces of the page that changed, each by its element id
                           (or reload, when the page's own shape changed and it must be loaded again)
  view {frame, dx, dy, zoom, ax, ay}   a frame panned by (dx, dy) and zoomed about (ax, ay), in its own units;
                           returns the frame's new extent (its setting's key and value) and the changed pieces
  pin {action, label, ...} a label (its element id) moved, deleted, reset or reworded on the page; returns the new pins and the
                           changed pieces. Actions: move (dx, dy for a label that moves freely; x, y on its guide,
                           or free, for one that slides), delete, reset (the clicked label alone goes back to
                           automatic), text (text), forget (index: a pin whose feature is gone)
The session never writes settings.json: saving is the workbench's.
"""
import importlib.util, json, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import printmap as pm
import labels as LB

DPI, MARGIN = 150, 0.15          # the preview's raster resolution, and the ground kept around the frame for panning


class Session:
    def __init__(self, project, fonts, files, editor='one'):
        self.project, self.fonts, self.files, self.editor = Path(project), fonts, files, editor
        self.scripts = self.project / 'Scripts'
        self.mod = None
        self.values = {}
        self.pages = {}           # n -> {'head', 'parts', 'page'}
        self.map = None

    def read(self):
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location('map_definition', self.scripts / 'map.py')
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        import build, relief
        self.cache = build.scratch(self.project, self.mod.NAME) / f'preview-{self.editor}'
        self.cache.mkdir(parents=True, exist_ok=True)
        for f in self.cache.iterdir():
            f.unlink()
        # nothing read before is trusted: the files may have changed since
        pm.CACHE.clear(); pm._read_cache.clear(); LB._chain_cache.clear(); relief._dems.clear()
        m = pm.Map(self.project, self.mod.SETTINGS, pm.load_settings(self.scripts))
        self.values = dict(m.values)
        self.labels_key = next((s['key'] for s in m.settings if s['type'] == 'labels'), None)
        return m.settings

    def build(self):
        """Build every page from the current values; returns {n: (head, parts)}."""
        m = pm.Map(self.project, self.mod.SETTINGS, [{'key': k, 'value': v} for k, v in self.values.items()],
                   dpi=DPI, margin=MARGIN, preview=True, fonts=self.fonts, raster_base=self.files)
        m.name = self.mod.NAME
        self.mod.pages(m)
        built = {p.n: (*p.render(self.cache), p) for p in m.pages}
        self.map = m
        pm.trim()                                   # keep only the stages this build used
        used = ''.join(h for _, parts, _ in built.values() for _, h in parts if '<image' in h)
        for f in self.cache.iterdir():              # and only the pictures it shows
            if f.name not in used:
                f.unlink()
        return built

    @staticmethod
    def shape(head, parts):
        """What must stay the same for a page to be patched in place rather than loaded again."""
        return (re.sub(r'body\{[^}]*\}', '', head), [i for i, _ in parts], [h for i, h in parts if i is None])

    def apply(self):
        """Rebuild and say what changed since the last build."""
        t = time.time()
        built = self.build()
        out = {'reload': False, 'fragments': []}
        if set(built) != set(self.pages):
            out['reload'] = True
        else:
            for n, (head, parts, page) in built.items():
                old = self.pages[n]
                if self.shape(head, parts) != self.shape(old[0], old[1]):
                    out['reload'] = True
                    break
                before = dict(p for p in old[1] if p[0])
                out['fragments'] += [{'page': n, 'id': i, 'html': h} for i, h in parts if i and before.get(i) != h]
        self.pages = built
        if out['reload']:
            out['fragments'] = []
        out.update(pages=self.page_list(), notes=self.notes(), ms=round((time.time() - t) * 1000))
        return out

    def page_list(self):
        return [{'n': n, 'size': p.size, 'w_in': p.w_in, 'h_in': p.h_in, 'k': p.k,
                 'frames': [{'id': f.id, 'key': f.key} for f in p.frames]} for n, (_, _, p) in sorted(self.pages.items())]

    def notes(self):
        """What the labels need said: those left off, pins waiting outside the frame, pins whose feature is gone."""
        pins = self.values.get(self.labels_key) or []
        index = lambda pin: next((i for i, p in enumerate(pins) if p is pin), -1)
        out = {'unplaced': [], 'dormant': [], 'orphans': [], 'overlaps': []}
        for n, (_, _, page) in sorted(self.pages.items()):
            for f in page.frames:
                r = f.label_result
                if not r:
                    continue
                out['unplaced'] += [u.split(': ', 1)[1] for u in r.unplaced]
                out['dormant'] += [{'index': index(p), 'layer': p.get('layer'), 'key': str(p.get('key'))} for p in r.dormant]
                out['orphans'] += [{'index': index(p), 'layer': p.get('layer'), 'key': str(p.get('key'))} for p in r.orphans]
                out['overlaps'] += [[a.text, b.text] for a, b in r.overlapping_pins]
        return out

    # ---- pins

    def find(self, eid):
        for n, (_, _, page) in self.pages.items():
            for f in page.frames:
                if f.label_result and eid in f.label_result.ids:
                    return page, f, f.label_result.ids[eid]
        raise ValueError('that label is no longer on the page')

    def pin(self, msg):
        pins = list(self.values.get(self.labels_key) or [])
        action = msg['action']
        if action == 'forget':
            pins = [p for i, p in enumerate(pins) if i != msg['index']]
        else:
            page, f, l = self.find(msg['label'])
            base = {'page': page.n, 'frame': f.key, 'layer': l.layer, 'key': l.key}
            lonlat = lambda xy: [round(float(v), 6) for v in f.frame_to_lonlat(*xy)]
            own = next((i for i, p in enumerate(pins) if p is l.pin), None)
            slides = l.glyphs is not None and l.line is not None
            mine = lambda p: (p.get('page', 1), p.get('frame', 'main'), p.get('layer'), str(p.get('key'))) == (page.n, f.key, l.layer, l.key)

            def here():
                """A place pin holding the label where it is now."""
                if slides:
                    return {**base, 'pin': 'place', 'line': l.line, 'along': round(l.along, 1), 'offset': round(l.offset, 2)}
                if l.kind == 'point':
                    return {**base, 'pin': 'place', 'offset': [round(float(l.lines[0][1] - l.anchor[0]), 2), round(float(l.lines[0][2] - l.anchor[1]), 2)]}
                return {**base, 'pin': 'place', 'at': lonlat(l.centre)}

            def put(pin):
                """Set the label's pin: in place of its own, or new, with (for an automatic label on a line) an
                omit pin where it was, so no automatic label returns there."""
                if own is not None:
                    for k in ('text', 'id'):
                        if k in pins[own]:
                            pin[k] = pins[own][k]
                    pins[own] = pin
                else:
                    if l.rule['kind'] in ('line', 'contour'):      # the pair is marked, so resetting one label undoes just these two
                        n = 1 + max([p.get('id', 0) for p in pins] + [p.get('of', 0) for p in pins] + [0])
                        pin['id'] = n
                        pins.append({**base, 'pin': 'omit', 'at': lonlat(l.anchor), 'of': n})
                    pins.append(pin)

            if action == 'move':
                if slides and not msg.get('free'):
                    g = f.label_result.guides[l.guide]
                    d = (g.smooth[:, 0] - msg['x']) ** 2 + (g.smooth[:, 1] - msg['y']) ** 2
                    put({**base, 'pin': 'place', 'line': l.line, 'along': round(int(d.argmin()) * LB.STEP / f.s, 1), 'offset': round(l.offset, 2)})
                elif l.kind == 'point':
                    start = (l.lines[0][1] + msg['dx'], l.lines[0][2] + msg['dy'])
                    put({**base, 'pin': 'place', 'offset': [round(float(start[0] - l.anchor[0]), 2), round(float(start[1] - l.anchor[1]), 2)]})
                else:
                    put({**base, 'pin': 'place', 'at': lonlat((l.centre[0] + msg['dx'], l.centre[1] + msg['dy']))})
            elif action == 'text':
                text = str(msg['text']).strip()
                pin = pins[own] if own is not None else here()
                pin = {k: v for k, v in pin.items() if k != 'text'}
                if text and text != self.natural_text(f, l):
                    pin['text'] = text if not slides else text.replace('\n', ' ')
                if own is not None:
                    pins[own] = pin
                else:
                    put(pin)
            elif action == 'delete':
                if own is not None:
                    pins.pop(own)
                if l.rule['kind'] in ('line', 'contour'):
                    pins.append({**base, 'pin': 'omit', 'at': lonlat(l.anchor if l.anchor else l.centre)})
                else:
                    pins = [p for p in pins if not (mine(p) and p.get('pin') == 'omit')] + [{**base, 'pin': 'omit'}]
            elif action == 'reset':          # the clicked label alone: its own pin, and the omit pin made with it
                if own is not None:
                    n = pins[own].get('id')
                    pins = [p for i, p in enumerate(pins) if i != own and not (n and p.get('of') == n)]
            else:
                raise ValueError('unknown pin action ' + action)
        before = self.values.get(self.labels_key)
        self.values[self.labels_key] = pins
        try:
            out = self.apply()
        except Exception:
            self.values[self.labels_key] = before
            raise
        out['labels'] = pins
        return out

    @staticmethod
    def natural_text(f, l):
        """A label's own wording, from its feature."""
        L = next(x for x in f.layers if x['id'] == l.layer)
        g = L['data']
        for k, t in zip(LB.value_of(g, l.rule['key']), LB.value_of(g, l.rule['text'])):
            if LB.keystr(k) == l.key:
                return str(t)
        return l.text

    # ---- messages

    def handle(self, msg):
        op = msg['op']
        if op == 'open':
            settings = self.read()
            self.pages = {}
            out = self.apply()
            return {'name': self.mod.NAME, 'dir': str(self.cache), 'settings': settings, 'pages': out['pages'], 'notes': out['notes'], 'ms': out['ms']}
        if op == 'page':
            head, parts, _ = self.pages[int(msg['n'])]
            return {'html': head + '\n'.join(h for _, h in parts) + '\n</body></html>\n'}
        if op == 'set':
            before = dict(self.values)
            self.values.update({k: v for k, v in msg['values'].items() if k in self.values})
            try:
                return self.apply()
            except Exception:
                self.values = before          # the page stays as it was; the workbench keeps the edit to try again
                raise
        if op == 'pin':
            return self.pin(msg)
        if op == 'view':
            f = next(f for _, _, p in self.pages.values() for f in p.frames if f.id == msg['frame'])
            key = next(k for k, v in self.values.items() if v is f.view)
            z = min(max(float(msg.get('zoom', 1)), 0.05), 20)
            ax, ay = msg.get('ax', f.w / 2), msg.get('ay', f.h / 2)
            cx, cy = ax + (f.w / 2 - msg.get('dx', 0) - ax) / z, ay + (f.h / 2 - msg.get('dy', 0) - ay) / z
            lon, lat = f.frame_to_lonlat(cx, cy)
            before = self.values[key]
            # a drag keeps the width exactly; a zoom may take it from a city block to the whole globe
            width = f.view['width_km'] if z == 1 else round(min(max(f.view['width_km'] / z, 0.05), 40000), 3)
            self.values[key] = {'center': [round(lon, 5), round(lat, 5)], 'width_km': width}
            try:
                out = self.apply()
            except Exception:
                self.values[key] = before
                raise
            return {**out, 'key': key, 'value': self.values[key]}
        raise ValueError('unknown message ' + op)


def main():
    s = Session(*sys.argv[1:5])
    out = sys.stdout
    sys.stdout = sys.stderr                      # nothing but answers goes down the pipe
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        try:
            reply = {'id': msg.get('id'), **s.handle(msg)}
        except pm.BuildError as e:
            reply = {'id': msg.get('id'), 'error': str(e)}
        except Exception as e:
            import traceback
            traceback.print_exc()
            reply = {'id': msg.get('id'), 'error': f'{type(e).__name__}: {e}'}
        out.write(json.dumps(reply, ensure_ascii=False, default=float) + '\n')
        out.flush()


if __name__ == '__main__':
    main()
