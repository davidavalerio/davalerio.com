// The workbench: a private page for tuning the site's 3D pieces live and saving what works back
// where each piece is made. Run on the mini from the repo root:
//   node _writing/workbench/workbench.mjs
// It listens on 127.0.0.1:8795 and, for as long as it runs, Tailscale publishes it to David's own
// devices at https://<the mini's tailnet name>:8796/ (the tailnet's rules keep shared users to port 443).
//
// Every piece is a map definition in _writing/maps/ with something to tune:
//   terrain maps (a definition with views): exaggeration, the palette colours its layers use (maps/map.css,
//     light and dark), and its named views; saved into the definition and map.css, then the pages are rebuilt.
//   models (a definition with `model` and `build`): the settings their Drive build declares in
//     <build>/Scripts/settings.json, shown from the build's own web copy (<build>/Web/); saved into that file.
// A new kind of piece declares its settings the same way: a list of {key, label, group, type, value}, where
// type is number (with min, max, step), color, or view. The Drive folder holding the builds is read from
// ~/.local/share/workbench/config.json ({"gis": "<folder>"}).
import { createServer } from 'node:http';
import { readFileSync, writeFileSync, readdirSync, existsSync } from 'node:fs';
import { join, normalize } from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { homedir } from 'node:os';
import { sendFile, ROOT } from '../serve.mjs';

const PORT = 8795, PUBLIC_PORT = 8796;
const HERE = join(ROOT, '_writing', 'workbench');
const MAPS = join(ROOT, '_writing', 'maps');
const CSS = join(ROOT, 'maps', 'map.css');
const config = JSON.parse(readFileSync(join(homedir(), '.local/share/workbench/config.json'), 'utf8'));

// --- Reading the pieces

function yamlScalar(text, key) {
    const m = new RegExp('^' + key + ':\\s*(.+)$', 'm').exec(text);
    return m ? m[1].trim() : null;
}

function pieces() {
    return readdirSync(MAPS).filter(f => f.endsWith('.yaml')).map(f => {
        const text = readFileSync(join(MAPS, f), 'utf8'), name = f.slice(0, -5);
        const kind = /^views:/m.test(text) ? 'terrain' : yamlScalar(text, 'model') && yamlScalar(text, 'build') ? 'model' : null;
        return kind && { name, title: yamlScalar(text, 'title'), kind, build: yamlScalar(text, 'build') };
    }).filter(Boolean);
}

// The definition as the site built it, from the map's own page
function terrainDef(name) {
    const html = readFileSync(join(ROOT, 'maps', name, 'index.html'), 'utf8');
    const m = new RegExp('<script type="application/json" id="map-def-' + name + '">([\\s\\S]*?)</script>').exec(html);
    return JSON.parse(m[1].replace(/<\\\//g, '</'));
}

// map.css: the light palette is the first :root block, the dark one the :root inside the dark media query
function palette() {
    const css = readFileSync(CSS, 'utf8');
    const darkAt = css.indexOf('prefers-color-scheme: dark');
    const read = part => Object.fromEntries([...part.matchAll(/(--map-[\w-]+):\s*(#[0-9A-Fa-f]{6});/g)].map(m => [m[1], m[2].toUpperCase()]));
    return { css, darkAt, light: read(css.slice(0, darkAt)), dark: read(css.slice(darkAt, css.indexOf('}', css.indexOf(':root', darkAt)))) };
}

const titleCase = s => s.replace(/(^|[-\s])(\w)/g, (_, a, b) => (a ? ' ' : '') + b.toUpperCase());

function tokensUsed(value, out = new Set()) {
    if (typeof value === 'string' && value.startsWith('--map-')) out.add(value);
    else if (Array.isArray(value)) value.forEach(v => tokensUsed(v, out));
    else if (value && typeof value === 'object') Object.values(value).forEach(v => tokensUsed(v, out));
    return out;
}

function terrainSettings(name) {
    const def = terrainDef(name), pal = palette(), list = [];
    list.push({ key: 'exaggeration', label: 'Terrain Exaggeration', group: 'Shape', type: 'number', min: 1, max: 8, step: 0.1, value: def.exaggeration || 1 });
    const tokens = [...tokensUsed(def.layers.map(l => l.paint))].sort();
    for (const mode of ['light', 'dark'])
        for (const t of tokens)
            list.push({ key: `token.${t}.${mode}`, label: titleCase(t.slice(6)), group: mode === 'light' ? 'Colors, Light Mode' : 'Colors, Dark Mode', type: 'color', value: pal[mode][t] });
    for (const [v, cam] of Object.entries(def.views))
        list.push({ key: 'view.' + v, label: titleCase(v), group: 'Views', type: 'view', value: { center: cam.center, zoom: cam.zoom, pitch: cam.pitch || 0, bearing: cam.bearing || 0 } });
    return { def, settings: list };
}

const settingsFile = p => join(config.gis, p.build, 'Scripts', 'settings.json');

function piece(name) {
    const p = pieces().find(x => x.name === name);
    if (!p) return null;
    if (p.kind === 'terrain') return { ...p, ...terrainSettings(name) };
    return { ...p, settings: JSON.parse(readFileSync(settingsFile(p), 'utf8')).settings };
}

// --- Saving

const num = (v, d) => +(+v).toFixed(d);
const yamlList = a => '[' + a.join(', ') + ']';

function setViewYaml(text, view, cam) {
    const lines = text.split('\n');
    const start = lines.indexOf('views:');
    let i = lines.indexOf('  ' + view + ':', start);
    if (start < 0 || i < 0) throw new Error('no view ' + view);
    let end = i + 1;
    while (end < lines.length && (lines[end].startsWith('    ') || lines[end] === '')) end++;
    const block = lines.slice(i + 1, end);
    const want = { center: yamlList([num(cam.center[0], 4), num(cam.center[1], 4)]), zoom: num(cam.zoom, 2), pitch: num(cam.pitch, 1), bearing: num(cam.bearing, 1) };
    for (const k of ['center', 'zoom', 'pitch', 'bearing']) {
        const at = block.findIndex(l => l.startsWith('    ' + k + ':'));
        const keep = !(k === 'pitch' || k === 'bearing') || want[k] !== 0;
        if (at >= 0) keep ? (block[at] = '    ' + k + ': ' + want[k]) : block.splice(at, 1);
        else if (keep) block.splice(block.findIndex(l => l.startsWith('    zoom:')) + 1, 0, '    ' + k + ': ' + want[k]);
    }
    lines.splice(i + 1, end - i - 1, ...block);
    return lines.join('\n');
}

function saveTerrain(p, values) {
    const file = join(MAPS, p.name + '.yaml');
    let text = readFileSync(file, 'utf8');
    const before = piece(p.name).settings;
    const changed = before.filter(s => JSON.stringify(s.value) !== JSON.stringify(values[s.key]) && s.key in values);
    let pal = palette(), css = pal.css;
    for (const s of changed) {
        const v = values[s.key];
        if (s.key === 'exaggeration') text = text.replace(/^exaggeration:.*$/m, 'exaggeration: ' + num(v, 2));
        else if (s.key.startsWith('view.')) text = setViewYaml(text, s.key.slice(5), v);
        else if (s.key.startsWith('token.')) {
            const [, token, mode] = /^token\.(--map-[\w-]+)\.(light|dark)$/.exec(s.key);
            const re = new RegExp('(' + token + ':\\s*)#[0-9A-Fa-f]{6}');
            if (mode === 'light') css = css.slice(0, pal.darkAt).replace(re, '$1' + v.toUpperCase()) + css.slice(pal.darkAt);
            else css = css.slice(0, pal.darkAt) + css.slice(pal.darkAt).replace(re, '$1' + v.toUpperCase());
        }
    }
    if (!changed.length) return 0;
    writeFileSync(file, text);
    writeFileSync(CSS, css);
    // The map's page carries its definition, so rebuild the map pages for the workbench (and the site) to read it
    const r = spawnSync('uv', ['run', '-q', '--no-project', '--with', 'pyyaml', 'python3', '_writing/build.py', '--maps-only'], { cwd: ROOT, encoding: 'utf8' });
    if (r.status !== 0) throw new Error('build.py: ' + (r.stderr || r.stdout).trim().split('\n').pop());
    return changed.length;
}

function saveModel(p, values) {
    const file = settingsFile(p);
    const doc = JSON.parse(readFileSync(file, 'utf8'));
    let n = 0;
    for (const s of doc.settings)
        if (s.key in values && JSON.stringify(s.value) !== JSON.stringify(values[s.key])) {
            s.value = values[s.key];
            n++;
        }
    if (n) writeFileSync(file, JSON.stringify(doc, null, 1) + '\n');
    return n;
}

// --- The server

const json = (res, code, body) => res.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }).end(JSON.stringify(body));

const server = createServer((req, res) => {
    const url = new URL(req.url, 'http://x');
    const path = normalize(decodeURIComponent(url.pathname));
    if (path.includes('..')) return res.writeHead(400).end();
    try {
        if (path === '/api/pieces') return json(res, 200, pieces());
        let m = /^\/api\/piece\/([\w-]+)$/.exec(path);
        if (m && req.method === 'GET') {
            const p = piece(m[1]);
            return p ? json(res, 200, p) : json(res, 404, { error: 'no such piece' });
        }
        if (m && req.method === 'POST') {
            let body = '';
            req.on('data', d => (body += d));
            req.on('end', () => {
                try {
                    const p = pieces().find(x => x.name === m[1]);
                    if (!p) return json(res, 404, { error: 'no such piece' });
                    const values = JSON.parse(body).values;
                    const n = p.kind === 'terrain' ? saveTerrain(p, values) : saveModel(p, values);
                    json(res, 200, { saved: n, piece: piece(p.name) });
                } catch (e) {
                    json(res, 500, { error: e.message });
                }
            });
            return;
        }
        // A model's files, from its Drive build's web copy
        m = /^\/build\/([\w-]+)\/(.+)$/.exec(path);
        if (m) {
            const p = pieces().find(x => x.name === m[1] && x.kind === 'model');
            return p ? sendFile(req, res, join(config.gis, p.build, 'Web', m[2])) : res.writeHead(404).end();
        }
        if (path === '/' || path.startsWith('/workbench/')) return sendFile(req, res, join(HERE, path === '/' ? 'index.html' : path.slice('/workbench/'.length)));
        if (path.startsWith('/maps/') || path.startsWith('/writing/')) return sendFile(req, res, join(ROOT, path));
        res.writeHead(404).end('Not found');
    } catch (e) {
        json(res, 500, { error: e.message });
    }
});

server.listen(PORT, '127.0.0.1', () => {
    const dns = JSON.parse(spawnSync('tailscale', ['status', '--json'], { encoding: 'utf8' }).stdout).Self.DNSName.replace(/\.$/, '');
    // Foreground serve: Tailscale stops publishing the moment this process ends.
    const ts = spawn('tailscale', ['serve', '--https=' + PUBLIC_PORT, 'http://127.0.0.1:' + PORT], { stdio: 'ignore' });
    const stop = () => {
        ts.kill();
        process.exit(0);
    };
    process.on('SIGINT', stop);
    process.on('SIGTERM', stop);
    console.log(`Workbench: https://${dns}:${PUBLIC_PORT}/  (on the mini: http://127.0.0.1:${PORT}/)`);
    if (!existsSync(join(HERE, 'index.html'))) console.warn('index.html is missing');
});
