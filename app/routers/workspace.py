"""Workspace (tuition centre) self-setup by the signed-in admin.

The host configures only the admin's email and password. After signing in, the admin creates
their workspace (centre name, UPI ID, payee name, contact) and can edit it later in Settings.
"""
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.audit import audit
from app.db import get_db
from app.models import AdminUser, Tenant
from app.ratelimit import rate_limit
from app.schemas import SignupRequest, WorkspaceCreate, WorkspaceUpdate
from app.security import AdminContext, SessionContext, hash_password, issue_admin_token, require_admin, require_session
from app.services.subscription import subscription_out
from app.services.upi import DEFAULT_MERCHANT_CODE

router = APIRouter(prefix="/api/v1", tags=["workspace"])
limit = Depends(rate_limit("admin", 240))

EDITABLE = ("name", "upi_vpa", "upi_payee_name", "contact_phone", "contact_email",
            "verification_grace_hours", "reminder_frequency_hours", "ack_sms_enabled", "auto_send_links",
            "upi_is_merchant", "merchant_code")


def workspace_out(t: Optional[Tenant]) -> Optional[dict]:
    if t is None:
        return None
    return {
        "name": t.name,
        "short_code": t.short_code,
        "logo_url": t.logo_url,
        "upi_vpa": t.upi_vpa,
        "upi_payee_name": t.upi_payee_name,
        "contact_phone": t.contact_phone,
        "contact_email": t.contact_email,
        "verification_grace_hours": t.verification_grace_hours,
        "reminder_frequency_hours": t.reminder_frequency_hours,
        "ack_sms_enabled": t.ack_sms_enabled,
        "auto_send_links": bool(t.auto_send_links),
        "upi_is_merchant": bool(t.upi_is_merchant),
        "merchant_code": t.merchant_code,
        "subscription": subscription_out(t),
    }


def _unique_code(db: Session, wanted: Optional[str], name: str) -> str:
    if wanted:
        code = wanted.upper()
        if db.query(Tenant).filter(Tenant.short_code == code).first():
            raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Receipt prefix {code} is already used")
        return code
    words = re.findall(r"[A-Za-z0-9]+", name)
    base = ("".join(w[0] for w in words) or "FF").upper()[:6]
    if len(base) < 2:
        base = (base + re.sub(r"[^A-Za-z0-9]", "", name).upper() + "FF")[:4]
    code, n = base, 1
    while db.query(Tenant).filter(Tenant.short_code == code).first():
        n += 1
        code = f"{base}{n}"
    return code


@router.post("/auth/signup", dependencies=[Depends(rate_limit("signup", 5, 3600))], status_code=201, tags=["auth"])
def signup(body: SignupRequest, db: Session = Depends(get_db)):
    """New centre owner creates an account; next step is creating their workspace (30-day trial)."""
    email = body.email.strip().lower()
    if db.query(AdminUser.id).filter(AdminUser.email == email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, detail="An account with this email already exists. Please sign in.")
    admin = AdminUser(tenant_id=None, email=email, name=body.name.strip(), password_hash=hash_password(body.password))
    db.add(admin)
    db.commit()
    return {"access_token": issue_admin_token(admin), "token_type": "bearer", "name": admin.name,
            "has_workspace": False, "is_platform_admin": False}


@router.get("/auth/me", dependencies=[limit], tags=["auth"])
def me(session: SessionContext = Depends(require_session), db: Session = Depends(get_db)):
    t = db.get(Tenant, session.tenant_id) if session.tenant_id else None
    return {
        "admin_id": session.admin_id,
        "name": session.name,
        "email": session.email,
        "has_workspace": t is not None,
        "is_platform_admin": session.is_platform_admin,
        # `centre` kept for older app builds
        "centre": {"name": t.name, "logo_url": t.logo_url} if t else None,
        "workspace": workspace_out(t),
    }


@router.post("/workspace", dependencies=[limit], status_code=201)
def create_workspace(body: WorkspaceCreate, session: SessionContext = Depends(require_session),
                     db: Session = Depends(get_db)):
    admin = db.query(AdminUser).filter(AdminUser.id == session.admin_id).with_for_update().one()
    if admin.tenant_id is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Your workspace already exists - edit it in Settings")
    t = Tenant(
        name=body.name.strip(),
        short_code=_unique_code(db, body.short_code, body.name),
        upi_vpa=body.upi_vpa,
        upi_payee_name=body.upi_payee_name.strip(),
        contact_phone=(body.contact_phone or "").strip() or None,
        contact_email=(body.contact_email or "").strip() or None,
        upi_is_merchant=body.upi_is_merchant,
        merchant_code=(body.merchant_code or DEFAULT_MERCHANT_CODE) if body.upi_is_merchant else None,
    )
    db.add(t)
    db.flush()
    admin.tenant_id = t.id
    audit(db, tenant_id=t.id, actor_type="ADMIN", actor_id=admin.id, action="WORKSPACE_CREATED",
          source="workspace_setup", details=workspace_out(t))
    db.commit()
    return workspace_out(t)


@router.get("/workspace", dependencies=[limit])
def get_workspace(admin: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    return workspace_out(db.get(Tenant, admin.tenant_id))


@router.patch("/workspace", dependencies=[limit])
def update_workspace(body: WorkspaceUpdate, admin: AdminContext = Depends(require_admin),
                     db: Session = Depends(get_db)):
    t = db.query(Tenant).filter(Tenant.id == admin.tenant_id).with_for_update().one()
    changes = {}
    for field in EDITABLE:
        value = getattr(body, field)
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip() or None
            if value is None and field in ("name", "upi_vpa", "upi_payee_name"):
                continue
        old = getattr(t, field)
        if old != value:
            changes[field] = {"from": old, "to": value}
            setattr(t, field, value)
    if t.upi_is_merchant and not t.merchant_code:
        t.merchant_code = DEFAULT_MERCHANT_CODE
        changes.setdefault("merchant_code", {"from": None, "to": DEFAULT_MERCHANT_CODE})
    if changes:
        # The UPI ID decides where parents' money goes: always audited with old and new values.
        audit(db, tenant_id=t.id, actor_type="ADMIN", actor_id=admin.admin_id,
              action="UPI_ID_CHANGED" if "upi_vpa" in changes else "WORKSPACE_UPDATED",
              source="settings", details=changes)
        db.commit()
    return workspace_out(t)
