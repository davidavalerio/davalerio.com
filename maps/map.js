// Maps on davalerio.com. One module for the three ways a map appears:
//   .scrolly    essay passages that each turn a pinned map to a named view
//   .map-embed  a map set into an essay that readers can explore
//   .map-full   a map page
// Each element names its map (data-map); the page carries that map's
// definition as JSON (script#map-def-<name>), built from _writing/maps/<name>.yaml.
// Without WebGL 2, or if the map fails, the still images already in the page stay.

import * as maplibregl from './lib/maplibre-gl.mjs';

const protocol = new pmtiles.Protocol();
maplibregl.addProtocol('pmtiles', protocol.tile);

const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
const dark = matchMedia('(prefers-color-scheme: dark)');
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
function camera(v, el) {
    const fit = el ? Math.log2(Math.sqrt((el.clientWidth * el.clientHeight) / (1200 * 800))) : 0;
    return { center: v.center, zoom: v.zoom + Math.max(-0.8, Math.min(0, fit)), pitch: v.pitch || 0, bearing: v.bearing || 0 };
}

function makeMap(el, name, opts = {}) {
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
    const state = { map, def, labels, current: null };
    // Restyle when the device switches between light and dark.
    dark.addEventListener('change', () => {
        const css = getComputedStyle(document.documentElement);
        map.setPaintProperty('paper', 'background-color', css.getPropertyValue('--paper').trim());
        for (const l of def.layers) {
            for (const [k, v] of Object.entries(l.paint || {})) map.setPaintProperty(l.id, k, resolve(v, css));
        }
    });
    return state;
}

function show(state, viewName, animate) {
    const { map, def, labels } = state;
    const v = def.views[viewName];
    if (!v) return;
    state.current = viewName;
    for (const l of def.layers) map.setLayoutProperty(l.id, 'visibility', v.show.includes(l.id) ? 'visible' : 'none');
    for (const [id, m] of Object.entries(labels)) m.getElement().classList.toggle('off', !v.show.includes(id));
    map.setTerrain(v.terrain ? { source: 'terrain', exaggeration: def.exaggeration || 1 } : null);
    const cam = camera(v, map.getContainer());
    if (animate && !reduceMotion.matches) map.flyTo({ ...cam, duration: 2200, essential: false });
    else map.jumpTo(cam);
}

// The stills stay until the map has drawn once; a failed tile load brings them back.
function whenDrawn(state, el) {
    state.map.once('idle', () => el.classList.add('ready'));
    state.map.on('error', e => {
        console.warn('map:', e.error?.message || e);
        if (e.sourceId || e.tile) {
            el.classList.remove('ready', 'live');
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
    const state = makeMap(el, el.dataset.map, { view: el.dataset.view, cooperative: true });
    state.map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right');
    state.map.once('load', () => show(state, el.dataset.view, false));
    whenDrawn(state, el);
}

function full(el) {
    const view = stillView || el.dataset.view;
    if (stillView) document.documentElement.classList.add('still-mode');
    el.classList.add('live');
    // On a touch screen the page must still scroll past the map, so moving it takes two fingers.
    const state = makeMap(el, el.dataset.map, { view, interactive: !stillView, cooperative: matchMedia('(pointer: coarse)').matches });
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
            el.querySelector('.map-canvas')?.remove();
        }
    };
}

if (supported()) {
    document.querySelectorAll('.scrolly[data-map]').forEach(start(scrolly));
    document.querySelectorAll('.map-embed[data-map]').forEach(start(embed));
    document.querySelectorAll('.map-full[data-map]').forEach(start(full));
}
