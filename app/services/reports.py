"""The eight operational reports (all tenant-scoped, read-only)."""
from typing import Any, Callable, Dict, List

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Installment, PaymentAdjustment, PaymentDeclaration, Student, VerifiedPayment
from app.services.declarations import _iso
from app.states import REVIEWABLE_STATUSES


def _decl_row(d: PaymentDeclaration) -> Dict[str, Any]:
    i = d.installment
    return {
        "declaration_id": d.id,
        "reference": d.public_ref,
        "student_name": i.student.name,
        "student_id": i.student.student_code,
        "installment": i.description,
        "billing_month": i.billing_month,
        "declared_amount_paise": d.declared_amount_paise,
        "payment_type": d.payment_type,
        "upi_app": d.upi_app,
        "status": d.declaration_status,
        "declared_at": _iso(d.declared_at),
        "reviewed_at": _iso(d.reviewed_at),
        "rejection_reason": d.rejection_reason,
    }


def _decls(db: Session, tenant_id: str, statuses=None) -> List[Dict[str, Any]]:
    q = db.query(PaymentDeclaration).filter(PaymentDeclaration.tenant_id == tenant_id)
    if statuses:
        q = q.filter(PaymentDeclaration.declaration_status.in_(statuses))
    return [_decl_row(d) for d in q.order_by(PaymentDeclaration.created_at.desc()).all()]


def _inst_row(i: Installment) -> Dict[str, Any]:
    return {
        "installment_id": i.id,
        "student_name": i.student.name,
        "student_id": i.student.student_code,
        "parent_mobile": i.student.parent_mobile,
        "installment": i.description,
        "billing_month": i.billing_month,
        "due_date": i.due_date.isoformat(),
        "total_paise": i.total_paise,
        "paid_paise": i.paid_paise,
        "outstanding_paise": i.outstanding_paise,
        "excess_paise": i.excess_paise,
        "status": i.status,
    }


def declarations_report(db, tenant_id):
    return _decls(db, tenant_id)


def pending_report(db, tenant_id):
    return _decls(db, tenant_id, [s.value for s in REVIEWABLE_STATUSES])


def rejected_report(db, tenant_id):
    return _decls(db, tenant_id, ["REJECTED"])


def verified_report(db, tenant_id):
    rows = (
        db.query(VerifiedPayment, Installment)
        .join(Installment, Installment.id == VerifiedPayment.installment_id)
        .filter(VerifiedPayment.tenant_id == tenant_id)
        .order_by(VerifiedPayment.verified_at.desc())
        .all()
    )
    return [
        {
            "verified_payment_id": p.id,
            "student_name": i.student.name,
            "student_id": i.student.student_code,
            "installment": i.description,
            "amount_paise": p.amount_paise,
            "applied_paise": p.applied_paise,
            "excess_paise": p.excess_paise,
            "bank_reference": p.bank_reference,
            "reconciliation_source": p.reconciliation_source,
            "verified_by": p.verified_by,
            "verified_at": _iso(p.verified_at),
        }
        for p, i in rows
    ]


def partial_report(db, tenant_id):
    insts = db.query(Installment).filter(Installment.tenant_id == tenant_id, Installment.status == "PARTIALLY_PAID").all()
    return [_inst_row(i) for i in insts]


def duplicate_report(db, tenant_id):
    """Installments that received more than one verified credit and ended with an excess."""
    multi = (
        db.query(VerifiedPayment.installment_id, func.count(VerifiedPayment.id))
        .filter(VerifiedPayment.tenant_id == tenant_id)
        .group_by(VerifiedPayment.installment_id)
        .having(func.count(VerifiedPayment.id) > 1)
        .all()
    )
    out = []
    for inst_id, n in multi:
        i = db.get(Installment, inst_id)
        if i.excess_paise > 0:
            row = _inst_row(i)
            row["verified_payment_count"] = n
            out.append(row)
    return out


def excess_report(db, tenant_id):
    rows = (
        db.query(PaymentAdjustment, Installment)
        .join(Installment, Installment.id == PaymentAdjustment.installment_id)
        .filter(PaymentAdjustment.tenant_id == tenant_id)
        .order_by(PaymentAdjustment.created_at.desc())
        .all()
    )
    return [
        {
            "adjustment_id": a.id,
            "student_name": i.student.name,
            "student_id": i.student.student_code,
            "installment": i.description,
            "amount_paise": a.amount_paise,
            "status": a.status,
            "note": a.note,
            "created_at": _iso(a.created_at),
            "resolved_at": _iso(a.resolved_at),
        }
        for a, i in rows
    ]


def outstanding_report(db, tenant_id):
    insts = (
        db.query(Installment)
        .join(Student)
        .filter(Installment.tenant_id == tenant_id, Installment.status != "PAID")
        .order_by(Installment.due_date)
        .all()
    )
    return [_inst_row(i) for i in insts]


REPORTS: Dict[str, Callable[[Session, str], List[Dict[str, Any]]]] = {
    "payment-declarations": declarations_report,
    "pending-verification": pending_report,
    "verified-payments": verified_report,
    "rejected-declarations": rejected_report,
    "partial-payments": partial_report,
    "duplicate-payments": duplicate_report,
    "excess-payments": excess_report,
    "outstanding-fees": outstanding_report,
}
