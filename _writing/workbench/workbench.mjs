// The workbench: a private page for tuning the site's maps live and saving what works back
// where each piece is made. Run on the mini from the repo root:
//   node _writing/workbench/workbench.mjs
// It listens on 127.0.0.1:8795 and, for as long as it runs, Tailscale publishes it to David's own
// devices at https://<the mini's tailnet name>:8796/ (the tailnet's rules keep shared users to port 443).
//
// Every map definition in _writing/maps/ is a piece. Each has its page's text (the title, the note and the
// credits), edited in place on the page as the workbench shows it and saved into the definition, after which
// the map pages are rebuilt; a new title also goes into the map's entry in the homepage's Maps list. The legend
// beneath the map is edited there too, whole (groups and items added, removed, moved, retyped, recoloured) and
// saved into the definition. Beyond the text:
//   interactive maps (a definition with views): exaggeration when it has terrain, the palette colours its
//     layers use (maps/map.css, light and dark), and its named views; saved into the definition and map.css.
//   models (a definition with `model` and `build`): the settings their Drive build declares in
//     <build>/Scripts/settings.json, shown from the build's own web copy (<build>/Web/); saved into that file.
//   print pages (any other definition): their image, with nothing more to tune.
// Beside the definitions, every print map in the Drive GIS folder (a project with Scripts/map.py and
// Scripts/settings.json) is a piece of its own. Opening one starts a Python session for it (the print engine,
// _writing/print/session.py), kept running and spoken to over its pipes, one JSON message a line; the page it
// builds is shown in a frame, tuned live, and saved into the project's settings.json, after which its PDF is
// rebuilt in the background and moved into the project when its checks pass.
// A new kind of piece declares its settings the same way: a list of {key, label, group, type, value}, where
// type is number (with min, max, step), color, view, or text (a print map adds toggle, choice and labels). The
// Drive folder holding the builds is read from ~/.local/share/workbench/config.json ({"gis": "<folder>"}).
// For testing, WORKBENCH_PORT serves a second workbench on another port (not published over Tailscale) and
// WORKBENCH_GIS points it at another GIS folder.
import { createServer } from 'node:http';
import { readFileSync, writeFileSync, readdirSync, existsSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { join, normalize } from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { homedir } from 'node:os';
import { sendFile, ROOT } from '../serve.mjs';

const PORT = +(process.env.WORKBENCH_PORT || 8795), PUBLIC_PORT = 8796;
const HERE = join(ROOT, '_writing', 'workbench');
const MAPS = join(ROOT, '_writing', 'maps');
const CSS = join(ROOT, 'maps', 'map.css');
const HOME = join(ROOT, 'index.html');
const config = JSON.parse(readFileSync(join(homedir(), '.local/share/workbench/config.json'), 'utf8'));
if (process.env.WORKBENCH_GIS) config.gis = process.env.WORKBENCH_GIS;

// --- Reading the pieces

function yamlScalar(text, key) {
    const m = new RegExp('^' + key + ':\\s*(.+)$', 'm').exec(text);
    if (!m) return null;
    const v = m[1].trim();
    if (/^'.*'$/.test(v)) return v.slice(1, -1).replace(/''/g, "'");
    if (/^".*"$/.test(v)) return JSON.parse(v);
    return v;
}

// The print maps: every project in the GIS folder with a map.py and a settings.json in its Scripts folder
function printPieces() {
    return readdirSync(config.gis, { withFileTypes: true }).filter(d => d.isDirectory()
        && existsSync(join(config.gis, d.name, 'Scripts', 'map.py')) && existsSync(join(config.gis, d.name, 'Scripts', 'settings.json')))
        .map(d => {
            let title = d.name;
            try {
                title = JSON.parse(readFileSync(join(config.gis, d.name, 'Scripts', 'settings.json'), 'utf8')).settings.find(s => s.key === 'title')?.value || title;
            } catch { /* a settings file that will not read still lists, under its folder's name */ }
            return { name: 'print-' + d.name.toLowerCase().replace(/[^a-z0-9]+/g, '-'), title, kind: 'print', folder: d.name, group: 'Print Maps' };
        }).sort((a, b) => a.title.localeCompare(b.title));
}

function pieces() {
    return [...readdirSync(MAPS).filter(f => f.endsWith('.yaml')).map(f => {
        const text = readFileSync(join(MAPS, f), 'utf8'), name = f.slice(0, -5);
        const kind = /^views:/m.test(text) ? 'terrain' : yamlScalar(text, 'model') && yamlScalar(text, 'build') ? 'model' : 'page';
        return { name, title: yamlScalar(text, 'title'), kind, build: yamlScalar(text, 'build'), image: yamlScalar(text, 'image') };
    }).sort((a, b) => a.title.localeCompare(b.title)), ...printPieces()];
}

// The page's text and its legend, read with PyYAML as build.py reads them
const TEXT = [['title', 'Title'], ['note', 'Description'], ['credits', 'Credits']];
function textSettings(name) {
    const r = spawnSync('uv', ['run', '-q', '--no-project', '--with', 'pyyaml', 'python3', '-c',
        'import sys, json, yaml; d = yaml.safe_load(open(sys.argv[1])); print(json.dumps({**{k: d.get(k) or "" for k in ("title", "note", "credits")}, "legend": d.get("legend") or []}))',
        join(MAPS, name + '.yaml')], { encoding: 'utf8' });
    if (r.status !== 0) throw new Error('could not read the definition: ' + (r.stderr || '').trim().split('\n').pop());
    const d = JSON.parse(r.stdout);
    return [...TEXT.map(([k, label]) => ({ key: 'text.' + k, label, group: 'Text', type: 'text', value: String(d[k]) })),
        { key: 'legend', label: 'Legend', group: 'Text', type: 'legend', value: d.legend }];
}

// The legend: groups, each an optional heading and its items, each a label with a swatch (fill, dot or line,
// a line solid, dashed or dotted) in a hex colour or a --map token, and an optional code
const ITEM_KEYS = ['code', 'shape', 'dash', 'color', 'label'];
function checkLegend(legend) {
    if (!Array.isArray(legend)) throw new Error('the legend is not a list of groups');
    const colours = palette().light;   // the map's colour tokens (a token holding no colour is refused)
    return legend.map(g => {
        const items = (g.items || []).map(it => {
            const out = {};
            const label = String(it.label ?? '').replace(/\s+/g, ' ').trim();
            if (!label) throw new Error('every legend item needs a label');
            if (!/^#[0-9A-Fa-f]{6}$/.test(it.color || '') && !(it.color in colours)) throw new Error('"' + label + '" has no colour');
            const shape = it.shape || 'fill';
            if (!['fill', 'dot', 'line'].includes(shape)) throw new Error('"' + label + '" has an unknown shape');
            const code = String(it.code ?? '').trim();
            if (code) out.code = code;
            if (shape !== 'fill') out.shape = shape;
            if (shape === 'line' && ['dashed', 'dotted'].includes(it.dash)) out.dash = it.dash;
            out.color = it.color;
            out.label = label;
            return out;
        });
        if (!items.length) throw new Error('every legend group needs an item');
        const heading = String(g.heading ?? '').replace(/\s+/g, ' ').trim();
        return heading ? { heading, items } : { items };
    });
}
const q = v => "'" + String(v).replace(/'/g, "''") + "'";
function legendYaml(legend) {
    const lines = ['legend:'];
    for (const g of legend) {
        if (g.heading) lines.push('- heading: ' + q(g.heading), '  items:');
        else lines.push('- items:');
        for (const it of g.items) lines.push('  - {' + ITEM_KEYS.filter(k => k in it).map(k => k + ': ' + q(it[k])).join(', ') + '}');
    }
    return lines;
}
// Replaces the legend block (its line and every line under it, indented or a top-level list item), or adds
// it at the end; an emptied legend is dropped
function setLegendYaml(text, legend) {
    const lines = text.split('\n');
    const block = legend.length ? legendYaml(legend) : [];
    const i = lines.findIndex(l => l.startsWith('legend:'));
    if (i < 0) {
        while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
        return [...lines, ...block, ''].join('\n');
    }
    let end = i + 1;
    for (let j = i + 1; j < lines.length && (/^(\s+\S|- )/.test(lines[j]) || /^\s*(#|$)/.test(lines[j])); j++)
        if (!/^\s*(#|$)/.test(lines[j])) end = j + 1;
    lines.splice(i, end - i, ...block);
    return lines.join('\n');
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
    for (let j = i + 1; j < lines.length && (/^\s+\S/.test(lines[j]) || /^\s*(#|$)/.test(lines[j])); j++)
        if (!/^\s*(#|$)/.test(lines[j])) end = j + 1;
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
    const before = piece(p.name).settings.filter(s => s.key.startsWith('text.') || s.key === 'legend' || p.kind === 'terrain');
    const changed = before.filter(s => JSON.stringify(s.value) !== JSON.stringify(values[s.key]) && s.key in values);
    const pal = palette();
    let css = pal.css;
    for (const s of changed) {
        const v = values[s.key];
        if (s.key.startsWith('text.')) text = setTextYaml(text, s.key.slice(5), v);
        else if (s.key === 'legend') text = setLegendYaml(text, checkLegend(v));
        else if (s.key === 'exaggeration') text = text.replace(/^exaggeration:.*$/m, 'exaggeration: ' + num(v, 2));
        else if (s.key.startsWith('view.')) text = setViewYaml(text, s.key.slice(5), v);
        else if (s.key.startsWith('token.')) {
            const [, token, mode] = /^token\.(--map-[\w-]+)\.(light|dark)$/.exec(s.key);
            const re = new RegExp('(' + token + ':\\s*)#[0-9A-Fa-f]{6}');
            if (mode === 'light') css = css.slice(0, pal.darkAt).replace(re, '$1' + v.toUpperCase()) + css.slice(pal.darkAt);
            else css = css.slice(0, pal.darkAt) + css.slice(pal.darkAt).replace(re, '$1' + v.toUpperCase());
        }
    }
    if (changed.some(s => s.key === 'text.title') && !values['text.title'].trim()) throw new Error('a map needs a title');
    if (changed.length) writeAndBuild(file, text, css === pal.css ? undefined : css);
    syncHome(p.name);
    return changed.length;
}

// The map's entry in the homepage's Maps list carries its title; every save brings it in line with the
// definition, so a save whose homepage write failed is put right by the next
function syncHome(name) {
    const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const title = esc(textSettings(name).find(s => s.key === 'text.title').value);
    if (!title) return;
    const home = readFileSync(HOME, 'utf8');
    const next = home.replace(new RegExp('(<a href="/maps/' + name + '/">)[^<]*(</a>)', 'g'), (_, a, b) => a + title + b);
    if (next !== home) writeFileSync(HOME, next);
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

// --- Print maps: the session, saving, and the build

const PRINT = join(ROOT, '_writing', 'print');
const sessions = new Map();   // piece name -> its running session
const builds = new Map();     // piece name -> { state: 'building' | 'done' | 'failed', message }

function session(p) {
    let s = sessions.get(p.name);
    if (s && !s.dead) return s;
    const proc = spawn('uv', ['run', '-q', '--project', PRINT, 'python', join(PRINT, 'session.py'), join(config.gis, p.folder), '/print-fonts/', '/print/' + p.name + '/files/'],
        { cwd: ROOT, stdio: ['pipe', 'pipe', 'inherit'] });
    s = { proc, waiting: new Map(), next: 1, dead: false, name: null, settings: null, idle: null };
    createInterface({ input: proc.stdout }).on('line', line => {
        let msg;
        try {
            msg = JSON.parse(line);
        } catch {
            return;
        }
        const w = s.waiting.get(msg.id);
        if (!w) return;
        s.waiting.delete(msg.id);
        msg.error ? w.no(new Error(msg.error)) : w.ok(msg);
    });
    proc.on('exit', () => {
        s.dead = true;
        for (const w of s.waiting.values()) w.no(new Error('the print session stopped'));
        s.waiting.clear();
    });
    sessions.set(p.name, s);
    return s;
}
// One message to a piece's session, and its answer. A session left alone for half an hour is stopped; the
// next message starts another.
function ask(p, msg) {
    const s = session(p);
    clearTimeout(s.idle);
    s.idle = setTimeout(() => s.proc.kill(), 30 * 60 * 1000);
    return new Promise((ok, no) => {
        const id = s.next++;
        s.waiting.set(id, { ok, no });
        s.proc.stdin.write(JSON.stringify({ ...msg, id }) + '\n');
    });
}
async function openPrint(p) {
    const r = await ask(p, { op: 'open' });
    const s = sessions.get(p.name);
    s.name = r.name;
    s.settings = r.settings;
    return { ...p, title: r.settings.find(x => x.key === 'title')?.value || p.title, settings: r.settings, pages: r.pages, notes: r.notes, building: builds.get(p.name) || null };
}
// Saving writes the changed values into the project's settings.json (a setting the map has since declared
// joins the file with it), then rebuilds the PDF in the background
function savePrint(p, values) {
    const file = join(config.gis, p.folder, 'Scripts', 'settings.json');
    const doc = JSON.parse(readFileSync(file, 'utf8'));
    const declared = sessions.get(p.name)?.settings || [];
    let n = 0;
    for (const [k, v] of Object.entries(values)) {
        let s = doc.settings.find(x => x.key === k);
        if (!s) {
            const d = declared.find(x => x.key === k);
            if (!d) continue;
            doc.settings.push((s = { ...d }));
        }
        if (JSON.stringify(s.value) !== JSON.stringify(v)) {
            s.value = v;
            n++;
        }
    }
    if (n) writeFileSync(file, JSON.stringify(doc, null, 1) + '\n');
    buildPrint(p);
    const saved = Object.fromEntries(doc.settings.map(s => [s.key, s.value]));
    return { saved: n, piece: { ...p, title: saved.title || p.title, settings: (declared.length ? declared : doc.settings).map(s => ({ ...s, value: s.key in saved ? saved[s.key] : s.value })) } };
}
// The build: in scratch, checked, and only then moved into the project; a failed build leaves the last PDF
function buildPrint(p) {
    builds.get(p.name)?.proc?.kill();
    const proc = spawn('uv', ['run', '-q', '--project', PRINT, 'python', join(PRINT, 'build.py'), join(config.gis, p.folder), '--move'], { cwd: ROOT });
    const b = { state: 'building', message: 'Building the PDF…', proc };
    let out = '';
    proc.stdout.on('data', d => (out += d));
    proc.stderr.on('data', d => (out += d));
    proc.on('exit', code => {
        if (builds.get(p.name) !== b) return;   // a later save's build has taken over
        const lines = out.trim().split('\n');
        const pdf = /BUILD PASSED (.*)$/.exec(lines[lines.length - 1] || '');
        if (code === 0 && pdf) Object.assign(b, { state: 'done', message: 'PDF built and in the project: ' + pdf[1].split('/').pop() });
        else Object.assign(b, { state: 'failed', message: 'The PDF was not rebuilt (the last one stands): ' + (lines.filter(l => /problems:|BUILD STOPPED|Error/.test(l)).pop() || lines.pop() || 'the build failed') });
        b.proc = null;
    });
    builds.set(p.name, b);
}
const stopAll = () => {
    for (const s of sessions.values()) s.proc.kill();
    for (const b of builds.values()) b.proc?.kill();
};

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
    const fail = e => json(res, 500, { error: e.message });
    try {
        if (path === '/api/pieces') return json(res, 200, pieces());
        // Print maps: their page and its files, and the messages to their session
        let m = /^\/(?:api\/piece|api\/print|print)\/(print-[\w-]+)(?:\/(.*))?$/.exec(path);
        const pp = m && printPieces().find(x => x.name === m[1]);
        if (m && !pp) return json(res, 404, { error: 'no such piece' });
        if (pp) {
            const rest = m[2] || '';
            if (req.method === 'GET') {
                if (path.startsWith('/api/piece/')) return void openPrint(pp).then(p => json(res, 200, p), fail);
                if (rest === 'build') return json(res, 200, (({ state, message }) => ({ state, message }))(builds.get(pp.name) || { state: 'none', message: '' }));
                const page = /^page\/(\d+)$/.exec(rest);
                if (page) return void ask(pp, { op: 'page', n: +page[1] }).then(r => res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' }).end(r.html), fail);
                const file = /^files\/([\w.-]+)$/.exec(rest);
                const s = sessions.get(pp.name);
                if (file && s?.name) return sendFile(req, res, join(homedir(), '.local/share/workbench/print', s.name, 'preview', file[1]));
                return res.writeHead(404).end();
            }
            if (req.method === 'POST') {
                const origin = req.headers.origin;
                if (!/^application\/json\b/.test(req.headers['content-type'] || '') || (origin && new URL(origin).host !== req.headers.host))
                    return json(res, 403, { error: 'not from the workbench' });
                let body = '';
                req.on('data', d => (body += d));
                req.on('end', () => {
                    try {
                        const data = JSON.parse(body);
                        if (path.startsWith('/api/piece/')) return json(res, 200, savePrint(pp, data.values));
                        if (['set', 'pin', 'view'].includes(rest)) return void ask(pp, { ...data, op: rest }).then(r => json(res, 200, r), fail);
                        json(res, 404, { error: 'no such message' });
                    } catch (e) {
                        fail(e);
                    }
                });
                return;
            }
        }
        m = /^\/print-fonts\/([\w.-]+)$/.exec(path);
        if (m) return sendFile(req, res, join(PRINT, 'fonts', m[1]));
        m = /^\/api\/piece\/([\w-]+)$/.exec(path);
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
        if (path.startsWith('/maps/') || path.startsWith('/writing/') || path.startsWith('/fonts/') || path === '/favicon.png') return sendFile(req, res, join(ROOT, path));
        // the site's own tab icon, the DV mark that follows light and dark, from the build's copy of it
        if (path === '/favicon.svg') {
            const uri = readFileSync(join(ROOT, '_writing', 'favicon.txt'), 'utf8').trim();
            return res.writeHead(200, { 'Content-Type': 'image/svg+xml' }).end(decodeURIComponent(uri.slice(uri.indexOf(',') + 1)));
        }
        res.writeHead(404).end('Not found');
    } catch (e) {
        json(res, 500, { error: e.message });
    }
});

server.listen(PORT, '127.0.0.1', () => {
    if (process.env.WORKBENCH_PORT) {   // a second workbench for testing: on the mini only, not published
        const stop = () => (stopAll(), process.exit(0));
        process.on('SIGINT', stop);
        process.on('SIGTERM', stop);
        return console.log(`Workbench (test): http://127.0.0.1:${PORT}/  GIS folder: ${config.gis}`);
    }
    const dns = JSON.parse(spawnSync('tailscale', ['status', '--json'], { encoding: 'utf8' }).stdout).Self.DNSName.replace(/\.$/, '');
    // Foreground serve: Tailscale stops publishing the moment this process ends.
    const ts = spawn('tailscale', ['serve', '--https=' + PUBLIC_PORT, 'http://127.0.0.1:' + PORT], { stdio: 'ignore' });
    const stop = () => {
        ts.kill();
        stopAll();
        process.exit(0);
    };
    process.on('SIGINT', stop);
    process.on('SIGTERM', stop);
    console.log(`Workbench: https://${dns}:${PUBLIC_PORT}/  (on the mini: http://127.0.0.1:${PORT}/)`);
    if (!existsSync(join(HERE, 'index.html'))) console.warn('index.html is missing');
});
