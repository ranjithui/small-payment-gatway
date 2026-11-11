"""Administrator payment verification (the only path that creates verified payments)."""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.audit import audit, notify_admin
from app.db import to_naive_utc, utcnow
from app.models import (
    BankTransaction,
    Installment,
    PaymentAdjustment,
    PaymentDeclaration,
    ReminderSchedule,
    Receipt,
    SmsOutbox,
    Tenant,
    VerifiedPayment,
)
from app.security import AdminContext
from app.services import reminders
from app.services.declarations import _iso, active_declaration
from app.services.links import create_payment_link, link_url
from app.services.receipts import issue_receipt, receipt_url
from app.services.sms import enqueue_sms, format_rupees
from app.states import (
    REVIEWABLE_STATUSES,
    DeclarationStatus as D,
    InstallmentStatus,
    InvalidTransition,
    check_admin_transition,
    installment_status_for,
)


# ------------------------------------------------------------------ lookups
def get_declaration(db: Session, admin: AdminContext, declaration_id: str, lock: bool = False) -> PaymentDeclaration:
    q = db.query(PaymentDeclaration).filter(
        PaymentDeclaration.id == declaration_id, PaymentDeclaration.tenant_id == admin.tenant_id
    )
    if lock:
        q = q.with_for_update()
    decl = q.first()
    if decl is None:  # also covers other tenants' declarations (tenant isolation)
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Declaration not found")
    return decl


def _lock_installment(db: Session, installment_id: str) -> Installment:
    return db.query(Installment).filter(Installment.id == installment_id).with_for_update().one()


def _transition(action: str, decl: PaymentDeclaration) -> D:
    try:
        return check_admin_transition(action, D(decl.declaration_status))
    except InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))


# ------------------------------------------------------------------ serialisation
def serialize_declaration(db: Session, decl: PaymentDeclaration, detail: bool = False) -> Dict[str, Any]:
    inst = decl.installment
    student = inst.student
    out: Dict[str, Any] = {
        "id": decl.id,
        "reference": decl.public_ref,
        "student_name": student.name,
        "student_id": student.student_code,
        "parent_mobile": student.parent_mobile,
        "installment": {
            "id": inst.id,
            "description": inst.description,
            "billing_month": inst.billing_month,
            "due_date": inst.due_date.isoformat(),
            "total_paise": inst.total_paise,
            "paid_paise": inst.paid_paise,
            "excess_paise": inst.excess_paise,
            "status": inst.status,
        },
        "expected_amount_paise": inst.outstanding_paise,
        "declared_amount_paise": decl.declared_amount_paise,
        "payment_type": decl.payment_type,
        "declared_at": _iso(decl.declared_at),
        "created_at": _iso(decl.created_at),
        "upi_app": decl.upi_app,
        "approximate_payment_time": decl.approximate_payment_time,
        "parent_notes": decl.clarification_note,
        "parent_upi_reference": decl.parent_upi_reference,
        "outstanding_paise": inst.outstanding_paise,
        "verification_status": decl.declaration_status,
        "clarification_request": decl.clarification_request,
        "clarification_requested_at": _iso(decl.clarification_requested_at),
        "admin_note": decl.admin_note,
        "rejection_reason": decl.rejection_reason,
        "reviewed_at": _iso(decl.reviewed_at),
        "grace_expires_at": _iso(decl.grace_expires_at),
        "verification_overdue": decl.grace_expiry_notified_at is not None,
    }
    if detail:
        payments = (
            db.query(VerifiedPayment)
            .filter(VerifiedPayment.installment_id == inst.id)
            .order_by(VerifiedPayment.verified_at)
            .all()
        )
        others = (
            db.query(PaymentDeclaration)
            .filter(PaymentDeclaration.installment_id == inst.id, PaymentDeclaration.id != decl.id)
            .order_by(PaymentDeclaration.created_at.desc())
            .all()
        )
        student_history = (
            db.query(VerifiedPayment, Installment)
            .join(Installment, Installment.id == VerifiedPayment.installment_id)
            .filter(Installment.student_id == student.id, Installment.id != inst.id)
            .order_by(VerifiedPayment.verified_at.desc())
            .limit(10)
            .all()
        )
        rems = db.query(ReminderSchedule).filter(ReminderSchedule.installment_id == inst.id).all()
        from app.services.reconciliation import suggest_matches

        out.update(
            {
                "verified_payments": [_payment_out(p) for p in payments],
                "previous_declarations": [
                    {
                        "reference": d.public_ref,
                        "status": d.declaration_status,
                        "declared_amount_paise": d.declared_amount_paise,
                        "declared_at": _iso(d.declared_at),
                        "rejection_reason": d.rejection_reason,
                    }
                    for d in others
                ],
                "student_payment_history": [
                    {
                        "installment": i.description,
                        "billing_month": i.billing_month,
                        "amount_paise": p.amount_paise,
                        "verified_at": _iso(p.verified_at),
                    }
                    for p, i in student_history
                ],
                "reminders": [
                    {
                        "scheduled_for": _iso(r.scheduled_for),
                        "amount_paise": r.amount_paise,
                        "status": r.status,
                        "sync_status": r.sync_status,
                    }
                    for r in rems
                ],
                "suggested_bank_matches": suggest_matches(db, decl),
            }
        )
    return out


def _payment_out(p: VerifiedPayment) -> Dict[str, Any]:
    return {
        "id": p.id,
        "amount_paise": p.amount_paise,
        "applied_paise": p.applied_paise,
        "excess_paise": p.excess_paise,
        "bank_reference": p.bank_reference,
        "paid_at": _iso(p.paid_at),
        "verified_at": _iso(p.verified_at),
        "verified_by": p.verified_by,
        "reconciliation_source": p.reconciliation_source,
        "declaration_id": p.declaration_id,
    }


def pending_queue(db: Session, admin: AdminContext) -> List[Dict[str, Any]]:
    decls = (
        db.query(PaymentDeclaration)
        .filter(
            PaymentDeclaration.tenant_id == admin.tenant_id,
            PaymentDeclaration.declaration_status.in_([s.value for s in REVIEWABLE_STATUSES]),
        )
        .order_by(PaymentDeclaration.declared_at.is_(None), PaymentDeclaration.declared_at, PaymentDeclaration.created_at)
        .all()
    )
    return [serialize_declaration(db, d) for d in decls]


def acknowledge(db: Session, admin: AdminContext, decl: PaymentDeclaration) -> None:
    """Admin opened a freshly reported declaration: PAYMENT_REPORTED -> PENDING_VERIFICATION."""
    if decl.declaration_status != D.PAYMENT_REPORTED.value:
        return
    prev = decl.declaration_status
    decl.declaration_status = _transition("acknowledge", decl).value
    audit(
        db,
        tenant_id=admin.tenant_id,
        actor_type="ADMIN",
        actor_id=admin.admin_id,
        action="DECLARATION_OPENED_FOR_REVIEW",
        source="admin_dashboard",
        declaration_id=decl.id,
        installment_id=decl.installment_id,
        previous_status=prev,
        new_status=decl.declaration_status,
    )
    db.commit()


# ------------------------------------------------------------------ recording money
def apply_transactions(
    db: Session,
    *,
    admin: AdminContext,
    tenant: Tenant,
    inst: Installment,
    transactions: List[Dict[str, Any]],
    evidence_note: Optional[str],
    declaration: Optional[PaymentDeclaration],
    source: str,
) -> Dict[str, Any]:
    """Create verified payments, update the balance, flag excess, issue the receipt. No commit."""
    now = utcnow()
    if not transactions:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="At least one bank transaction is required")

    seen_refs = set()
    resolved: List[Dict[str, Any]] = []
    for tx in transactions:
        ref = (tx.get("bank_reference") or "").strip() or None
        bank_tx = None
        if tx.get("bank_transaction_id"):
            bank_tx = (
                db.query(BankTransaction)
                .filter(BankTransaction.id == tx["bank_transaction_id"], BankTransaction.tenant_id == tenant.id)
                .first()
            )
            if bank_tx is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Bank transaction not found")
            if bank_tx.amount_paise != tx["amount_paise"]:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Amount does not match the imported bank transaction",
                )
            ref = ref or bank_tx.bank_reference
            if db.query(VerifiedPayment).filter(VerifiedPayment.bank_transaction_id == bank_tx.id).first():
                raise HTTPException(
                    status.HTTP_409_CONFLICT, detail="This bank transaction has already been recorded"
                )
        if not ref and not (evidence_note and evidence_note.strip()):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Record the bank reference (UTR) or an evidence note for every transaction",
            )
        if ref:
            if ref in seen_refs:
                raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Bank reference {ref} is listed twice")
            seen_refs.add(ref)
            dup = (
                db.query(VerifiedPayment)
                .filter(VerifiedPayment.tenant_id == tenant.id, VerifiedPayment.bank_reference == ref)
                .first()
            )
            if dup is not None:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail=f"Bank transaction {ref} has already been credited. A transaction can never be credited twice.",
                )
        resolved.append({**tx, "bank_reference": ref, "bank_tx": bank_tx})

    prev_inst_status = inst.status
    payments: List[VerifiedPayment] = []
    adjustments: List[PaymentAdjustment] = []
    for tx in resolved:
        amount = int(tx["amount_paise"])
        applied = min(amount, inst.outstanding_paise)
        excess = amount - applied
        vp = VerifiedPayment(
            tenant_id=tenant.id,
            installment_id=inst.id,
            declaration_id=declaration.id if declaration else None,
            bank_transaction_id=tx["bank_tx"].id if tx["bank_tx"] else None,
            amount_paise=amount,
            applied_paise=applied,
            excess_paise=excess,
            bank_reference=tx["bank_reference"],
            paid_at=to_naive_utc(tx.get("paid_at")),
            payment_mode="UPI",
            evidence_note=evidence_note,
            reconciliation_source="BANK_IMPORT_MATCH" if tx["bank_tx"] else source,
            verified_by=admin.admin_id,
            verified_at=now,
        )
        db.add(vp)
        db.flush()
        inst.paid_paise += applied
        inst.excess_paise += excess
        payments.append(vp)
        audit(
            db,
            tenant_id=tenant.id,
            actor_type="ADMIN",
            actor_id=admin.admin_id,
            action="VERIFIED_PAYMENT_RECORDED",
            source=source.lower(),
            declaration_id=declaration.id if declaration else None,
            installment_id=inst.id,
            details={
                "verified_payment_id": vp.id,
                "amount_paise": amount,
                "applied_paise": applied,
                "excess_paise": excess,
                "bank_reference": vp.bank_reference,
                "evidence_note": evidence_note,
            },
        )
        if excess > 0:
            adj = PaymentAdjustment(
                tenant_id=tenant.id,
                installment_id=inst.id,
                verified_payment_id=vp.id,
                amount_paise=excess,
                kind="EXCESS",
                status="FLAGGED",
                note="Payment exceeds outstanding balance - refund or adjust against a future fee",
            )
            db.add(adj)
            adjustments.append(adj)
            notify_admin(
                db,
                tenant_id=tenant.id,
                kind="EXCESS_PAYMENT",
                message=(
                    f"Excess payment of Rs. {format_rupees(excess)} for {inst.student.name} "
                    f"({inst.description}) flagged for refund or adjustment."
                ),
                installment_id=inst.id,
                declaration_id=declaration.id if declaration else None,
            )

    inst.status = installment_status_for(inst.total_paise, inst.paid_paise).value
    inst.updated_at = now
    if inst.status != prev_inst_status:
        audit(
            db,
            tenant_id=tenant.id,
            actor_type="ADMIN",
            actor_id=admin.admin_id,
            action="INSTALLMENT_STATUS_CHANGED",
            source=source.lower(),
            declaration_id=declaration.id if declaration else None,
            installment_id=inst.id,
            previous_status=prev_inst_status,
            new_status=inst.status,
        )

    total = sum(p.amount_paise for p in payments)
    receipt = issue_receipt(
        db,
        tenant=tenant,
        installment=inst,
        amount_paise=total,
        declaration_id=declaration.id if declaration else None,
        issued_by=admin.admin_id,
    )
    for p in payments:
        p.receipt_id = receipt.id

    # Reminders: stop for the settled amount, keep only the remaining balance.
    if inst.status == InstallmentStatus.PAID.value:
        reminders.cancel_all(db, inst, reason="Installment fully paid")
    else:
        reminders.rebase_to_outstanding(db, inst)

    return {"payments": payments, "receipt": receipt, "adjustments": adjustments, "total_paise": total}


def confirm(db: Session, admin: AdminContext, declaration_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    if not (body.get("confirm") and body.get("bank_transaction_checked") and body.get("belongs_to_centre")):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Confirm that you checked the actual bank transaction and that it was received in the centre's "
                "account before verifying."
            ),
        )
    decl = get_declaration(db, admin, declaration_id, lock=True)
    new_status = _transition("confirm", decl)
    inst = _lock_installment(db, decl.installment_id)
    tenant: Tenant = inst.tenant
    transactions = body["transactions"]
    total = sum(int(t["amount_paise"]) for t in transactions)
    if total != decl.declared_amount_paise and not body.get("amount_difference_acknowledged"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Verified amount Rs. {format_rupees(total)} differs from the declared amount "
                f"Rs. {format_rupees(decl.declared_amount_paise)}. Acknowledge the difference to continue."
            ),
        )

    result = apply_transactions(
        db,
        admin=admin,
        tenant=tenant,
        inst=inst,
        transactions=transactions,
        evidence_note=body.get("evidence_note"),
        declaration=decl,
        source="ADMIN_VERIFICATION",
    )
    now = utcnow()
    prev = decl.declaration_status
    decl.declaration_status = new_status.value
    decl.verified_payment_id = result["payments"][0].id
    decl.reviewed_by = admin.admin_id
    decl.reviewed_at = now
    decl.clarification_request = None
    if body.get("admin_note"):
        decl.admin_note = body["admin_note"]
    decl.updated_at = now

    receipt: Receipt = result["receipt"]
    enqueue_sms(
        db,
        tenant=tenant,
        to_mobile=inst.student.parent_mobile,
        kind="PAYMENT_VERIFIED",
        dedupe_key=f"verified:{decl.id}",
        installment_id=inst.id,
        declaration_id=decl.id,
        amount=format_rupees(result["total_paise"]),
        student_name=inst.student.name,
        receipt_link=receipt_url(receipt),
    )
    audit(
        db,
        tenant_id=tenant.id,
        actor_type="ADMIN",
        actor_id=admin.admin_id,
        action="DECLARATION_VERIFIED",
        source="admin_dashboard",
        declaration_id=decl.id,
        installment_id=inst.id,
        previous_status=prev,
        new_status=decl.declaration_status,
        reason=body.get("admin_note"),
        details={
            "verified_total_paise": result["total_paise"],
            "declared_amount_paise": decl.declared_amount_paise,
            "receipt_number": receipt.receipt_number,
            "installment_status": inst.status,
            "evidence_note": body.get("evidence_note"),
        },
    )
    db.flush()
    return {
        "declaration": serialize_declaration(db, decl),
        "installment_status": inst.status,
        "outstanding_paise": inst.outstanding_paise,
        "receipt": {"receipt_number": receipt.receipt_number, "url": receipt_url(receipt)},
        "payments": [_payment_out(p) for p in result["payments"]],
        "excess_flagged_paise": sum(a.amount_paise for a in result["adjustments"]),
    }


def record_manual_payment(
    db: Session, admin: AdminContext, installment_id: str, body: Dict[str, Any]
) -> Dict[str, Any]:
    """Record a real bank credit with no declaration (e.g. a parent's accidental second transfer)."""
    if not (body.get("confirm") and body.get("bank_transaction_checked") and body.get("belongs_to_centre")):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Confirm that you checked the actual bank transaction before recording it.",
        )
    inst = (
        db.query(Installment)
        .filter(Installment.id == installment_id, Installment.tenant_id == admin.tenant_id)
        .with_for_update()
        .first()
    )
    if inst is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Installment not found")
    result = apply_transactions(
        db,
        admin=admin,
        tenant=inst.tenant,
        inst=inst,
        transactions=body["transactions"],
        evidence_note=body.get("evidence_note"),
        declaration=None,
        source="ADMIN_MANUAL",
    )
    receipt = result["receipt"]
    enqueue_sms(
        db,
        tenant=inst.tenant,
        to_mobile=inst.student.parent_mobile,
        kind="PAYMENT_VERIFIED",
        dedupe_key=f"verified-manual:{result['payments'][0].id}",
        installment_id=inst.id,
        amount=format_rupees(result["total_paise"]),
        student_name=inst.student.name,
        receipt_link=receipt_url(receipt),
    )
    db.flush()
    return {
        "installment_status": inst.status,
        "outstanding_paise": inst.outstanding_paise,
        "receipt": {"receipt_number": receipt.receipt_number, "url": receipt_url(receipt)},
        "payments": [_payment_out(p) for p in result["payments"]],
        "excess_flagged_paise": sum(a.amount_paise for a in result["adjustments"]),
    }


# ------------------------------------------------------------------ reject / clarify / reopen
def reject(db: Session, admin: AdminContext, declaration_id: str, reason: str, admin_note: Optional[str]) -> Dict[str, Any]:
    decl = get_declaration(db, admin, declaration_id, lock=True)
    new_status = _transition("reject", decl)
    inst = _lock_installment(db, decl.installment_id)
    now = utcnow()
    prev = decl.declaration_status
    decl.declaration_status = new_status.value
    decl.rejection_reason = reason
    decl.reviewed_by = admin.admin_id
    decl.reviewed_at = now
    decl.clarification_request = None
    if admin_note:
        decl.admin_note = admin_note
    decl.updated_at = now
    reminders.restore_after_rejection(db, inst, inst.tenant, now)
    enqueue_sms(
        db,
        tenant=inst.tenant,
        to_mobile=inst.student.parent_mobile,
        kind="PAYMENT_REJECTED",
        dedupe_key=f"rejected:{decl.id}:{now.isoformat()}",
        installment_id=inst.id,
        declaration_id=decl.id,
        student_name=inst.student.name,
    )
    audit(
        db,
        tenant_id=admin.tenant_id,
        actor_type="ADMIN",
        actor_id=admin.admin_id,
        action="DECLARATION_REJECTED",
        source="admin_dashboard",
        declaration_id=decl.id,
        installment_id=inst.id,
        previous_status=prev,
        new_status=decl.declaration_status,
        reason=reason,
    )
    db.flush()
    return {"declaration": serialize_declaration(db, decl), "installment_status": inst.status}


def request_clarification(db: Session, admin: AdminContext, declaration_id: str, message: str) -> Dict[str, Any]:
    decl = get_declaration(db, admin, declaration_id, lock=True)
    new_status = _transition("request_clarification", decl)
    inst = decl.installment
    now = utcnow()
    prev = decl.declaration_status
    decl.declaration_status = new_status.value
    decl.clarification_request = message
    decl.clarification_requested_at = now
    decl.updated_at = now
    _, token = create_payment_link(db, inst, valid_days=14)
    enqueue_sms(
        db,
        tenant=inst.tenant,
        to_mobile=inst.student.parent_mobile,
        kind="CLARIFICATION_REQUESTED",
        dedupe_key=f"clarify:{decl.id}:{now.isoformat()}",
        installment_id=inst.id,
        declaration_id=decl.id,
        student_name=inst.student.name,
        payment_link=link_url(token),
    )
    audit(
        db,
        tenant_id=admin.tenant_id,
        actor_type="ADMIN",
        actor_id=admin.admin_id,
        action="CLARIFICATION_REQUESTED",
        source="admin_dashboard",
        declaration_id=decl.id,
        installment_id=inst.id,
        previous_status=prev,
        new_status=decl.declaration_status,
        reason=message,
    )
    db.flush()
    return {"declaration": serialize_declaration(db, decl), "installment_status": inst.status}


def reopen(db: Session, admin: AdminContext, declaration_id: str, reason: str) -> Dict[str, Any]:
    decl = get_declaration(db, admin, declaration_id, lock=True)
    new_status = _transition("reopen", decl)
    inst = _lock_installment(db, decl.installment_id)
    if inst.status == InstallmentStatus.PAID.value:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Installment is already fully paid")
    other = active_declaration(db, inst.id)
    if other is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Another declaration ({other.public_ref}) is already active for this installment",
        )
    now = utcnow()
    prev = decl.declaration_status
    decl.declaration_status = new_status.value
    decl.rejection_reason = None
    decl.reviewed_by = None
    decl.reviewed_at = None
    decl.grace_expires_at = reminders.pause_for_verification(db, inst, inst.tenant, now)
    decl.grace_expiry_notified_at = None
    decl.updated_at = now
    audit(
        db,
        tenant_id=admin.tenant_id,
        actor_type="ADMIN",
        actor_id=admin.admin_id,
        action="DECLARATION_REOPENED",
        source="admin_dashboard",
        declaration_id=decl.id,
        installment_id=inst.id,
        previous_status=prev,
        new_status=decl.declaration_status,
        reason=reason,
    )
    db.flush()
    return {"declaration": serialize_declaration(db, decl), "installment_status": inst.status}


def installment_payment_status(db: Session, admin: AdminContext, installment_id: str) -> Dict[str, Any]:
    from app.states import display_status as _display
    from app.services.declarations import latest_declaration

    inst = (
        db.query(Installment)
        .filter(Installment.id == installment_id, Installment.tenant_id == admin.tenant_id)
        .first()
    )
    if inst is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Installment not found")
    active = active_declaration(db, inst.id)
    latest = active or latest_declaration(db, inst.id)
    rems = db.query(ReminderSchedule).filter(ReminderSchedule.installment_id == inst.id).all()
    pending_sms = (
        db.query(SmsOutbox)
        .filter(SmsOutbox.installment_id == inst.id, SmsOutbox.status.in_(["QUEUED", "DISPATCHED"]))
        .count()
    )
    return {
        "installment_id": inst.id,
        "installment_status": inst.status,
        "declaration_status": latest.declaration_status if latest else None,
        "active_declaration_id": active.id if active else None,
        "display_status": _display(
            InstallmentStatus(inst.status),
            D(active.declaration_status) if active else None,
            D(latest.declaration_status) if latest else None,
            False,
        ).value,
        "total_paise": inst.total_paise,
        "paid_paise": inst.paid_paise,
        "outstanding_paise": inst.outstanding_paise,
        "excess_paise": inst.excess_paise,
        "reminders_paused_until": _iso(inst.reminders_paused_until),
        "reminders": [
            {
                "id": r.id,
                "scheduled_for": _iso(r.scheduled_for),
                "amount_paise": r.amount_paise,
                "status": r.status,
                "sync_status": r.sync_status,
                "version": r.version,
            }
            for r in rems
        ],
        "pending_sms": pending_sms,
    }
