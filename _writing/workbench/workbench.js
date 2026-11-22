// The workbench page: pick a piece, tune it live from the settings it declares, save or revert.
// Each kind of piece has an adapter that draws it and applies a setting to it; the panel is built
// from the declarations alone (number, color, view), so a new kind needs only its adapter.
import GUI from './lib/lil-gui.esm.min.js';

const $ = id => document.getElementById(id);
const stage = $('stage'), panel = $('panel'), status = $('status'), saveBtn = $('save'), revertBtn = $('revert'), select = $('piece');
let gui = null, current = null, saved = null, values = null, adapter = null;

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
    stage.append(view);
    const api = await turnable(view, { base: '/build/' + piece.name + '/', fields: true });
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
    stage.append(el);
    const def = piece.def;
    const state = makeMap(el, piece.name, { view: def.start });
    state.map.addControl(new maplibre.NavigationControl({ visualizePitch: true }), 'top-right');
    await new Promise(ok => state.map.once('load', ok));
    show(state, def.start, false);
    // Palette edits go onto the page as overrides of the tokens, for whichever mode the screen is in
    const tokens = {};
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

const ADAPTERS = { model: modelAdapter, terrain: terrainAdapter };

// --- The panel

function build(piece) {
    gui?.destroy();
    gui = new GUI({ container: panel, title: piece.title, width: 320 });
    const folders = {};
    const folder = name => (folders[name] ||= gui.addFolder(name));
    for (const s of piece.settings) {
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
    say('Loading…');
    adapter?.cleanup?.();
    stage.replaceChildren();
    const res = await fetch('/api/piece/' + name);
    if (!res.ok) return say('Could not read ' + name, 'error');
    current = await res.json();
    saved = Object.fromEntries(current.settings.map(s => [s.key, s.value]));
    values = structuredClone(saved);
    try {
        adapter = await ADAPTERS[current.kind](current);
    } catch (e) {
        console.error(e);
        return say('Could not draw ' + current.title + ': ' + e.message, 'error');
    }
    build(current);
    history.replaceState(null, '', '?piece=' + name);
    changed();
}

saveBtn.addEventListener('click', async () => {
    saveBtn.disabled = true;
    say('Saving…');
    const res = await fetch('/api/piece/' + current.name, { method: 'POST', body: JSON.stringify({ values }) });
    const body = await res.json();
    if (!res.ok) return say('Not saved: ' + body.error, 'error') || (saveBtn.disabled = false);
    saved = Object.fromEntries(body.piece.settings.map(s => [s.key, s.value]));
    values = structuredClone(saved);
    gui.controllersRecursive().forEach(c => c.updateDisplay());
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
