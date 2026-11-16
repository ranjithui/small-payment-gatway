"""Professional SMS formats: fee request with UPI details, acknowledgement, thank-you confirmation."""
import uuid

from app.db import SessionLocal
from app.models import Installment, SmsOutbox, Tenant
from app.services.sms import fee_request_text, month_label


def key():
    return str(uuid.uuid4())


def test_fee_request_sms_shows_upi_details_and_short_link(env):
    db = SessionLocal()
    inst = db.get(Installment, env.inst())
    t = db.get(Tenant, inst.tenant_id)
    t.upi_mobile = "+919876543210"
    db.flush()
    text = fee_request_text(inst, "https://x.test/p/abcdefghijklmnop")
    name, vpa, ref = t.name, t.upi_vpa, inst.payment_reference
    db.rollback(); db.close()
    assert text.startswith(f"{name}: Fee of Rs.")
    assert f"UPI ID: {vpa}" in text
    assert "or Mobile: 98765 43210" in text
    assert "Name shown: " in text
    assert ref and f"Note: {ref}" in text
    assert "After paying, tap to confirm:\nhttps://x.test/p/abcdefghijklmnop" in text
    assert text.endswith("Never share your UPI PIN or OTP.")
    assert "₹" not in text  # rupee sign would force 70-character SMS parts


def test_new_links_are_short_and_both_paths_work(env):
    r = env.c.post(f"/api/v1/installments/{env.inst()}/payment-links", json={}, headers={**env.admin, "Idempotency-Key": key()})
    assert r.status_code in (200, 201), r.text
    url = r.json()["url"]
    token = url.rsplit("/", 1)[-1]
    assert "/p/" in url and len(token) == 16
    assert env.c.get(f"/p/{token}").status_code == 200
    assert env.c.get(f"/pay/{token}").status_code == 200
    assert env.c.get(f"/api/v1/payment-declarations/public/{token}").status_code == 200


def test_ack_and_thank_you_messages(env):
    r = env.declare(action="PAID", upi_reference="12 34")
    assert r.status_code == 201
    db = SessionLocal()
    ack = db.query(SmsOutbox).filter_by(kind="DECLARATION_ACK").one()
    assert "We received your payment report of Rs.2,500" in ack.body
    assert "usually within 24 hours" in ack.body and "Ref: " in ack.body
    from app.models import PaymentDeclaration
    assert db.query(PaymentDeclaration).one().parent_upi_reference == "1234"
    db.close()

    decl_id = env.decl_id()
    r = env.confirm(decl_id, [{"amount_paise": 250000, "bank_reference": "UTR-THANKS-1"}])
    assert r.status_code == 200, r.text
    db = SessionLocal()
    v = db.query(SmsOutbox).filter_by(kind="PAYMENT_VERIFIED").one().body
    db.close()
    assert "is confirmed. Thank you!" in v and "/r/" in v and "Balance due" not in v
    receipt_token = v.rsplit("/r/", 1)[1]
    assert env.c.get(f"/r/{receipt_token}").status_code == 200


def test_bad_transaction_id_rejected(env):
    assert env.declare(action="PAID", upi_reference="12;drop").status_code == 422


def test_month_label():
    assert month_label("2026-11") == "Nov 2026"
