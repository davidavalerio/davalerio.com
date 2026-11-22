// Capture the still image of every view of a map, run from the repo root:
//   node _writing/stills.mjs <map name>
// Each view is drawn by the map's own page in headless Chrome, in light mode,
// at 1200 × 800, and saved to maps/<name>/stills/<view>.jpg. The stills are what
// readers see in email, the feed, print and without JavaScript, so they are
// rebuilt whenever a map's data or views change; run build.py afterward.
// Needs playwright-core in ~/.local/share/site-stills (npm install --prefix there).
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { serve } from './serve.mjs';

const require = createRequire(join(homedir(), '.local/share/site-stills/node_modules/'));
const { chromium } = require('playwright-core');
const name = process.argv[2];
if (!name) throw new Error('usage: node _writing/stills.mjs <map name>');

const yaml = readFileSync(`_writing/maps/${name}.yaml`, 'utf8');
const views = [...yaml.split(/^views:\s*$/m)[1].matchAll(/^  ([\w-]+):\s*$/gm)].map(m => m[1]);
const out = `maps/${name}/stills`;
mkdirSync(out, { recursive: true });

const server = await serve(8643);
const browser = await chromium.launch({ channel: 'chrome', args: ['--use-angle=metal', '--enable-gpu'] });
const page = await browser.newPage({ viewport: { width: 1200, height: 800 }, colorScheme: 'light', deviceScaleFactor: 1 });
for (const view of views) {
    await page.goto(`http://127.0.0.1:8643/maps/${name}/?still=${view}`);
    await page.waitForFunction(() => document.documentElement.dataset.still === 'ready', null, { timeout: 60000 });
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${out}/${view}.jpg`, type: 'jpeg', quality: 86, clip: { x: 0, y: 0, width: 1200, height: 800 } });
    console.log(`${out}/${view}.jpg`);
}
await browser.close();
server.close();
