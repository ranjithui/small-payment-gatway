"""SMS outbox for the Android SIM-based SMS engine.

FeeFlow never talks to an SMS gateway. Messages are queued here and picked up by the
registered Android device during its normal sync (see app/routers/devices.py); the
phone sends them from its physical SIM and acknowledges the result.

Every message has a ``dedupe_key`` with a UNIQUE constraint so retries, double taps or
repeated jobs can never queue the same acknowledgement twice.
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.models import SmsOutbox, Tenant

TEMPLATES = {
    "DECLARATION_ACK": (
        "Dear Parent, we have received your payment confirmation for {student_name}. "
        "Our team is verifying the transaction. Please wait for confirmation. - {centre_name}"
    ),
    "PAYMENT_VERIFIED": (
        "Dear Parent, your payment of Rs. {amount} for {student_name} has been verified successfully. "
        "Your receipt: {receipt_link}. - {centre_name}"
    ),
    "PAYMENT_REJECTED": (
        "Dear Parent, we could not verify your payment for {student_name}. "
        "Please contact {centre_name} for assistance."
    ),
    "CLARIFICATION_REQUESTED": (
        "Dear Parent, {centre_name} needs a few more details to verify your payment for {student_name}. "
        "Please open your payment link: {payment_link}"
    ),
    "FEE_REMINDER": (
        "Dear Parent, fee of Rs. {amount} for {student_name} ({description}) is due on {due_date}. "
        "Pay here: {payment_link} - {centre_name}"
    ),
}


def format_rupees(paise: int) -> str:
    rupees, p = divmod(paise, 100)
    return f"{rupees:,}" if p == 0 else f"{rupees:,}.{p:02d}"


def enqueue_sms(
    db: Session,
    *,
    tenant: Tenant,
    to_mobile: str,
    kind: str,
    dedupe_key: str,
    installment_id: Optional[str] = None,
    declaration_id: Optional[str] = None,
    reminder_id: Optional[str] = None,
    **params: str,
) -> Optional[SmsOutbox]:
    """Queue an SMS unless an identical one (same dedupe_key) already exists."""
    existing = db.query(SmsOutbox).filter(SmsOutbox.dedupe_key == dedupe_key).first()
    if existing is not None:
        return None
    # Also check objects added in this unit of work but not yet flushed.
    for obj in db.new:
        if isinstance(obj, SmsOutbox) and obj.dedupe_key == dedupe_key:
            return None
    body = TEMPLATES[kind].format(centre_name=tenant.name, **params)
    msg = SmsOutbox(
        tenant_id=tenant.id,
        to_mobile=to_mobile,
        body=body,
        kind=kind,
        dedupe_key=dedupe_key,
        installment_id=installment_id,
        declaration_id=declaration_id,
        reminder_id=reminder_id,
    )
    db.add(msg)
    return msg
