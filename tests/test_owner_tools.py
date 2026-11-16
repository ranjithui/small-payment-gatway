"""v2.7 owner tools: dashboard, bulk reminders, bulk fees, SMS health, search, warnings, overpayments, family."""
import uuid
from datetime import date, timedelta

from app.db import SessionLocal
from app.models import Installment, PaymentAdjustment, SmsOutbox, Student


def key():
    return "k-" + uuid.uuid4().hex


def H(env):
    return {**env.admin, "Idempotency-Key": key()}


def _set_due(env, code, days):
    db = SessionLocal()
    i = db.get(Installment, env.inst(code))
    i.due_date = date.today() + timedelta(days=days)
    db.commit(); db.close()


def test_dashboard_numbers_and_pending_actions(env):
    d = env.c.get("/api/v1/dashboard", headers=env.admin).json()
    s = d["stats"]
    assert s["students_total"] == 4 and s["fees_generated_paise"] == 4 * 250000
    assert s["awaiting_verification"] == 0 and s["collected_today_paise"] == 0
    kinds = {a["kind"] for a in d["pending_actions"]}
    assert "NOT_REMINDED" in kinds and "VERIFY" not in kinds

    env.declare(action="PAID")
    d = env.c.get("/api/v1/dashboard", headers=env.admin).json()
    assert d["stats"]["awaiting_verification"] == 1
    verify = [a for a in d["pending_actions"] if a["kind"] == "VERIFY"][0]
    assert verify["text"] == "1 payment waiting for verification" and verify["button"] == "Verify now"

    env.confirm(env.decl_id(), [{"amount_paise": 250000, "bank_reference": "UTR-DASH-1"}])
    d = env.c.get("/api/v1/dashboard", headers=env.admin).json()
    assert d["stats"]["collected_today_paise"] == 250000 and d["stats"]["collected_paise"] == 250000
    assert d["recent_payments"][0]["student_name"] == "Aarav Sharma"
    assert env.c.get("/api/v1/dashboard").status_code == 401
    # other centre sees only its own numbers
    assert env.c.get("/api/v1/dashboard", headers=env.other_admin).json()["stats"]["students_total"] == 1


def test_bulk_reminders_skip_parents_awaiting_verification_and_track_progress(env):
    for code in ("BM-1002", "BM-1003"):
        _set_due(env, code, -3)
    env.declare("BM-1003", action="PAID")  # already reported paying: must not be chased
    body = {"target": "OVERDUE"}
    p = env.c.post("/api/v1/bulk/reminders/preview", json=body, headers=env.admin).json()
    assert p["count"] == 1 and p["recipients"][0]["student_name"] == "Diya Patel"
    assert p["recipients"][0]["overdue"] is True and "UPI ID:" in p["sample_message"] and p["sms_parts"] >= 1
    assert env.c.post("/api/v1/bulk/reminders/preview", json={"target": "CLASS"}, headers=env.admin).status_code == 422

    k = key()
    r = env.c.post("/api/v1/bulk/reminders/send", json=body, headers={**env.admin, "Idempotency-Key": k})
    assert r.status_code == 201 and r.json()["queued"] == 1
    batch = r.json()["batch_id"]
    again = env.c.post("/api/v1/bulk/reminders/send", json=body, headers={**env.admin, "Idempotency-Key": k})
    assert again.json()["batch_id"] == batch  # double tap: one batch only
    st = env.c.get(f"/api/v1/bulk/batches/{batch}", headers=env.admin).json()
    assert st["total"] == 1 and st["queued"] == 1 and not st["done"]

    # The SMS phone picks it up, the send fails, the owner retries
    sync = env.c.get("/api/v1/devices/sync", headers=env.device).json()
    msg = [m for m in sync["sms"] if m["kind"] == "FEE_REMINDER"][0]
    env.c.post("/api/v1/devices/ack", json={"sms": [{"id": msg["id"], "status": "FAILED", "error": "No signal"}]},
               headers=env.device)
    st = env.c.get(f"/api/v1/bulk/batches/{batch}", headers=env.admin).json()
    assert st["failed"] == 1 and st["done"] and st["failed_items"][0]["student_name"] == "Diya Patel"
    health = env.c.get("/api/v1/sms/health", headers=env.admin).json()
    assert health["failed_count"] == 1 and "Retry" in health["advice"]
    rr = env.c.post("/api/v1/sms/retry-failed", json={"batch_id": batch}, headers=H(env)).json()
    assert rr["resent"] == 1
    st = env.c.get(f"/api/v1/bulk/batches/{batch}", headers=env.admin).json()
    assert st["total"] == 1 and st["failed"] == 0 and st["queued"] == 1
    assert env.c.get("/api/v1/sms/health", headers=env.admin).json()["failed_count"] == 0
    # another centre cannot see the batch
    assert env.c.get(f"/api/v1/bulk/batches/{batch}", headers=env.other_admin).status_code == 404


def test_bulk_one_off_fees_with_preview_and_no_duplicates(env):
    body = {"description": "Exam fee", "amount_paise": 50000, "billing_month": "2026-12",
            "due_date": "2026-12-10", "target": "ALL"}
    p = env.c.post("/api/v1/bulk/fees/preview", json=body, headers=env.admin).json()
    assert p["count"] == 4 and p["duplicates"] == 0 and p["total_paise"] == 200000
    ex = env.c.post("/api/v1/bulk/fees/preview", json={**body, "exclude_ids": [p["rows"][0]["student_id"]]},
                    headers=env.admin).json()
    assert ex["count"] == 3
    r = env.c.post("/api/v1/bulk/fees/create", json=body, headers=H(env))
    assert r.status_code == 201 and r.json() == {"created": 4, "skipped_duplicates": 0}
    p = env.c.post("/api/v1/bulk/fees/preview", json={**body, "description": "exam FEE "}, headers=env.admin).json()
    assert p["count"] == 0 and p["duplicates"] == 4
    assert env.c.post("/api/v1/bulk/fees/create", json=body, headers=H(env)).json()["created"] == 0


def test_generate_missing_monthly_fees(env):
    db = SessionLocal()
    st = db.query(Student).filter_by(student_code="BM-1002").one()
    st.monthly_fee_paise, st.fee_due_day = 200000, 10
    db.commit(); db.close()
    d = env.c.get("/api/v1/dashboard", headers=env.admin).json()
    assert any(a["kind"] == "GENERATE" and a["count"] == 1 for a in d["pending_actions"])
    assert env.c.post("/api/v1/fees/generate-monthly", headers=H(env)).json()["created"] == 1
    assert env.c.post("/api/v1/fees/generate-monthly", headers=H(env)).json()["created"] == 0


def test_search_filters_and_warnings_before_confirming(env):
    env.declare(action="PAID", upi_reference="77778888")
    s = lambda **p: env.c.get("/api/v1/payment-verification/search", params=p, headers=env.admin).json()["items"]
    assert len(s(q="aarav")) == 1 and len(s(utr="77778888")) == 1 and len(s(amount=2500)) == 1
    assert s(q="nobody") == [] and s(status="VERIFIED") == [] and s(amount=999) == []
    assert s(q="aarav")[0]["class_grade"] is None and "student_ref" in s(q="aarav")[0]
    assert len(s(date_from=(date.today() - timedelta(days=1)).isoformat())) == 1

    env.confirm(env.decl_id(), [{"amount_paise": 250000, "bank_reference": "77778888"}])
    assert len(s(status="VERIFIED", utr="77778888")) == 1
    chk = env.c.get("/api/v1/verified-payments/check-reference", params={"ref": "77778888"}, headers=env.admin).json()
    assert chk["used"] and "Aarav Sharma" in chk["message"]
    assert env.c.get("/api/v1/verified-payments/check-reference", params={"ref": "77778888"},
                     headers=env.other_admin).json() == {"used": False}

    # Another parent quotes the same transaction ID and a different amount
    env.declare("BM-1002", action="PAID", upi_reference="77778888")
    d = env.c.get(f"/api/v1/payment-declarations/{env.decl_id('BM-1002')}", headers=env.admin).json()
    texts = " | ".join(w["text"] for w in d["warnings"])
    assert "already recorded for another payment" in texts
    env.declare("BM-1003", action="PAID", payment_type="PARTIAL", declared_amount_paise=100000)
    d = env.c.get(f"/api/v1/payment-declarations/{env.decl_id('BM-1003')}", headers=env.admin).json()
    assert d["warnings"][0]["level"] == "info" and "Part payments are allowed" in d["warnings"][0]["text"]


def test_overpayment_applied_to_next_fee_is_not_counted_twice(env):
    r = env.c.post(f"/api/v1/installments/{env.inst()}/payments", json={
        "confirm": True, "bank_transaction_checked": True, "belongs_to_centre": True,
        "transactions": [{"amount_paise": 300000, "bank_reference": "UTR-OVER-1"}]}, headers=H(env))
    assert r.status_code == 201
    adj = env.c.get("/api/v1/adjustments", headers=env.admin).json()["items"]
    assert len(adj) == 1 and adj[0]["amount_paise"] == 50000
    assert env.c.post(f"/api/v1/adjustments/{adj[0]['id']}/apply", json={}, headers=H(env)).status_code == 409

    db = SessionLocal()
    sid = db.get(Installment, env.inst()).student_id
    db.close()
    env.c.post("/api/v1/installments", json={"student_id": sid, "description": "Monthly tuition fee",
               "billing_month": "2099-01", "total_paise": 250000, "due_date": "2099-01-10",
               "send_link_sms": False, "schedule_reminders": False}, headers=H(env))
    k = key()
    out = env.c.post(f"/api/v1/adjustments/{adj[0]['id']}/apply", json={}, headers={**env.admin, "Idempotency-Key": k})
    assert out.status_code == 200, out.text
    j = out.json()
    assert j["status"] == "ADJUSTED" and j["applied_to"]["outstanding_paise"] == 200000
    assert env.c.post(f"/api/v1/adjustments/{adj[0]['id']}/apply", json={},
                      headers={**env.admin, "Idempotency-Key": k}).json() == j  # replay
    assert env.c.post(f"/api/v1/adjustments/{adj[0]['id']}/apply", json={}, headers=H(env)).status_code == 409
    db = SessionLocal()
    assert db.get(Installment, env.inst()).excess_paise == 0
    assert db.query(PaymentAdjustment).filter_by(status="FLAGGED").count() == 0
    db.close()
    d = env.c.get("/api/v1/dashboard", headers=env.admin).json()
    assert d["stats"]["collected_today_paise"] == 300000  # the moved Rs.500 is not new money


def test_family_page_shows_all_children_and_links_only_within_the_family(env):
    r = env.c.post("/api/v1/students", json={"name": "Anika Sharma", "parent_mobile": "+919800000001",
                                             "class_grade": "Class 3", "monthly_fee_paise": 150000, "fee_due_day": 10},
                   headers=H(env))
    assert r.status_code == 201, r.text
    v = env.view().json()
    assert v["student"]["parent_name"] == "Parent of Aarav"
    kids = {c["name"]: c for c in v["family"]["children"]}
    assert set(kids) == {"Aarav Sharma", "Anika Sharma"} and kids["Aarav Sharma"]["current"]
    sib_fee = kids["Anika Sharma"]["fees"][0]
    assert "id" not in sib_fee and sib_fee["reference"]
    link = env.c.post(f"/api/v1/pay/{env.token()}/family-link", json={"reference": sib_fee["reference"]}).json()
    assert link["same"] is False and "/p/" in link["url"]
    sib = env.c.get(f"/api/v1/payment-declarations/public/{link['url'].rsplit('/', 1)[1]}").json()
    assert sib["student"]["name"] == "Anika Sharma"
    # Another family's fee can never be opened from this link
    other_ref = env.view("BM-1002").json()["payment_reference"]
    assert env.c.post(f"/api/v1/pay/{env.token()}/family-link", json={"reference": other_ref}).status_code == 404
    assert len(env.view("BM-1002").json()["family"]["children"]) == 1
