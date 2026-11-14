import { chromium } from 'playwright';
const [,, url, out, w='1440', h='900', full='1'] = process.argv;
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: +w, height: +h } });
await p.goto(url, { waitUntil: 'networkidle', timeout: 60000 });
// trigger lazy loading
for (let y = 0; y < 20000; y += 600) { await p.evaluate(y => window.scrollTo(0, y), y); await p.waitForTimeout(120); }
await p.evaluate(() => window.scrollTo(0, 0)); await p.waitForTimeout(800);
await p.screenshot({ path: out, fullPage: full === '1' });
console.log(await p.evaluate(() => document.body.scrollHeight));
await b.close();
