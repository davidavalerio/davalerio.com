// The workbench page: pick a map, tune it live from the settings it declares, save or revert.
// Each kind of piece has an adapter that draws it and applies a setting to it; the panel is built
// from the declarations alone (number, color, view), so a new kind needs only its adapter. The page's
// text (type text) and its legend are not in the panel: they are shown around the piece as on the site and
// edited in place.
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
// The colour tokens the map's layers use, from the site's map stylesheet
function mapTokens() {
    const names = new Set();
    for (const sheet of document.styleSheets) {
        try {
            for (const rule of sheet.cssRules)
                for (const prop of rule.style || []) if (prop.startsWith('--map-')) names.add(prop);
        } catch { /* a sheet from another origin */ }
    }
    return [...names].sort();
}
const swatchColor = c => (c.startsWith('--') ? 'var(' + c + ')' : c);
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
    const custom = !it.color.startsWith('--');
    row.append(pick([...tokens.map(t => [t, t.slice(6).replace(/-/g, ' ')]), ['custom', 'Other Colour']], custom ? 'custom' : it.color, v => {
        it.color = v === 'custom' ? (custom ? it.color : '#888888') : v;
    }));
    if (custom) {
        const input = document.createElement('input');
        input.type = 'color';
        input.value = it.color.toLowerCase();
        input.addEventListener('input', () => (it.color = input.value.toUpperCase(), changed(), row.previousElementSibling?.querySelector('.swatch')?.replaceWith(swatch(it))));
        input.addEventListener('change', legendChanged);
        row.append(input);
    }
    return row;
}
let openSwatch = null;   // the item whose swatch settings are showing
function drawLegend() {
    if (!legendEl) return;
    const legend = values.legend;
    const groups = document.createElement('div');
    groups.className = 'map-legend';
    const move = (list, i, by) => list.splice(i + by, 0, list.splice(i, 1)[0]);
    legend.forEach((g, gi) => {
        const box = document.createElement('div');
        box.className = 'wb-legend-group';
        const head = document.createElement('div');
        head.className = 'wb-legend-row';
        head.append(legendText('h3', '', g.heading, 'Heading', v => (v ? (g.heading = v) : delete g.heading)),
            button('↑', 'Move this group up', () => gi > 0 && (move(legend, gi, -1), legendChanged())),
            button('↓', 'Move this group down', () => gi < legend.length - 1 && (move(legend, gi, 1), legendChanged())),
            button('×', 'Remove this group', () => (legend.splice(gi, 1), legendChanged())));
        const ul = document.createElement('ul');
        g.items.forEach((it, ii) => {
            const li = document.createElement('li');
            const sw = swatch(it);
            sw.classList.add('wb-swatch');
            sw.title = 'Shape and colour';
            sw.addEventListener('click', () => ((openSwatch = openSwatch === it ? null : it), drawLegend()));
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
        if (s.type === 'text' || s.type === 'legend') continue;
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
        page.append(text.legend, text.credits);
        current = piece;
        adapter = drawn;
        saved = Object.fromEntries(current.settings.map(s => [s.key, s.value]));
        values = structuredClone(saved);
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
}

saveBtn.addEventListener('click', async () => {
    // Only what this page changed is sent, so a save from another open workbench is not undone
    const edits = Object.fromEntries(Object.keys(values).filter(k => JSON.stringify(values[k]) !== JSON.stringify(saved[k])).map(k => [k, values[k]]));
    const controls = gui.controllersRecursive();
    const lock = on => {
        if (legendEl) legendEl.inert = on;
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
    // A new title shows in the list of maps and at the head of the panel
    select.selectedOptions[0].text = saved['text.title'];
    gui.title(saved['text.title']);
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
