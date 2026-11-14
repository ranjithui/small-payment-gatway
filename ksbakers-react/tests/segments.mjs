import { chromium } from 'playwright';
const [,, url, prefix, w='1440', h='900'] = process.argv;
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: +w, height: +h } });
await p.goto(url, { waitUntil: 'networkidle', timeout: 60000 });
for (let y = 0; y < 20000; y += 600) { await p.evaluate(y => window.scrollTo(0, y), y); await p.waitForTimeout(120); }
await p.waitForTimeout(800);
const H = await p.evaluate(() => document.documentElement.scrollHeight);
let i = 0;
for (let y = 0; y < H; y += +h) {
  await p.screenshot({ path: `${prefix}-${i++}.png`, fullPage: true, clip: { x: 0, y, width: +w, height: Math.min(+h, H - y) } });
}
console.log(H, i);
await b.close();
