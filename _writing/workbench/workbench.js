// The workbench page: pick a map, tune it live from the settings it declares, save or revert.
// Each kind of piece has an adapter that draws it and applies a setting to it; the panel is built
// from the declarations alone (number, color, view), so a new kind needs only its adapter. The page's
// text (type text) is not in the panel: it is shown around the piece as on the site and edited in place.
import GUI from './lib/lil-gui.esm.min.js';

const $ = id => document.getElementById(id);
const stage = $('stage'), panel = $('panel'), status = $('status'), saveBtn = $('save'), revertBtn = $('revert'), select = $('piece');
let gui = null, current = null, saved = null, values = null, adapter = null, generation = 0, host = stage;

const say = (text, cls = '') => {
    status.textContent = text;
    status.className = cls;
};
const same = () => JSON.stringify(values) === JSON.stringify(saved);
function changed() {
    const dirty = !same();
    saveBtn.disabled = revertBtn.disabled = !dirty;
    say(dirty ? 'Unsaved Changes' : 'Saved', dirty ? 'unsaved' : '');
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

const ADAPTERS = { model: modelAdapter, terrain: terrainAdapter, page: pageAdapter };

// --- The page's text, edited in place

const textEls = {};
function textBox(tag, key, cls, placeholder) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    el.contentEditable = 'false';   // editable once the piece has loaded and its text is in
    el.spellcheck = true;
    el.dataset.key = key;
    el.dataset.placeholder = placeholder;
    el.addEventListener('keydown', e => e.key === 'Enter' && (e.preventDefault(), el.blur()));
    el.addEventListener('input', () => {
        values[key] = el.textContent.replace(/\s+/g, ' ').trim();
        changed();
    });
    textEls[key] = el;
    return el;
}

// The title and the note above the piece and the credits below it, laid out as the map's page has them
function pageText(piece) {
    const header = document.createElement('header');
    header.className = 'post wb-text';
    const h1 = document.createElement('h1');
    h1.textContent = piece.title;
    const sub = document.createElement('p');
    sub.className = 'subtitle';
    sub.append(textBox('span', 'text.note', 'wb-edit', 'Description'));
    header.append(h1, sub);
    return { header, credits: textBox('p', 'text.credits', 'map-credits wb-edit', 'Credits') };
}

function apply(k, v) {
    if (textEls[k]) textEls[k].textContent = v;
    else adapter.set(k, v);
}

// --- The panel

function build(piece) {
    gui?.destroy();
    gui = new GUI({ container: panel, title: piece.title, width: 320 });
    const folders = {};
    const folder = name => (folders[name] ||= gui.addFolder(name));
    for (const s of piece.settings) {
        if (s.type === 'text') continue;
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
        const on = Object.fromEntries(adapter.parts.map(p => [p.id, true]));
        for (const p of adapter.parts) f.add(on, p.id).name(p.label).onChange(v => adapter.show(p.id, v));
    }
}

async function open(name) {
    const mine = ++generation;   // a later choice of piece wins over a load still under way
    say('Loading…');
    select.disabled = true;
    adapter?.cleanup?.();
    adapter = null;
    gui?.destroy();
    gui = null;
    stage.replaceChildren();
    for (const k in textEls) delete textEls[k];
    try {
        const res = await fetch('/api/piece/' + name);
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || 'could not read it');
        const piece = await res.json();
        if (mine !== generation) return;
        const page = document.createElement('div');
        page.className = 'wb-page';
        stage.append(page);
        const text = pageText(piece);
        page.append(text.header);
        host = page;
        const drawn = await ADAPTERS[piece.kind](piece);
        if (mine !== generation) return drawn.cleanup?.();
        page.append(text.credits);
        current = piece;
        adapter = drawn;
        saved = Object.fromEntries(current.settings.map(s => [s.key, s.value]));
        values = structuredClone(saved);
        for (const k in textEls) {
            textEls[k].textContent = values[k] ?? '';
            textEls[k].contentEditable = 'plaintext-only';
        }
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
}

saveBtn.addEventListener('click', async () => {
    // Only what this page changed is sent, so a save from another open workbench is not undone
    const edits = Object.fromEntries(Object.keys(values).filter(k => JSON.stringify(values[k]) !== JSON.stringify(saved[k])).map(k => [k, values[k]]));
    const controls = gui.controllersRecursive();
    const lock = on => {
        controls.forEach(c => c.enable(!on));   // nothing can be edited, or the piece switched, while the save is under way
        Object.values(textEls).forEach(el => (el.contentEditable = on ? 'false' : 'plaintext-only'));
        select.disabled = revertBtn.disabled = saveBtn.disabled = on;
    };
    lock(true);
    say('Saving…');
    let res = null, body;
    try {
        res = await fetch('/api/piece/' + current.name, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ values: edits }) });
        body = await res.json();
    } catch (e) {
        body = { error: e.message };
    }
    lock(false);
    if (!res?.ok) {
        changed();
        return say('Not saved: ' + body.error, 'error');
    }
    saved = Object.fromEntries(body.piece.settings.map(s => [s.key, s.value]));
    // Anything saved elsewhere since this page opened comes into the controls and the preview too
    for (const [k, v] of Object.entries(saved))
        if (JSON.stringify(values[k]) !== JSON.stringify(v)) {
            values[k] = structuredClone(v);
            apply(k, values[k]);
        }
    controls.forEach(c => c.updateDisplay());
    changed();
    say(current.kind === 'model' ? 'Saved. The next rebuild uses these settings.' : 'Saved. The site changes when the map is deployed.');
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
for (const p of list) select.append(new Option(p.title, p.name));
const wanted = new URLSearchParams(location.search).get('piece');
select.value = list.some(p => p.name === wanted) ? wanted : list[0].name;
open(select.value);
