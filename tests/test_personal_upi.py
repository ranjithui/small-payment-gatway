"""Personal UPI mode: UPI-linked mobile number, QR without amount, page data and element wiring."""
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


def q(uri):
    return {k: v[0] for k, v in parse_qs(urlparse(uri).query).items()}


def test_personal_mode_flags_and_mobile_number(env):
    u = env.view().json()["upi"]
    assert u["personal_mode"] is True and u["upi_mobile"] is None

    r = env.c.patch("/api/v1/workspace", headers=env.admin, json={"upi_mobile": "098765 43210"})
    assert r.status_code == 200 and r.json()["upi_mobile"] == "+919876543210"
    u = env.view().json()["upi"]
    assert u["upi_mobile"] == "98765 43210"  # what parents type in "Pay phone number"

    assert env.c.patch("/api/v1/workspace", headers=env.admin, json={"upi_mobile": "12345"}).status_code == 422
    env.c.patch("/api/v1/workspace", headers=env.admin, json={"name": "Bright Minds Tuition Centre"})  # untouched
    assert env.view().json()["upi"]["upi_mobile"] == "98765 43210"
    r = env.c.patch("/api/v1/workspace", headers=env.admin, json={"upi_mobile": ""})  # remove
    assert r.json()["upi_mobile"] is None and env.view().json()["upi"]["upi_mobile"] is None

    env.c.patch("/api/v1/workspace", headers=env.admin, json={"upi_is_merchant": True})
    assert env.view().json()["upi"]["personal_mode"] is False


def test_workspace_created_with_upi_mobile(env):
    r = env.c.post("/api/v1/auth/signup", json={"name": "Owner", "email": "own@x.in", "password": "strong-pass-123"})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = env.c.post("/api/v1/workspace", headers=h, json={"name": "Personal Centre", "upi_vpa": "personal@okhdfcbank",
                                                         "upi_payee_name": "Personal", "upi_mobile": "9876543210"})
    assert r.status_code == 201 and r.json()["upi_mobile"] == "+919876543210"


def test_qr_without_amount(env):
    v = env.view().json()
    any_uri = v["upi"]["uris_any_amount"]["QR"]
    assert "am" not in q(any_uri) and q(any_uri)["tn"] == v["payment_reference"]
    r = env.c.get(f"/api/v1/pay/{env.token()}/qr.png?any_amount=1&download=1")
    assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert r.headers["content-disposition"].endswith('-no-amount.png"')
    # Personal UPI: even without any_amount the QR never carries an amount (apps block pre-filled amounts)
    assert env.c.get(f"/api/v1/pay/{env.token()}/qr.png").content == env.c.get(f"/api/v1/pay/{env.token()}/qr.png?any_amount=1").content
    # Merchant UPI: the QR carries the exact amount
    env.c.patch("/api/v1/workspace", headers=env.admin, json={"account_type": "MERCHANT"})
    assert env.c.get(f"/api/v1/pay/{env.token()}/qr.png").content != env.c.get(f"/api/v1/pay/{env.token()}/qr.png?any_amount=1").content


def test_open_app_attempt_is_recorded(env):
    r = env.c.post(f"/api/v1/pay/{env.token()}/attempts", json={"method": "MANUAL", "event": "OPEN_APP", "detail": "GPAY"})
    assert r.status_code == 202
    rows = env.c.get("/api/v1/reports/payment-attempts", headers=env.admin).json()["items"]
    assert rows[0]["event"] == "OPEN_APP" and rows[0]["detail"] == "GPAY"


def test_every_element_the_payment_script_uses_exists():
    """An element missing from pay.html would crash the parent page ("Link not available")."""
    js = (STATIC / "js" / "pay.js").read_text(encoding="utf-8")
    html = (STATIC / "pay.html").read_text(encoding="utf-8")
    used = set(re.findall(r'\$\("([A-Za-z0-9_]+)"\)', js))
    ids = set(re.findall(r'id="([A-Za-z0-9_]+)"', html))
    assert used - ids == set(), sorted(used - ids)


def test_every_element_the_admin_script_uses_exists():
    js = (STATIC / "js" / "admin.js").read_text(encoding="utf-8")
    html = (STATIC / "admin.html").read_text(encoding="utf-8")
    used = set(re.findall(r'\$\("([A-Za-z0-9_]+)"\)', js))
    ids = set(re.findall(r'id="([A-Za-z0-9_]+)"', html))
    # admin.js also builds some elements dynamically inside panels
    dynamic = set(re.findall(r'id="([A-Za-z0-9_]+)"', js))
    assert used - ids - dynamic == set(), sorted(used - ids - dynamic)
