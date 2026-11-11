"""Acceptance scenarios A-G from the specification, end to end through the HTTP API."""
from app.db import SessionLocal
from app.models import PaymentDeclaration, ReminderSchedule, SmsOutbox, VerifiedPayment
from tests.conftest import key


def _sms(kind=None):
    db = SessionLocal()
    q = db.query(SmsOutbox)
    if kind:
        q = q.filter(SmsOutbox.kind == kind)
    rows = q.all()
    db.close()
    return rows


def test_scenario_a_normal_payment(env):
    # 1-2. Parent opens link, sees fee details and UPI options
    v = env.view().json()
    assert v["display_status"] == "UNPAID"
    assert v["fee"]["outstanding_paise"] == 250000
    assert v["can_pay"] and set(v["upi"]["uris"]) >= {"GPAY", "PHONEPE", "PAYTM", "OTHER_UPI", "QR"}
    assert v["upi"]["uris"]["OTHER_UPI"].startswith("upi://pay?pa=brightminds@okaxis")
    assert "am=2500.00" in v["upi"]["uris"]["GPAY"] and "cu=INR" in v["upi"]["uris"]["GPAY"]
    assert env.inst() not in str(v) and env.data["tenant_ids"]["t1"] not in str(v)  # no internal IDs exposed

    # Parent picks GPay -> PAYMENT_INITIATED (not proof of payment)
    r = env.declare(action="INITIATED", upi_app="GPAY")
    assert r.status_code == 201 and r.json()["display_status"] == "PAYMENT_INITIATED"
    assert env.pending() == []  # initiated only - nothing for admin to verify yet

    # 5. Taps "I have paid" (one tap, no UTR, no amount)
    r = env.declare(action="PAID")
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["display_status"] == "PAYMENT_REPORTED"
    assert v["awaiting_verification"] and not v["can_pay"] and v["upi"] is None
    assert "Please do not make another payment" in v["message"]
    assert v["declaration"]["upi_app"] == "GPAY"
    assert v["fee"]["outstanding_paise"] == 250000  # not marked paid
    assert v["receipts"] == []  # no receipt yet

    # Acknowledgement SMS queued exactly once; reminders paused
    assert len(_sms("DECLARATION_ACK")) == 1
    st = env.status()
    assert st["installment_status"] == "UNPAID" and st["reminders_paused_until"]

    # 7-8. Admin reviews and confirms with bank evidence
    decl_id = env.decl_id()
    detail = env.c.get(f"/api/v1/payment-declarations/{decl_id}", headers=env.admin).json()
    assert detail["verification_status"] == "PENDING_VERIFICATION"  # opened for review
    assert detail["parent_mobile"] == "+919800000001"
    r = env.confirm(decl_id, [{"amount_paise": 250000, "bank_reference": "UTR111"}])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["installment_status"] == "PAID" and body["outstanding_paise"] == 0
    assert body["receipt"]["receipt_number"] == "BMTC-000001"

    # 9-11. Installment PAID, receipt, reminders cancelled (pending device sync), SMS
    v = env.view().json()
    assert v["display_status"] == "PAID" and not v["can_pay"] and len(v["receipts"]) == 1
    st = env.status()
    assert all(r["status"] == "CANCELLED" and r["sync_status"] == "PENDING_SYNC" for r in st["reminders"])
    verified_sms = _sms("PAYMENT_VERIFIED")
    assert len(verified_sms) == 1 and "Rs. 2,500" in verified_sms[0].body and "/receipt/" in verified_sms[0].body

    # Receipt page renders
    rr = env.c.get(v["receipts"][0]["url"].replace("http://localhost:8000", ""))
    assert rr.status_code == 200 and "PAID IN FULL" in rr.text

    # Audit trail exists for each step
    logs = env.c.get(f"/api/v1/audit-logs?declaration_id={decl_id}", headers=env.admin).json()["items"]
    actions = {a["action"] for a in logs}
    assert {"PARENT_INITIATED", "PARENT_PAID", "DECLARATION_OPENED_FOR_REVIEW", "DECLARATION_VERIFIED",
            "VERIFIED_PAYMENT_RECORDED", "INSTALLMENT_STATUS_CHANGED"} <= actions


def test_scenario_b_parent_did_not_pay(env):
    env.declare(action="PAID")
    decl_id = env.decl_id()
    r = env.reject(decl_id)
    assert r.status_code == 200, r.text
    assert r.json()["declaration"]["verification_status"] == "REJECTED"
    assert r.json()["installment_status"] == "UNPAID"

    v = env.view().json()
    assert v["display_status"] == "REJECTED"
    assert "could not be verified" in v["message"]
    assert v["can_pay"]  # a new declaration is allowed
    assert v["fee"]["outstanding_paise"] == 250000
    assert len(_sms("PAYMENT_REJECTED")) == 1
    assert _sms("PAYMENT_VERIFIED") == []

    # Reminders restored but not immediately (respect frequency)
    st = env.status()
    assert st["reminders_paused_until"] is None
    assert all(r["status"] == "SCHEDULED" for r in st["reminders"])

    # Parent can submit a fresh confirmation after checking their bank
    r = env.declare(action="PAID")
    assert r.status_code == 201 and r.json()["display_status"] == "PAYMENT_REPORTED"


def test_scenario_c_payment_processing(env):
    r = env.declare(action="PROCESSING", upi_app="PHONEPE")
    assert r.status_code == 201
    v = r.json()
    assert v["display_status"] == "PAYMENT_PROCESSING"
    assert "do not repeat the payment" in v["message"]
    # Admin can review processing declarations
    items = env.pending()
    assert len(items) == 1 and items[0]["verification_status"] == "PAYMENT_PROCESSING"
    # Parent later confirms
    r = env.declare(action="PAID")
    assert r.json()["display_status"] == "PAYMENT_REPORTED"
    # Still only one declaration
    db = SessionLocal()
    assert db.query(PaymentDeclaration).filter_by(installment_id=env.inst()).count() == 1
    db.close()


def test_scenario_d_duplicate_clicks(env):
    k = key()
    first = env.declare(action="PAID", idem=k)
    replay = env.declare(action="PAID", idem=k)  # network retry with same key
    other = env.declare(action="PAID")  # a second tap with a new key
    third = env.declare(action="PAID")
    assert first.status_code == 201
    assert replay.status_code == 201 and replay.json() == first.json()
    assert other.status_code == 200 and other.json()["duplicate"] is True
    assert third.json()["declaration"]["reference"] == first.json()["declaration"]["reference"]

    db = SessionLocal()
    assert db.query(PaymentDeclaration).filter_by(installment_id=env.inst()).count() == 1
    assert db.query(VerifiedPayment).count() == 0
    db.close()
    assert len(_sms("DECLARATION_ACK")) == 1  # no duplicate acknowledgement SMS

    # Refresh shows the existing status
    assert env.view().json()["display_status"] == "PAYMENT_REPORTED"

    # Other actions are blocked while pending
    r = env.declare(action="FAILED")
    assert r.status_code == 409


def test_scenario_e_partial_payment(env):
    # Validation of partial amount against outstanding
    r = env.declare(action="PAID", payment_type="PARTIAL")
    assert r.status_code == 422
    r = env.declare(action="PAID", payment_type="PARTIAL", declared_amount_paise=999999)
    assert r.status_code == 422
    r = env.declare(action="PAID", payment_type="PARTIAL", declared_amount_paise=100000)
    assert r.status_code == 201
    assert r.json()["declaration"]["declared_amount_paise"] == 100000

    decl_id = env.decl_id()
    r = env.confirm(decl_id, [{"amount_paise": 100000, "bank_reference": "UTR-P1"}])
    assert r.status_code == 200, r.text
    assert r.json()["installment_status"] == "PARTIALLY_PAID"
    assert r.json()["outstanding_paise"] == 150000

    v = env.view().json()
    assert v["display_status"] == "PARTIALLY_PAID"
    assert v["fee"]["paid_paise"] == 100000 and v["fee"]["outstanding_paise"] == 150000
    assert v["can_pay"] and v["upi"]["amount_paise"] == 150000
    # Reminders continue only for the remaining balance
    st = env.status()
    active = [r for r in st["reminders"] if r["status"] == "SCHEDULED"]
    assert active and all(r["amount_paise"] == 150000 for r in active)
    db = SessionLocal()
    bodies = [r.body for r in db.query(ReminderSchedule).filter_by(installment_id=env.inst(), status="SCHEDULED")]
    db.close()
    assert all("Rs. 1,500" in b for b in bodies)

    # Admin must acknowledge when verified amount differs from declared
    env.declare(action="PAID")
    decl2 = env.decl_id()
    r = env.confirm(decl2, [{"amount_paise": 140000, "bank_reference": "UTR-P2"}])
    assert r.status_code == 422
    r = env.confirm(decl2, [{"amount_paise": 140000, "bank_reference": "UTR-P2"}], amount_difference_acknowledged=True)
    assert r.status_code == 200 and r.json()["outstanding_paise"] == 10000


def test_scenario_f_already_paid(env):
    env.declare(action="PAID")
    env.confirm(env.decl_id(), [{"amount_paise": 250000, "bank_reference": "UTR-F"}])
    # Parent opens the old link again
    v = env.view().json()
    assert v["display_status"] == "PAID"
    assert v["can_pay"] is False and v["upi"] is None
    assert v["receipts"] and v["receipts"][0]["url"].startswith("http")
    assert v["message"].startswith("Payment successfully verified!")
    r = env.declare(action="PAID")
    assert r.status_code == 409
    assert env.c.get(f"/api/v1/pay/{env.token()}/qr.svg").status_code == 409


def test_scenario_g_duplicate_actual_transfer(env):
    env.declare(action="PAID")
    decl_id = env.decl_id()
    # Parent genuinely paid twice; admin records both bank credits separately
    r = env.confirm(
        decl_id,
        [{"amount_paise": 250000, "bank_reference": "UTR-G1"}, {"amount_paise": 250000, "bank_reference": "UTR-G2"}],
        amount_difference_acknowledged=True,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["installment_status"] == "PAID"
    assert body["excess_flagged_paise"] == 250000
    assert len(body["payments"]) == 2

    excess = env.c.get("/api/v1/reports/excess-payments", headers=env.admin).json()["items"]
    assert len(excess) == 1 and excess[0]["status"] == "FLAGGED" and excess[0]["amount_paise"] == 250000
    dup = env.c.get("/api/v1/reports/duplicate-payments", headers=env.admin).json()["items"]
    assert len(dup) == 1 and dup[0]["verified_payment_count"] == 2

    # Resolve the excess as refunded
    r = env.c.post(
        f"/api/v1/adjustments/{excess[0]['adjustment_id']}/resolve",
        json={"status": "REFUNDED", "note": "Refunded via UPI"},
        headers=env.admin,
    )
    assert r.status_code == 200


def test_scenario_g_second_transfer_after_paid(env):
    env.declare(action="PAID")
    env.confirm(env.decl_id(), [{"amount_paise": 250000, "bank_reference": "UTR-X1"}])
    # Later the admin spots a second genuine credit and records it without a declaration
    r = env.c.post(
        f"/api/v1/installments/{env.inst()}/payments",
        json={
            "confirm": True, "bank_transaction_checked": True, "belongs_to_centre": True,
            "transactions": [{"amount_paise": 250000, "bank_reference": "UTR-X2"}],
        },
        headers={**env.admin, "Idempotency-Key": key()},
    )
    assert r.status_code == 201, r.text
    assert r.json()["excess_flagged_paise"] == 250000
    st = env.status()
    assert st["paid_paise"] == 250000 and st["excess_paise"] == 250000
