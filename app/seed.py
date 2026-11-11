"""Demo/test data. Run:  python -m app.seed   (prints payment links and admin logins)

Test credentials below are for LOCAL DEVELOPMENT ONLY.
"""
from datetime import date, timedelta
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import AdminUser, Device, Installment, Student, Tenant
from app.security import hash_password, sha256_hex
from app.services.links import create_payment_link, link_url
from app.services.reminders import schedule_initial_reminders

DEMO_ADMIN_EMAIL = "admin@brightminds.test"
DEMO_ADMIN_PASSWORD = "feeflow-demo-123"
DEMO_DEVICE_TOKEN = "demo-device-token-brightminds-0001"
OTHER_ADMIN_EMAIL = "admin@sunrise.test"
OTHER_ADMIN_PASSWORD = "feeflow-demo-456"
# An admin with no workspace yet, to try the "Create your workspace" first sign-in locally.
NEW_ADMIN_EMAIL = "founder@newcentre.test"
NEW_ADMIN_PASSWORD = "feeflow-demo-789"

def _tenant(db: Session, **kw) -> Tenant:
    t = Tenant(**kw)
    db.add(t)
    db.flush()
    return t

def seed(db: Session) -> Dict[str, Any]:
    now = utcnow()
    out: Dict[str, Any] = {"links": {}, "installments": {}}

    t1 = _tenant(
        db,
        name="Bright Minds Tuition Centre",
        short_code="BMTC",
        logo_url=None,
        contact_phone="+91 98765 43210",
        contact_email="office@brightminds.test",
        upi_vpa="brightminds@okaxis",
        upi_payee_name="Bright Minds Tuition",
        verification_grace_hours=24,
        reminder_frequency_hours=72,
        ack_sms_enabled=True,
    )
    t2 = _tenant(
        db,
        name="Sunrise Academy",
        short_code="SUNA",
        contact_phone="+91 90000 11111",
        upi_vpa="sunrise@ybl",
        upi_payee_name="Sunrise Academy",
    )
    a1 = AdminUser(tenant_id=t1.id, email=DEMO_ADMIN_EMAIL, name="Priya (Admin)", password_hash=hash_password(DEMO_ADMIN_PASSWORD))
    a2 = AdminUser(tenant_id=t2.id, email=OTHER_ADMIN_EMAIL, name="Sunrise Admin", password_hash=hash_password(OTHER_ADMIN_PASSWORD))
    a3 = AdminUser(tenant_id=None, email=NEW_ADMIN_EMAIL, name="New Centre Founder", password_hash=hash_password(NEW_ADMIN_PASSWORD))
    db.add_all([a1, a2, a3])
    db.add(Device(tenant_id=t1.id, name="Office Android phone", token_hash=sha256_hex(DEMO_DEVICE_TOKEN)))

    students = [
        ("BM-1001", "Aarav Sharma", "+919800000001"),
        ("BM-1002", "Diya Patel", "+919800000002"),
        ("BM-1003", "Kabir Singh", "+919800000003"),
        ("BM-1004", "Meera Iyer", "+919800000004"),
    ]
    month = now.strftime("%Y-%m")
    due = date.today() + timedelta(days=5)
    for code, name, mobile in students:
        s = Student(tenant_id=t1.id, student_code=code, name=name, parent_name=f"Parent of {name.split()[0]}", parent_mobile=mobile)
        db.add(s)
        db.flush()
        inst = Installment(
            tenant_id=t1.id, student_id=s.id, description="Monthly tuition fee", billing_month=month,
            total_paise=250000, due_date=due, status="UNPAID",
        )
        db.add(inst)
        db.flush()
        _, token = create_payment_link(db, inst, valid_days=30)
        schedule_initial_reminders(db, inst, t1, start=now + timedelta(days=1))
        out["links"][code] = token
        out["installments"][code] = inst.id

    # Other tenant (for isolation tests)
    s2 = Student(tenant_id=t2.id, student_code="SA-1", name="Rohan Das", parent_mobile="+919811111111")
    db.add(s2)
    db.flush()
    inst2 = Installment(
        tenant_id=t2.id, student_id=s2.id, description="Term fee", billing_month=month,
        total_paise=500000, due_date=due, status="UNPAID",
    )
    db.add(inst2)
    db.flush()
    _, token2 = create_payment_link(db, inst2)
    out["links"]["SA-1"] = token2
    out["installments"]["SA-1"] = inst2.id

    # An expired link for BM-1004

    _, expired = create_payment_link(db, db.get(Installment, out["installments"]["BM-1004"]), expires_at=now - timedelta(days=1))
    out["links"]["BM-1004-expired"] = expired

    db.commit()
    out["tenant_ids"] = {"t1": t1.id, "t2": t2.id}
    return out

if __name__ == "__main__":  # pragma: no cover
    import sys

    from app.config import settings
    from app.db import SessionLocal, create_all, get_engine, init_engine
    from app.db import Base

    init_engine(settings.database_url)
    if "--reset" in sys.argv:
        from app import models  # noqa: F401

        Base.metadata.drop_all(get_engine())
    create_all()
    db = SessionLocal()
    if db.query(Tenant).count():
        print("Database already seeded. Use `python -m app.seed --reset` to start over.")
        sys.exit(0)
    data = seed(db)
    print("\nFeeFlow demo data created.\n")
    print(f"Admin dashboard : {settings.public_base_url}/admin")
    print(f"  login         : {DEMO_ADMIN_EMAIL} / {DEMO_ADMIN_PASSWORD}")
    print(f"  no workspace  : {NEW_ADMIN_EMAIL} / {NEW_ADMIN_PASSWORD}  (try the workspace setup)")
    print(f"Device token    : {DEMO_DEVICE_TOKEN}\n")
    print("Parent payment links:")
    for code, token in data["links"].items():
        print(f"  {code:16s} {link_url(token)}")
