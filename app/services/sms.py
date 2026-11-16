"""SMS outbox for the Android SIM-based SMS engine.

FeeFlow never talks to an SMS gateway. Messages are queued here and picked up by the
registered Android device during its normal sync (see app/routers/devices.py); the
phone sends them from its physical SIM and acknowledges the result.

Every message has a ``dedupe_key`` with a UNIQUE constraint so retries, double taps or
repeated jobs can never queue the same acknowledgement twice.
"""
from typing import Any, Dict, Optional
from urllib.parse import quote

from sqlalchemy.orm import Session

import calendar

from app.models import Installment, SmsOutbox, Tenant

# Professional, short messages. "Rs." (not the rupee sign) keeps an SMS in the 160-character GSM
# encoding; the rupee sign would switch it to 70-character parts.
TEMPLATES = {
    "DECLARATION_ACK": (
        "{centre_name}: We received your payment report of Rs.{amount} for {student_name} ({month}). "
        "We will confirm after checking our bank account, usually within {hours} hours. Ref: {reference}"
    ),
    "PAYMENT_VERIFIED": (
        "{centre_name}: Payment of Rs.{amount} for {student_name} ({month}) is confirmed. Thank you!"
        "{balance_line} Receipt: {receipt_link}"
    ),
    "PAYMENT_REJECTED": (
        "{centre_name}: We could not find your payment for {student_name} ({month}) in our bank account. "
        "Please contact us{contact_line} before paying again."
    ),
    "CLARIFICATION_REQUESTED": (
        "{centre_name}: We need a few more details to verify your payment for {student_name} ({month}). "
        "Please open: {payment_link}"
    ),
    # Fee requests and reminders are built by fee_request_text() (UPI details are optional lines).
    "FEE_REMINDER": "{body}",
}


def month_label(billing_month: Optional[str]) -> str:
    """'2026-11' -> 'Nov 2026'."""
    try:
        y, m = (int(x) for x in (billing_month or "").split("-"))
        return f"{calendar.month_abbr[m]} {y}"
    except (ValueError, IndexError):
        return billing_month or ""


def display_mobile(m: Optional[str]) -> Optional[str]:
    """+919876543210 -> "98765 43210" (what parents type in 'Pay phone number')."""
    if not m:
        return None
    d = "".join(c for c in m if c.isdigit())[-10:]
    return f"{d[:5]} {d[5:]}" if len(d) == 10 else None


def fee_request_text(inst: Installment, url: str) -> str:
    """Fee link / reminder SMS: the parent can pay from any UPI app without opening the link,
    then taps the link to confirm."""
    t = inst.tenant
    lines = [
        f"{t.name}: Fee of Rs.{format_rupees(inst.outstanding_paise)} for {inst.student.name} "
        f"({month_label(inst.billing_month)}) is due on {inst.due_date.strftime('%d-%b')}.",
        "",
    ]
    if t.upi_vpa:
        lines.append("Pay using any UPI app to:")
        lines.append(f"UPI ID: {t.upi_vpa}")
        mobile = display_mobile(t.upi_mobile)
        if mobile:
            lines.append(f"or Mobile: {mobile}")
        lines.append(f"Name shown: {t.upi_payee_name or t.name}")
        if inst.payment_reference:
            lines.append(f"Note: {inst.payment_reference}")
        lines += ["", "After paying, tap to confirm:", url]
    else:
        lines += ["Pay and confirm here:", url]
    lines += ["", "Never share your UPI PIN or OTP."]
    return "\n".join(lines)


def _standard_params(db: Session, tenant: Tenant, installment_id: Optional[str]) -> dict:
    """Values every message may use, taken from the fee itself."""
    out = {"hours": tenant.verification_grace_hours,
           "contact_line": f" on {tenant.contact_phone}" if tenant.contact_phone else "",
           "month": "", "reference": "", "balance_line": ""}
    inst = db.get(Installment, installment_id) if installment_id else None
    if inst is not None:
        out["month"] = month_label(inst.billing_month)
        out["reference"] = inst.payment_reference or ""
        if inst.outstanding_paise > 0:
            out["balance_line"] = f" Balance due: Rs.{format_rupees(inst.outstanding_paise)}."
    return out


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
    values = _standard_params(db, tenant, installment_id)
    values.update(params)
    body = TEMPLATES[kind].format(centre_name=tenant.name, **values)
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


def whatsapp_url(to_mobile: str, text: str) -> str:
    """wa.me link that opens WhatsApp to the parent with the message filled in."""
    digits = "".join(c for c in to_mobile if c.isdigit())
    if len(digits) == 10:
        digits = "91" + digits
    return f"https://wa.me/{digits}?text={quote(text)}"


def sms_out(msg: SmsOutbox) -> Dict[str, Any]:
    return {"id": msg.id, "kind": msg.kind, "status": msg.status, "error": msg.error,
            "to_mobile": msg.to_mobile, "sent_at": msg.sent_at.isoformat() + "Z" if msg.sent_at else None}


def thank_you_out(db: Session, dedupe_key: str) -> Optional[Dict[str, Any]]:
    """The thank-you message sent to the parent after a verified payment, for the admin's
    'Payment confirmed' screen: SMS status plus a WhatsApp link with the same text."""
    msg = next((o for o in db.new if isinstance(o, SmsOutbox) and o.dedupe_key == dedupe_key), None)
    if msg is None:
        msg = db.query(SmsOutbox).filter(SmsOutbox.dedupe_key == dedupe_key).first()
    if msg is None:
        return None
    db.flush()
    return {**sms_out(msg), "message": msg.body, "whatsapp_url": whatsapp_url(msg.to_mobile, msg.body)}
