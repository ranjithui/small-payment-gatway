import { fetchFlight } from './rsc.mjs';
const { flight } = await fetchFlight('https://ksbakers.com' + process.argv[2]);
const keys = {}; for (const m of flight.matchAll(/"([a-z_A-Z]+)":[\[{]/g)) keys[m[1]] = (keys[m[1]] || 0) + 1;
console.log(flight.length, JSON.stringify(keys));
const q = process.argv[3]; if (q) { const i = flight.indexOf(q); console.log(flight.slice(i, i + 2500)); }
