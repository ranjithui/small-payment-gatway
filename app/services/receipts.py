"""Receipt generation (only after verified payments)."""
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Installment, Receipt, Tenant
from app.security import new_secret_token


def receipt_url(receipt: Receipt) -> str:
    return f"{settings.public_base_url}/receipt/{receipt.public_token}"


def issue_receipt(
    db: Session,
    *,
    tenant: Tenant,
    installment: Installment,
    amount_paise: int,
    declaration_id: Optional[str],
    issued_by: Optional[str],
) -> Receipt:
    count = db.query(func.count(Receipt.id)).filter(Receipt.tenant_id == tenant.id).scalar() or 0
    receipt = Receipt(
        tenant_id=tenant.id,
        installment_id=installment.id,
        declaration_id=declaration_id,
        receipt_number=f"{tenant.short_code}-{count + 1:06d}",
        public_token=new_secret_token(18),
        amount_paise=amount_paise,
        total_paid_after_paise=installment.paid_paise,
        balance_after_paise=installment.outstanding_paise,
        issued_by=issued_by,
    )
    db.add(receipt)
    db.flush()
    return receipt
