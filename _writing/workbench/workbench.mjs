// The workbench: a private page for tuning the site's maps live and saving what works back
// where each piece is made. Run on the mini from the repo root:
//   node _writing/workbench/workbench.mjs
// It listens on 127.0.0.1:8795 and, for as long as it runs, Tailscale publishes it to David's own
// devices at https://<the mini's tailnet name>:8796/ (the tailnet's rules keep shared users to port 443).
//
// Every map definition in _writing/maps/ is a piece. Each has its page's text (the title, the note and the
// credits), edited in place on the page as the workbench shows it and saved into the definition, after which
// the map pages are rebuilt; a new title also goes into the map's entry in the homepage's Maps list. Beyond the text:
//   interactive maps (a definition with views): exaggeration when it has terrain, the palette colours its
//     layers use (maps/map.css, light and dark), and its named views; saved into the definition and map.css.
//   models (a definition with `model` and `build`): the settings their Drive build declares in
//     <build>/Scripts/settings.json, shown from the build's own web copy (<build>/Web/); saved into that file.
//   print pages (any other definition): their image, with nothing more to tune.
// A new kind of piece declares its settings the same way: a list of {key, label, group, type, value}, where
// type is number (with min, max, step), color, view, or text. The Drive folder holding the builds is read from
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
const HOME = join(ROOT, 'index.html');
const config = JSON.parse(readFileSync(join(homedir(), '.local/share/workbench/config.json'), 'utf8'));

// --- Reading the pieces

function yamlScalar(text, key) {
    const m = new RegExp('^' + key + ':\\s*(.+)$', 'm').exec(text);
    return m ? m[1].trim().replace(/^(['"])(.*)\1$/, '$2') : null;
}

function pieces() {
    return readdirSync(MAPS).filter(f => f.endsWith('.yaml')).map(f => {
        const text = readFileSync(join(MAPS, f), 'utf8'), name = f.slice(0, -5);
        const kind = /^views:/m.test(text) ? 'terrain' : yamlScalar(text, 'model') && yamlScalar(text, 'build') ? 'model' : 'page';
        return { name, title: yamlScalar(text, 'title'), kind, build: yamlScalar(text, 'build'), image: yamlScalar(text, 'image') };
    }).sort((a, b) => a.title.localeCompare(b.title));
}

// The page's text, read with PyYAML as build.py reads it
const TEXT = [['title', 'Title'], ['note', 'Description'], ['credits', 'Credits']];
function textSettings(name) {
    const r = spawnSync('uv', ['run', '-q', '--no-project', '--with', 'pyyaml', 'python3', '-c',
        'import sys, json, yaml; d = yaml.safe_load(open(sys.argv[1])); print(json.dumps({k: d.get(k) or "" for k in ("title", "note", "credits")}))',
        join(MAPS, name + '.yaml')], { encoding: 'utf8' });
    if (r.status !== 0) throw new Error('could not read the definition: ' + (r.stderr || '').trim().split('\n').pop());
    const d = JSON.parse(r.stdout);
    return TEXT.map(([k, label]) => ({ key: 'text.' + k, label, group: 'Text', type: 'text', value: String(d[k]) }));
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
    if (def.sources.terrain) list.push({ key: 'exaggeration', label: 'Terrain Exaggeration', group: 'Shape', type: 'number', min: 1, max: 8, step: 0.1, value: def.exaggeration || 1 });
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
    const text = textSettings(name);
    if (p.kind === 'terrain') {
        const t = terrainSettings(name);
        return { ...p, def: t.def, settings: [...text, ...t.settings] };
    }
    if (p.kind === 'model') return { ...p, settings: [...text, ...JSON.parse(readFileSync(settingsFile(p), 'utf8')).settings] };
    return { ...p, settings: text };
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

// A text setting (markdown) replaces its key's whole value (continuation lines included): one line as a
// single-quoted line, several as a literal block, so its line breaks, lists and links keep. It goes in after
// the keys that come before it when the definition lacks it, and is dropped when emptied.
const ORDER = ['title', 'date', 'page', 'note', 'start', 'credits'];
function valueEnd(lines, i) {
    let end = i + 1;
    for (let j = i + 1; j < lines.length && (/^\s+\S/.test(lines[j]) || !lines[j].trim()); j++)
        if (lines[j].trim()) end = j + 1;
    return end;
}
function setTextYaml(text, key, value) {
    const lines = text.split('\n');
    value = value.replace(/\r/g, '').split('\n').map(l => l.replace(/\s+$/, '')).join('\n').replace(/\n{3,}/g, '\n\n').trim();
    const line = !value ? []
        : value.includes('\n') ? [key + ': |-', ...value.split('\n').map(l => (l ? '  ' + l : ''))]
        : [key + ": '" + value.replace(/'/g, "''") + "'"];
    let i = lines.findIndex(l => l.startsWith(key + ':'));
    if (i >= 0) {
        lines.splice(i, valueEnd(lines, i) - i, ...line);
    } else if (value) {
        let at = 0;
        for (const k of ORDER.slice(0, ORDER.indexOf(key))) {
            const j = lines.findIndex(l => l.startsWith(k + ':'));
            if (j >= 0) at = Math.max(at, valueEnd(lines, j));
        }
        lines.splice(at, 0, ...line);
    }
    return lines.join('\n');
}

// Writes the definition (and the palette), then rebuilds the map pages, which carry both, for the workbench
// and the site to read; if the build fails, both go back as they were
function writeAndBuild(file, text, css) {
    const original = readFileSync(file, 'utf8'), originalCss = readFileSync(CSS, 'utf8');
    writeFileSync(file, text);
    if (css !== undefined) writeFileSync(CSS, css);
    const r = spawnSync('uv', ['run', '-q', '--no-project', '--with', 'pyyaml', 'python3', '_writing/build.py', '--maps-only'], { cwd: ROOT, encoding: 'utf8' });
    if (r.status !== 0) {
        writeFileSync(file, original);
        writeFileSync(CSS, originalCss);
        throw new Error('build.py: ' + (r.stderr || r.stdout || 'failed').trim().split('\n').pop());
    }
}

function saveDefinition(p, values) {
    const file = join(MAPS, p.name + '.yaml');
    let text = readFileSync(file, 'utf8');
    const before = piece(p.name).settings.filter(s => s.key.startsWith('text.') || p.kind === 'terrain');
    const changed = before.filter(s => JSON.stringify(s.value) !== JSON.stringify(values[s.key]) && s.key in values);
    const pal = palette();
    let css = pal.css;
    for (const s of changed) {
        const v = values[s.key];
        if (s.key.startsWith('text.')) text = setTextYaml(text, s.key.slice(5), v);
        else if (s.key === 'exaggeration') text = text.replace(/^exaggeration:.*$/m, 'exaggeration: ' + num(v, 2));
        else if (s.key.startsWith('view.')) text = setViewYaml(text, s.key.slice(5), v);
        else if (s.key.startsWith('token.')) {
            const [, token, mode] = /^token\.(--map-[\w-]+)\.(light|dark)$/.exec(s.key);
            const re = new RegExp('(' + token + ':\\s*)#[0-9A-Fa-f]{6}');
            if (mode === 'light') css = css.slice(0, pal.darkAt).replace(re, '$1' + v.toUpperCase()) + css.slice(pal.darkAt);
            else css = css.slice(0, pal.darkAt) + css.slice(pal.darkAt).replace(re, '$1' + v.toUpperCase());
        }
    }
    if (!changed.length) return 0;
    const title = changed.find(s => s.key === 'text.title') && values['text.title'].replace(/\s+/g, ' ').trim();
    if (title === '') throw new Error('a map needs a title');
    writeAndBuild(file, text, css === pal.css ? undefined : css);
    // A new title also goes into the map's entry in the homepage's Maps list
    if (title) {
        const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        const home = readFileSync(HOME, 'utf8');
        writeFileSync(HOME, home.replace(new RegExp('(<a href="/maps/' + p.name + '/">)[^<]*(</a>)', 'g'), (_, a, b) => a + esc(title) + b));
    }
    return changed.length;
}

function saveModel(p, values) {
    const file = settingsFile(p);
    const doc = JSON.parse(readFileSync(file, 'utf8'));
    let n = 0;
    for (const s of doc.settings)
        if (!s.key.startsWith('text.') && s.key in values && JSON.stringify(s.value) !== JSON.stringify(values[s.key])) {
            s.value = values[s.key];
            n++;
        }
    if (n) writeFileSync(file, JSON.stringify(doc, null, 1) + '\n');
    return n;
}

// --- The server

const json = (res, code, body) => res.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }).end(JSON.stringify(body));

const server = createServer((req, res) => {
    let path;
    try {
        path = normalize(decodeURIComponent(new URL(req.url, 'http://x').pathname));
    } catch {
        return res.writeHead(400).end();
    }
    if (path.includes('..')) return res.writeHead(400).end();
    try {
        if (path === '/api/pieces') return json(res, 200, pieces());
        let m = /^\/api\/piece\/([\w-]+)$/.exec(path);
        if (m && req.method === 'GET') {
            const p = piece(m[1]);
            return p ? json(res, 200, p) : json(res, 404, { error: 'no such piece' });
        }
        if ((m || path === '/api/render') && req.method === 'POST') {
            // Saves (and text to render) come only from the workbench's own page: JSON (which another site's page cannot send without
            // asking first, and is never answered) from the same origin it is served at
            const origin = req.headers.origin;
            if (!/^application\/json\b/.test(req.headers['content-type'] || '') || (origin && new URL(origin).host !== req.headers.host))
                return json(res, 403, { error: 'not from the workbench' });
            let body = '';
            req.on('data', d => (body += d));
            req.on('end', () => {
                try {
                    // A note or credits as the map's page will show it, through build.py's own markdown
                    if (!m) {
                        const r = spawnSync('uv', ['run', '-q', '--no-project', '--with', 'pyyaml', 'python3', '_writing/build.py', '--md'],
                            { cwd: ROOT, encoding: 'utf8', input: String(JSON.parse(body).text ?? '') });
                        if (r.status !== 0) throw new Error('build.py: ' + (r.stderr || 'failed').trim().split('\n').pop());
                        return json(res, 200, { html: r.stdout.trim() });
                    }
                    const p = pieces().find(x => x.name === m[1]);
                    if (!p) return json(res, 404, { error: 'no such piece' });
                    const values = JSON.parse(body).values;
                    // The page's text (and an interactive map's settings) go into the definition; a model's other settings into its build
                    let n = saveDefinition(p, values);
                    if (p.kind === 'model') n += saveModel(p, values);
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
