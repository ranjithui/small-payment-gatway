"""Owner tools: dashboard, bulk reminders, bulk one-off fees, SMS health, verification search,
pre-confirmation warnings, applying an overpayment to the next fee, and the parent's family view.

Nothing here ever marks a fee as paid on its own: money is only recorded through
verification.apply_transactions, after an administrator's explicit action.
"""
import math
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.audit import audit
from app.db import utcnow
from app.models import (
    Device,
    Installment,
    PaymentAdjustment,
    PaymentDeclaration,
    Receipt,
    ReminderSchedule,
    SmsOutbox,
    Student,
    Tenant,
    VerifiedPayment,
    new_id,
)
from app.security import AdminContext
from app.services import fees
from app.services.declarations import _iso
from app.services.links import create_payment_link, link_url
from app.services.receipts import receipt_url
from app.services.sms import enqueue_sms, fee_request_text, format_rupees, month_label, sms_out
from app.states import REVIEWABLE_STATUSES

IST = timedelta(hours=5, minutes=30)
REVIEWABLE = [s.value for s in REVIEWABLE_STATUSES]
# Money moved from one fee to another is not new money: never count it as "collected".
TRANSFER_SOURCE = "EXCESS_TRANSFER"


def ist_today() -> date:
    return (utcnow() + IST).date()


def ist_day_start_utc(d: date) -> datetime:
    return datetime.combine(d, time()) - IST


def sms_parts(text: str) -> int:
    """GSM-7 SMS parts (160 characters, or 153 per part when split). Non-GSM text uses 70 / 67."""
    gsm = all(ord(c) < 128 for c in text)
    single, multi = (160, 153) if gsm else (70, 67)
    n = len(text)
    return 1 if n <= single else math.ceil(n / multi)


# ------------------------------------------------------------------ SMS helpers
def _resent_ids(db: Session, tenant_id: str, since: datetime) -> Set[str]:
    rows = (db.query(SmsOutbox.dedupe_key)
            .filter(SmsOutbox.tenant_id == tenant_id, SmsOutbox.dedupe_key.like("resend:%"),
                    SmsOutbox.created_at >= since)
            .all())
    return {k.split(":")[1] for (k,) in rows if k.count(":") >= 2}


def unresolved_failed(db: Session, tenant_id: str, days: int = 30, batch_id: Optional[str] = None) -> List[SmsOutbox]:
    """Failed messages that have not been sent again yet."""
    since = utcnow() - timedelta(days=days)
    q = db.query(SmsOutbox).filter(SmsOutbox.tenant_id == tenant_id, SmsOutbox.status == "FAILED",
                                   SmsOutbox.created_at >= since)
    if batch_id:
        q = q.filter(SmsOutbox.batch_id == batch_id)
    resent = _resent_ids(db, tenant_id, since)
    return [m for m in q.order_by(SmsOutbox.created_at.desc()).all() if m.id not in resent]


def resend(db: Session, admin: AdminContext, old: SmsOutbox) -> SmsOutbox:
    new = SmsOutbox(tenant_id=old.tenant_id, to_mobile=old.to_mobile, body=old.body, kind=old.kind,
                    dedupe_key=f"resend:{old.id}:{utcnow().isoformat()}", installment_id=old.installment_id,
                    declaration_id=old.declaration_id, batch_id=old.batch_id)
    db.add(new)
    db.flush()
    audit(db, tenant_id=admin.tenant_id, actor_type="ADMIN", actor_id=admin.admin_id, action="SMS_RESENT",
          source="admin", installment_id=old.installment_id, details={"sms_id": old.id, "new_sms_id": new.id})
    return new


def retry_failed(db: Session, admin: AdminContext, batch_id: Optional[str] = None) -> Dict[str, Any]:
    failed = unresolved_failed(db, admin.tenant_id, batch_id=batch_id)
    new_ids = [resend(db, admin, m).id for m in failed]
    return {"resent": len(new_ids), "sms_ids": new_ids}


def _student_names(db: Session, installment_ids: Iterable[Optional[str]]) -> Dict[str, Tuple[str, Optional[str]]]:
    ids = [i for i in set(installment_ids) if i]
    if not ids:
        return {}
    rows = (db.query(Installment.id, Student.name, Student.class_grade)
            .join(Student, Student.id == Installment.student_id).filter(Installment.id.in_(ids)).all())
    return {i: (n, c) for i, n, c in rows}


def sms_health(db: Session, tenant_id: str) -> Dict[str, Any]:
    now = utcnow()
    device = (db.query(Device).filter(Device.tenant_id == tenant_id, Device.active.is_(True))
              .order_by(Device.last_seen_at.desc()).first())
    online = bool(device and device.last_seen_at and device.last_seen_at >= now - timedelta(minutes=30))
    today_start = ist_day_start_utc(ist_today())
    sent_today = (db.query(func.count(SmsOutbox.id))
                  .filter(SmsOutbox.tenant_id == tenant_id, SmsOutbox.status == "SENT",
                          SmsOutbox.sent_at >= today_start).scalar() or 0)
    waiting = (db.query(func.count(SmsOutbox.id))
               .filter(SmsOutbox.tenant_id == tenant_id, SmsOutbox.status.in_(["QUEUED", "DISPATCHED"])).scalar() or 0)
    failed = unresolved_failed(db, tenant_id)
    names = _student_names(db, (m.installment_id for m in failed))
    if device is None:
        advice = "No SMS phone is connected. Open the FeeFlow app on the centre's phone and tap 'Use this phone for SMS'."
    elif not online:
        advice = "The SMS phone has not been online for over 30 minutes. Open the FeeFlow app on it, or check its internet."
    elif failed:
        advice = "Some messages failed. Check the phone's SIM balance and signal, then tap 'Retry all'."
    else:
        advice = "Everything is working."
    return {
        "phone": {
            "connected": device is not None,
            "name": device.name if device else None,
            "online": online,
            "last_seen_at": _iso(device.last_seen_at) if device else None,
        },
        "sent_today": sent_today,
        "waiting": waiting,
        "failed_count": len(failed),
        "failed": [
            {**sms_out(m), "student_name": names.get(m.installment_id, (None, None))[0],
             "created_at": _iso(m.created_at)}
            for m in failed[:50]
        ],
        "advice": advice,
        "limit_note": ("Android may ask 'Allow FeeFlow to send a large number of messages?' when many reminders go "
                       "out together. Choose 'Always allow'. FeeFlow sends at most 30 messages per sync."),
    }


# ------------------------------------------------------------------ dashboard
def _monthly_missing(db: Session, tenant: Tenant, today: date) -> List[Student]:
    month = fees.month_of(today)
    have = {sid for (sid,) in db.query(Installment.student_id)
            .filter(Installment.tenant_id == tenant.id, Installment.billing_month == month,
                    Installment.kind == "MONTHLY").all()}
    out = []
    for st in (db.query(Student).filter(Student.tenant_id == tenant.id, Student.active.is_(True),
                                        Student.monthly_fee_paise.isnot(None), Student.fee_due_day.isnot(None)).all()):
        if st.id in have or (st.joined_on and st.joined_on.replace(day=1) > today.replace(day=1)):
            continue
        out.append(st)
    return out


def _reminded_installments(db: Session, tenant_id: str, ids: List[str]) -> Set[str]:
    if not ids:
        return set()
    sms = {i for (i,) in db.query(SmsOutbox.installment_id)
           .filter(SmsOutbox.tenant_id == tenant_id, SmsOutbox.installment_id.in_(ids),
                   SmsOutbox.kind == "FEE_REMINDER", SmsOutbox.status != "FAILED").all()}
    sched = {i for (i,) in db.query(ReminderSchedule.installment_id)
             .filter(ReminderSchedule.installment_id.in_(ids), ReminderSchedule.status == "SENT").all()}
    return sms | sched


def _awaiting_installments(db: Session, tenant_id: str) -> Set[str]:
    return {i for (i,) in db.query(PaymentDeclaration.installment_id)
            .filter(PaymentDeclaration.tenant_id == tenant_id,
                    PaymentDeclaration.declaration_status.in_(REVIEWABLE)).all()}


def dashboard(db: Session, tenant: Tenant, month: Optional[str] = None) -> Dict[str, Any]:
    today = ist_today()
    current = fees.month_of(today)
    month = month or current
    insts = db.query(Installment).filter(Installment.tenant_id == tenant.id, Installment.billing_month == month).all()
    students_total = (db.query(func.count(Student.id))
                      .filter(Student.tenant_id == tenant.id, Student.active.is_(True)).scalar() or 0)
    awaiting = (db.query(func.count(PaymentDeclaration.id))
                .filter(PaymentDeclaration.tenant_id == tenant.id,
                        PaymentDeclaration.declaration_status.in_(REVIEWABLE)).scalar() or 0)
    overdue = (db.query(Installment).join(Student, Student.id == Installment.student_id)
               .filter(Installment.tenant_id == tenant.id, Installment.status != "PAID",
                       Installment.due_date < today, Student.active.is_(True)).all())
    awaiting_ids = _awaiting_installments(db, tenant.id)
    overdue_open = [i for i in overdue if i.id not in awaiting_ids]
    collected_today = (db.query(func.coalesce(func.sum(VerifiedPayment.amount_paise), 0))
                       .filter(VerifiedPayment.tenant_id == tenant.id,
                               VerifiedPayment.reconciliation_source != TRANSFER_SOURCE,
                               VerifiedPayment.verified_at >= ist_day_start_utc(today)).scalar() or 0)
    health = sms_health(db, tenant.id)
    unpaid_month = [i for i in insts if i.status != "PAID" and i.id not in awaiting_ids]
    reminded = _reminded_installments(db, tenant.id, [i.id for i in unpaid_month])
    not_reminded = [i for i in unpaid_month if i.id not in reminded]
    missing = _monthly_missing(db, tenant, today) if month == current else []
    excess_open = (db.query(func.count(PaymentAdjustment.id))
                   .filter(PaymentAdjustment.tenant_id == tenant.id, PaymentAdjustment.status == "FLAGGED").scalar() or 0)

    actions: List[Dict[str, Any]] = []

    def act(kind: str, count: int, text: str, button: str, tone: str = "warn") -> None:
        if count:
            actions.append({"kind": kind, "count": count, "text": text, "button": button, "tone": tone})

    act("VERIFY", awaiting, f"{awaiting} payment{'s' if awaiting != 1 else ''} waiting for verification",
        "Verify now", "bad")
    n_over = len({i.student_id for i in overdue_open})
    act("OVERDUE", n_over, f"{n_over} student{'s have' if n_over != 1 else ' has'} overdue fees "
        f"(Rs.{format_rupees(sum(i.outstanding_paise for i in overdue_open))})", "Send reminders")
    act("SMS_FAILED", health["failed_count"], f"{health['failed_count']} SMS message"
        f"{'s' if health['failed_count'] != 1 else ''} failed", "Retry all", "bad")
    if health["waiting"] and not health["phone"]["online"]:
        act("SMS_PHONE", health["waiting"], f"{health['waiting']} SMS waiting - the SMS phone is not connected",
            "Check SMS phone", "bad")
    n_rem = len({i.student_id for i in not_reminded})
    act("NOT_REMINDED", n_rem, f"{n_rem} student{'s have' if n_rem != 1 else ' has'} not been sent "
        f"the {month_label(month)} fee reminder", "Send reminders")
    act("GENERATE", len(missing), f"{month_label(current)} fees not yet created for {len(missing)} "
        f"student{'s' if len(missing) != 1 else ''}", "Generate fees")
    act("EXCESS", excess_open, f"{excess_open} overpayment{'s' if excess_open != 1 else ''} to apply or refund",
        "Review", "info")

    recent = (db.query(VerifiedPayment, Installment).join(Installment, Installment.id == VerifiedPayment.installment_id)
              .filter(VerifiedPayment.tenant_id == tenant.id, VerifiedPayment.reconciliation_source != TRANSFER_SOURCE)
              .order_by(VerifiedPayment.verified_at.desc()).limit(5).all())
    return {
        "month": month,
        "month_label": month_label(month),
        "today": today.isoformat(),
        "stats": {
            "students_total": students_total,
            "fees_generated_paise": sum(i.total_paise for i in insts),
            "fees_count": len(insts),
            "collected_paise": sum(i.paid_paise for i in insts),
            "outstanding_paise": sum(i.outstanding_paise for i in insts),
            "paid_count": sum(1 for i in insts if i.status == "PAID"),
            "awaiting_verification": awaiting,
            "overdue_students": len({i.student_id for i in overdue}),
            "overdue_paise": sum(i.outstanding_paise for i in overdue),
            "collected_today_paise": collected_today,
            "sms_failed": health["failed_count"],
            "sms_waiting": health["waiting"],
        },
        "sms_phone": health["phone"],
        "pending_actions": actions,
        "recent_payments": [
            {"student_name": i.student.name, "description": i.description, "billing_month": i.billing_month,
             "amount_paise": p.amount_paise, "verified_at": _iso(p.verified_at)}
            for p, i in recent
        ],
        "classes": sorted({c for (c,) in db.query(Student.class_grade)
                           .filter(Student.tenant_id == tenant.id, Student.active.is_(True),
                                   Student.class_grade.isnot(None)).distinct().all() if c}),
    }


def generate_monthly_now(db: Session, admin: AdminContext, tenant: Tenant) -> Dict[str, Any]:
    today = ist_today()
    created = 0
    for st in _monthly_missing(db, tenant, today):
        if fees.create_monthly_fee(db, tenant, st, fees.month_of(today), actor_id=admin.admin_id) is not None:
            created += 1
    return {"created": created, "billing_month": fees.month_of(today)}


# ------------------------------------------------------------------ bulk reminders
TARGETS = ("OVERDUE", "MONTH", "CLASS", "ALL_UNPAID", "SELECTED")


def reminder_recipients(db: Session, tenant: Tenant, target: str, class_grade: Optional[str] = None,
                        student_ids: Optional[List[str]] = None, exclude_ids: Optional[List[str]] = None,
                        month: Optional[str] = None) -> List[Installment]:
    """One fee per student (their oldest unpaid one). Students whose payment is awaiting verification
    are left out, so a parent who has already paid is never chased."""
    if target not in TARGETS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown target")
    today = ist_today()
    q = (db.query(Installment).join(Student, Student.id == Installment.student_id)
         .filter(Installment.tenant_id == tenant.id, Installment.status != "PAID", Student.active.is_(True)))
    if target == "OVERDUE":
        q = q.filter(Installment.due_date < today)
    elif target == "MONTH":
        q = q.filter(Installment.billing_month == (month or fees.month_of(today)))
    elif target == "CLASS":
        if not class_grade:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Choose a class")
        q = q.filter(Student.class_grade == class_grade)
    elif target == "SELECTED":
        q = q.filter(Student.id.in_(student_ids or []))
    awaiting = _awaiting_installments(db, tenant.id)
    exclude = set(exclude_ids or [])
    chosen: Dict[str, Installment] = {}
    for inst in q.order_by(Installment.due_date, Installment.created_at).all():
        if inst.id in awaiting or inst.student_id in exclude or inst.student_id in chosen:
            continue
        chosen[inst.student_id] = inst
    return sorted(chosen.values(), key=lambda i: ((i.student.class_grade or ""), i.student.name.lower()))


def _recipient_out(i: Installment) -> Dict[str, Any]:
    s = i.student
    return {"student_id": s.id, "student_name": s.name, "student_code": s.student_code,
            "class_grade": s.class_grade, "parent_mobile": s.parent_mobile,
            "description": i.description, "billing_month": i.billing_month, "due_date": i.due_date.isoformat(),
            "amount_paise": i.outstanding_paise, "overdue": i.due_date < ist_today()}


def reminders_preview(db: Session, tenant: Tenant, body: Dict[str, Any]) -> Dict[str, Any]:
    rec = reminder_recipients(db, tenant, body["target"], body.get("class_grade"), body.get("student_ids"),
                              body.get("exclude_ids"), body.get("month"))
    sample = fee_request_text(rec[0], f"{link_url('x' * 16)}") if rec else None
    return {"count": len(rec), "recipients": [_recipient_out(i) for i in rec], "sample_message": sample,
            "sms_parts": sms_parts(sample) if sample else 0,
            "sms_phone_active": fees.sms_phone_active(db, tenant.id)}


def reminders_send(db: Session, admin: AdminContext, tenant: Tenant, body: Dict[str, Any]) -> Dict[str, Any]:
    rec = reminder_recipients(db, tenant, body["target"], body.get("class_grade"), body.get("student_ids"),
                              body.get("exclude_ids"), body.get("month"))
    if not rec:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="No students to remind.")
    batch_id = new_id()
    ids = []
    for inst in rec:
        _, token = create_payment_link(db, inst, valid_days=45)
        msg = enqueue_sms(db, tenant=tenant, to_mobile=inst.student.parent_mobile, kind="FEE_REMINDER",
                          dedupe_key=f"bulk:{batch_id}:{inst.id}", installment_id=inst.id,
                          body=fee_request_text(inst, link_url(token)))
        if msg is not None:
            msg.batch_id = batch_id
            db.flush()
            ids.append(msg.id)
    audit(db, tenant_id=tenant.id, actor_type="ADMIN", actor_id=admin.admin_id, action="BULK_REMINDERS_QUEUED",
          source="bulk", details={"batch_id": batch_id, "target": body["target"], "count": len(ids),
                                  "class_grade": body.get("class_grade")})
    return {"batch_id": batch_id, "queued": len(ids), "sms_phone_active": fees.sms_phone_active(db, tenant.id)}


def batch_status(db: Session, tenant_id: str, batch_id: str) -> Dict[str, Any]:
    rows = db.query(SmsOutbox).filter(SmsOutbox.tenant_id == tenant_id, SmsOutbox.batch_id == batch_id).all()
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Batch not found")
    resent = _resent_ids(db, tenant_id, utcnow() - timedelta(days=60))
    live = [m for m in rows if not (m.status == "FAILED" and m.id in resent)]
    counts = {k: sum(1 for m in live if m.status == k) for k in ("QUEUED", "DISPATCHED", "SENT", "FAILED")}
    names = _student_names(db, (m.installment_id for m in live if m.status == "FAILED"))
    total = len(live)
    return {"batch_id": batch_id, "total": total, **{k.lower(): v for k, v in counts.items()},
            "done": counts["SENT"] + counts["FAILED"] == total,
            "progress_percent": round(100 * (counts["SENT"] + counts["FAILED"]) / total) if total else 100,
            "failed_items": [{**sms_out(m), "student_name": names.get(m.installment_id, (None,))[0]}
                             for m in live if m.status == "FAILED"]}


# ------------------------------------------------------------------ bulk one-off fees
def _fee_students(db: Session, tenant: Tenant, body: Dict[str, Any]) -> List[Student]:
    q = db.query(Student).filter(Student.tenant_id == tenant.id, Student.active.is_(True))
    target = body.get("target", "ALL")
    if target == "CLASS":
        if not body.get("class_grade"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Choose a class")
        q = q.filter(Student.class_grade == body["class_grade"])
    elif target == "SELECTED":
        q = q.filter(Student.id.in_(body.get("student_ids") or []))
    exclude = set(body.get("exclude_ids") or [])
    return [s for s in q.order_by(Student.class_grade, Student.name).all() if s.id not in exclude]


def _existing_same_fee(db: Session, tenant_id: str, student_ids: List[str], description: str, month: str) -> Set[str]:
    if not student_ids:
        return set()
    return {sid for (sid,) in db.query(Installment.student_id)
            .filter(Installment.tenant_id == tenant_id, Installment.student_id.in_(student_ids),
                    Installment.billing_month == month,
                    func.lower(Installment.description) == description.strip().lower()).all()}


def fees_preview(db: Session, tenant: Tenant, body: Dict[str, Any]) -> Dict[str, Any]:
    students = _fee_students(db, tenant, body)
    dup = _existing_same_fee(db, tenant.id, [s.id for s in students], body["description"], body["billing_month"])
    rows = [{"student_id": s.id, "student_name": s.name, "student_code": s.student_code, "class_grade": s.class_grade,
             "amount_paise": body["amount_paise"], "duplicate": s.id in dup} for s in students]
    new = [r for r in rows if not r["duplicate"]]
    return {"count": len(new), "duplicates": len(rows) - len(new), "total_paise": len(new) * body["amount_paise"],
            "rows": rows}


def fees_create(db: Session, admin: AdminContext, tenant: Tenant, body: Dict[str, Any]) -> Dict[str, Any]:
    students = _fee_students(db, tenant, body)
    dup = _existing_same_fee(db, tenant.id, [s.id for s in students], body["description"], body["billing_month"])
    created = 0
    for s in students:
        if s.id in dup:
            continue
        fees.create_fee(db, tenant=tenant, student=s, actor_id=admin.admin_id, description=body["description"].strip(),
                        billing_month=body["billing_month"], total_paise=body["amount_paise"],
                        due_date=body["due_date"], kind="ONE_OFF", send_link_sms=False,
                        schedule_reminders=bool(tenant.auto_send_links))
        created += 1
    audit(db, tenant_id=tenant.id, actor_type="ADMIN", actor_id=admin.admin_id, action="BULK_FEES_CREATED",
          source="bulk", details={"description": body["description"], "billing_month": body["billing_month"],
                                  "amount_paise": body["amount_paise"], "created": created, "skipped": len(dup)})
    return {"created": created, "skipped_duplicates": len(dup)}


# ------------------------------------------------------------------ verification search + warnings
def search_declarations(db: Session, tenant_id: str, *, q: Optional[str] = None, status_filter: str = "PENDING",
                        class_grade: Optional[str] = None, amount_paise: Optional[int] = None,
                        date_from: Optional[date] = None, date_to: Optional[date] = None,
                        utr: Optional[str] = None, limit: int = 100) -> List[PaymentDeclaration]:
    qry = (db.query(PaymentDeclaration)
           .join(Installment, Installment.id == PaymentDeclaration.installment_id)
           .join(Student, Student.id == Installment.student_id)
           .filter(PaymentDeclaration.tenant_id == tenant_id))
    statuses = {"PENDING": REVIEWABLE, "VERIFIED": ["VERIFIED"], "REJECTED": ["REJECTED"],
                "ALL": REVIEWABLE + ["VERIFIED", "REJECTED", "PAYMENT_PROCESSING", "FAILED"]}.get(status_filter)
    if statuses is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown status filter")
    qry = qry.filter(PaymentDeclaration.declaration_status.in_(statuses))
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        qry = qry.filter(or_(func.lower(Student.name).like(like), func.lower(Student.student_code).like(like),
                             Student.parent_mobile.like(f"%{q.strip()}%"),
                             func.lower(Installment.payment_reference).like(like),
                             func.lower(PaymentDeclaration.public_ref).like(like)))
    if class_grade:
        qry = qry.filter(Student.class_grade == class_grade)
    if amount_paise:
        qry = qry.filter(PaymentDeclaration.declared_amount_paise == amount_paise)
    when = func.coalesce(PaymentDeclaration.declared_at, PaymentDeclaration.created_at)
    if date_from:
        qry = qry.filter(when >= ist_day_start_utc(date_from))
    if date_to:
        qry = qry.filter(when < ist_day_start_utc(date_to + timedelta(days=1)))
    if utr and utr.strip():
        u = utr.strip()
        paid_with = db.query(VerifiedPayment.declaration_id).filter(VerifiedPayment.tenant_id == tenant_id,
                                                                     VerifiedPayment.bank_reference.like(f"%{u}%"))
        qry = qry.filter(or_(PaymentDeclaration.parent_upi_reference.like(f"%{u}%"),
                             PaymentDeclaration.id.in_(paid_with)))
    return qry.order_by(when.desc()).limit(min(max(limit, 1), 200)).all()


def declaration_warnings(db: Session, decl: PaymentDeclaration) -> List[Dict[str, str]]:
    """Plain-language checks shown before the admin confirms. They never block a legitimate split payment."""
    inst = decl.installment
    out: List[Dict[str, str]] = []
    if decl.declaration_status not in REVIEWABLE:
        return out  # already decided: nothing to warn about
    if inst.status == "PAID":
        out.append({"level": "bad", "text": "This fee is already fully paid. Confirming again would record extra money."})
    ref = (decl.parent_upi_reference or "").strip()
    if len(ref) >= 6:
        used = (db.query(VerifiedPayment).filter(VerifiedPayment.tenant_id == decl.tenant_id,
                                                 VerifiedPayment.bank_reference == ref).first())
        if used is not None:
            out.append({"level": "bad", "text": f"The transaction ID {ref} the parent gave is already recorded "
                                                f"for another payment."})
    if decl.declared_amount_paise != inst.outstanding_paise and inst.status != "PAID":
        if decl.payment_type == "PARTIAL":
            out.append({"level": "info", "text": f"Part payment: parent reported Rs.{format_rupees(decl.declared_amount_paise)} "
                                                 f"of Rs.{format_rupees(inst.outstanding_paise)} due. Part payments are allowed."})
        else:
            out.append({"level": "warn", "text": f"Parent reported Rs.{format_rupees(decl.declared_amount_paise)} but "
                                                 f"Rs.{format_rupees(inst.outstanding_paise)} is due. Check the bank amount."})
    others = (db.query(func.count(PaymentDeclaration.id))
              .filter(PaymentDeclaration.installment_id == inst.id, PaymentDeclaration.id != decl.id,
                      PaymentDeclaration.declaration_status.in_(["VERIFIED"] + REVIEWABLE)).scalar() or 0)
    if others:
        out.append({"level": "warn", "text": f"This fee has {others} other payment report"
                                             f"{'s' if others != 1 else ''}. Make sure it isn't the same money."})
    return out


def check_reference(db: Session, tenant_id: str, ref: str) -> Dict[str, Any]:
    ref = (ref or "").strip()
    vp = (db.query(VerifiedPayment).filter(VerifiedPayment.tenant_id == tenant_id,
                                           VerifiedPayment.bank_reference == ref).first()) if ref else None
    if vp is None:
        return {"used": False}
    inst = db.get(Installment, vp.installment_id)
    return {"used": True, "student_name": inst.student.name, "description": inst.description,
            "billing_month": inst.billing_month, "amount_paise": vp.amount_paise,
            "message": f"Already recorded: Rs.{format_rupees(vp.amount_paise)} for {inst.student.name} "
                       f"({inst.description}, {month_label(inst.billing_month)})."}


# ------------------------------------------------------------------ overpayment -> next fee
def apply_excess(db: Session, admin: AdminContext, adjustment_id: str,
                 target_installment_id: Optional[str] = None) -> Dict[str, Any]:
    from app.services.verification import apply_transactions  # avoid an import cycle

    adj = (db.query(PaymentAdjustment)
           .filter(PaymentAdjustment.id == adjustment_id, PaymentAdjustment.tenant_id == admin.tenant_id)
           .with_for_update().first())
    if adj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Overpayment not found")
    if adj.status != "FLAGGED":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This overpayment has already been resolved")
    src = db.get(Installment, adj.installment_id)
    q = (db.query(Installment).filter(Installment.tenant_id == admin.tenant_id, Installment.student_id == src.student_id,
                                      Installment.status != "PAID", Installment.id != src.id))
    if target_installment_id:
        q = q.filter(Installment.id == target_installment_id)
    target = q.order_by(Installment.due_date, Installment.created_at).with_for_update().first()
    if target is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="No unpaid fee to apply this to. Create the student's next fee first.")
    tenant = db.get(Tenant, admin.tenant_id)
    result = apply_transactions(
        db, admin=admin, tenant=tenant, inst=target,
        transactions=[{"amount_paise": adj.amount_paise, "bank_reference": None}],
        evidence_note=(f"Overpayment of Rs.{format_rupees(adj.amount_paise)} from {src.description} "
                       f"({month_label(src.billing_month)}) applied to this fee"),
        declaration=None, source=TRANSFER_SOURCE,
    )
    prev = adj.status
    adj.status = "ADJUSTED"
    adj.note = (f"{adj.note or ''}\nApplied to {target.description} ({month_label(target.billing_month)}), "
                f"receipt {result['receipt'].receipt_number}").strip()
    adj.resolved_by = admin.admin_id
    adj.resolved_at = utcnow()
    src.excess_paise = max(src.excess_paise - adj.amount_paise, 0)
    audit(db, tenant_id=admin.tenant_id, actor_type="ADMIN", actor_id=admin.admin_id, action="EXCESS_APPLIED_TO_FEE",
          source="admin", installment_id=target.id, previous_status=prev, new_status=adj.status,
          details={"adjustment_id": adj.id, "amount_paise": adj.amount_paise, "from_installment_id": src.id,
                   "receipt_number": result["receipt"].receipt_number})
    db.flush()
    return {"adjustment_id": adj.id, "status": adj.status, "applied_to": {
        "description": target.description, "billing_month": target.billing_month,
        "installment_status": target.status, "outstanding_paise": target.outstanding_paise},
        "receipt": {"receipt_number": result["receipt"].receipt_number, "url": receipt_url(result["receipt"])}}


# ------------------------------------------------------------------ parent family view
def family_view(db: Session, inst: Installment) -> Dict[str, Any]:
    """Every child of this parent (same mobile number, same centre): unpaid fees and recent history.
    Fees are identified by their payment reference (already shown to parents), never by internal ids."""
    me = inst.student
    kids = (db.query(Student).filter(Student.tenant_id == inst.tenant_id, Student.parent_mobile == me.parent_mobile,
                                     Student.active.is_(True)).order_by(Student.name).all())
    if me not in kids:
        kids.append(me)
    ids = [k.id for k in kids]
    awaiting = _awaiting_installments(db, inst.tenant_id)
    unpaid = (db.query(Installment).filter(Installment.student_id.in_(ids), Installment.status != "PAID")
              .order_by(Installment.due_date).all())
    history = (db.query(Installment).filter(Installment.student_id.in_(ids), Installment.paid_paise > 0)
               .order_by(Installment.due_date.desc()).limit(12).all())
    receipts: Dict[str, Receipt] = {}
    if history:
        for r in (db.query(Receipt).filter(Receipt.installment_id.in_([h.id for h in history]))
                  .order_by(Receipt.issued_at).all()):
            receipts[r.installment_id] = r  # latest wins
    names = {k.id: k.name for k in kids}
    return {
        "children": [
            {"name": k.name, "class_grade": k.class_grade, "current": k.id == me.id,
             "fees": [{"reference": f.payment_reference, "description": f.description, "billing_month": f.billing_month,
                       "due_date": f.due_date.isoformat(), "outstanding_paise": f.outstanding_paise,
                       "status": f.status, "current": f.id == inst.id, "awaiting_verification": f.id in awaiting}
                      for f in unpaid if f.student_id == k.id]}
            for k in kids
        ],
        "history": [
            {"student_name": names.get(h.student_id, ""), "description": h.description, "billing_month": h.billing_month,
             "total_paise": h.total_paise, "paid_paise": h.paid_paise, "status": h.status,
             "receipt_url": receipt_url(receipts[h.id]) if h.id in receipts else None}
            for h in history
        ],
    }


def family_link(db: Session, inst: Installment, reference: str) -> Dict[str, Any]:
    me = inst.student
    target = (db.query(Installment).join(Student, Student.id == Installment.student_id)
              .filter(Installment.tenant_id == inst.tenant_id, Installment.payment_reference == reference,
                      Student.parent_mobile == me.parent_mobile).first())
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Fee not found")
    if target.status == "PAID":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This fee is already paid")
    if target.id == inst.id:
        return {"same": True}
    _, token = create_payment_link(db, target, valid_days=14)
    return {"same": False, "url": link_url(token)}
