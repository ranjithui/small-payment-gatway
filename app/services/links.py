"""Secure payment links. Only the SHA-256 of a token is stored; the raw token lives in the URL."""
from datetime import datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.config import settings
from app.db import utcnow
from app.models import Installment, PaymentLink
from app.security import new_secret_token, sha256_hex


def create_payment_link(
    db: Session, installment: Installment, valid_days: int = 30, expires_at: Optional[datetime] = None
) -> Tuple[PaymentLink, str]:
    token = new_secret_token(24)
    link = PaymentLink(
        tenant_id=installment.tenant_id,
        installment_id=installment.id,
        token_hash=sha256_hex(token),
        expires_at=expires_at or (utcnow() + timedelta(days=valid_days)),
    )
    db.add(link)
    db.flush()
    return link, token


def link_url(token: str) -> str:
    return f"{settings.public_base_url}/pay/{token}"
