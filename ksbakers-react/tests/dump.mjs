import { chromium } from 'playwright';
import fs from 'fs';
const [,, url, out, w='1440', h='900'] = process.argv;
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: +w, height: +h } });
await p.goto(url, { waitUntil: 'networkidle', timeout: 60000 });
for (let y = 0; y < 20000; y += 600) { await p.evaluate(y => window.scrollTo(0, y), y); await p.waitForTimeout(100); }
await p.evaluate(() => window.scrollTo(0, 0)); await p.waitForTimeout(800);
fs.writeFileSync(out + '.html', await p.evaluate(() => document.body.outerHTML));
await p.screenshot({ path: out + '-top.png' });
await b.close();
