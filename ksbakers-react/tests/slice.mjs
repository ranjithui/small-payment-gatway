// Slice a tall PNG into viewport-height tiles: node tests/slice.mjs file.png [tileHeight]
import { chromium } from 'playwright';
import fs from 'fs';
const [,, file, th = '1100'] = process.argv;
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
const data = fs.readFileSync(file).toString('base64');
await p.setContent(`<body style="margin:0"><img id=i src="data:image/png;base64,${data}">`);
const { w, h } = await p.evaluate(() => ({ w: i.naturalWidth, h: i.naturalHeight }));
let n = 0;
for (let y = 0; y < h; y += +th) await p.screenshot({ path: file.replace(/\.png$/, `.s${n++}.png`), fullPage: true, clip: { x: 0, y, width: w, height: Math.min(+th, h - y) } });
console.log(n);
await b.close();
