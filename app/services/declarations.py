"""Parent-side payment declaration workflow ("I have paid", processing, failed)."""
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.audit import audit, notify_admin
from app.config import settings
from app.db import utcnow
from app.models import Installment, PaymentDeclaration, PaymentLink, Receipt, Tenant
from app.security import new_secret_token
from app.services import reminders
from app.services.receipts import receipt_url
from app.services.sms import enqueue_sms, format_rupees
from app.services.upi import DEFAULT_MERCHANT_CODE, UPI_APPS, amount_str, build_uris
from app.states import (
    ACTIVE_STATUS_VALUES,
    DeclarationStatus as D,
    InstallmentStatus,
    InvalidTransition,
    check_admin_transition,
    check_parent_transition,
    display_status,
)

PARENT_ACTIONS = {
    "INITIATED": D.PAYMENT_INITIATED,
    "PROCESSING": D.PAYMENT_PROCESSING,
    "PAID": D.PAYMENT_REPORTED,
    "FAILED": D.FAILED,
}

PARENT_MESSAGES = {
    "REPORTED": (
        "Thank you! Your payment confirmation has been submitted. The tuition centre is checking the "
        "transaction. You will receive confirmation once the payment has been verified. Please do not make "
        "another payment while this confirmation is being checked."
    ),
    "PROCESSING": (
        "Thanks for letting us know. Bank transfers can take a little time. Please do not repeat the payment "
        "right away. Check your UPI app or bank, then come back to this page and tap 'I have paid' once the "
        "money has been debited."
    ),
    "FAILED": (
        "No problem. If money was NOT debited from your account, you can try the payment again. If money was "
        "debited, please wait for your bank to update and contact the tuition centre."
    ),
    "VERIFIED": "Payment successfully verified!",
    "REJECTED": (
        "Payment could not be verified. Please contact your tuition centre or submit a new confirmation after "
        "checking your bank transaction."
    ),
    "PAID": "Payment successfully verified! This fee has been fully paid. Thank you!",
    "EXPIRED": "This payment link has expired. Please contact your tuition centre for a new link.",
}


def new_public_ref() -> str:
    return "PD-" + new_secret_token(9).replace("-", "X").replace("_", "Y").upper()[:10]


def active_declaration(db: Session, installment_id: str) -> Optional[PaymentDeclaration]:
    return (
        db.query(PaymentDeclaration)
        .filter(
            PaymentDeclaration.installment_id == installment_id,
            PaymentDeclaration.declaration_status.in_(ACTIVE_STATUS_VALUES),
        )
        .first()
    )


def latest_declaration(db: Session, installment_id: str) -> Optional[PaymentDeclaration]:
    return (
        db.query(PaymentDeclaration)
        .filter(PaymentDeclaration.installment_id == installment_id)
        .order_by(PaymentDeclaration.updated_at.desc(), PaymentDeclaration.created_at.desc())
        .first()
    )


def _lock_installment(db: Session, installment_id: str) -> Installment:
    # SELECT ... FOR UPDATE on Postgres serialises concurrent taps; ignored by SQLite.
    inst = db.query(Installment).filter(Installment.id == installment_id).with_for_update().one()
    return inst


def expire_stale(db: Session, link: PaymentLink, now: datetime) -> None:
    """Lazily expire INITIATED declarations whose payment link has expired."""
    if link.expires_at > now:
        return
    decl = active_declaration(db, link.installment_id)
    if decl is not None and decl.declaration_status == D.PAYMENT_INITIATED.value:
        prev = decl.declaration_status
        decl.declaration_status = check_admin_transition("expire", D(prev)).value
        audit(
            db,
            tenant_id=decl.tenant_id,
            actor_type="SYSTEM",
            actor_id=None,
            action="DECLARATION_EXPIRED",
            source="public_link",
            declaration_id=decl.id,
            installment_id=decl.installment_id,
            previous_status=prev,
            new_status=decl.declaration_status,
            reason="Payment link expired before the parent reported payment",
        )
        db.flush()


def public_view(db: Session, link: PaymentLink) -> Dict[str, Any]:
    """Everything the parent payment page needs. Never exposes internal IDs."""
    now = utcnow()
    expire_stale(db, link, now)
    inst = link.installment
    tenant: Tenant = inst.tenant
    student = inst.student
    active = active_declaration(db, inst.id)
    latest = active or latest_declaration(db, inst.id)
    link_expired = link.expires_at <= now
    inst_status = InstallmentStatus(inst.status)
    disp = display_status(
        inst_status,
        D(active.declaration_status) if active else None,
        D(latest.declaration_status) if latest else None,
        link_expired,
    )

    awaiting_verification = active is not None and active.declaration_status in (
        D.PAYMENT_REPORTED.value,
        D.PENDING_VERIFICATION.value,
    )
    can_pay = inst.outstanding_paise > 0 and not link_expired and not awaiting_verification

    receipts = (
        db.query(Receipt).filter(Receipt.installment_id == inst.id).order_by(Receipt.issued_at.desc()).all()
    )

    decl_out = None
    if latest is not None:
        decl_out = {
            "reference": latest.public_ref,
            "status": latest.declaration_status,
            "payment_type": latest.payment_type,
            "declared_amount_paise": latest.declared_amount_paise,
            "upi_app": latest.upi_app,
            "declared_at": _iso(latest.declared_at),
            "updated_at": _iso(latest.updated_at),
            "clarification_request": latest.clarification_request,
            "clarification_requested_at": _iso(latest.clarification_requested_at),
            "can_add_information": latest.declaration_status
            in (D.PAYMENT_REPORTED.value, D.PENDING_VERIFICATION.value),
        }

    note = f"{student.student_code} {inst.billing_month} fee"
    upi = None
    mc = (tenant.merchant_code or DEFAULT_MERCHANT_CODE) if tenant.upi_is_merchant else None
    txn_ref = "FF" + inst.id.replace("-", "")[:24].upper()  # stable per fee, alphanumeric, <= 35 chars
    if can_pay:
        upi = {
            "payee_vpa": tenant.upi_vpa,
            "payee_name": tenant.upi_payee_name,
            "amount_paise": inst.outstanding_paise,
            "note": note,
            "uris": build_uris(tenant.upi_vpa, tenant.upi_payee_name, inst.outstanding_paise, note, mc, txn_ref),
            "uris_any_amount": build_uris(tenant.upi_vpa, tenant.upi_payee_name, None, note, mc, txn_ref),
            # Pay-by-UPI-ID fallback (works even when an app blocks pre-filled links to personal UPI IDs)
            "is_merchant": bool(tenant.upi_is_merchant),
            "amount_text": amount_str(inst.outstanding_paise),
        }

    if disp.value in PARENT_MESSAGES:
        message = PARENT_MESSAGES[disp.value]
    elif awaiting_verification:
        message = PARENT_MESSAGES["REPORTED"]
    elif disp == "PAYMENT_PROCESSING":
        message = PARENT_MESSAGES["PROCESSING"]
    elif disp == "PARTIALLY_PAID":
        message = (
            f"Rs. {format_rupees(inst.paid_paise)} has been verified. "
            f"Remaining balance: Rs. {format_rupees(inst.outstanding_paise)}."
        )
    else:
        message = None

    return {
        "centre": {
            "name": tenant.name,
            "logo_url": tenant.logo_url,
            "contact_phone": tenant.contact_phone,
            "contact_email": tenant.contact_email,
        },
        "student": {"name": student.name, "student_id": student.student_code},
        "fee": {
            "description": inst.description,
            "billing_month": inst.billing_month,
            "total_paise": inst.total_paise,
            "paid_paise": inst.paid_paise,
            "outstanding_paise": inst.outstanding_paise,
            "due_date": inst.due_date.isoformat(),
        },
        "installment_status": inst.status,
        "display_status": disp.value,
        "link_expired": link_expired,
        "link_expires_at": _iso(link.expires_at),
        "can_pay": can_pay,
        "awaiting_verification": awaiting_verification,
        "upi": upi,
        "declaration": decl_out,
        "receipts": [
            {
                "receipt_number": r.receipt_number,
                "amount_paise": r.amount_paise,
                "issued_at": _iso(r.issued_at),
                "url": receipt_url(r),
            }
            for r in receipts
        ],
        "message": message,
    }


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() + "Z" if dt else None


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail=detail)


def submit_parent_action(
    db: Session,
    link: PaymentLink,
    *,
    action: str,
    payment_type: str,
    declared_amount_paise: Optional[int],
    upi_app: Optional[str],
    approximate_payment_time: Optional[str],
    note: Optional[str],
    idempotency_key: str,
    parent_identifier: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply a parent action. Must be called inside run_idempotent (does not commit)."""
    now = utcnow()
    target = PARENT_ACTIONS[action]
    if link.expires_at <= now:
        raise HTTPException(status.HTTP_410_GONE, detail=PARENT_MESSAGES["EXPIRED"])
    if upi_app is not None and upi_app not in UPI_APPS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown UPI application")

    inst = _lock_installment(db, link.installment_id)
    tenant: Tenant = inst.tenant
    if inst.outstanding_paise <= 0:
        raise _conflict("This fee has already been fully paid. No further payment is needed.")

    active = active_declaration(db, inst.id)
    current = D(active.declaration_status) if active else None

    # Duplicate "I have paid" taps: return the existing declaration, never create another.
    if current in (D.PAYMENT_REPORTED, D.PENDING_VERIFICATION):
        if target == D.PAYMENT_REPORTED:
            view = public_view(db, link)
            view["duplicate"] = True
            return view
        raise _conflict(
            "Your payment confirmation is already being verified by the tuition centre. "
            "Please do not make another payment."
        )

    try:
        check_parent_transition(current, target)
    except InvalidTransition as exc:
        raise _conflict(str(exc))

    # Amount handling: one tap for full payment; partial payments need the actual amount.
    outstanding = inst.outstanding_paise
    if payment_type == "PARTIAL" and target == D.PAYMENT_REPORTED:
        if not declared_amount_paise or declared_amount_paise <= 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Please enter the amount you paid")
        if declared_amount_paise > outstanding:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Amount cannot be more than the outstanding balance of Rs. {format_rupees(outstanding)}",
            )
        amount = declared_amount_paise
        if amount == outstanding:
            payment_type = "FULL"
    else:
        payment_type = "FULL" if payment_type != "PARTIAL" else "PARTIAL"
        amount = declared_amount_paise if (payment_type == "PARTIAL" and declared_amount_paise) else outstanding
        amount = min(amount, outstanding)

    if active is None:
        decl = PaymentDeclaration(
            public_ref=new_public_ref(),
            tenant_id=inst.tenant_id,
            installment_id=inst.id,
            payment_link_id=link.id,
            parent_identifier=parent_identifier,
            declared_amount_paise=amount,
            payment_type=payment_type,
            payment_mode="UPI",
            upi_app=upi_app,
            declaration_status=target.value,
            idempotency_key=idempotency_key,
        )
        db.add(decl)
        db.flush()
        prev_status = None
    else:
        decl = active
        prev_status = decl.declaration_status
        decl.declaration_status = target.value
        decl.declared_amount_paise = amount
        decl.payment_type = payment_type
        if upi_app:
            decl.upi_app = upi_app
        decl.updated_at = now

    if approximate_payment_time:
        decl.approximate_payment_time = approximate_payment_time[:50]
    if note:
        decl.clarification_note = _append_note(decl.clarification_note, now, note)

    if target == D.PAYMENT_REPORTED:
        decl.declared_at = now
        decl.grace_expires_at = reminders.pause_for_verification(db, inst, tenant, now)
        notify_admin(
            db,
            tenant_id=tenant.id,
            kind="PAYMENT_REPORTED",
            message=(
                f"{inst.student.name} ({inst.student.student_code}) reported a "
                f"{'partial ' if payment_type == 'PARTIAL' else ''}payment of Rs. {format_rupees(amount)} "
                f"for {inst.description}. Please verify against your bank account."
            ),
            declaration_id=decl.id,
            installment_id=inst.id,
        )
        if tenant.ack_sms_enabled:
            enqueue_sms(
                db,
                tenant=tenant,
                to_mobile=inst.student.parent_mobile,
                kind="DECLARATION_ACK",
                dedupe_key=f"ack:{decl.id}",
                installment_id=inst.id,
                declaration_id=decl.id,
                student_name=inst.student.name,
            )

    audit(
        db,
        tenant_id=tenant.id,
        actor_type="PARENT",
        actor_id=parent_identifier or "payment_link",
        action=f"PARENT_{action}",
        source="parent_payment_page",
        declaration_id=decl.id,
        installment_id=inst.id,
        previous_status=prev_status,
        new_status=decl.declaration_status,
        details={"payment_type": payment_type, "declared_amount_paise": amount, "upi_app": upi_app},
    )
    db.flush()
    view = public_view(db, link)
    view["duplicate"] = False
    return view


def _append_note(existing: Optional[str], now: datetime, text: str) -> str:
    line = f"[{now.strftime('%Y-%m-%d %H:%M')} UTC] {text.strip()[:500]}"
    return f"{existing}\n{line}" if existing else line


def submit_parent_clarification(
    db: Session,
    link: PaymentLink,
    public_ref: str,
    *,
    note: str,
    upi_reference: Optional[str],
    approximate_payment_time: Optional[str],
    upi_app: Optional[str],
) -> Dict[str, Any]:
    now = utcnow()
    decl = (
        db.query(PaymentDeclaration)
        .filter(PaymentDeclaration.public_ref == public_ref, PaymentDeclaration.payment_link_id == link.id)
        .first()
    )
    if decl is None:
        # Also accept declarations made through an older link for the same installment.
        decl = (
            db.query(PaymentDeclaration)
            .filter(
                PaymentDeclaration.public_ref == public_ref,
                PaymentDeclaration.installment_id == link.installment_id,
            )
            .first()
        )
    if decl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Payment confirmation not found")
    try:
        new_status = check_admin_transition("parent_clarification", D(decl.declaration_status))
    except InvalidTransition:
        raise _conflict("This payment confirmation can no longer be updated")
    if upi_app is not None and upi_app not in UPI_APPS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown UPI application")

    prev = decl.declaration_status
    decl.clarification_note = _append_note(decl.clarification_note, now, note)
    if upi_reference:
        decl.parent_upi_reference = upi_reference.strip()[:64]
    if approximate_payment_time:
        decl.approximate_payment_time = approximate_payment_time[:50]
    if upi_app:
        decl.upi_app = upi_app
    decl.clarification_request = None
    decl.declaration_status = new_status.value
    decl.updated_at = now
    notify_admin(
        db,
        tenant_id=decl.tenant_id,
        kind="PARENT_CLARIFICATION",
        message=f"Parent added information to payment confirmation {decl.public_ref}.",
        declaration_id=decl.id,
        installment_id=decl.installment_id,
    )
    audit(
        db,
        tenant_id=decl.tenant_id,
        actor_type="PARENT",
        actor_id="payment_link",
        action="PARENT_CLARIFICATION",
        source="parent_payment_page",
        declaration_id=decl.id,
        installment_id=decl.installment_id,
        previous_status=prev,
        new_status=decl.declaration_status,
        details={"has_upi_reference": bool(upi_reference)},
    )
    db.flush()
    return public_view(db, link)


def payment_link_url(token: str) -> str:
    return f"{settings.public_base_url}/pay/{token}"
