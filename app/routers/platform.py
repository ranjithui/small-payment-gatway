"""Platform owner console API: every workspace, its trial/subscription state, and actions.

Only aggregate counts are returned - never students' or parents' personal details.
"""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.audit import audit
from app.db import get_db, utcnow
from app.models import AdminUser, AuditLog, Device, Installment, Student, Tenant
from app.ratelimit import rate_limit
from app.schemas import PlatformExtendTrial, PlatformSuspend
from app.security import SessionContext, require_platform_admin
from app.services.declarations import _iso
from app.services.subscription import effective_status, subscription_out

router = APIRouter(prefix="/api/v1/platform", tags=["platform"])
limit = Depends(rate_limit("platform", 240))


def _counts(db: Session, model, col_tenant):
    return dict(db.query(col_tenant, func.count(model.id)).group_by(col_tenant).all())


def workspace_row(db: Session, t: Tenant, counts: dict) -> dict:
    owner = db.query(AdminUser).filter(AdminUser.tenant_id == t.id).order_by(AdminUser.created_at).first()
    return {
        "id": t.id,
        "name": t.name,
        "short_code": t.short_code,
        "owner_email": owner.email if owner else None,
        "owner_name": owner.name if owner else None,
        "created_at": _iso(t.created_at),
        "upi_vpa": t.upi_vpa,
        **subscription_out(t),
        "suspended_reason": t.suspended_reason,
        "students": counts["students"].get(t.id, 0),
        "fees": counts["fees"].get(t.id, 0),
        "fees_paid": counts["paid"].get(t.id, 0),
        "sms_phone_active": t.id in counts["devices"],
        "last_activity_at": _iso(counts["activity"].get(t.id)),
    }


def _all_counts(db: Session) -> dict:
    return {
        "students": _counts(db, Student, Student.tenant_id),
        "fees": _counts(db, Installment, Installment.tenant_id),
        "paid": dict(db.query(Installment.tenant_id, func.count(Installment.id))
                     .filter(Installment.status == "PAID").group_by(Installment.tenant_id).all()),
        "devices": {r[0] for r in db.query(Device.tenant_id).filter(Device.active.is_(True)).all()},
        "activity": dict(db.query(AuditLog.tenant_id, func.max(AuditLog.created_at)).group_by(AuditLog.tenant_id).all()),
    }


@router.get("/workspaces", dependencies=[limit])
def list_workspaces(_: SessionContext = Depends(require_platform_admin), db: Session = Depends(get_db)):
    counts = _all_counts(db)
    rows = [workspace_row(db, t, counts) for t in db.query(Tenant).order_by(Tenant.created_at.desc()).all()]
    summary = {s: sum(1 for r in rows if r["status"] == s) for s in ("TRIAL", "ACTIVE", "EXPIRED", "SUSPENDED")}
    pending_owners = db.query(func.count(AdminUser.id)).filter(AdminUser.tenant_id.is_(None),
                                                               AdminUser.is_platform_admin.is_(False)).scalar()
    return {"items": rows, "summary": {**summary, "total": len(rows), "signed_up_without_workspace": pending_owners}}


def _tenant(db: Session, workspace_id: str) -> Tenant:
    t = db.query(Tenant).filter(Tenant.id == workspace_id).with_for_update().first()
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return t


def _done(db: Session, session: SessionContext, t: Tenant, action: str, prev: str, reason=None, details=None) -> dict:
    audit(db, tenant_id=t.id, actor_type="ADMIN", actor_id=session.admin_id, action=action, source="platform",
          previous_status=prev, new_status=effective_status(t), reason=reason,
          details={"platform_admin": session.email, **(details or {})})
    db.commit()
    return workspace_row(db, t, _all_counts(db))


@router.post("/workspaces/{workspace_id}/extend-trial", dependencies=[limit])
def extend_trial(workspace_id: str, body: PlatformExtendTrial, session: SessionContext = Depends(require_platform_admin),
                 db: Session = Depends(get_db)):
    t = _tenant(db, workspace_id)
    prev = effective_status(t)
    if t.status == "SUSPENDED":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Reactivate the workspace first")
    base = max(t.trial_ends_at or utcnow(), utcnow())
    t.trial_ends_at = base + timedelta(days=body.days)
    if t.status != "ACTIVE":
        t.status = "TRIAL"
    return _done(db, session, t, "PLATFORM_TRIAL_EXTENDED", prev, details={"days": body.days,
                                                                           "trial_ends_at": t.trial_ends_at})


@router.post("/workspaces/{workspace_id}/activate", dependencies=[limit])
def activate(workspace_id: str, session: SessionContext = Depends(require_platform_admin), db: Session = Depends(get_db)):
    t = _tenant(db, workspace_id)
    prev = effective_status(t)
    t.status = "ACTIVE"
    t.suspended_reason = None
    return _done(db, session, t, "PLATFORM_ACTIVATED", prev)


@router.post("/workspaces/{workspace_id}/suspend", dependencies=[limit])
def suspend(workspace_id: str, body: PlatformSuspend, session: SessionContext = Depends(require_platform_admin),
            db: Session = Depends(get_db)):
    t = _tenant(db, workspace_id)
    if session.tenant_id == t.id:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="You cannot suspend your own workspace")
    prev = effective_status(t)
    t.status = "SUSPENDED"
    t.suspended_reason = body.reason
    return _done(db, session, t, "PLATFORM_SUSPENDED", prev, reason=body.reason)


@router.post("/workspaces/{workspace_id}/reactivate", dependencies=[limit])
def reactivate(workspace_id: str, session: SessionContext = Depends(require_platform_admin), db: Session = Depends(get_db)):
    t = _tenant(db, workspace_id)
    if t.status != "SUSPENDED":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Workspace is not suspended")
    prev = effective_status(t)
    # Back to a trial if time is left, otherwise it shows as EXPIRED until extended/activated.
    t.status = "TRIAL"
    t.suspended_reason = None
    return _done(db, session, t, "PLATFORM_REACTIVATED", prev)
