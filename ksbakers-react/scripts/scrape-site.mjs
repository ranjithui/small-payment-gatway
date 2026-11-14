// Pulls the structured data that ksbakers.com renders from (RSC payload) into src/data/*.json.
import fs from 'fs';
import { fetchFlight, extractObject } from './rsc.mjs';

const BASE = 'https://ksbakers.com';
const out = {};
const grab = (flight, key, open = '{') => extractObject(flight, key, 0, open)?.value ?? null;

// Global layout data + home page sections
{
  const { flight } = await fetchFlight(BASE + '/');
  out.layout = {
    navLinks: grab(flight, 'navLinks', '['),
    cakeNav: grab(flight, 'cakeNav', '['),
    announcements: grab(flight, 'announcements', '['),
    footer: grab(flight, 'footer'),
  };
  out.home = {};
  for (const k of ['heroBanners', 'promoBanners', 'homeStats', 'categoryTiles', 'testimonials', 'blogPosts'])
    out.home[k] = grab(flight, k, '[') ?? grab(flight, k);
  out.home.homeCta = grab(flight, 'homeCta');
}

// Category + sub-category listings
const sm = await (await fetch(BASE + '/sitemap.xml')).text();
const listingPaths = [...sm.matchAll(/<loc>https:\/\/ksbakers\.com(\/[^<]*)<\/loc>/g)].map(m => m[1])
  .filter(p => /^\/(birthday-cakes|theme-cakes|anniversary-cakes|occasion-cakes)(\/|$)/.test(p));
for (const extra of ['/products']) listingPaths.push(extra);
out.listings = {};
for (const p of listingPaths) {
  const { flight } = await fetchFlight(BASE + p);
  const listing = grab(flight, 'listing');
  out.listings[p] = listing
    ? { ...listing, cakes: (listing.cakes || []).map(c => c.slug) }
    : { cakes: [...flight.matchAll(/"slug":"([^"]+)","name":"[^"]+","type"/g)].map(m => m[1]) };
  console.log(p, out.listings[p].cakes.length);
}

// Other pages with structured data
const pageKeys = {
  '/about': ['content', 'values', 'strengths', 'team', 'accolades', 'press'],
  '/stores': ['page', 'outlets'],
  '/faq': ['page', 'faqs'],
  '/gifting': ['banners', 'tiles'],
  '/gifting/hampers': ['listing', 'hampers', 'snack_boxes'],
  '/add-ons': ['listing', 'categories', 'addons'],
  '/blog': ['data', 'posts'],
};
out.pages = {};
for (const [p, keys] of Object.entries(pageKeys)) {
  const { flight } = await fetchFlight(BASE + p);
  out.pages[p] = {};
  for (const k of keys) out.pages[p][k] = grab(flight, k) ?? grab(flight, k, '[');
}

fs.writeFileSync('scripts/raw/site.json', JSON.stringify(out, null, 1));
console.log('done');
