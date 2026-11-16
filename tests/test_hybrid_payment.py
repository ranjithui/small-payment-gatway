"""UPI intent + dynamic QR + manual payment: references, validation, QR, diagnostics, remarks."""
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from app.db import SessionLocal, utcnow
from app.models import Installment, PaymentAttempt, SmsOutbox, Tenant
from app.services.fees import create_fee
from app.services.references import backfill_references
from app.services.upi import validate_upi_uri
from tests.conftest import key

ANDROID_UA = "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36"
IPHONE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"
DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"


def q(uri):
    return {k: v[0] for k, v in parse_qs(urlparse(uri).query).items()}


def _tenant(db, code="BMTC"):
    return db.query(Tenant).filter_by(short_code=code).one()


# ------------------------------------------------------------------ Feature A: UPI link
def test_upi_link_uses_centre_settings_amount_and_reference(env):
    v = env.view().json()
    u = v["upi"]
    p = q(u["uris"]["OTHER_UPI"])
    assert u["uris"]["OTHER_UPI"].startswith("upi://pay?")  # generic: Android lets the parent choose the app
    assert p == {"pa": "brightminds@okaxis", "pn": "Bright Minds Tuition", "am": "2500.00", "cu": "INR",
                 "tn": v["payment_reference"]}
    assert v["payment_reference"] == "BM-1001-" + v["fee"]["billing_month"]
    assert u["reference"] == v["payment_reference"] and v["upi_error"] is None
    # same payment details in the QR link
    assert u["uris"]["QR"].split("?", 1)[1] == u["uris"]["OTHER_UPI"].split("?", 1)[1]
    assert validate_upi_uri(u["uris"]["QR"], vpa="brightminds@okaxis", payee_name="Bright Minds Tuition",
                            amount_paise=250000, reference=v["payment_reference"]) == []


def test_validator_catches_tampering():
    good = "upi://pay?pa=a.b@okaxis&pn=Centre&am=10.00&cu=INR&tn=ST-1-2026-11"
    ok = dict(vpa="a.b@okaxis", payee_name="Centre", amount_paise=1000, reference="ST-1-2026-11")
    assert validate_upi_uri(good, **ok) == []
    assert "amount does not match the fee" in validate_upi_uri(good.replace("10.00", "1.00"), **ok)
    assert any("payee" in x for x in validate_upi_uri(good.replace("a.b@okaxis", "thief@ybl"), **ok))
    assert any("reference" in x for x in validate_upi_uri(good.replace("tn=ST-1-2026-11", "tn=X"), **ok))
    assert any("upi://pay" in x for x in validate_upi_uri("https://evil.example/pay?" + good.split("?")[1], **ok))


def test_invalid_centre_upi_id_disables_payment_safely(env):
    db = SessionLocal()
    _tenant(db).upi_vpa = "not-a-upi-id"  # e.g. data entered before validation existed
    db.commit()
    db.close()
    v = env.view().json()
    assert v["can_pay"] is True and v["upi"] is None
    assert "not set up correctly" in v["upi_error"]
    assert env.c.get(f"/api/v1/pay/{env.token()}/qr.png").status_code == 409
    assert env.c.get(f"/api/v1/pay/{env.token()}/qr.svg").status_code == 409


def test_payment_references_are_unique_and_backfilled(env):
    db = SessionLocal()
    inst = db.get(Installment, env.inst())
    month = inst.billing_month
    second, _ = create_fee(db, tenant=inst.tenant, student=inst.student, actor_id=None, description="Exam fee",
                           billing_month=month, total_paise=50000, due_date=inst.due_date)
    db.commit()
    assert second.payment_reference == f"BM-1001-{month}-2"
    # Existing fees without a reference get one on start-up
    db.query(Installment).update({Installment.payment_reference: None})
    db.commit()
    assert backfill_references(db) >= 5
    refs = [r for (r,) in db.query(Installment.payment_reference).filter(Installment.tenant_id == inst.tenant_id)]
    assert None not in refs and len(refs) == len(set(refs))
    db.close()


# ------------------------------------------------------------------ Feature B: QR
def test_qr_png_standard_and_high_resolution(env):
    std = env.c.get(f"/api/v1/pay/{env.token()}/qr.png")
    big = env.c.get(f"/api/v1/pay/{env.token()}/qr.png?size=large&download=1")
    assert std.status_code == big.status_code == 200
    assert std.content[:8] == big.content[:8] == b"\x89PNG\r\n\x1a\n"
    w = lambda b: int.from_bytes(b[16:20], "big")  # PNG IHDR width
    assert w(std.content) < 500 < 900 < w(big.content)
    ref = env.view().json()["payment_reference"]
    assert big.headers["content-disposition"] == f'attachment; filename="FeeFlow-UPI-QR-{ref}.png"'
    assert env.c.get(f"/api/v1/pay/{env.token()}/qr.png?size=huge").status_code == 422


def test_qr_not_available_for_expired_link(env):
    assert env.c.get(f"/api/v1/pay/{env.token('BM-1004-expired')}/qr.png").status_code == 410


# ------------------------------------------------------------------ Feature C: manual payment data
def test_manual_payment_details_come_from_server(env):
    v = env.view().json()
    u = v["upi"]
    assert u["payee_vpa"] == "brightminds@okaxis" and u["payee_name"] == "Bright Minds Tuition"
    assert u["amount_text"] == "2500.00" and u["reference"] == v["payment_reference"]
    assert v["centre"]["name"] == "Bright Minds Tuition Centre" and v["student"]["student_id"] == "BM-1001"
    assert "id" not in v["student"] and env.inst() not in str(v)  # no internal IDs


# ------------------------------------------------------------------ Feature D: declaration
def test_declaration_message_and_amount_from_server(env):
    # A parent cannot change the amount of a full payment
    r = env.declare(action="PAID", payment_type="FULL", declared_amount_paise=100)
    assert r.status_code == 201
    v = r.json()
    assert v["declaration"]["declared_amount_paise"] == 250000
    assert v["message"].startswith("Thank you! Your payment declaration has been submitted. The tuition centre will "
                                   "verify your payment and update your fee status.")
    assert v["fee"]["paid_paise"] == 0 and v["installment_status"] == "UNPAID"
    assert env.declare(action="PAID").json()["duplicate"] is True
    failed = env.declare("BM-1002", action="FAILED").json()
    assert failed["message"].startswith("Your payment could not be completed. Please try again or use another payment method.")


# ------------------------------------------------------------------ Feature F: diagnostics
def _attempt(env, code="BM-1001", ua=ANDROID_UA, **body):
    return env.c.post(f"/api/v1/pay/{env.token(code)}/attempts", json=body, headers={"User-Agent": ua})


def test_payment_attempts_are_recorded_without_sensitive_data(env):
    assert _attempt(env, method="UPI_INTENT", event="STARTED").status_code == 202
    assert _attempt(env, method="UPI_INTENT", event="NO_APP").status_code == 202
    assert _attempt(env, ua=IPHONE_UA, method="QR", event="QR_DOWNLOADED", detail="large").status_code == 202
    assert _attempt(env, ua=DESKTOP_UA, method="MANUAL", event="COPIED", detail="vpa").status_code == 202
    assert _attempt(env, method="CARD", event="STARTED").status_code == 422
    assert _attempt(env, method="MANUAL", event="COPIED", detail="my upi pin 1234").status_code == 422
    assert _attempt(env, method="MANUAL", event="COPIED", detail="<script>").status_code == 422
    assert _attempt(env, "BM-1004-expired", method="QR", event="QR_SHOWN").status_code == 410

    db = SessionLocal()
    rows = db.query(PaymentAttempt).filter_by(installment_id=env.inst()).order_by(PaymentAttempt.created_at).all()
    assert [(a.method, a.event, a.device) for a in rows] == [
        ("UPI_INTENT", "STARTED", "ANDROID"), ("UPI_INTENT", "NO_APP", "ANDROID"),
        ("QR", "QR_DOWNLOADED", "IOS"), ("MANUAL", "COPIED", "DESKTOP")]
    assert all(a.payment_reference == rows[0].payment_reference and not a.declared for a in rows)
    db.close()

    env.declare(action="PAID")  # declaring links the earlier attempts
    db = SessionLocal()
    assert all(a.declared for a in db.query(PaymentAttempt).filter_by(installment_id=env.inst()))
    db.close()

    # Admin sees them on the declaration and in the report; another centre never does
    detail = env.c.get(f"/api/v1/payment-declarations/{env.decl_id()}", headers=env.admin).json()
    assert len(detail["payment_attempts"]) == 4 and detail["payment_reference"].startswith("BM-1001-")
    report = env.c.get("/api/v1/reports/payment-attempts", headers=env.admin).json()["items"]
    assert len(report) == 4 and {r["declared"] for r in report} == {"yes"}
    assert env.c.get("/api/v1/reports/payment-attempts", headers=env.other_admin).json()["items"] == []


def test_attempt_logging_is_capped_per_fee(env, monkeypatch):
    from app.routers import public

    monkeypatch.setattr(public, "MAX_ATTEMPTS_PER_FEE", 3)
    results = [_attempt(env, method="QR", event="QR_SHOWN").json()["recorded"] for _ in range(5)]
    assert results == [True, True, True, False, False]


# ------------------------------------------------------------------ Feature E: admin verification
def test_admin_remarks_are_internal_appended_and_audited(env):
    env.declare(action="PAID")
    decl_id = env.decl_id()
    url = f"/api/v1/payment-verification/{decl_id}/remarks"
    k = key()
    r1 = env.c.post(url, json={"remark": "Called parent, will check HDFC statement"}, headers={**env.admin, "Idempotency-Key": k})
    assert r1.status_code == 200
    replay = env.c.post(url, json={"remark": "Called parent, will check HDFC statement"}, headers={**env.admin, "Idempotency-Key": k})
    assert replay.json() == r1.json()
    env.c.post(url, json={"remark": "Credit seen 11:05"}, headers={**env.admin, "Idempotency-Key": key()})
    note = env.c.get(f"/api/v1/payment-declarations/{decl_id}", headers=env.admin).json()["admin_note"]
    assert note.count("\n") == 1 and "HDFC statement" in note and "Credit seen 11:05" in note and "Priya" in note
    assert "HDFC" not in str(env.view().json())  # never shown to parents
    assert env.c.post(url, json={"remark": "xy"}, headers={**env.other_admin, "Idempotency-Key": key()}).status_code == 404
    logs = env.c.get(f"/api/v1/audit-logs?declaration_id={decl_id}", headers=env.admin).json()["items"]
    assert sum(1 for a in logs if a["action"] == "ADMIN_REMARK_ADDED") == 2


def test_end_to_end_confirm_receipt_sms_and_reference(env):
    env.declare(action="PAID")
    decl_id = env.decl_id()
    pending = env.c.get("/api/v1/payment-verification/pending", headers=env.admin).json()["items"]
    assert pending[0]["payment_reference"].startswith("BM-1001-")
    r = env.confirm(decl_id, [{"amount_paise": 250000, "bank_reference": "UTR-HYB-1"}])
    assert r.status_code == 200 and r.json()["installment_status"] == "PAID"
    dup = env.confirm(env.decl_id("BM-1002") if env.declare("BM-1002", action="PAID") else None,
                      [{"amount_paise": 250000, "bank_reference": "UTR-HYB-1"}])
    assert dup.status_code == 409  # a bank reference can never be credited twice
    db = SessionLocal()
    assert db.query(SmsOutbox).filter_by(kind="PAYMENT_VERIFIED").count() == 1
    db.close()
    v = env.view().json()
    assert v["display_status"] == "PAID" and v["upi"] is None and v["receipts"]
    assert env.c.post(f"/api/v1/pay/{env.token()}/attempts", json={"method": "QR", "event": "QR_SHOWN"}).status_code == 202


def test_reject_keeps_fee_outstanding(env):
    env.declare(action="PAID")
    r = env.reject(env.decl_id(), reason="No credit in bank statement")
    assert r.status_code == 200 and r.json()["installment_status"] == "UNPAID"
    assert env.view().json()["can_pay"] is True


# ------------------------------------------------------------------ cache-busting (old cached script + new page)
def test_pages_reference_content_versioned_assets(env):
    import re

    for path in (f"/pay/{env.token()}", "/admin", "/platform"):
        r = env.c.get(path)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        refs = re.findall(r'(/static/[^"\']+\.(?:js|css))(\?v=[0-9a-f]{12})?', r.text)
        assert refs and all(v for _, v in refs), (path, refs)  # every script/style is versioned
    html = env.c.get(f"/pay/{env.token()}").text
    js = re.search(r'/static/js/pay\.js\?v=[0-9a-f]{12}', html).group(0)
    assert env.c.get(js).headers["cache-control"] == "public, max-age=31536000, immutable"
    assert env.c.get("/static/js/pay.js").headers["cache-control"] == "no-cache"


def test_asset_version_changes_when_file_changes(tmp_path, monkeypatch):
    from app.routers import pages

    monkeypatch.setattr(pages, "STATIC", tmp_path)
    (tmp_path / "js").mkdir()
    f = tmp_path / "js" / "x.js"
    f.write_text("v1")
    a = pages.versioned('<script src="/static/js/x.js"></script>')
    import os, time
    f.write_text("v2")
    os.utime(f, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
    b = pages.versioned('<script src="/static/js/x.js"></script>')
    assert a != b and "?v=" in a and "?v=" in b
    assert pages.versioned('<script src="/static/js/missing.js"></script>') == '<script src="/static/js/missing.js"></script>'
