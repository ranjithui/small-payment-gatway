import { chromium } from 'playwright';
const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
p.on('response', async r => { const t = r.request().resourceType(); if (['fetch', 'xhr'].includes(t)) console.log(r.status(), r.request().method(), r.url().slice(0, 200)); });
for (const u of process.argv.slice(2)) { console.log('##', u); await p.goto('https://ksbakers.com' + u, { waitUntil: 'networkidle' }); for (let y = 0; y < 8000; y += 700) { await p.evaluate(y => scrollTo(0, y), y); await p.waitForTimeout(100); } await p.waitForTimeout(1000); }
await b.close();
