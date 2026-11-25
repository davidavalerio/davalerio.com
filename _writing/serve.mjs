// A local server for previewing the site, run from the repo root:
//   node _writing/serve.mjs [port]
// It answers byte-range requests, which the map tiles need (Python's
// http.server does not). Drafts built with `build.py --drafts` are at /_preview/<slug>/.
import { createServer } from 'node:http';
import { createReadStream, statSync } from 'node:fs';
import { extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(fileURLToPath(import.meta.url), '..', '..');
const TYPES = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.mjs': 'text/javascript',
    '.json': 'application/json', '.jpg': 'image/jpeg', '.png': 'image/png', '.svg': 'image/svg+xml', '.pdf': 'application/pdf',
    '.xml': 'application/xml', '.pmtiles': 'application/octet-stream', '.woff2': 'font/woff2' };

export function serve(port = 8642) {
    const server = createServer((req, res) => {
        let path = normalize(decodeURIComponent(new URL(req.url, 'http://x').pathname));
        if (path.includes('..')) return res.writeHead(400).end();
        sendFile(req, res, join(ROOT, path));
    });
    return new Promise(ok => server.listen(port, '127.0.0.1', () => ok(server)));
}

// Send one file (a folder sends its index.html), answering byte ranges. The workbench serves with it too.
export function sendFile(req, res, file) {
    let stat;
    try {
        stat = statSync(file);
        if (stat.isDirectory()) stat = statSync((file = join(file, 'index.html')));
    } catch {
        return res.writeHead(404).end('Not found');
    }
    const headers = { 'Content-Type': TYPES[extname(file)] || 'application/octet-stream', 'Accept-Ranges': 'bytes' };
    const range = /bytes=(\d*)-(\d*)/.exec(req.headers.range || '');
    if (range && (range[1] || range[2])) {
        const start = range[1] ? +range[1] : Math.max(0, stat.size - +range[2]);
        const end = range[1] && range[2] ? Math.min(+range[2], stat.size - 1) : stat.size - 1;
        if (start > end || start >= stat.size) {
            return res.writeHead(416, { 'Content-Range': `bytes */${stat.size}` }).end();
        }
        res.writeHead(206, { ...headers, 'Content-Range': `bytes ${start}-${end}/${stat.size}`, 'Content-Length': end - start + 1 });
        return createReadStream(file, { start, end }).pipe(res);
    }
    res.writeHead(200, { ...headers, 'Content-Length': stat.size, 'Cache-Control': 'no-store' });
    createReadStream(file).pipe(res);
}
export { ROOT, TYPES };

if (process.argv[1] === fileURLToPath(import.meta.url)) {
    const port = +(process.argv[2] || 8642);
    await serve(port);
    console.log(`http://127.0.0.1:${port}/`);
}
