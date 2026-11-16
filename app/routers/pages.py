"""HTML pages: parent payment page, admin dashboard, receipt."""
import hashlib
import re
from html import escape
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.ratelimit import rate_limit
from app.routers.public import receipt_payload
from app.services.sms import format_rupees

STATIC = Path(__file__).resolve().parent.parent / "static"
router = APIRouter(include_in_schema=False)

_NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}

# Cache-busting: every /static/*.js|css reference in a page gets ?v=<content hash>, so phones always
# load the script that matches the page (an old cached script + new page = broken page).
_ASSET_RE = re.compile(r'(["\'])(/static/[^"\'?]+\.(?:js|css))\1')
_hash_cache: dict = {}


def asset_version(rel_path: str) -> str:
    f = STATIC / rel_path
    mtime = f.stat().st_mtime_ns
    cached = _hash_cache.get(rel_path)
    if cached and cached[0] == mtime:
        return cached[1]
    digest = hashlib.sha256(f.read_bytes()).hexdigest()[:12]
    _hash_cache[rel_path] = (mtime, digest)
    return digest


def versioned(html: str) -> str:
    def sub(m):
        path = m.group(2)
        try:
            return f"{m.group(1)}{path}?v={asset_version(path[len('/static/'):])}{m.group(1)}"
        except OSError:
            return m.group(0)
    return _ASSET_RE.sub(sub, html)


def page(name: str) -> HTMLResponse:
    return HTMLResponse(versioned((STATIC / name).read_text(encoding="utf-8")), headers=_NO_STORE)


@router.get("/")
def root():
    return RedirectResponse("/admin")


@router.get("/pay/{secure_token}")
@router.get("/p/{secure_token}")  # short link used in SMS
def pay_page(secure_token: str):
    # The token is read client-side from the URL; the page never embeds data server-side.
    return page("pay.html")


@router.get("/admin")
def admin_page():
    return page("admin.html")


@router.get("/platform")
def platform_page():
    return page("platform.html")


@router.get("/r/{receipt_token}", response_class=HTMLResponse, dependencies=[Depends(rate_limit("public_read", 60))])
@router.get("/receipt/{receipt_token}", response_class=HTMLResponse, dependencies=[Depends(rate_limit("public_read", 60))])
def receipt_page(receipt_token: str, db: Session = Depends(get_db)):
    r = receipt_payload(db, receipt_token)
    e = lambda v: escape(str(v)) if v is not None else ""  # noqa: E731
    status_line = "PAID IN FULL" if r["balance_after_paise"] == 0 else f"Balance due: Rs. {format_rupees(r['balance_after_paise'])}"
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Receipt {e(r['receipt_number'])}</title>
<link rel="stylesheet" href="/static/css/app.css?v={asset_version("css/app.css")}"></head><body class="page">
<main class="card receipt">
  <div class="brand">FeeFlow</div>
  <h1>{e(r['centre']['name'])}</h1>
  <p class="muted">Payment receipt</p>
  <dl class="kv">
    <dt>Receipt no.</dt><dd>{e(r['receipt_number'])}</dd>
    <dt>Date</dt><dd data-utc="{e(r['issued_at'])}">{e(r['issued_at'][:10])}</dd>
    <dt>Student</dt><dd>{e(r['student']['name'])} ({e(r['student']['student_id'])})</dd>
    <dt>Fee</dt><dd>{e(r['fee']['description'])} &middot; {e(r['fee']['billing_month'])}</dd>
    <dt>Amount received</dt><dd class="big">Rs. {format_rupees(r['amount_paise'])}</dd>
    <dt>Total paid</dt><dd>Rs. {format_rupees(r['total_paid_after_paise'])} of Rs. {format_rupees(r['fee']['total_paise'])}</dd>
  </dl>
  <p class="pill {'ok' if r['balance_after_paise'] == 0 else 'warn'}">{e(status_line)}</p>
  <p class="muted small">Verified by {e(r['centre']['name'])} against bank records. Contact: {e(r['centre']['contact_phone'])}</p>
  <button class="btn secondary" onclick="window.print()">Print / Save</button>
</main></body></html>"""
    return HTMLResponse(html, headers=_NO_STORE)
