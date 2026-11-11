"""Audit logging and admin notifications."""
import json
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models import AdminNotification, AuditLog


def audit(
    db: Session,
    *,
    tenant_id: str,
    actor_type: str,
    actor_id: Optional[str],
    action: str,
    source: str,
    declaration_id: Optional[str] = None,
    installment_id: Optional[str] = None,
    previous_status: Optional[str] = None,
    new_status: Optional[str] = None,
    reason: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> AuditLog:
    entry = AuditLog(
        tenant_id=tenant_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        source=source,
        declaration_id=declaration_id,
        installment_id=installment_id,
        previous_status=previous_status,
        new_status=new_status,
        reason=reason,
        details_json=json.dumps(details, default=str) if details else None,
    )
    db.add(entry)
    return entry


def notify_admin(
    db: Session,
    *,
    tenant_id: str,
    kind: str,
    message: str,
    declaration_id: Optional[str] = None,
    installment_id: Optional[str] = None,
) -> AdminNotification:
    n = AdminNotification(
        tenant_id=tenant_id, kind=kind, message=message, declaration_id=declaration_id, installment_id=installment_id
    )
    db.add(n)
    return n
