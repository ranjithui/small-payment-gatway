import { chromium } from 'playwright';
import fs from 'fs';
const [,, outDir, w = '1440', h = '900', ...paths] = process.argv;
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: +w, height: +h } });
for (const path of paths) {
  const name = path === '/' ? 'home' : path.replace(/^\//, '').replace(/\//g, '__');
  try {
    await p.goto('https://ksbakers.com' + path, { waitUntil: 'networkidle', timeout: 60000 });
    for (let y = 0; y < 15000; y += 700) { await p.evaluate(y => window.scrollTo(0, y), y); await p.waitForTimeout(80); }
    await p.evaluate(() => window.scrollTo(0, 0)); await p.waitForTimeout(700);
    fs.writeFileSync(`${outDir}/${name}.html`, (await p.evaluate(() => document.body.outerHTML)).replace(/></g, '>\n<'));
    await p.screenshot({ path: `${outDir}/${name}.png`, fullPage: true });
    console.log(name, await p.evaluate(() => document.documentElement.scrollHeight));
  } catch (e) { console.log('ERR', path, e.message); }
}
await b.close();
