// Helpers to read the Next.js RSC flight payload embedded in server-rendered HTML.
export async function fetchFlight(url) {
  const res = await fetch(url, { headers: { 'user-agent': 'Mozilla/5.0' } });
  const html = await res.text();
  let flight = '';
  const re = /self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)/g;
  let m;
  while ((m = re.exec(html))) flight += JSON.parse(m[1]);
  return { html, flight, status: res.status };
}

// Find `"key":{...}` (or `"key":[...]`) and parse the balanced JSON value.
export function extractObject(text, key, from = 0, open = '{') {
  const i = text.indexOf(`"${key}":${open}`, from);
  if (i < 0) return null;
  const s = text.indexOf(open, i);
  let depth = 0, inStr = false;
  for (let j = s; j < text.length; j++) {
    const c = text[j];
    if (inStr) { if (c === '\\') j++; else if (c === '"') inStr = false; continue; }
    if (c === '"') inStr = true;
    else if (c === '{' || c === '[') depth++;
    else if (c === '}' || c === ']') {
      depth--;
      if (!depth) return { value: JSON.parse(text.slice(s, j + 1)), end: j + 1 };
    }
  }
  return null;
}
