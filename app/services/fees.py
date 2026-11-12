"""Fees: monthly fee generation from student profiles, one-off fees, and sending fee links."""
import calendar
import logging
from datetime import date, datetime, time, timedelta
from typing import Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import audit
from app.db import utcnow
from app.models import Device, Installment, Student, Tenant
from app.services.links import create_payment_link, link_url
from app.services.reminders import schedule_initial_reminders
from app.services.sms import enqueue_sms, format_rupees
from app.services.subscription import WRITABLE, effective_status

log = logging.getLogger("feeflow.fees")

MONTHLY_DESCRIPTION = "Monthly tuition fee"


def month_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def due_date_for(billing_month: str, due_day: int) -> date:
    y, m = (int(x) for x in billing_month.split("-"))
    return date(y, m, min(due_day, calendar.monthrange(y, m)[1]))


def link_sms_text_params(inst: Installment, url: str) -> dict:
    return {
        "amount": format_rupees(inst.outstanding_paise),
        "student_name": inst.student.name,
        "description": inst.description,
        "due_date": inst.due_date.strftime("%d %b %Y"),
        "payment_link": url,
    }


def create_fee(
    db: Session,
    *,
    tenant: Tenant,
    student: Student,
    actor_id: Optional[str],
    description: str,
    billing_month: str,
    total_paise: int,
    due_date: date,
    kind: str = "ONE_OFF",
    send_link_sms: bool = False,
    schedule_reminders: bool = True,
    link_valid_days: int = 45,
) -> Tuple[Installment, str]:
    """Create an installment + its secure link (+ optional SMS and reminder schedule). No commit."""
    inst = Installment(tenant_id=tenant.id, student_id=student.id, description=description,
                       billing_month=billing_month, total_paise=total_paise, due_date=due_date,
                       status="UNPAID", kind=kind)
    db.add(inst)
    db.flush()
    _, token = create_payment_link(db, inst, valid_days=link_valid_days)
    url = link_url(token)
    if schedule_reminders:
        # First reminder at 9:00 IST (03:30 UTC) the day before the due date, but never in the past.
        first = datetime.combine(due_date - timedelta(days=1), time(3, 30))
        schedule_initial_reminders(db, inst, tenant, start=max(first, utcnow() + timedelta(hours=1)))
    if send_link_sms:
        enqueue_sms(db, tenant=tenant, to_mobile=student.parent_mobile, kind="FEE_REMINDER",
                    dedupe_key=f"fee-link:{inst.id}", installment_id=inst.id, **link_sms_text_params(inst, url))
    audit(db, tenant_id=tenant.id, actor_type="ADMIN" if actor_id else "SYSTEM", actor_id=actor_id,
          action="INSTALLMENT_CREATED", source="monthly_plan" if kind == "MONTHLY" else "admin",
          installment_id=inst.id,
          details={"total_paise": total_paise, "due_date": due_date, "billing_month": billing_month,
                   "kind": kind, "link_sms": send_link_sms})
    return inst, url


def create_monthly_fee(db: Session, tenant: Tenant, student: Student, billing_month: str,
                       actor_id: Optional[str] = None) -> Optional[Installment]:
    """This month's fee from the student's plan; None if no plan or it already exists. No commit."""
    if not student.active or not student.monthly_fee_paise or not student.fee_due_day:
        return None
    exists = (db.query(Installment.id)
              .filter(Installment.student_id == student.id, Installment.billing_month == billing_month,
                      Installment.kind == "MONTHLY")
              .first())
    if exists:
        return None
    auto = bool(tenant.auto_send_links)
    inst, _ = create_fee(
        db, tenant=tenant, student=student, actor_id=actor_id, description=MONTHLY_DESCRIPTION,
        billing_month=billing_month, total_paise=student.monthly_fee_paise,
        due_date=due_date_for(billing_month, student.fee_due_day), kind="MONTHLY",
        # Automatic sending is a workspace setting (off by default: use the "Send fee link" button).
        send_link_sms=auto, schedule_reminders=auto,
    )
    return inst


def generate_monthly_fees(db: Session, today: Optional[date] = None) -> int:
    """Create the current month's fee for every student on a monthly plan (idempotent)."""
    today = today or utcnow().date()
    billing_month = month_of(today)
    last_day = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
    created = 0
    for tenant in db.query(Tenant).all():
        if effective_status(tenant) not in WRITABLE:
            continue
        students = (db.query(Student)
                    .filter(Student.tenant_id == tenant.id, Student.active.is_(True),
                            Student.monthly_fee_paise.isnot(None), Student.fee_due_day.isnot(None))
                    .all())
        for st in students:
            if st.joined_on and st.joined_on > last_day:
                continue
            try:
                with db.begin_nested():
                    if create_monthly_fee(db, tenant, st, billing_month) is not None:
                        created += 1
            except IntegrityError:  # another worker created it first (unique index)
                pass
    db.commit()
    if created:
        log.info("Generated %s monthly fees for %s", created, billing_month)
    return created


def latest_unpaid_fee(db: Session, student: Student) -> Optional[Installment]:
    return (db.query(Installment)
            .filter(Installment.student_id == student.id, Installment.status != "PAID")
            .order_by(Installment.due_date.desc(), Installment.created_at.desc())
            .first())


def sms_phone_active(db: Session, tenant_id: str) -> bool:
    return db.query(Device.id).filter(Device.tenant_id == tenant_id, Device.active.is_(True)).first() is not None


def prepare_fee_link(db: Session, tenant: Tenant, student: Student, actor_id: str) -> Tuple[Installment, str]:
    """Latest unpaid fee (creating this month's fee from the plan if needed) and a fresh secure link."""
    inst = latest_unpaid_fee(db, student)
    if inst is None:
        inst = create_monthly_fee(db, tenant, student, month_of(utcnow().date()), actor_id=actor_id)
    if inst is None:
        return None, None  # type: ignore[return-value]
    _, token = create_payment_link(db, inst, valid_days=45)
    return inst, link_url(token)


def share_message(inst: Installment, url: str, centre_name: str) -> str:
    p = link_sms_text_params(inst, url)
    return (f"Dear Parent, fee of Rs. {p['amount']} for {p['student_name']} ({p['description']}) is due on "
            f"{p['due_date']}. Pay here: {url} - {centre_name}")
