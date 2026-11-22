// Capture the still image of every view of a map, run from the repo root:
//   node _writing/stills.mjs <map name>
// Each view is drawn by the map's own page in headless Chrome, in light mode,
// at 1200 × 800, and saved to maps/<name>/stills/<view>.jpg. The map pages are
// rebuilt from the current definitions first. The stills are what readers see
// in email, the feed, print and without JavaScript, so they are captured again
// whenever a map's data or views change; run build.py afterward.
// Needs playwright-core in ~/.local/share/site-stills (npm install --prefix there).
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { serve } from './serve.mjs';

const require = createRequire(join(homedir(), '.local/share/site-stills/node_modules/'));
const { chromium } = require('playwright-core');
const name = process.argv[2];
if (!name) throw new Error('usage: node _writing/stills.mjs <map name>');

execFileSync('uv', ['run', '-q', '--no-project', '--with', 'pyyaml', 'python3', '_writing/build.py', '--maps-only'], { stdio: 'inherit' });
const page = readFileSync(`maps/${name}/index.html`, 'utf8');
const def = JSON.parse(page.match(new RegExp(`id="map-def-${name}">(.*?)</script>`, 's'))[1].replaceAll('<\\/', '</'));
const views = Object.keys(def.views);
const out = `maps/${name}/stills`;
mkdirSync(out, { recursive: true });

const server = await serve(8643);
const browser = await chromium.launch({ channel: 'chrome', args: ['--use-angle=metal', '--enable-gpu'] });
const tab = await browser.newPage({ viewport: { width: 1200, height: 800 }, colorScheme: 'light', deviceScaleFactor: 1 });
for (const view of views) {
    await tab.goto(`http://127.0.0.1:8643/maps/${name}/?still=${view}`);
    await tab.waitForFunction(() => document.documentElement.dataset.still === 'ready', null, { timeout: 60000 });
    await tab.waitForTimeout(500);
    await tab.screenshot({ path: `${out}/${view}.jpg`, type: 'jpeg', quality: 86, clip: { x: 0, y: 0, width: 1200, height: 800 } });
    console.log(`${out}/${view}.jpg`);
}
await browser.close();
server.close();
