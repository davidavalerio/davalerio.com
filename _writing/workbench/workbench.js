// The workbench page: pick a map, tune it live from the settings it declares, save or revert.
// Each kind of piece has an adapter that draws it and applies a setting to it; the panel is built
// from the declarations alone (number, color, view), so a new kind needs only its adapter. The page's
// text (type text) and its legend are not in the panel: they are shown around the piece as on the site and
// edited in place.
import GUI from './lib/lil-gui.esm.min.js';

const $ = id => document.getElementById(id);
const stage = $('stage'), panel = $('panel'), status = $('status'), saveBtn = $('save'), revertBtn = $('revert'), select = $('piece');
let gui = null, current = null, saved = null, values = null, adapter = null, generation = 0, host = stage;
const EDITOR = crypto.randomUUID().slice(0, 8);   // this tab: a print map's session and unsaved preview are its own
let editorId = EDITOR, notice = '';   // a notice to show once the piece has opened again

const say = (text, cls = '') => {
    status.textContent = text;
    status.className = cls;
};
const same = () => JSON.stringify(values) === JSON.stringify(saved);
let saving = false;   // from a click on Save until its answer is applied: nothing here unlocks a control meanwhile
function changed() {
    const dirty = !same();
    if (!saving) {
        saveBtn.disabled = revertBtn.disabled = !dirty;
        say(dirty ? 'Unsaved Changes' : 'Saved', dirty ? 'unsaved' : '');
    }
    adapter?.changed?.();   // a piece that redraws from the values (a print map) hears of every change here
}

// --- Adapters

async function modelAdapter(piece) {
    const { turnable } = await import('/maps/model.js');
    const view = document.createElement('div');
    view.className = 'model-view';
    host.append(view);
    // drawn from the build's web copy, with the settings as saved, which may be newer than that build
    const api = await turnable(view, { base: '/build/' + piece.name + '/', fields: true, settings: Object.fromEntries(piece.settings.map(s => [s.key, s.value])) });
    return {
        set: (k, v) => api.set(k, v),
        view: () => api.view(),
        goto: (k, v) => api.set(k, v),
        parts: api.parts(),
        show: (id, on) => api.show(id, on),
        cleanup: () => api.stop(),
    };
}

async function terrainAdapter(piece) {
    const script = document.createElement('script');
    script.type = 'application/json';
    script.id = 'map-def-' + piece.name;
    script.textContent = JSON.stringify(piece.def);
    document.head.querySelector('#' + script.id)?.remove();
    document.head.append(script);
    const { makeMap, show, restyle, camera } = await import('/maps/map.js');
    const maplibre = await import('/maps/lib/maplibre-gl.mjs');
    const el = document.createElement('div');
    el.className = 'wb-map';
    host.append(el);
    const state = makeMap(el, piece.name, { view: piece.def.start });
    const def = state.def;   // the copy the live map reads, so edits reach show() and restyle()
    state.map.addControl(new maplibre.NavigationControl({ visualizePitch: true }), 'top-right');
    await new Promise(ok => state.map.once('load', ok));
    show(state, def.start, false);
    // The palette goes onto the page as overrides of the tokens, both modes seeded from the settings,
    // applied for whichever mode the screen is in
    const tokens = {};
    for (const s of piece.settings) {
        const m = /^token\.(--map-[\w-]+)\.(light|dark)$/.exec(s.key);
        if (m) (tokens[m[1]] ||= {})[m[2]] = s.value;
    }
    const dark = matchMedia('(prefers-color-scheme: dark)');
    let timer = 0;
    const paint = () => {
        for (const [t, v] of Object.entries(tokens)) {
            const val = dark.matches ? v.dark : v.light;
            if (val) document.documentElement.style.setProperty(t, val);
        }
        clearTimeout(timer);   // a color picker fires as it is dragged; repaint once it rests
        timer = setTimeout(() => restyle(state), 150);
    };
    dark.addEventListener('change', paint);
    paint();
    // A view is saved as it would be framed on a 1200 x 800 map: the zoom the site takes off for a smaller map is put back
    const cam = () => {
        const m = state.map, c = m.getCenter(), fit = camera({ center: [0, 0], zoom: 0 }, el).zoom;
        return { center: [+c.lng.toFixed(4), +c.lat.toFixed(4)], zoom: +(m.getZoom() - fit).toFixed(2), pitch: +m.getPitch().toFixed(1), bearing: +m.getBearing().toFixed(1) };
    };
    return {
        set(k, v) {
            if (k === 'exaggeration') {
                def.exaggeration = v;
                if (state.map.getTerrain()) state.map.setTerrain({ source: 'terrain', exaggeration: v });
            } else if (k.startsWith('token.')) {
                const [, t, mode] = /^token\.(--map-[\w-]+)\.(light|dark)$/.exec(k);
                (tokens[t] ||= {})[mode] = v;
                paint();
            } else if (k.startsWith('view.')) {
                Object.assign(def.views[k.slice(5)], v);
            }
        },
        view: cam,
        goto(k) {
            show(state, k.slice(5), true);
        },
        parts: [],
        cleanup() {
            Object.keys(tokens).forEach(t => document.documentElement.style.removeProperty(t));
            dark.removeEventListener('change', paint);
            state.map.remove();
        },
    };
}

// A print page: its image, with nothing to tune
async function pageAdapter(piece) {
    if (piece.image) {
        const img = document.createElement('img');
        img.className = 'wb-image';
        img.src = '/maps/' + piece.name + '/' + piece.image;
        host.append(img);
    }
    return { set() {}, view() {}, goto() {}, parts: [] };
}

// A print map: the page its session builds, in a frame of its own, scaled to fit. Paint settings (colours,
// weights, opacity, an unlabelled layer shown or hidden) change a variable on the page at once; every other
// change goes to the session, which sends back the pieces of the page that changed. The title, subtitle and
// credits are edited on the page itself, the frame is dragged and pinched to change its extent, and labels are
// dragged (a name on a line slides along it), deleted, reset with a double-click, and reworded with Return.
async function printAdapter(piece) {
    const CID = editorId;   // this opening of the map: its session is its own, and goes when it closes
    const name = piece.name, api = '/api/print/' + name + '/';
    const kinds = Object.fromEntries(piece.settings.map(s => [s.key, s]));
    const isPaint = k => kinds[k]?.effect === 'paint';
    const labelsKey = piece.settings.find(s => s.type === 'labels')?.key;
    let alive = true;   // false once another piece is opened: whatever is still on its way back is then dropped
    const viewOnly = piece.role === 'viewer';   // another tab is editing this map
    let rev = piece.rev, locked = viewOnly;      // the preview this page shows; whether its page may be edited just now
    // This tab has stopped being the map's editor (another took over): it says so and opens the map again, view-only
    let demoted = false;
    function demote() {
        if (demoted || !alive) return;
        demoted = true;
        notice = 'Another tab has taken over this map. The unsaved changes in this tab were discarded.';
        open(current.name);
    }
    const send = async (what, body) => {
        const res = await fetch(api + what + '?cid=' + CID, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        return res.json();
    };
    // The session is stopped after half an hour left alone. The next message then finds none: the map is opened
    // again, given back every unsaved value, and the message sent once more.
    const unsaved = () => Object.fromEntries(Object.keys(values).filter(k => !isPaint(k) && JSON.stringify(values[k]) !== JSON.stringify(saved[k])).map(k => [k, values[k]]));
    async function revive() {
        const res = await fetch('/api/piece/' + name + '?cid=' + CID);
        const again = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(again.error || 'the map could not be opened again');
        if (again.role === 'viewer' && !viewOnly) return demote();
        rev = again.rev;
        const edits = unsaved();
        if (Object.keys(edits).length) {
            const r = await send('set', { values: edits, rev });
            if (r.error) throw new Error(r.error);
            rev = r.rev;
        }
    }
    // Every message names the preview it was made on (`body.rev`, or the current one). One made on a preview since
    // replaced is not acted on: a change of settings is sent again on the current preview; a label or extent edit
    // is dropped, the page redrawn, and the reason said.
    const post = async (what, body) => {
        const made = body.rev;
        let r = await send(what, { ...body, rev: made ?? rev });
        if (r.stale) {
            await revive();
            await load();
            if (made !== undefined) throw new Error('the map had to be opened again, so that change was not applied; the page shows it as it stands');
            r = await send(what, { ...body, rev });
        }
        if (r.viewer) return demote(), new Promise(() => {});
        if (r.outdated) {
            rev = r.rev;
            if (made === undefined) r = await send(what, { ...body, rev });
            else {
                await load();
                throw new Error('the page had changed since that was done, so it was not applied; the page shows it as it stands');
            }
        }
        if (r.error) throw new Error(r.error);
        return r;
    };
    const times = (window.wbTimes ||= []);   // how long each kind of change took, for whoever measures
    const timed = (what, t0, server) => requestAnimationFrame(() => times.push({ what, ms: Math.round(performance.now() - t0), server }));

    // --- the page in its frame, with a small bar above it
    const wrap = document.createElement('div');
    wrap.className = 'wb-print';
    const bar = document.createElement('div');
    bar.className = 'wb-print-bar';
    const view = document.createElement('div');
    view.className = 'wb-print-view';
    const sizer = document.createElement('div');
    const frame = document.createElement('iframe');
    frame.title = piece.title;
    sizer.append(frame);
    view.append(sizer);
    const notes = document.createElement('div');
    notes.className = 'wb-print-notes';
    wrap.append(bar, view, notes);
    host.append(wrap);

    let pages = piece.pages, pageN = 1, zoom = 'fit', scale = 1, doc = null, sel = null, sent = structuredClone(Object.fromEntries(piece.settings.map(s => [s.key, s.value])));
    const size = () => pages.find(p => p.n === pageN) || pages[0];
    function layout() {
        const p = size(), w = p.w_in * 96, h = p.h_in * 96;
        scale = zoom === 'fit' ? Math.min((view.clientWidth - 2) / w, Math.max(240, innerHeight - view.getBoundingClientRect().top - 60) / h) : zoom;
        frame.style.width = w + 'px';
        frame.style.height = h + 'px';
        frame.style.transform = 'scale(' + scale + ')';
        sizer.style.width = w * scale + 'px';
        sizer.style.height = h * scale + 'px';
        zoomLabel.textContent = Math.round(scale * 100) + '%';
    }
    const tool = (text, title, act) => {
        const b = button(text, title, act);
        b.classList.add('wb-print-tool');
        return b;
    };
    const zoomLabel = document.createElement('span');
    const pagePick = document.createElement('select');
    pagePick.title = 'Page';
    function drawBar() {
        pagePick.replaceChildren(...pages.map(p => new Option('Page ' + p.n + ', ' + p.size, p.n, false, p.n === pageN)));
        pagePick.hidden = pages.length < 2;
    }
    pagePick.addEventListener('change', () => ((pageN = +pagePick.value), load()));
    bar.append(tool('Fit', 'Fit the page in the window', () => ((zoom = 'fit'), layout())),
        tool('Actual Size', 'The page at the size it prints', () => ((zoom = 1), layout())),
        tool('−', 'Smaller', () => ((zoom = Math.max(0.1, scale / 1.25)), layout())),
        tool('+', 'Larger', () => ((zoom = Math.min(8, scale * 1.25)), layout())), zoomLabel, pagePick);
    addEventListener('resize', layout);
    if (viewOnly) {
        const note = document.createElement('span');
        note.className = 'wb-print-viewonly';
        note.textContent = 'This map is being edited in another tab. This tab is view-only.';
        bar.append(note, tool('Take Over', 'Edit the map here; the other tab becomes view-only and its unsaved changes are discarded', async () => {
            await send('take', {});
            open(current.name);
        }));
        frame.style.pointerEvents = 'none';
    }

    // --- paint: a variable on the page
    const cssName = k => '--' + k.replace(/\./g, '-');
    const paint = (k, v) => doc?.body.style.setProperty(cssName(k), kinds[k].type === 'toggle' ? (v ? 'inline' : 'none') : v);

    // --- the page's own text, edited on it; while a box is being edited it shows the setting as typed
    const rendered = {};   // the session's latest drawing of each text box, shown again once the box is left
    function dress() {
        doc.querySelectorAll('[data-setting]').forEach(el => {
            el.contentEditable = locked ? 'false' : 'plaintext-only';
            el.spellcheck = true;
            rendered[el.id] ??= el.outerHTML;
        });
        doc.body.classList.toggle('wb-ghosts', ghosts);
    }
    function showNotes(n) {
        const line = (label, items) => {
            if (!items.length) return null;
            const p = document.createElement('p');
            p.append(label + ': ', ...items.flatMap((it, i) => (i ? [', ', it] : [it])));
            return p;
        };
        const gone = n.orphans.map(o => {
            const span = document.createElement('span');
            span.append(o.key + ' ', button('×', 'Remove this pin', () => pin({ action: 'forget', index: o.index })));
            return span;
        });
        notes.replaceChildren(...[line('Labels Left Off', n.unplaced), line('Pins Waiting Outside the Frame', n.dormant.map(d => d.key)),
            line('Pins Whose Feature Is Gone', gone), line('Pins Set on Top of Each Other', n.overlaps.map(o => o.join(' and ')))].filter(Boolean));
    }

    // --- the session's answer: changed pieces swapped in by their ids, or the page loaded again
    async function take(r, what, t0) {
        if (!alive) return;
        rev = r.rev;
        pages = r.pages;
        if (!pages.some(p => p.n === pageN)) pageN = 1;
        drawBar();
        if (r.reload && doc.activeElement?.closest?.('[data-setting]')) stale = true;   // not under a box being typed in: once it is left
        else if (r.reload) {
            for (const k in rendered) delete rendered[k];
            await load();
        } else {
            const busy = doc.activeElement;
            for (const f of r.fragments) {
                if (f.page !== pageN) continue;
                if (f.id in rendered) rendered[f.id] = f.html;
                const el = doc.getElementById(f.id);
                if (el && el !== busy) el.outerHTML = f.html;
            }
            doc.querySelectorAll('[id$="-content"]').forEach(g => g.removeAttribute('transform'));
            select_(null);
            dress();
        }
        showNotes(r.notes);
        timed(what, t0, r.ms);
    }
    // every change but paint goes to the session, a moment after the controls rest, one message at a time
    let timer = 0, busy = Promise.resolve();
    let refusal = '';   // the last edit the session would not take, for a save that was waiting on it
    const bad = {};     // settings whose present value the session refused, and why: nothing is saved while one stands
    const queue = job => (busy = busy.then(() => alive && job()).catch(e => {
        console.error(e);
        refusal = 'The page could not be redrawn: ' + e.message;
        if (alive) say(refusal, 'error');
    }));
    function sync() {
        const edits = Object.fromEntries(Object.keys(values).filter(k => !isPaint(k) && JSON.stringify(values[k]) !== JSON.stringify(sent[k])).map(k => [k, structuredClone(values[k])]));
        // a value the session has refused is not sent again by itself, only along with a change to something
        if (!Object.keys(edits).some(k => bad[k]?.value !== JSON.stringify(edits[k]))) return;
        Object.assign(sent, structuredClone(edits));
        const t0 = performance.now();
        const what = Object.keys(edits).every(k => kinds[k]?.effect === 'structure') ? 'structure' : 'geometry';
        queue(async () => {
            let r;
            try {
                r = await post('set', { values: edits });
            } catch (e) {   // the page could not be built with these: they stay marked until a value that can be is sent
                for (const k in edits) bad[k] = { why: e.message, value: JSON.stringify(edits[k]) }, delete sent[k];   // unsent again, so the next change sends every one of them
                throw e;
            }
            for (const k in edits) delete bad[k];
            return take(r, what, t0);
        });
    }
    // a change made on the page (a pin, the extent) comes back as a value, which is then the page's and ours
    function adopt(key, value) {
        if (!alive) return;
        values[key] = value;
        sent[key] = structuredClone(value);
        changed();
    }
    const pin = (body, made = rev) => queue(async () => {      // `made`: the preview the label was seen on when it was dragged
        const t0 = performance.now();
        const r = await post('pin', { ...body, rev: made });
        await take(r, 'pin', t0);
        adopt(labelsKey, r.labels);
    });

    // --- the page's own events: text, labels, the frame's extent
    const svgPoint = (svg, e) => new DOMPoint(e.clientX, e.clientY).matrixTransform(svg.getScreenCTM().inverse());
    function select_(el) {
        doc.querySelectorAll('.wb-sel').forEach(x => x.remove());
        sel = el;
        if (!el) return;
        const b = el.getBBox(), r = doc.createElementNS('http://www.w3.org/2000/svg', 'rect');
        for (const [k, v] of Object.entries({ x: b.x - 1.5, y: b.y - 1, width: b.width + 3, height: b.height + 2, class: 'wb-sel' })) r.setAttribute(k, v);
        el.parentNode.append(r);
    }
    // a guide's runs, as arrays of points, and the nearest place on them to a point
    function nearest(guide, p) {
        let best = null;
        for (const run of guide.getAttribute('d').split('M').filter(Boolean)) {
            const pts = run.trim().split(' ').map(s => s.split(',').map(Number));
            for (let i = 0, along = 0; i < pts.length - 1; i++) {
                const [ax, ay] = pts[i], [bx, by] = pts[i + 1], dx = bx - ax, dy = by - ay, len = Math.hypot(dx, dy) || 1e-9;
                const t = Math.max(0, Math.min(1, ((p.x - ax) * dx + (p.y - ay) * dy) / (len * len)));
                const x = ax + t * dx, y = ay + t * dy, d = Math.hypot(p.x - x, p.y - y);
                if (!best || d < best.d) best = { d, x, y, pts, at: along + t * len };
                along += len;
            }
        }
        return best;
    }
    // a label's glyphs set along a run of its guide, its middle at `at`: positions from the advances the engine sent
    function relay(el, pts, at) {
        const xs = el.dataset.x.split(' ').map(Number), ns = el.dataset.n.split(' ').map(Number), w = +el.dataset.w, off = +el.dataset.off;
        const cum = [0];
        for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
        const point = d => {
            d = Math.max(0, Math.min(cum[cum.length - 1], d));
            let i = 1;
            while (i < cum.length - 1 && cum[i] < d) i++;
            const t = (d - cum[i - 1]) / (cum[i] - cum[i - 1] || 1e-9), a = pts[i - 1], b = pts[i];
            return { x: a[0] + t * (b[0] - a[0]), y: a[1] + t * (b[1] - a[1]), ang: Math.atan2(b[1] - a[1], b[0] - a[0]) };
        };
        const a = point(at - w / 2), b = point(at + w / 2), dir = b.x < a.x ? -1 : 1;   // read left to right
        const X = [], Y = [], R = [];
        xs.forEach((x, i) => {
            const q = point(at + dir * (x - w / 2)), ang = dir > 0 ? q.ang : q.ang + Math.PI;
            const px = q.x + Math.sin(ang) * off, py = q.y - Math.cos(ang) * off;   // off is the baseline's rise above the line
            for (let j = 0; j < ns[i]; j++) X.push(px.toFixed(2)), Y.push(py.toFixed(2)), R.push((ang * 180 / Math.PI).toFixed(2));
        });
        el.setAttribute('x', X.join(' '));
        el.setAttribute('y', Y.join(' '));
        el.setAttribute('rotate', R.join(' '));
    }

    let drag = null, edit = null, wheel = null, stale = false;
    function wire() {
        doc.head.insertAdjacentHTML('beforeend', `<style>
            [data-setting]{outline:1px dashed transparent;outline-offset:3px;border-radius:2px;min-width:2em}
            [data-setting]:hover{outline-color:#D9D5CC}[data-setting]:focus{outline:1px solid #5E4B8B}
            svg text[data-layer]{cursor:grab;user-select:none;-webkit-user-select:none}
            body.wb-ghosts svg .ghosts{display:inline}
            .wb-sel{fill:none;stroke:#5E4B8B;stroke-width:0.5;stroke-dasharray:1.5 1;pointer-events:none}
            .wb-dragging, .wb-dragging *{cursor:grabbing !important}
            .wb-label-edit{position:absolute;z-index:5;font:12px 'Source Sans 3',sans-serif;padding:2px 4px;border:1px solid #5E4B8B;background:#fff;color:#22282B;resize:none}
        </style>`);
        doc.addEventListener('focusin', e => {
            const el = e.target.closest?.('[data-setting]');
            if (el) el.textContent = values[el.dataset.setting] ?? '';
        });
        doc.addEventListener('focusout', e => {
            const el = e.target.closest?.('[data-setting]');
            if (!el) return;
            setTimeout(() => {
                if (stale) {
                    stale = false;
                    for (const k in rendered) delete rendered[k];
                    return queue(load);
                }
                if (el.isConnected) el.outerHTML = rendered[el.id];
                dress();
            });
        });
        doc.addEventListener('input', e => {
            const el = e.target.closest?.('[data-setting]');
            if (!el) return;
            const key = el.dataset.setting;
            values[key] = el.classList.contains('credits') ? el.innerText.replace(/[ \t]+\n/g, '\n').replace(/\n+$/, '') : el.textContent.replace(/\s+/g, ' ').trim();
            changed();
        });
        doc.addEventListener('keydown', e => {
            const el = e.target.closest?.('[data-setting]');
            if (el && (e.key === 'Escape' || (e.key === 'Enter' && !el.classList.contains('credits')))) return e.preventDefault(), el.blur();
            if (el || edit) return;
            keys(e);
        });
        doc.addEventListener('pointerdown', e => {
            if (e.button !== 0 || edit || locked) return;
            const label = e.target.closest?.('text[data-layer]'), svg = e.target.closest?.('svg[id]');
            if (label) {
                const s = label.ownerSVGElement;
                drag = { type: 'label', el: label, id: label.id, rev, svg: s, x: e.clientX, y: e.clientY, start: svgPoint(s, e), on: false,
                    guide: label.dataset.guide ? doc.getElementById(label.dataset.guide) : null, was: ['x', 'y', 'rotate'].map(a => label.getAttribute(a)) };
            } else if (svg && !svg.dataset.fixed && doc.getElementById(svg.id + '-content') && !e.target.closest('[data-setting]')) {
                drag = { type: 'frame', rev, svg, x: e.clientX, y: e.clientY, start: svgPoint(svg, e), on: false };
                select_(null);
            } else select_(null);
        });
        doc.addEventListener('pointermove', e => {
            if (!drag) return;
            if (!drag.on) {
                if (Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < 5) return;   // a press that moves five pixels becomes a drag
                drag.on = true;
                doc.body.classList.add('wb-dragging');
                select_(null);
                doc.getSelection()?.removeAllRanges();
            }
            e.preventDefault();
            const p = svgPoint(drag.svg, e), dx = p.x - drag.start.x, dy = p.y - drag.start.y;
            drag.dx = dx;
            drag.dy = dy;
            if (drag.type === 'frame') return doc.getElementById(drag.svg.id + '-content').setAttribute('transform', `translate(${dx} ${dy})`);
            const near = drag.guide && nearest(drag.guide, p);
            if (near && near.d <= 24) {              // a name on a line slides along it
                drag.el.removeAttribute('transform');
                relay(drag.el, near.pts, near.at);
                drag.on_line = { x: near.x, y: near.y };
            } else {                                  // anything else moves freely (a name pulled well off its line comes free of it)
                if (drag.guide) ['x', 'y', 'rotate'].forEach((a, i) => drag.el.setAttribute(a, drag.was[i]));
                drag.el.setAttribute('transform', `translate(${dx} ${dy})`);
                drag.on_line = null;
            }
        }, { passive: false });
        const up = () => {
            const d = drag;
            drag = null;
            if (!d) return;
            doc.body.classList.remove('wb-dragging');
            if (d.on && d.rev !== rev) {   // the page was redrawn under the drag: what was dragged is no longer what is shown
                say('The page was redrawn while that was being dragged, so it was not applied; the page shows it as it stands.', 'error');
                return void queue(load);
            }
            // the edit goes to the session as made on the preview the drag began on, by the id the label had then
            if (d.type === 'label') {
                if (!d.on) return d.el.isConnected ? select_(d.el) : undefined;
                if (d.on_line) pin({ action: 'move', label: d.id, x: d.on_line.x, y: d.on_line.y }, d.rev);
                else pin({ action: 'move', label: d.id, dx: d.dx, dy: d.dy, free: true }, d.rev);
            } else if (d.on) moveView(d.svg, { dx: d.dx, dy: d.dy }, d.rev);
        };
        doc.addEventListener('pointerup', up);
        doc.addEventListener('pointercancel', up);
        doc.addEventListener('dblclick', e => {
            const label = e.target.closest?.('text[data-layer]');
            if (label && !locked) pin({ action: 'reset', label: label.id });
        });
        // a pinch, or the wheel with Control or Command held, zooms the frame's extent about the pointer
        doc.addEventListener('wheel', e => {
            const svg = e.target.closest?.('svg[id]'), content = svg && doc.getElementById(svg.id + '-content');
            if (!content || locked || svg.dataset.fixed || !(e.ctrlKey || e.metaKey)) return;
            e.preventDefault();
            const p = svgPoint(svg, e);
            wheel ||= { svg, ax: p.x, ay: p.y, zoom: 1, rev };
            wheel.zoom = Math.max(0.2, Math.min(5, wheel.zoom * Math.exp(-e.deltaY * 0.01)));
            content.setAttribute('transform', `translate(${wheel.ax} ${wheel.ay}) scale(${wheel.zoom}) translate(${-wheel.ax} ${-wheel.ay})`);
            clearTimeout(wheel.timer);
            wheel.timer = setTimeout(endWheel, 250);
        }, { passive: false });
    }
    // a zoom gesture at rest (or cut short by a save) goes to the session as one change of extent
    function endWheel() {
        const w = wheel;
        if (!w) return;
        clearTimeout(w.timer);
        wheel = null;
        moveView(w.svg, { zoom: w.zoom, ax: w.ax, ay: w.ay }, w.rev);
    }
    const moveView = (svg, how, made = rev) => queue(async () => {
        const t0 = performance.now();
        let r;
        try {
            r = await post('view', { frame: svg.id, ...how, rev: made });
        } catch (e) {   // an extent the map cannot take: the frame goes back to where it was
            doc.getElementById(svg.id + '-content')?.removeAttribute('transform');
            throw e;
        }
        await take(r, 'geometry', t0);
        adopt(r.key, r.value);
    });
    // Delete removes the selected label; Return rewords it (Shift-Return for a line break in a name that is not on a line)
    function keys(e) {
        if (!sel || locked) return;
        if (e.key === 'Delete' || e.key === 'Backspace') {
            e.preventDefault();
            pin({ action: 'delete', label: sel.id });
        } else if (e.key === 'Escape') select_(null);
        else if (e.key === 'Enter') {
            e.preventDefault();
            const el = sel, id = el.id, made = rev, r = el.getBoundingClientRect(), box = doc.createElement('textarea');   // the label and the preview as they were when the box opened
            box.className = 'wb-label-edit';
            box.rows = 2;
            box.value = el.querySelector('tspan') ? [...el.querySelectorAll('tspan')].map(t => t.textContent).join('\n') : el.textContent;
            box.style.left = r.left + doc.defaultView.scrollX + 'px';
            box.style.top = r.bottom + doc.defaultView.scrollY + 4 + 'px';
            doc.body.append(box);
            edit = box;
            box.focus();
            box.select();
            const done = keep => {
                if (edit !== box) return;
                edit = null;
                const text = box.value;
                box.remove();
                if (keep) pin({ action: 'text', label: id, text }, made);
            };
            box.addEventListener('keydown', ev => {
                ev.stopPropagation();   // the page's own keys (Return, Delete) are not for a box being typed in
                if (ev.key === 'Escape') done(false);
                else if (ev.key === 'Enter' && !ev.shiftKey) ev.preventDefault(), done(true);
            });
            box.addEventListener('blur', () => done(true));
        }
    }
    const outerKeys = e => {
        if (sel && !e.target.closest('input, textarea, select, [contenteditable]')) keys(e);
    };
    addEventListener('keydown', outerKeys);

    let ghosts = false;
    async function load() {
        const t0 = performance.now();
        let head = await fetch('/print/' + name + '/page/' + pageN + '?cid=' + CID, { method: 'HEAD' });
        if (head.status === 409) {   // the session was stopped while the map sat open
            await revive();
            head = await fetch('/print/' + name + '/page/' + pageN + '?cid=' + CID, { method: 'HEAD' });
        }
        rev = +head.headers.get('X-Preview-Revision') || rev;
        const url = '/print/' + name + '/page/' + pageN + '?cid=' + CID + '&rev=' + rev;
        return new Promise(ok => {
            frame.addEventListener('load', async () => {
                doc = frame.contentDocument;
                sel = null;
                wire();
                for (const s of piece.settings) if (isPaint(s.key) && values) paint(s.key, values[s.key]);
                dress();
                layout();
                await doc.fonts.ready;
                timed('load', t0);
                ok();
            }, { once: true });
            frame.src = url + '&t=' + Date.now();
        });
    }
    drawBar();
    await load();
    showNotes(piece.notes);
    let building = 0;
    // the session lives as long as this editor: word every half minute keeps it, and it is told when the editor goes
    // (the answer also says whether this tab is still the map's editor; if another has taken over, it learns here)
    const beat = setInterval(async () => {
        const r = await send('ping', {}).catch(() => ({}));
        if (r.role === 'viewer' && !viewOnly) demote();
    }, 10000);
    const bye = () => navigator.sendBeacon(api + 'close?cid=' + CID, new Blob(['{}'], { type: 'application/json' }));
    addEventListener('pagehide', bye);
    return {
        ownsView: true,
        tokens: Object.fromEntries(piece.settings.filter(s => s.type === 'color').map(s => [s.key, s.label])),
        set(k, v) {
            if (isPaint(k)) {
                const t0 = performance.now();
                paint(k, v);
                timed('paint', t0);
            }
        },
        changed() {
            clearTimeout(timer);
            timer = setTimeout(() => ((timer = 0), sync()), 150);
        },
        view() {},
        goto() {},
        parts: labelsKey ? [{ id: 'ghosts', label: 'Labels Left Off', on: false }] : [],
        show(id, on) {
            ghosts = on;
            doc.body.classList.toggle('wb-ghosts', on);
        },
        // after a save the PDF is rebuilt in the background; the status line says how it went
        afterSave() {
            say('Saved. Building the PDF…');
            const mine = ++building, t0 = performance.now();
            const poll = async () => {
                if (mine !== building) return;
                const b = await (await fetch(api + 'build?cid=' + CID)).json();
                if (b.state === 'building') return setTimeout(poll, 500);
                times.push({ what: 'build', ms: Math.round(performance.now() - t0) });
                if (same()) say(b.state === 'done' ? 'Saved. ' + b.message : b.message, b.state === 'done' ? '' : 'error');
            };
            setTimeout(poll, 500);
        },
        // every change still on its way (a pin dropped, the extent, a control at rest) is in the values before a save reads them
        // (returns the refusal, if an edit drained here was refused: the save then stops)
        async settle() {
            refusal = '';
            endWheel();
            if (timer) clearTimeout(timer), (timer = 0), sync();
            await busy;
            const held = Object.keys(bad).map(k => kinds[k]?.label || k);
            return refusal || (held.length ? `${held.join(', ')}: the page cannot be drawn with this (${Object.values(bad)[0].why}). Change it and save again.` : '');
        },
        viewOnly,
        demote,
        // the page itself, its labels, extent and text, is shut while a save runs, and for good in a view-only tab
        lock(on) {
            locked = on || viewOnly;
            frame.style.pointerEvents = locked ? 'none' : '';
            if (doc) dress();
        },
        cleanup() {
            alive = false;
            clearInterval(beat);
            removeEventListener('pagehide', bye);
            bye();
            clearTimeout(timer);
            clearTimeout(wheel?.timer);
            building++;
            removeEventListener('resize', layout);
            removeEventListener('keydown', outerKeys);
        },
    };
}

const ADAPTERS = { model: modelAdapter, terrain: terrainAdapter, page: pageAdapter, print: printAdapter };

// --- The page's text, edited in place

const textEls = {};
function textBox(tag, key, cls, placeholder, plain = false) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    el.contentEditable = 'false';   // editable once the piece has loaded and its text is in
    el.spellcheck = true;
    el.dataset.key = key;
    el.dataset.placeholder = placeholder;
    if (plain) el.dataset.plain = '';   // one line of plain text (the title): Enter ends the edit
    // While being edited the box shows its markdown, line breaks and all; once left, it shows it rendered
    el.addEventListener('focus', () => (el.textContent = values[key] ?? ''));
    el.addEventListener('blur', () => render(el, values[key]));
    el.addEventListener('keydown', e => {
        if (e.key === 'Escape' || (plain && e.key === 'Enter')) return e.preventDefault(), el.blur();
        if (!plain && (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') e.preventDefault(), link();
    });
    el.addEventListener('input', () => {
        values[key] = plain ? el.textContent.replace(/\s+/g, ' ').trim() : el.innerText.replace(/[ \t]+\n/g, '\n').trim();
        changed();
    });
    textEls[key] = el;
    return el;
}

// Command-K makes the selected words a markdown link, [words](), with the cursor where the address goes
// (or in the brackets when nothing is selected), as Obsidian and VS Code do; a selection across paragraphs
// cannot be one link, so it is left as it is
function link() {
    const sel = getSelection(), words = sel.toString();
    if (/\n\s*\n/.test(words)) return;
    document.execCommand('insertText', false, '[' + words + ']()');   // through the editor, so Undo takes it back
    for (let i = 0; i < (words ? 1 : 3); i++) sel.modify('move', 'backward', 'character');
}

// Markdown rendered by build.py, as the map's page renders it; only the box's latest render is shown
const renders = new WeakMap();
async function render(el, text) {
    const mine = (renders.get(el) || 0) + 1;
    renders.set(el, mine);
    if (!text || 'plain' in el.dataset) return void (el.textContent = text ?? '');
    try {
        const res = await fetch('/api/render', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
        const { html, error } = await res.json();
        if (error) throw new Error(error);
        // An editable box keeps every line break it holds, so the HTML's own (between its tags) are taken out
        if (document.activeElement !== el && renders.get(el) === mine) el.innerHTML = html.replace(/\n/g, '');
    } catch (e) {
        if (document.activeElement !== el && renders.get(el) === mine) el.textContent = text;
        say('Could not show the text as the page will: ' + e.message, 'error');
    }
}

// The title and the note above the piece and the credits below it, laid out as the map's page has them
function pageText(piece) {
    const header = document.createElement('header');
    header.className = 'post wb-text';
    header.append(textBox('h1', 'text.title', 'wb-edit', 'Title', true), textBox('div', 'text.note', 'subtitle wb-edit', 'Description'));
    legendEl = document.createElement('div');
    legendEl.className = 'wb-legend';
    return { header, legend: legendEl, credits: textBox('div', 'text.credits', 'map-credits wb-edit', 'Credits') };
}

function apply(k, v) {
    if (textEls[k]) render(textEls[k], v);
    else if (k === 'legend') drawLegend();
    else adapter.set(k, v);
}

// --- The legend, beneath the map as on its page: headings and labels retyped in place, a swatch clicked to
// change its shape and colour, and items and groups moved, added and removed with the small buttons beside them

let legendEl = null;
const button = (text, title, act) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'wb-tool';
    b.textContent = text;
    b.title = title;
    b.addEventListener('click', e => (e.preventDefault(), act()));
    return b;
};
function legendChanged() {
    // a group left with no items goes, so the legend stays saveable
    for (let i = values.legend.length - 1; i >= 0; i--) if (!values.legend[i].items.length) values.legend.splice(i, 1);
    drawLegend();
    changed();
}
// One line of text edited in place; Enter or leaving it ends the edit
function legendText(tag, cls, text, placeholder, set) {
    const el = document.createElement(tag);
    el.className = cls + ' wb-edit';
    el.contentEditable = 'plaintext-only';
    el.spellcheck = true;
    el.dataset.placeholder = placeholder;
    el.textContent = text ?? '';
    el.addEventListener('keydown', e => (e.key === 'Enter' || e.key === 'Escape') && (e.preventDefault(), el.blur()));
    el.addEventListener('input', () => (set(el.textContent.replace(/\s+/g, ' ').trim()), changed()));
    return el;
}
// The colour tokens the map's layers use, from the site's map stylesheet (only those holding a colour)
function mapTokens() {
    if (adapter?.tokens) return Object.keys(adapter.tokens);
    const names = new Set();
    for (const sheet of document.styleSheets) {
        try {
            for (const rule of sheet.cssRules)
                for (const prop of rule.style || [])
                    if (prop.startsWith('--map-') && /^#[0-9A-Fa-f]{6}$/.test(rule.style.getPropertyValue(prop).trim())) names.add(prop);
        } catch { /* a sheet from another origin */ }
    }
    return [...names].sort();
}
const swatchColor = c => (c.startsWith('--') ? 'var(' + c + ')' : adapter?.tokens && c in adapter.tokens ? values[c] : c);
function swatch(it) {
    const el = document.createElement('span');
    const shape = it.shape || 'fill';
    el.className = 'swatch' + (shape === 'fill' ? '' : ' ' + shape) + (shape === 'line' && it.dash ? ' ' + it.dash : '');
    el.style[shape === 'line' ? 'borderTopColor' : 'background'] = swatchColor(it.color);
    return el;
}
// The swatch's settings, opened under its item: shape, line style, and colour (a map token or any colour)
function swatchEditor(it) {
    const row = document.createElement('div');
    row.className = 'wb-swatch-edit';
    const pick = (options, value, set) => {
        const sel = document.createElement('select');
        for (const [v, label] of options) sel.append(new Option(label, v, false, v === value));
        sel.addEventListener('change', () => (set(sel.value), legendChanged()));
        return sel;
    };
    row.append(pick([['fill', 'Fill'], ['dot', 'Dot'], ['line', 'Line']], it.shape || 'fill', v => {
        if (v === 'fill') delete it.shape; else it.shape = v;
        if (v !== 'line') delete it.dash;
    }));
    if (it.shape === 'line') row.append(pick([['', 'Solid'], ['dashed', 'Dashed'], ['dotted', 'Dotted']], it.dash || '', v => {
        if (v) it.dash = v; else delete it.dash;
    }));
    const tokens = mapTokens();
    const custom = !tokens.includes(it.color);
    row.append(pick([...tokens.map(t => [t, adapter?.tokens ? adapter.tokens[t] : t.slice(6).replace(/-/g, ' ')]), ['custom', 'Other Colour']], custom ? 'custom' : it.color, v => {
        it.color = v === 'custom' ? (custom ? it.color : '#888888') : v;
    }));
    const code = document.createElement('input');
    code.type = 'text';
    code.placeholder = 'Code';
    code.value = it.code ?? '';
    code.size = 6;
    code.addEventListener('change', () => {
        if (code.value.trim()) it.code = code.value.trim(); else delete it.code;
        legendChanged();
    });
    if (custom) {
        const input = document.createElement('input');
        input.type = 'color';
        input.value = it.color.toLowerCase();
        input.addEventListener('input', () => (it.color = input.value.toUpperCase(), changed(), row.previousElementSibling?.querySelector('.swatch')?.replaceWith(swatch(it))));
        input.addEventListener('change', legendChanged);
        row.append(input);
    }
    row.append(code);
    return row;
}
let openSwatch = null;   // the item whose swatch settings are showing

// Dragging: a group by its heading, an item by its row. A press that moves a few pixels becomes a drag (so a
// plain click still edits the text), a line in the accent shows where it will land, and letting go moves it.
let drag = null;
function dropTarget(x, y) {
    const el = document.elementFromPoint(x, y);
    if (!el || !legendEl.contains(el)) return null;
    if (drag.type === 'group') {
        const box = el.closest('.wb-legend-group');
        if (!box) return null;
        const r = box.getBoundingClientRect();
        return { el: box, gi: +box.dataset.gi, after: y > r.top + r.height / 2 };
    }
    const li = el.closest('li[data-ii]');
    if (li) {
        const r = li.getBoundingClientRect();
        return { el: li, gi: +li.dataset.gi, ii: +li.dataset.ii, after: y > r.top + r.height / 2 };
    }
    const box = el.closest('.wb-legend-group');   // over a heading: to the top of that group
    return box ? { el: box.querySelector('li[data-ii]') || box, gi: +box.dataset.gi, ii: 0, after: false } : null;
}
function showDrop(t) {
    legendEl.querySelectorAll('.wb-drop-before, .wb-drop-after').forEach(e => e.classList.remove('wb-drop-before', 'wb-drop-after'));
    if (t) t.el.classList.add(t.after ? 'wb-drop-after' : 'wb-drop-before');
}
function startDrag(e, what) {
    if (e.button !== 0 || e.target.closest('button, select, input')) return;
    // in a heading or label already being edited, a press selects text rather than dragging
    if (e.target.closest('[contenteditable]') === document.activeElement) return;
    drag = { ...what, x: e.clientX, y: e.clientY, on: false, target: null };
}
addEventListener('pointermove', e => {
    if (!drag) return;
    if (!drag.on) {
        if (Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < 5) return;
        drag.on = true;
        document.activeElement?.blur?.();
        getSelection().removeAllRanges();
        document.body.classList.add('wb-dragging');
    }
    e.preventDefault();
    drag.target = dropTarget(e.clientX, e.clientY);
    showDrop(drag.target);
});
// the click a drop ends in is not taken as a click on what lies under it
let quietUntil = 0;
addEventListener('click', e => performance.now() < quietUntil && (e.stopPropagation(), e.preventDefault()), { capture: true });
function endDrag() {
    const d = drag;
    drag = null;
    if (d?.on) {
        document.body.classList.remove('wb-dragging');
        showDrop(null);
    }
    return d;
}
addEventListener('pointercancel', endDrag);
addEventListener('pointerup', () => {
    const d = endDrag();
    if (!d?.on) return;
    quietUntil = performance.now() + 250;
    const t = d.target, legend = values.legend;
    if (!t) return;
    if (d.type === 'group') {
        let to = t.gi + (t.after ? 1 : 0);
        if (d.gi < to) to--;
        legend.splice(to, 0, legend.splice(d.gi, 1)[0]);
    } else {
        const [it] = legend[d.gi].items.splice(d.ii, 1);
        let to = t.ii + (t.after ? 1 : 0);
        if (t.gi === d.gi && d.ii < to) to--;
        legend[t.gi].items.splice(to, 0, it);
    }
    legendChanged();
});
function drawLegend() {
    if (!legendEl || !values.legend) return;
    const legend = values.legend;
    const groups = document.createElement('div');
    groups.className = 'map-legend';
    const move = (list, i, by) => list.splice(i + by, 0, list.splice(i, 1)[0]);
    legend.forEach((g, gi) => {
        const box = document.createElement('div');
        box.className = 'wb-legend-group';
        box.dataset.gi = gi;
        const head = document.createElement('div');
        head.className = 'wb-legend-row';
        head.addEventListener('pointerdown', e => startDrag(e, { type: 'group', gi }));
        head.append(legendText('h3', '', g.heading, 'Heading', v => (v ? (g.heading = v) : delete g.heading)),
            button('↑', 'Move this group up', () => gi > 0 && (move(legend, gi, -1), legendChanged())),
            button('↓', 'Move this group down', () => gi < legend.length - 1 && (move(legend, gi, 1), legendChanged())),
            button('×', 'Remove this group', () => (legend.splice(gi, 1), legendChanged())));
        const ul = document.createElement('ul');
        g.items.forEach((it, ii) => {
            const li = document.createElement('li');
            li.dataset.gi = gi;
            li.dataset.ii = ii;
            li.addEventListener('pointerdown', e => startDrag(e, { type: 'item', gi, ii }));
            const sw = swatch(it);
            sw.classList.add('wb-swatch');
            sw.title = 'Shape, colour and code';
            sw.tabIndex = 0;
            sw.setAttribute('role', 'button');
            const toggle = () => ((openSwatch = openSwatch === it ? null : it), drawLegend());
            sw.addEventListener('click', toggle);
            sw.addEventListener('keydown', e => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), toggle()));
            li.append(sw);
            if ('code' in it || g.items.some(x => x.code)) li.append(legendText('span', 'code', it.code, 'Code', v => (v ? (it.code = v) : delete it.code)));
            li.append(legendText('span', 'wb-label', it.label, 'Label', v => (it.label = v)),
                // up and down cross into the neighbouring group at either end
                button('↑', 'Move up', () => {
                    if (ii > 0) move(g.items, ii, -1);
                    else if (gi > 0) legend[gi - 1].items.push(g.items.shift());
                    legendChanged();
                }),
                button('↓', 'Move down', () => {
                    if (ii < g.items.length - 1) move(g.items, ii, 1);
                    else if (gi < legend.length - 1) legend[gi + 1].items.unshift(g.items.pop());
                    legendChanged();
                }),
                button('×', 'Remove', () => (g.items.splice(ii, 1), legendChanged())));
            ul.append(li);
            if (openSwatch === it) ul.append(swatchEditor(it));
        });
        const add = button('+ Item', 'Add an item to this group', () => {
            const last = g.items[g.items.length - 1];
            const it = { color: last?.color || mapTokens()[0] || '#888888', label: '' };
            if (last?.shape) it.shape = last.shape;
            if (last?.dash) it.dash = last.dash;
            if (last && 'code' in last) it.code = '';
            g.items.push(it);
            openSwatch = it;
            legendChanged();
        });
        add.classList.add('wb-add');
        box.append(head, ul, add);
        groups.append(box);
    });
    const addGroup = button('+ Group', 'Add a group to the legend', () => {
        const it = { color: mapTokens()[0] || '#888888', label: '' };
        legend.push({ heading: '', items: [it] });
        openSwatch = it;
        legendChanged();
    });
    addGroup.classList.add('wb-add');
    legendEl.replaceChildren(groups, addGroup);
}

// --- The panel

function build(piece) {
    gui?.destroy();
    gui = new GUI({ container: panel, title: piece.title, width: 320 });
    const folders = {};
    const folder = name => (folders[name] ||= gui.addFolder(name));
    for (const s of piece.settings) {
        if (s.type === 'text' || s.type === 'legend' || s.type === 'labels') continue;
        const f = folder(s.group || 'Settings');
        if (s.type === 'number') {
            f.add(values, s.key, s.min, s.max, s.step).name(s.label).onChange(v => {
                adapter.set(s.key, v);
                changed();
            });
        } else if (s.type === 'color') {
            f.addColor(values, s.key).name(s.label).onChange(v => {
                adapter.set(s.key, v);
                changed();
            });
        } else if (s.type === 'toggle' || s.type === 'choice') {
            (s.type === 'choice' ? f.add(values, s.key, s.options) : f.add(values, s.key)).name(s.label).onChange(v => {
                adapter.set(s.key, v);
                changed();
            });
        } else if (s.type === 'view' && adapter.ownsView) {
            // the extent is changed on the map itself (dragged, pinched); the panel only takes it back
            f.add({ back() {
                values[s.key] = structuredClone(saved[s.key]);
                changed();
            } }, 'back').name('Return to Saved ' + s.label);
        } else if (s.type === 'view') {
            const row = {
                [`Go to ${s.label}`]: () => adapter.goto(s.key, values[s.key]),
                [`Use Current View as ${s.label}`]: () => {
                    values[s.key] = adapter.view();
                    adapter.set(s.key, values[s.key]);
                    changed();
                },
            };
            for (const label of Object.keys(row)) f.add(row, label);
        }
    }
    if (adapter.parts.length) {
        const f = gui.addFolder('Show (Not Saved)');
        const on = Object.fromEntries(adapter.parts.map(p => [p.id, p.on ?? true]));
        for (const p of adapter.parts) f.add(on, p.id).name(p.label).onChange(v => adapter.show(p.id, v));
    }
}

async function open(name) {
    const mine = ++generation;   // a later choice of piece wins over a load still under way
    saving = false;
    say('Loading…');
    select.disabled = true;
    adapter?.cleanup?.();
    adapter = null;
    values = saved = null;
    gui?.destroy();
    gui = null;
    stage.replaceChildren();
    for (const k in textEls) delete textEls[k];
    try {
        editorId = EDITOR + '-' + mine;   // each opening has its own, so the closing of the last cannot stop this one
        const res = await fetch('/api/piece/' + name + '?cid=' + editorId);
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || 'could not read it');
        const piece = await res.json();
        if (mine !== generation) return;
        const page = document.createElement('div');
        page.className = 'wb-page';
        stage.append(page);
        const print = piece.kind === 'print';
        if (print) page.classList.add('wb-page-print');
        const text = print ? null : pageText(piece);
        if (text) page.append(text.header);
        host = page;
        saved = Object.fromEntries(piece.settings.map(s => [s.key, s.value]));
        values = structuredClone(saved);
        const drawn = await ADAPTERS[piece.kind](piece);
        if (mine !== generation) return drawn.cleanup?.();
        if (text) page.append(text.legend, text.credits);
        else if (piece.settings.some(s => s.type === 'legend')) {
            legendEl = document.createElement('div');
            legendEl.className = 'wb-legend';
            page.append(legendEl);
        } else legendEl = null;
        current = piece;
        adapter = drawn;
        for (const k in textEls) {
            render(textEls[k], values[k]);
            textEls[k].contentEditable = 'plaintext-only';
        }
        openSwatch = null;
        drawLegend();
    } catch (e) {
        console.error(e);
        if (mine === generation) say('Could not open ' + name + ': ' + e.message, 'error');
        return;
    } finally {
        if (mine === generation) select.disabled = false;
    }
    build(current);
    history.replaceState(null, '', '?piece=' + name);
    changed();
    if (adapter.viewOnly) {      // another tab is editing this map: nothing here can be changed
        gui.controllersRecursive().forEach(c => c.disable());
        if (legendEl) legendEl.inert = true;
    }
    if (current.fallback) {      // a saved value the page could not be drawn with: its default is shown, and a save will replace it
        for (const k of current.fallback.keys) saved[k] = null;
        changed();
        say(`The saved ${current.fallback.labels.join(', ')} could not be used (${current.fallback.reason}); the default is shown, and Save will replace what is saved.`, 'error');
    }
    if (notice) say(notice, 'error'), (notice = '');
}

saveBtn.addEventListener('click', async () => {
    // From the click until the save's answer has been applied nothing can be edited, or the piece switched: the
    // panel, the legend, the text, and (a print map) the page itself with its labels and extent
    const controls = gui.controllersRecursive();
    const lock = on => {
        if (legendEl) legendEl.inert = on;
        controls.forEach(c => c.enable(!on));
        Object.values(textEls).forEach(el => (el.contentEditable = on ? 'false' : 'plaintext-only'));
        adapter.lock?.(on);
        select.disabled = revertBtn.disabled = saveBtn.disabled = on;
    };
    const mine = generation, editor = adapter;   // this editor: an answer that comes back to another is not applied
    saving = true;
    lock(true);
    say('Saving…');
    // a label, a zoom or a control still being drawn is finished first, and is part of what is saved; if one of them
    // is refused, nothing is saved, and the refusal stays in view
    const refused = await adapter.settle?.();
    if (mine !== generation || editor !== adapter) return void (saving = false);
    if (refused) {
        saving = false;
        lock(false);
        changed();
        return say('Not saved. ' + refused, 'error');
    }
    // Only what this page changed is sent, so a save from another open workbench is not undone
    const edits = Object.fromEntries(Object.keys(values).filter(k => JSON.stringify(values[k]) !== JSON.stringify(saved[k])).map(k => [k, values[k]]));
    let res = null, body;
    try {
        res = await fetch('/api/piece/' + current.name + '?cid=' + editorId, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ values: edits, stamp: current.stamp }) });
        body = await res.json();
    } catch (e) {
        body = { error: e.message };
    }
    if (mine !== generation || editor !== adapter) return void (saving = false);   // the editor was replaced while the save was out: its answer is not this one's
    if (!res?.ok) {
        saving = false;
        lock(false);
        changed();
        if (body.viewer) return adapter.demote?.();
        say('Not saved: ' + body.error, 'error');
        // a print map's settings file changed under this editor: nothing is merged; the map can be read again from disk
        if (body.conflict && confirm('Not saved: ' + body.error + '.\n\nRead the map again from disk? The unsaved changes here will be lost.')) open(current.name);
        return;
    }
    saved = Object.fromEntries(body.piece.settings.map(s => [s.key, s.value]));
    if (body.stamp) current.stamp = body.stamp;
    // Anything saved elsewhere since this page opened comes into the controls and the preview too
    for (const [k, v] of Object.entries(saved))
        if (JSON.stringify(values[k]) !== JSON.stringify(v)) {
            values[k] = structuredClone(v);
            apply(k, values[k]);
        }
    controls.forEach(c => c.updateDisplay());
    // A new title shows in the list of maps and at the head of the panel
    const title = saved['text.title'] ?? (current.kind === 'print' ? body.piece.title : saved.title) ?? current.title;   // a print map's as the list resolves it
    select.selectedOptions[0].text = title;
    gui.title(title);
    saving = false;
    lock(false);
    changed();
    if (adapter.afterSave) adapter.afterSave();
    else say(current.kind === 'model' ? 'Saved. The next rebuild uses these settings.' : 'Saved. The site changes when the map is deployed.');
});

revertBtn.addEventListener('click', () => open(current.name));
select.addEventListener('change', () => {
    if (!same() && !confirm('Leave without saving?')) return (select.value = current.name);
    open(select.value);
});
addEventListener('beforeunload', e => {
    if (values && !same()) e.preventDefault();
});

const list = await (await fetch('/api/pieces')).json();
const groups = {};
for (const p of list) {
    if (!p.group) select.append(new Option(p.title, p.name));
    else {
        if (!groups[p.group]) select.append((groups[p.group] = Object.assign(document.createElement('optgroup'), { label: p.group })));
        groups[p.group].append(new Option(p.title, p.name));
    }
}
const wanted = new URLSearchParams(location.search).get('piece');
select.value = list.some(p => p.name === wanted) ? wanted : list[0].name;
open(select.value);
