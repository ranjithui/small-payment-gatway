"""Future-ready reconciliation.

Only *authorized* sources are supported: bank statement import (admin uploads the
statement downloaded from their bank) and, later, bank feeds / provider APIs that
push into ``bank_transactions`` through this module. No scraping of e-mail, SMS or
bank websites.

Matching currently produces *suggestions* for the admin; an imported transaction can
be credited only through the admin confirm flow, which guarantees a bank transaction
is never credited twice (unique constraints on bank_reference and bank_transaction_id).
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import audit
from app.db import to_naive_utc
from app.models import BankTransaction, PaymentDeclaration, VerifiedPayment
from app.security import AdminContext

MATCH_WINDOW = timedelta(hours=48)


def _is_used(db: Session, bt: BankTransaction) -> bool:
    return (
        db.query(VerifiedPayment)
        .filter(
            VerifiedPayment.tenant_id == bt.tenant_id,
            (VerifiedPayment.bank_transaction_id == bt.id) | (VerifiedPayment.bank_reference == bt.bank_reference),
        )
        .first()
        is not None
    )


def suggest_matches(db: Session, decl: PaymentDeclaration) -> List[Dict[str, Any]]:
    anchor = decl.declared_at or decl.created_at
    q = db.query(BankTransaction).filter(
        BankTransaction.tenant_id == decl.tenant_id,
        BankTransaction.credited_at >= anchor - MATCH_WINDOW,
        BankTransaction.credited_at <= anchor + MATCH_WINDOW,
    )
    out = []
    for bt in q.all():
        if _is_used(db, bt):
            continue
        score = 0
        if bt.amount_paise == decl.declared_amount_paise:
            score += 2
        if decl.parent_upi_reference and decl.parent_upi_reference == bt.bank_reference:
            score += 3
        student_code = decl.installment.student.student_code
        if bt.narration and student_code.lower() in bt.narration.lower():
            score += 2
        if score >= 2:
            out.append(
                {
                    "bank_transaction_id": bt.id,
                    "bank_reference": bt.bank_reference,
                    "amount_paise": bt.amount_paise,
                    "credited_at": bt.credited_at.isoformat() + "Z",
                    "payer_vpa": bt.payer_vpa,
                    "narration": bt.narration,
                    "score": score,
                }
            )
    return sorted(out, key=lambda m: -m["score"])


def import_transactions(db: Session, admin: AdminContext, source: str, rows: List[Dict[str, Any]]) -> Dict[str, int]:
    created = skipped = 0
    for row in rows:
        exists = (
            db.query(BankTransaction)
            .filter_by(tenant_id=admin.tenant_id, source=source, bank_reference=row["bank_reference"])
            .first()
        )
        if exists:
            skipped += 1
            continue
        db.add(
            BankTransaction(
                tenant_id=admin.tenant_id,
                source=source,
                bank_reference=row["bank_reference"],
                amount_paise=row["amount_paise"],
                credited_at=to_naive_utc(row["credited_at"]),
                credited_to_vpa=row.get("credited_to_vpa"),
                payer_vpa=row.get("payer_vpa"),
                narration=row.get("narration"),
                imported_by=admin.admin_id,
            )
        )
        created += 1
    audit(
        db,
        tenant_id=admin.tenant_id,
        actor_type="ADMIN",
        actor_id=admin.admin_id,
        action="BANK_TRANSACTIONS_IMPORTED",
        source=source.lower(),
        details={"created": created, "skipped_duplicates": skipped},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Duplicate bank transactions in import")
    return {"created": created, "skipped_duplicates": skipped}
