// Maps on davalerio.com. One module for the three ways a map appears:
//   .scrolly    essay passages that each turn a pinned map to a named view
//   .map-embed  a map set into an essay that readers can explore
//   .map-full   a map page
// Each element names its map (data-map); the page carries that map's
// definition as JSON (script#map-def-<name>), built from _writing/maps/<name>.yaml.
// Without WebGL 2, or if the map fails, the still images already in the page stay.
// The workbench (_writing/workbench) imports makeMap, show and restyle to tune a map live.

import * as maplibregl from './lib/maplibre-gl.mjs';

const protocol = new pmtiles.Protocol();
maplibregl.addProtocol('pmtiles', protocol.tile);

const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
const params = new URLSearchParams(location.search);
const stillView = params.get('still');

function definition(name) {
    return JSON.parse(document.getElementById('map-def-' + name).textContent);
}

// Replace every "--token" string in a paint block with the page's current value.
function resolve(value, css) {
    if (typeof value === 'string' && value.startsWith('--')) return css.getPropertyValue(value).trim();
    if (Array.isArray(value)) return value.map(v => resolve(v, css));
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, resolve(v, css)]));
    return value;
}

function style(name, def, css) {
    const base = location.origin + '/maps/' + name + '/';
    const sources = {};
    for (const [id, s] of Object.entries(def.sources)) {
        sources[id] = { ...s, url: 'pmtiles://' + new URL(s.url, base).href };
    }
    const layers = [{ id: 'paper', type: 'background', paint: { 'background-color': css.getPropertyValue('--paper').trim() } }];
    for (const l of def.layers) {
        layers.push({ ...l, layout: { ...(l.layout || {}), visibility: 'none' }, paint: resolve(l.paint || {}, css) });
    }
    return { version: 8, sources, layers };
}

// Views are framed for a 1200 × 800 map; a smaller map zooms out to keep
// roughly the same area of ground in view, by at most 0.8 zoom levels.
export function camera(v, el) {
    const fit = el ? Math.log2(Math.sqrt((el.clientWidth * el.clientHeight) / (1200 * 800))) : 0;
    return { center: v.center, zoom: v.zoom + Math.max(-0.8, Math.min(0, fit)), pitch: v.pitch || 0, bearing: v.bearing || 0 };
}

export function makeMap(el, name, opts = {}) {
    const def = definition(name);
    const css = getComputedStyle(document.documentElement);
    const canvas = document.createElement('div');
    canvas.className = 'map-canvas';
    el.prepend(canvas);
    const first = def.views[opts.view || def.start || Object.keys(def.views)[0]];
    const map = new maplibregl.Map({
        container: canvas,
        style: style(name, def, css),
        ...camera(first, el),
        maxBounds: def.bounds ? [[def.bounds[0], def.bounds[1]], [def.bounds[2], def.bounds[3]]] : undefined,
        maxPitch: 75,
        attributionControl: false,
        cooperativeGestures: !!opts.cooperative,
        interactive: opts.interactive !== false,
        fadeDuration: 0,
    });
    const labels = {};
    for (const [id, l] of Object.entries(def.labels || {})) {
        const div = document.createElement('div');
        div.className = 'map-label off ' + (l.kind || '');
        div.textContent = l.text;
        labels[id] = new maplibregl.Marker({ element: div }).setLngLat(l.at).addTo(map);
    }
    const state = { map, def, labels, name, current: null };
    if (def.popup && opts.popups) popups(map, def.popup);
    return state;
}

// Clicking a feature of the named layer shows its title and lines, each a template of {field} names;
// a line that comes out empty is left out.
function popups(map, { layer, title, lines = [] }) {
    const fill = (t, p) => t.replace(/\{(\w+)\}/g, (_, k) => p[k] ?? '').replace(/^[\s,·]+|[\s,·]+$/g, '');
    map.on('click', layer, e => {
        // Several features can share a point (two parishes at one address): each gets its own block.
        const box = document.createElement('div');
        const seen = new Set();
        for (const f of e.features) {
            const key = JSON.stringify(f.properties);
            if (seen.has(key)) continue;
            seen.add(key);
            const block = document.createElement('div');
            block.className = 'map-popup-item';
            [[title, 'map-popup-title'], ...lines.map(l => [l, 'map-popup-line'])].forEach(([t, cls]) => {
                const text = fill(t, f.properties);
                if (!text) return;
                const row = document.createElement('div');
                row.className = cls;
                row.textContent = text;
                block.append(row);
            });
            box.append(block);
        }
        new maplibregl.Popup({ maxWidth: '18rem' }).setLngLat(e.lngLat).setDOMContent(box).addTo(map);
    });
    map.on('mouseenter', layer, () => (map.getCanvas().style.cursor = 'pointer'));
    map.on('mouseleave', layer, () => (map.getCanvas().style.cursor = ''));
}

// Read the palette again and repaint every layer with it.
export function restyle(state) {
    const css = getComputedStyle(document.documentElement);
    state.map.setPaintProperty('paper', 'background-color', css.getPropertyValue('--paper').trim());
    for (const l of state.def.layers) {
        for (const [k, v] of Object.entries(l.paint || {})) state.map.setPaintProperty(l.id, k, resolve(v, css));
    }
    // With terrain on, MapLibre keeps the old colours draped over it, so the style is loaded again whole,
    // with the same layers shown and the camera left where it is.
    if (state.map.getTerrain()) {
        state.map.setStyle(style(state.name, state.def, css), { diff: false });
        state.map.once('styledata', () => layers(state, state.def.views[state.current]));
    }
}

// The layers, labels and terrain a view shows
function layers(state, v) {
    const { map, def, labels } = state;
    for (const l of def.layers) map.setLayoutProperty(l.id, 'visibility', v.show.includes(l.id) ? 'visible' : 'none');
    for (const [id, m] of Object.entries(labels)) m.getElement().classList.toggle('off', !v.show.includes(id));
    map.setTerrain(v.terrain ? { source: 'terrain', exaggeration: def.exaggeration || 1 } : null);
}

export function show(state, viewName, animate) {
    const { map, def } = state;
    const v = def.views[viewName];
    if (!v) return;
    state.current = viewName;
    layers(state, v);
    const cam = camera(v, map.getContainer());
    if (animate && !reduceMotion.matches) map.flyTo({ ...cam, duration: 2200, essential: false });
    else map.jumpTo(cam);
}

// While the map loads, its still stands in the map's box at the scale and centre the map opens at, so the
// hand-over adds ground at the edges at most, never a jump in scale. The still is the view as a 1200 × 800 map
// shows it, held inside the map's bounds as MapLibre holds it (worked out here for a flat view); the live
// map's own opening camera, also held inside its bounds, places it. On screen only: print keeps the still whole.
const mx = lng => (lng + 180) / 360;
const my = lat => {
    const s = Math.sin((lat * Math.PI) / 180);
    return 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI);
};
const lat = y => (Math.atan(Math.sinh(Math.PI * (1 - 2 * y))) * 180) / Math.PI;
function stillCamera(v, b, w, h) {
    let zoom = v.zoom, x = mx(v.center[0]), y = my(v.center[1]);
    if (b) {
        const [x1, x2, y1, y2] = [mx(b[0]), mx(b[2]), my(b[3]), my(b[1])];
        zoom = Math.max(zoom, Math.log2(Math.max(w / (512 * (x2 - x1)), h / (512 * (y2 - y1)))));
        const ws = 512 * 2 ** zoom;
        x = Math.min(Math.max(x, x1 + w / 2 / ws), x2 - w / 2 / ws);
        y = Math.min(Math.max(y, y1 + h / 2 / ws), y2 - h / 2 / ws);
    }
    return { zoom, center: [x * 360 - 180, lat(y)] };
}
function placeStill(el, viewName, map) {
    const fig = el.querySelector('.still'), img = fig?.querySelector('img'), def = definition(el.dataset.map);
    const v = def.views[viewName];
    if (!img || !v) return;
    const w = +img.getAttribute('width'), h = +img.getAttribute('height');
    const cam = stillCamera(v, def.bounds, w, h);
    const s = 2 ** (map.getZoom() - cam.zoom), at = map.project(cam.center);
    fig.style.setProperty('--still-w', w * s + 'px');
    fig.style.setProperty('--still-h', h * s + 'px');
    fig.style.setProperty('--still-x', at.x + 'px');
    fig.style.setProperty('--still-y', at.y + 'px');
    fig.classList.add('placed');
}
const unplaceStill = el => el.querySelector('.still')?.classList.remove('placed');

// The stills stay until the map has drawn once; a failed tile load brings them back.
function whenDrawn(state, el) {
    state.map.once('idle', () => el.classList.add('ready'));
    state.map.on('error', e => {
        console.warn('map:', e.error?.message || e);
        if (e.sourceId || e.tile) {
            el.classList.remove('ready', 'live');
            unplaceStill(el);
            state.map.remove();
            el.querySelector('.map-canvas')?.remove();
        }
    });
}

function supported() {
    try {
        return !!document.createElement('canvas').getContext('webgl2');
    } catch {
        return false;
    }
}

function scrolly(el) {
    const name = el.dataset.map;
    const stage = el.querySelector('.map-stage');
    const steps = [...el.querySelectorAll('.map-step')];
    el.classList.add('live');
    const state = makeMap(stage, name, { view: (location.hash && steps.find(s => s.id === location.hash.slice(1)) || steps[0]).dataset.view, interactive: true });
    const { map } = state;
    const handlers = ['dragPan', 'scrollZoom', 'boxZoom', 'dragRotate', 'keyboard', 'doubleClickZoom', 'touchZoomRotate', 'touchPitch'];
    const lock = on => handlers.forEach(h => (on ? map[h].disable() : map[h].enable()));
    lock(true);
    const nav = new maplibregl.NavigationControl({ visualizePitch: true });
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'map-explore';
    button.textContent = 'Explore Map';
    stage.append(button);
    // A link to a passage (#view-…) opens the map on that passage's view.
    const linked = location.hash && steps.find(s => s.id === location.hash.slice(1));
    let active = (linked || steps[0]).dataset.view;
    let ready = false;
    button.addEventListener('click', () => {
        const exploring = el.classList.toggle('exploring');
        button.textContent = exploring ? 'Return to Story' : 'Explore Map';
        lock(!exploring);
        if (exploring) map.addControl(nav, 'top-right');
        else {
            map.removeControl(nav);
            show(state, active, false);
        }
        map.resize();
    });
    // Whatever passage is current when the map finishes loading is the one shown.
    map.once('load', () => {
        ready = true;
        show(state, active, false);
    });
    whenDrawn(state, el);
    // A passage takes over the map as it crosses the middle of the screen, or,
    // on a phone, the middle of the text below the pinned map.
    const seen = new IntersectionObserver(entries => {
        for (const e of entries) {
            if (!e.isIntersecting) continue;
            active = e.target.dataset.view;
            if (ready && !el.classList.contains('exploring') && state.current !== active) show(state, active, true);
        }
    }, { rootMargin: matchMedia('(max-width: 899px)').matches ? '-58% 0px -37% 0px' : '-45% 0px -45% 0px' });
    steps.forEach(s => seen.observe(s));
}

function embed(el) {
    el.classList.add('live');
    const state = makeMap(el, el.dataset.map, { view: el.dataset.view, cooperative: true, popups: true });
    placeStill(el, el.dataset.view, state.map);
    state.map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right');
    state.map.once('load', () => show(state, el.dataset.view, false));
    whenDrawn(state, el);
}

function full(el) {
    const view = stillView || el.dataset.view;
    if (stillView) document.documentElement.classList.add('still-mode');
    el.classList.add('live');
    // On a touch screen the page must still scroll past the map, so moving it takes two fingers.
    const state = makeMap(el, el.dataset.map, { view, interactive: !stillView, cooperative: matchMedia('(pointer: coarse)').matches, popups: !stillView });
    if (!stillView) placeStill(el, view, state.map);
    if (!stillView) state.map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right');
    state.map.once('load', () => {
        show(state, view, false);
        if (stillView) state.map.once('idle', () => (document.documentElement.dataset.still = 'ready'));
    });
    whenDrawn(state, el);
}

// A map that cannot start leaves its element as it was built: stills only.
function start(fn) {
    return el => {
        try {
            fn(el);
        } catch (e) {
            console.warn('map:', e.message);
            el.classList.remove('live', 'ready');
            unplaceStill(el);
            el.querySelector('.map-canvas')?.remove();
        }
    };
}

if (supported()) {
    document.querySelectorAll('.scrolly[data-map]').forEach(start(scrolly));
    document.querySelectorAll('.map-embed[data-map]').forEach(start(embed));
    document.querySelectorAll('.map-full[data-map]').forEach(start(full));
}
