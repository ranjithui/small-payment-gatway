import fs from 'fs';
import { fetchFlight, extractObject } from './rsc.mjs';
const sm = await (await fetch('https://ksbakers.com/sitemap.xml')).text();
const urls = [...sm.matchAll(/<loc>([^<]+\/cakes\/[^<]+)<\/loc>/g)].map(m => m[1]);
const out = []; let k = 0;
async function worker() {
  while (k < urls.length) {
    const u = urls[k++];
    try { const { flight } = await fetchFlight(u); const r = extractObject(flight, 'cake'); if (r) out.push(r.value); else console.log('no cake', u); }
    catch (e) { console.log('err', u, e.message); }
  }
}
await Promise.all(Array.from({ length: 8 }, worker));
fs.mkdirSync('scripts/raw', { recursive: true });
fs.writeFileSync('scripts/raw/products.json', JSON.stringify(out, null, 1));
console.log(urls.length, out.length);
