"""Human-readable payment reference per fee, e.g. "ST-0001-2026-11".

Used as the UPI transaction note (tn), on the QR, in manual payment details, in the admin
verification screens and on receipts, so the centre can match it against its bank statement.
Unique per centre: a second fee for the same student in the same month gets "-2", "-3", ...
"""
from sqlalchemy.orm import Session

from app.models import Installment, Student


def _base(inst: Installment, student: Student) -> str:
    return f"{student.student_code}-{inst.billing_month}"[:50]


def ensure_reference(db: Session, inst: Installment) -> str:
    """Assign the fee's reference if it has none yet (no commit). Returns it."""
    if inst.payment_reference:
        return inst.payment_reference
    student = inst.student if inst.student is not None else db.get(Student, inst.student_id)
    base = _base(inst, student)
    ref, n = base, 1
    while db.query(Installment.id).filter(Installment.tenant_id == inst.tenant_id,
                                          Installment.payment_reference == ref,
                                          Installment.id != inst.id).first():
        n += 1
        ref = f"{base[:46]}-{n}"
    inst.payment_reference = ref
    db.flush()
    return ref


def backfill_references(db: Session) -> int:
    """Give every existing fee a reference (runs on start-up; idempotent)."""
    rows = (db.query(Installment).filter(Installment.payment_reference.is_(None))
            .order_by(Installment.created_at).all())
    for inst in rows:
        ensure_reference(db, inst)
    db.commit()
    return len(rows)
