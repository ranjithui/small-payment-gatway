"""Reminder automation.

Reminder schedules are owned by the server and mirrored to the registered Android
device, which sends them from its SIM. Every change bumps ``version`` and sets
``sync_status = PENDING_SYNC``; the device receives the new state on its next sync
and must check its latest locally known state (status + hold_until) right before
sending. While the device is offline, the change is shown as "pending sync".
"""
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy.orm import Session

from app.audit import audit, notify_admin
from app.db import utcnow
from app.models import Installment, PaymentDeclaration, ReminderSchedule, Tenant
from app.services.sms import TEMPLATES, format_rupees
from app.states import DeclarationStatus as D


def _touch(rem: ReminderSchedule) -> None:
    rem.version += 1
    rem.sync_status = "PENDING_SYNC"
    rem.updated_at = utcnow()


def _render(db: Session, inst: Installment, amount_paise: int) -> str:
    from app.services.links import create_payment_link, link_url

    link, token = create_payment_link(db, inst, valid_days=45)
    return TEMPLATES["FEE_REMINDER"].format(
        amount=format_rupees(amount_paise),
        student_name=inst.student.name,
        description=inst.description,
        due_date=inst.due_date.strftime("%d %b %Y"),
        payment_link=link_url(token),
        centre_name=inst.tenant.name,
    )


def active_reminders(db: Session, installment_id: str) -> List[ReminderSchedule]:
    return (
        db.query(ReminderSchedule)
        .filter(ReminderSchedule.installment_id == installment_id, ReminderSchedule.status == "SCHEDULED")
        .order_by(ReminderSchedule.scheduled_for)
        .all()
    )


def add_reminder(db: Session, inst: Installment, when: datetime) -> ReminderSchedule:
    rem = ReminderSchedule(
        tenant_id=inst.tenant_id,
        installment_id=inst.id,
        scheduled_for=when,
        amount_paise=inst.outstanding_paise,
        to_mobile=inst.student.parent_mobile,
        body=_render(db, inst, inst.outstanding_paise),
    )
    db.add(rem)
    return rem


def schedule_initial_reminders(db: Session, inst: Installment, tenant: Tenant, start: datetime, count: int = 3) -> None:
    for i in range(count):
        add_reminder(db, inst, start + timedelta(hours=tenant.reminder_frequency_hours * i))


def pause_for_verification(db: Session, inst: Installment, tenant: Tenant, now: datetime) -> datetime:
    """Pause normal reminders for the configurable verification grace period (default 24h)."""
    until = now + timedelta(hours=tenant.verification_grace_hours)
    if inst.reminders_paused_until is None or inst.reminders_paused_until < until:
        inst.reminders_paused_until = until
        for rem in active_reminders(db, inst.id):
            _touch(rem)
    return until


def cancel_all(db: Session, inst: Installment, reason: str) -> int:
    n = 0
    for rem in active_reminders(db, inst.id):
        rem.status = "CANCELLED"
        rem.cancel_reason = reason
        _touch(rem)
        n += 1
    inst.reminders_paused_until = None
    return n


def rebase_to_outstanding(db: Session, inst: Installment) -> None:
    """After a partial verification, remaining reminders only ask for the remaining balance."""
    inst.reminders_paused_until = None
    rems = active_reminders(db, inst.id)
    for rem in rems:
        if rem.amount_paise != inst.outstanding_paise:
            rem.amount_paise = inst.outstanding_paise
            rem.body = _render(db, inst, inst.outstanding_paise)
        _touch(rem)
    if not rems and inst.outstanding_paise > 0:
        add_reminder(db, inst, utcnow() + timedelta(hours=inst.tenant.reminder_frequency_hours))


def restore_after_rejection(db: Session, inst: Installment, tenant: Tenant, now: datetime) -> None:
    """Restore reminder eligibility without firing an immediate duplicate reminder."""
    inst.reminders_paused_until = None
    earliest = now + timedelta(hours=tenant.reminder_frequency_hours)
    rems = active_reminders(db, inst.id)
    for rem in rems:
        if rem.scheduled_for < earliest:
            rem.scheduled_for = earliest
        _touch(rem)
    if not rems and inst.outstanding_paise > 0:
        add_reminder(db, inst, earliest)


def process_grace_expiry(db: Session, now: Optional[datetime] = None) -> int:
    """Declarations still unverified after the grace period: notify admin, restore reminders.

    The payment is NOT assumed to have failed; the declaration stays in the review queue.
    """
    now = now or utcnow()
    due = (
        db.query(PaymentDeclaration)
        .filter(
            PaymentDeclaration.declaration_status.in_([D.PAYMENT_REPORTED.value, D.PENDING_VERIFICATION.value]),
            PaymentDeclaration.grace_expires_at.isnot(None),
            PaymentDeclaration.grace_expires_at <= now,
            PaymentDeclaration.grace_expiry_notified_at.is_(None),
        )
        .all()
    )
    for decl in due:
        inst = decl.installment
        decl.grace_expiry_notified_at = now
        if inst.reminders_paused_until is not None and inst.reminders_paused_until <= now:
            inst.reminders_paused_until = None
            for rem in active_reminders(db, inst.id):
                _touch(rem)
        notify_admin(
            db,
            tenant_id=decl.tenant_id,
            kind="VERIFICATION_OVERDUE",
            message=(
                f"Payment confirmation {decl.public_ref} for {inst.student.name} is still awaiting verification "
                "after the grace period. Normal reminders have resumed per centre settings."
            ),
            declaration_id=decl.id,
            installment_id=inst.id,
        )
        audit(
            db,
            tenant_id=decl.tenant_id,
            actor_type="SYSTEM",
            actor_id=None,
            action="VERIFICATION_GRACE_EXPIRED",
            source="scheduler",
            declaration_id=decl.id,
            installment_id=inst.id,
            previous_status=decl.declaration_status,
            new_status=decl.declaration_status,
            reason="Grace period elapsed without admin verification; reminder eligibility restored",
        )
    db.commit()
    return len(due)
