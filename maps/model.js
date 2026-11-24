// Turnable models on map pages: a stack of sheets drawn with three.js from the files a
// Drive build writes into maps/<name>/<model>/ (model.json, and per sheet a .bin and a .jpg;
// the format is described in the build's web.py). Readers turn and zoom the stack, and a
// slider moves the sheets between the print's even spacing and their true depths. Without
// WebGL 2, or if anything fails, the page keeps its image.
//
// The workbench (_writing/workbench) uses the same viewer through `turnable`, with
// `fields: true` to colour the sheets live from their -field.png files, and changes the
// build's settings through the returned `set`, `show` and `view`.
import * as THREE from './lib/three.module.min.js';
import { OrbitControls } from './lib/OrbitControls.js';

const TAN = '#B59F86';
const PINS = { 'Williston': 54, 'Watford City': 38, 'Dickinson': 22 };
const SHAPE = ['ve', 'gap', 'thick'];
const rad = d => (d * Math.PI) / 180;
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const coarse = matchMedia('(pointer: coarse)').matches;
const SVG = 'http://www.w3.org/2000/svg';

const VERT = `attribute float bottom; uniform float veScale; uniform float thick; varying vec2 vUv; varying vec3 vN;
void main() {
    vUv = uv;
    vec3 p = position;
    p.z = p.z * veScale - bottom * thick;
    vN = vec3(normal.xy, normal.z / veScale);   // a surface stretched upward tilts its normals toward the side
    gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
}`;
// The print's lighting: each sheet's colour times ambient plus a key and a fill light, matte, no shadows.
// mode 0: the sheet's coloured texture; 1: one colour; 2: a field (red rim to bowl, green contour line)
// coloured here, mixed in sRGB as the build mixes it.
const FRAG = `uniform sampler2D map; uniform int mode; uniform vec3 color; uniform vec3 rim; uniform vec3 bowl; uniform vec3 line;
uniform vec3 keyDir; uniform vec3 fillDir; uniform float ambient; uniform float keyStrength; uniform float fillStrength;
varying vec2 vUv; varying vec3 vN;
vec3 linear(vec3 c) { return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), step(0.04045, c)); }
void main() {
    vec3 n = normalize(vN);
    float l = ambient + keyStrength * max(dot(n, keyDir), 0.0) + fillStrength * max(dot(n, fillDir), 0.0);
    vec3 base;
    if (mode == 0) base = texture2D(map, vUv).rgb;
    else if (mode == 1) base = color;
    else { vec2 f = texture2D(map, vUv).rg; base = linear(mix(mix(rim, bowl, f.r), line, f.g)); }
    gl_FragColor = vec4(base * l, 1.0);
    #include <colorspace_fragment>
}`;

function supported() {
    try {
        return !!document.createElement('canvas').getContext('webgl2');
    } catch {
        return false;
    }
}

const srgb = hex => new THREE.Vector3(...[1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255));
function toward(az, el) {   // a unit vector pointing at a light, from compass azimuth and elevation
    const a = rad(az), e = rad(el);
    return new THREE.Vector3(Math.sin(a) * Math.cos(e), Math.cos(a) * Math.cos(e), Math.sin(e));
}

// One sheet: its top from the mask and vertices, and its cut edge a wall whose depth is set by `thick`.
function sheet(s, buf, model, light) {
    const N = s.n, M = N + 1, side = model.side;
    const Q = new Uint8Array(buf, 0, N * N);
    const P = new Float32Array(buf, Math.ceil((N * N) / 4) * 4, s.verts * 3);
    const used = new Uint8Array(M * M);
    for (let j = 0; j < N; j++)
        for (let i = 0; i < N; i++)
            if (Q[j * N + i]) {
                const k = j * M + i;
                used[k] = used[k + 1] = used[k + M] = used[k + M + 1] = 1;
            }
    const vid = new Int32Array(M * M).fill(-1);
    let count = 0;
    for (let k = 0; k < M * M; k++) if (used[k]) vid[k] = count++;
    if (count !== s.verts) throw new Error(s.key + ' does not match its mask');

    const q = (j, i) => j >= 0 && i >= 0 && j < N && i < N && Q[j * N + i];
    const index = [], edges = [];
    for (let j = 0; j < N; j++)
        for (let i = 0; i < N; i++) {
            if (!q(j, i)) continue;
            const a = vid[j * M + i], b = vid[j * M + i + 1], c = vid[(j + 1) * M + i + 1], d = vid[(j + 1) * M + i];
            index.push(a, b, c, a, c, d);
            if (!q(j - 1, i)) edges.push(a, b);
            if (!q(j, i + 1)) edges.push(b, c);
            if (!q(j + 1, i)) edges.push(c, d);
            if (!q(j, i - 1)) edges.push(d, a);
        }
    const uv = new Float32Array(s.verts * 2);
    let zmin = Infinity, zmax = -Infinity;
    for (let v = 0; v < s.verts; v++) {
        uv[2 * v] = (P[3 * v] + side / 2) / side;
        uv[2 * v + 1] = (P[3 * v + 1] + side / 2) / side;
        zmin = Math.min(zmin, P[3 * v + 2]);
        zmax = Math.max(zmax, P[3 * v + 2]);
    }
    const top = new THREE.BufferGeometry();
    top.setAttribute('position', new THREE.BufferAttribute(P, 3));
    top.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
    top.setAttribute('bottom', new THREE.BufferAttribute(new Float32Array(s.verts), 1));
    top.setIndex(new THREE.BufferAttribute(new Uint32Array(index), 1));
    top.computeVertexNormals();

    // Each edge runs with the sheet on its left seen from above, so its wall faces outward.
    const n = edges.length / 2, w = new Float32Array(n * 18), low = new Float32Array(n * 6), rim = [];
    for (let e = 0; e < edges.length; e += 2) {
        const p0 = edges[e] * 3, p1 = edges[e + 1] * 3, o = (e / 2) * 18;
        const x0 = P[p0], y0 = P[p0 + 1], z0 = P[p0 + 2], x1 = P[p1], y1 = P[p1 + 1], z1 = P[p1 + 2];
        w.set([x0, y0, z0, x0, y0, z0, x1, y1, z1, x0, y0, z0, x1, y1, z1, x1, y1, z1], o);
        low.set([0, 1, 1, 0, 1, 0], (e / 2) * 6);
        if (e % 8 === 0) rim.push(new THREE.Vector3(x0, y0, z0));
    }
    const wall = new THREE.BufferGeometry();
    wall.setAttribute('position', new THREE.BufferAttribute(w, 3));
    wall.setAttribute('bottom', new THREE.BufferAttribute(low, 1));
    // flat normals, facing out from each edge
    const nrm = new Float32Array(n * 18);
    for (let k = 0; k < n; k++) {
        const o = k * 18, dx = w[o + 6] - w[o], dy = w[o + 7] - w[o + 1], len = Math.hypot(dx, dy) || 1;
        for (let v = 0; v < 6; v++) nrm.set([dy / len, -dx / len, 0], o + v * 3);
    }
    wall.setAttribute('normal', new THREE.BufferAttribute(nrm, 3));

    const uniforms = (extra) => ({
        map: { value: null }, mode: { value: 1 }, color: { value: new THREE.Color() },
        rim: { value: new THREE.Vector3() }, bowl: { value: new THREE.Vector3() }, line: { value: new THREE.Vector3() },
        ...light, ...extra,
    });
    const topMat = new THREE.ShaderMaterial({ vertexShader: VERT, fragmentShader: FRAG, uniforms: uniforms({}) });
    const wallMat = new THREE.ShaderMaterial({ vertexShader: VERT, fragmentShader: FRAG, uniforms: uniforms({}) });
    const group = new THREE.Group();
    group.add(new THREE.Mesh(top, topMat), new THREE.Mesh(wall, wallMat));
    return { s, group, rim, zmin, zmax, Q, N, topMat, wallMat };
}

function el(tag, cls, parent, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
}

function svg(tag, parent, attrs) {
    const e = document.createElementNS(SVG, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    parent.appendChild(e);
    return e;
}

export async function turnable(view, opts = {}) {
    const base = opts.base || view.dataset.model;
    const model = await (await fetch(base + 'model.json', { cache: opts.fields ? 'no-store' : 'default' })).json();
    const S = { ...model.settings, ...(opts.settings || {}) };   // the workbench passes the settings as saved
    const loader = new THREE.TextureLoader();
    const fielded = s => opts.fields && s.key !== 'ground';
    const [bufs, texs] = await Promise.all([
        Promise.all(model.sheets.map(s => fetch(base + s.key + '.bin', { cache: opts.fields ? 'no-store' : 'default' }).then(r => {
            if (!r.ok) throw new Error(s.key + '.bin ' + r.status);
            return r.arrayBuffer();
        }))),
        Promise.all(model.sheets.map(s => loader.loadAsync(base + s.key + (fielded(s) ? '-field.png' : '.jpg')))),
    ]);

    // The page around the canvas
    view.classList.add('live');
    const stage = el('div', 'model-stage', null);
    view.insertBefore(stage, view.firstChild);
    const canvas = el('canvas', 'model-canvas', stage);
    canvas.setAttribute('role', 'img');   // the image it replaces lends it its description
    canvas.setAttribute('aria-label', view.querySelector('.showpiece img')?.alt || '');
    const marks = svg('svg', stage, { class: 'model-marks', 'aria-hidden': 'true' });
    const labels = el('div', 'model-labels', stage);
    labels.setAttribute('aria-hidden', 'true');
    const bar = el('div', 'model-bar', null);
    stage.after(bar);
    const spacing = el('label', 'model-spacing', bar);
    el('span', null, spacing, 'True Depth');
    const slider = el('input', null, spacing);
    Object.assign(slider, { type: 'range', min: 0, max: 1, step: 0.001, value: 1 });
    slider.setAttribute('aria-label', 'Spacing between surfaces, from true depth to exploded');
    el('span', null, spacing, 'Exploded');
    const note = el('p', 'model-note', bar);
    const key = el('ul', 'model-key', null);
    bar.after(key);
    const swatches = {};
    for (const s of model.sheets) {
        const li = el('li', null, key);
        swatches[s.key] = el('span', 'swatch', li);
        swatches[s.key].style.background = s.wall;
        const t = el('div', null, li);
        el('strong', null, t, s.name);
        for (const line of s.lines) el('span', null, t, line);
    }

    // The scene
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    const scene = new THREE.Scene();
    const aniso = renderer.capabilities.getMaxAnisotropy();
    // Shared by every sheet's materials, so a change to the shape or the light reaches all of them.
    const shared = {
        veScale: { value: 1 }, thick: { value: model.thick },
        keyDir: { value: new THREE.Vector3() }, fillDir: { value: new THREE.Vector3() },
        ambient: { value: 0 }, keyStrength: { value: 0 }, fillStrength: { value: 0 },
    };
    const sheets = model.sheets.map((s, i) => sheet(s, bufs[i], model, shared));
    sheets.forEach((x, i) => {
        const t = texs[i];
        t.colorSpace = fielded(x.s) ? THREE.NoColorSpace : THREE.SRGBColorSpace;
        t.anisotropy = aniso;
        x.topMat.uniforms.map.value = t;
        x.topMat.uniforms.mode.value = fielded(x.s) ? 2 : 0;
        x.wallMat.uniforms.color.value.setStyle(x.s.wall);
        scene.add(x.group);
    });

    // The plumb line under Watford City: a thin rod, ink, light tan where the dark Bakken lies behind it
    const K = 80;
    const rodGeom = new THREE.CylinderGeometry(1, 1, 1, 8, K, true);
    rodGeom.rotateX(Math.PI / 2);
    rodGeom.translate(0, 0, 0.5);
    const rodColors = new Float32Array(rodGeom.attributes.position.count * 3);
    rodGeom.setAttribute('color', new THREE.BufferAttribute(rodColors, 3));
    const rod = new THREE.Mesh(rodGeom, new THREE.MeshBasicMaterial({ vertexColors: true }));
    rod.position.set(model.watford[0], model.watford[1], 0);
    scene.add(rod);
    const bakken = sheets.find(x => x.s.key === 'bakken');

    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 1, 6000);
    camera.up.set(0, 0, 1);
    const controls = new OrbitControls(camera, canvas);
    controls.enablePan = false;
    controls.enableDamping = true;
    controls.minPolarAngle = 0;
    controls.maxPolarAngle = rad(85);
    controls.minZoom = 0.5;
    controls.maxZoom = 10;
    if (coarse) {
        // The page must still scroll past the model, so turning it takes two fingers.
        controls.touches = { ONE: null, TWO: THREE.TOUCH.DOLLY_ROTATE };
        canvas.style.touchAction = 'pan-y';
    }

    let t = 1, ink = new THREE.Color(), dirty = true;
    const shown = { towns: true, plumb: true, labels: true };
    const ve = () => S.ve / model.ve;   // the geometry is built at model.ve
    const exploded = x => -model.sheets.indexOf(x.s) * S.gap;
    const level = x => x.s.true * ve() + (exploded(x) - x.s.true * ve()) * t;
    const tan = new THREE.Color(TAN);
    function colours() {
        ink.setStyle(css('--ink') || '#22282B');
        dirty = true;
    }
    colours();
    const scheme = matchMedia('(prefers-color-scheme: dark)');
    scheme.addEventListener('change', colours);

    function light() {
        shared.keyDir.value.copy(toward(S['light.key.azimuth'], S['light.key.elevation']));
        shared.fillDir.value.copy(toward(S['light.fill.azimuth'], S['light.fill.elevation']));
        shared.ambient.value = S['light.ambient'];
        shared.keyStrength.value = S['light.key.strength'];
        shared.fillStrength.value = S['light.fill.strength'];
        dirty = true;
    }
    // In the workbench, sheets below the ground take their colours from the settings; their walls follow,
    // a darker mix of rim and bowl, as the build makes them.
    function paint() {
        for (const x of sheets) {
            if (!fielded(x.s)) continue;
            const c = part => S['color.' + x.s.key + '.' + part];
            if (!c('rim')) continue;
            const u = x.topMat.uniforms;
            u.rim.value.copy(srgb(c('rim')));
            u.bowl.value.copy(srgb(c('bowl')));
            u.line.value.copy(srgb(c('contour')));
            const wall = u.rim.value.clone().add(u.bowl.value).multiplyScalar(0.5 * 0.72);
            const hex = '#' + wall.toArray().map(v => Math.round(v * 255).toString(16).padStart(2, '0')).join('');
            x.wallMat.uniforms.color.value.setStyle(hex);
            swatches[x.s.key].style.background = hex;
        }
        dirty = true;
    }

    const visible = () => sheets.filter(x => x.group.visible);
    function stack() {
        const v = visible().length ? visible() : sheets;
        const top = level(v[0]) + v[0].zmax * ve(), bottom = level(v.at(-1)) + v.at(-1).zmin * ve() - S.thick;
        return { top, bottom, mid: (top + bottom) / 2 };
    }
    function aim(azimuth, elevation) {   // point the camera from a compass azimuth and an angle above the horizon
        const a = rad(azimuth), e = rad(elevation);
        const dir = new THREE.Vector3(Math.sin(a) * Math.cos(e), Math.cos(a) * Math.cos(e), -Math.sin(e));
        controls.target.set(0, 0, stack().mid);
        camera.position.copy(controls.target).addScaledVector(dir, -3000);
        camera.lookAt(controls.target);
        controls.update();
    }
    aim(S.camera.azimuth, S.camera.elevation);

    // Frame the stack in the canvas, leaving a column on the right for the layer names
    let W = 1, H = 1, column = 0;
    const right = new THREE.Vector3(), up = new THREE.Vector3(), p = new THREE.Vector3();
    function fit() {
        W = stage.clientWidth;
        H = stage.clientHeight;
        renderer.setSize(W, H, false);
        column = W < 560 ? 92 : 170;
        stage.classList.toggle('narrow', W < 560);
        camera.updateMatrixWorld();
        right.setFromMatrixColumn(camera.matrixWorld, 0);
        up.setFromMatrixColumn(camera.matrixWorld, 1);
        let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
        const r = model.side / 2;
        for (const x of visible().length ? visible() : sheets)
            for (const z of [level(x) + x.zmax * ve(), level(x) + x.zmin * ve() - S.thick])
                for (let k = 0; k < 64; k++) {
                    const a = (k / 64) * 2 * Math.PI;
                    p.set(r * Math.cos(a), r * Math.sin(a), z).sub(controls.target);
                    const px = p.dot(right), py = p.dot(up);
                    x0 = Math.min(x0, px); x1 = Math.max(x1, px); y0 = Math.min(y0, py); y1 = Math.max(y1, py);
                }
        const pad = 14, top = 64;   // room above for the town names
        const w = W - column - 2 * pad, h = H - pad - top;
        const scale = Math.max((x1 - x0) / w, (y1 - y0) / h);
        camera.left = x0 - pad * scale - (w * scale - (x1 - x0)) / 2;
        camera.right = camera.left + W * scale;
        camera.bottom = y0 - pad * scale - (h * scale - (y1 - y0)) / 2;
        camera.top = camera.bottom + H * scale;
        camera.updateProjectionMatrix();
        dirty = true;
    }

    // Lay the sheets out for the slider's position and the shape settings, keeping the stack in frame
    function layout() {
        const before = controls.target.z;
        shared.veScale.value = ve();
        shared.thick.value = S.thick;
        sheets.forEach(x => (x.group.position.z = level(x)));
        const dz = stack().mid - before;
        controls.target.z += dz;
        camera.position.z += dz;
        const on = visible().length ? visible() : sheets;   // the rod runs from the top shown sheet to the bottom one
        const top = level(on[0]) + on[0].s.watford_z * ve(), bottom = level(on.at(-1)) + on.at(-1).s.watford_z * ve();
        rod.position.z = bottom;
        rod.scale.z = top - bottom;
        note.textContent = 'Relief exaggerated ' + S.ve + ' times. ' + (t < 0.002
            ? 'Layers at their true depths, exaggerated the same.'
            : 'Layer spacing not to scale.') + ' Depths at Watford City.';
        fit();
    }
    slider.addEventListener('input', () => {
        t = +slider.value;
        layout();
    });

    // Marks drawn over the canvas each frame: town pins, layer names with leaders, the plumb line's rings,
    // a north arrow and a scale bar
    const v = new THREE.Vector3();
    const screen = (x, y, z) => {
        v.set(x, y, z).project(camera);
        return [((v.x + 1) / 2) * W, ((1 - v.y) / 2) * H];
    };
    const towns = Object.entries(model.towns).filter(([n]) => n in PINS)
        .map(([n, xyz]) => ({ n, xyz, halo: svg('line', marks, { class: 'halo' }), line: svg('line', marks, { class: 'pin' }),
            dot: svg('circle', marks, { class: 'dot', r: 2.5 }), label: el('div', 'model-label town', labels, n) }));
    const names = sheets.map(x => ({ x, leader: svg('line', marks, { class: 'leader' }), label: el('div', 'model-label layer', labels, x.s.name) }));
    const rings = sheets.slice(1).map(x => ({ x, ring: svg('circle', marks, { class: x.s.key === 'bakken' ? 'ring light' : 'ring', r: 4 }) }));
    const north = svg('g', marks, { class: 'north' });
    const arrow = svg('line', north, {}), head = svg('path', north, {}), nlabel = svg('text', north, {});
    nlabel.textContent = 'N';
    const scaleG = svg('g', marks, { class: 'scale' });
    const sbar = svg('path', scaleG, {}), slabel = svg('text', scaleG, {});
    const hide = (e, off) => (e.style.display = off ? 'none' : '');

    function drawMarks() {
        marks.setAttribute('viewBox', `0 0 ${W} ${H}`);
        const groundOn = sheets[0].group.visible;
        for (const tw of towns) {
            [tw.halo, tw.line, tw.dot, tw.label].forEach(e => hide(e, !shown.towns || !groundOn));
            const [x, y] = screen(tw.xyz[0], tw.xyz[1], tw.xyz[2] * ve() + level(sheets[0]));
            const hgt = PINS[tw.n];
            for (const line of [tw.halo, tw.line])
                Object.entries({ x1: x, y1: y, x2: x, y2: y - hgt }).forEach(([k, val]) => line.setAttribute(k, val));
            tw.dot.setAttribute('cx', x);
            tw.dot.setAttribute('cy', y);
            tw.label.style.transform = `translate(${x}px, ${y - hgt - 2}px) translate(-50%, -100%)`;
        }
        // Each name sits in a column right of the stack, level with its sheet's rightmost visible edge
        const on = names.filter(nm => nm.x.group.visible && shown.labels);
        names.forEach(nm => [nm.leader, nm.label].forEach(e => hide(e, !on.includes(nm))));
        const anchors = on.map(nm => {
            let best = null;
            for (const r of nm.x.rim) {
                const s = screen(r.x, r.y, r.z * ve() + level(nm.x));
                if (!best || s[0] > best[0]) best = s;
            }
            return best;
        });
        const col = Math.min(Math.max(...anchors.map(a => a[0])) + 18, W - column + 8);
        let last = -Infinity;
        on.forEach((nm, i) => {
            const [ax, ay] = anchors[i], hgt = nm.label.offsetHeight;
            const ly = Math.max(ay, last + hgt / 2 + 6);
            last = ly + hgt / 2;
            Object.entries({ x1: ax + 4, y1: ay, x2: col - 5, y2: ly }).forEach(([k, val]) => nm.leader.setAttribute(k, val));
            nm.label.style.transform = `translate(${col}px, ${ly}px) translate(0, -50%)`;
        });
        for (const rg of rings) {
            hide(rg.ring, !shown.plumb || !rg.x.group.visible);
            const [x, y] = screen(model.watford[0], model.watford[1], level(rg.x) + rg.x.s.watford_z * ve());
            rg.ring.setAttribute('cx', x);
            rg.ring.setAttribute('cy', y);
        }
        // North: true north lying on the ground as the camera sees it
        const o = screen(0, 0, 0), n = screen(0, 1, 0);
        let dx = n[0] - o[0], dy = n[1] - o[1];
        const len = Math.hypot(dx, dy) || 1;
        dx /= len; dy /= len;
        const e = screen(1, 0, 0), L = 26 * Math.min(1, len / (Math.hypot(e[0] - o[0], e[1] - o[1]) || 1));   // shortened as the ground tilts away
        const cx = W - 28, cy = H - 52;
        arrow.setAttribute('x1', cx - dx * L / 2); arrow.setAttribute('y1', cy - dy * L / 2);
        arrow.setAttribute('x2', cx + dx * L / 2); arrow.setAttribute('y2', cy + dy * L / 2);
        const tx = cx + dx * L / 2, ty = cy + dy * L / 2;
        head.setAttribute('d', `M${tx + dx * 6},${ty + dy * 6} L${tx - dy * 4},${ty + dx * 4} L${tx + dy * 4},${ty - dx * 4} Z`);
        nlabel.setAttribute('x', tx + dx * 16); nlabel.setAttribute('y', ty + dy * 16 + 4);
        // Scale: miles across the screen
        const mpp = (camera.right - camera.left) / camera.zoom / W;
        const miles = [10, 25, 50, 100, 200].filter(m => m / mpp <= 130).pop() || 10;
        const bw = miles / mpp, bx = W - 16 - bw, by = H - 14;
        sbar.setAttribute('d', `M${bx},${by - 4} V${by} H${bx + bw} V${by - 4}`);
        slabel.setAttribute('x', bx + bw / 2); slabel.setAttribute('y', by - 7);
        slabel.textContent = miles + ' Miles';
    }

    // The rod's colour: tan where the Bakken sheet is what lies behind it on screen
    const look = new THREE.Vector3();
    function drawRod() {
        rod.visible = shown.plumb;
        const mpp = (camera.right - camera.left) / camera.zoom / W;
        rod.scale.x = rod.scale.y = 0.65 * mpp;
        camera.getWorldDirection(look);
        const bz = level(bakken) + bakken.s.watford_z * ve(), z0 = rod.position.z, h = rod.scale.z;
        const pos = rodGeom.attributes.position, col = rodGeom.attributes.color;
        for (let k = 0; k < pos.count; k++) {
            const z = z0 + pos.getZ(k) * h;
            let behind = false;
            if (bakken.group.visible && z > bz && look.z < 0) {
                const s = (bz - z) / look.z;
                const hx = model.watford[0] + look.x * s, hy = model.watford[1] + look.y * s;
                const i = Math.floor(((hx + model.side / 2) / model.side) * bakken.N), j = Math.floor(((hy + model.side / 2) / model.side) * bakken.N);
                behind = i >= 0 && j >= 0 && i < bakken.N && j < bakken.N && bakken.Q[j * bakken.N + i] === 1;
            }
            const c = behind ? tan : ink;
            col.setXYZ(k, c.r, c.g, c.b);
        }
        col.needsUpdate = true;
    }

    controls.addEventListener('change', () => (dirty = true));
    // If the browser takes the drawing context away, the image returns until it is given back.
    canvas.addEventListener('webglcontextlost', () => view.classList.remove('ready'));
    canvas.addEventListener('webglcontextrestored', () => {
        view.classList.add('ready');
        fit();
    });
    view.classList.add('ready');   // the stage takes the image's place, so it has a size to fit to
    const resize = new ResizeObserver(fit);
    resize.observe(stage);
    light();
    paint();
    layout();
    let running = true;
    (function frame() {
        if (!running) return;
        requestAnimationFrame(frame);
        controls.update();
        if (!dirty) return;
        dirty = false;
        drawRod();
        renderer.render(scene, camera);
        drawMarks();
    })();

    // For the workbench: change a setting, show or hide a part, read the camera angle, stop drawing
    return {
        model,
        set(k, value) {
            S[k] = value;
            if (SHAPE.includes(k)) layout();
            else if (k.startsWith('light.')) light();
            else if (k.startsWith('color.')) paint();
            else if (k === 'camera') {
                aim(value.azimuth, value.elevation);
                fit();
            }
        },
        show(part, on) {
            const x = sheets.find(x => x.s.key === part);
            if (x) {
                x.group.visible = on;
                layout();
            } else shown[part] = on;
            dirty = true;
        },
        view() {
            const d = new THREE.Vector3().subVectors(controls.target, camera.position).normalize();
            return { azimuth: +((Math.atan2(d.x, d.y) * 180) / Math.PI + 360).toFixed(1) % 360, elevation: +((Math.asin(-d.z) * 180) / Math.PI).toFixed(1) };
        },
        stop() {
            running = false;
            resize.disconnect();
            scheme.removeEventListener('change', colours);
            controls.dispose();
            scene.traverse(o => {
                o.geometry?.dispose();
                o.material?.uniforms?.map?.value?.dispose();
                o.material?.dispose();
            });
            renderer.dispose();
        },
        parts: () => [...sheets.map(x => ({ id: x.s.key, label: x.s.name })), { id: 'towns', label: 'Towns' }, { id: 'plumb', label: 'Plumb Line' }, { id: 'labels', label: 'Layer Names' }],
    };
}

if (supported()) {
    document.querySelectorAll('.model-view[data-model]').forEach(view =>
        turnable(view).catch(e => {
            console.warn('model:', e.message);
            view.classList.remove('live', 'ready');
            view.querySelectorAll('.model-stage, .model-bar, .model-key').forEach(x => x.remove());
        }));
}
