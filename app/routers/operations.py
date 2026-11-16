"""v2.7 owner tools: dashboard, bulk reminders / fees, SMS health, verification search, overpayments,
and the parent's family link. Every write is idempotent (Idempotency-Key) and tenant-scoped."""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Installment, PaymentAdjustment, Tenant
from app.ratelimit import rate_limit
from app.routers.admin import _idempotent
from app.schemas import (
    ApplyExcessRequest,
    BulkFeeRequest,
    BulkReminderRequest,
    FamilyLinkRequest,
    RetryFailedRequest,
)
from app.security import AdminContext, require_admin, require_writable_admin, resolve_payment_link
from app.services import operations as ops
from app.services.declarations import _iso
from app.services.verification import serialize_declaration

router = APIRouter(prefix="/api/v1", tags=["owner-tools"])
admin_limit = Depends(rate_limit("admin", 240))


def _tenant(db: Session, admin: AdminContext) -> Tenant:
    t = db.get(Tenant, admin.tenant_id) if admin.tenant_id else None
    if t is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Create your workspace first.")
    return t


# ------------------------------------------------------------------ dashboard
@router.get("/dashboard", dependencies=[admin_limit])
def dashboard(month: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$"),
              admin: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    return ops.dashboard(db, _tenant(db, admin), month)


@router.post("/fees/generate-monthly", dependencies=[admin_limit])
def generate_monthly(idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
                     admin: AdminContext = Depends(require_writable_admin), db: Session = Depends(get_db)):
    tenant = _tenant(db, admin)
    return _idempotent(db, admin, "generate-monthly", idempotency_key, {},
                       lambda: (200, ops.generate_monthly_now(db, admin, tenant)))


# ------------------------------------------------------------------ bulk reminders
@router.post("/bulk/reminders/preview", dependencies=[admin_limit])
def reminders_preview(body: BulkReminderRequest, admin: AdminContext = Depends(require_admin),
                      db: Session = Depends(get_db)):
    out = ops.reminders_preview(db, _tenant(db, admin), body.model_dump())
    db.rollback()  # preview never changes anything
    return out


@router.post("/bulk/reminders/send", dependencies=[admin_limit])
def reminders_send(body: BulkReminderRequest, idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
                   admin: AdminContext = Depends(require_writable_admin), db: Session = Depends(get_db)):
    tenant = _tenant(db, admin)
    data = body.model_dump()
    return _idempotent(db, admin, "bulk-reminders", idempotency_key, data,
                       lambda: (201, ops.reminders_send(db, admin, tenant, data)))


@router.get("/bulk/batches/{batch_id}", dependencies=[admin_limit])
def batch(batch_id: str, admin: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    return ops.batch_status(db, admin.tenant_id, batch_id)


# ------------------------------------------------------------------ bulk one-off fees
@router.post("/bulk/fees/preview", dependencies=[admin_limit])
def fees_preview(body: BulkFeeRequest, admin: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    return ops.fees_preview(db, _tenant(db, admin), body.model_dump())


@router.post("/bulk/fees/create", dependencies=[admin_limit])
def fees_create(body: BulkFeeRequest, idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
                admin: AdminContext = Depends(require_writable_admin), db: Session = Depends(get_db)):
    tenant = _tenant(db, admin)
    data = body.model_dump()
    return _idempotent(db, admin, "bulk-fees", idempotency_key, {**data, "due_date": data["due_date"].isoformat()},
                       lambda: (201, ops.fees_create(db, admin, tenant, data)))


# ------------------------------------------------------------------ SMS health
@router.get("/sms/health", dependencies=[admin_limit])
def sms_health(admin: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    return ops.sms_health(db, _tenant(db, admin).id)


@router.post("/sms/retry-failed", dependencies=[admin_limit])
def retry_failed(body: RetryFailedRequest, idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
                 admin: AdminContext = Depends(require_writable_admin), db: Session = Depends(get_db)):
    return _idempotent(db, admin, "sms-retry-failed", idempotency_key, body.model_dump(),
                       lambda: (200, ops.retry_failed(db, admin, body.batch_id)))


# ------------------------------------------------------------------ verification search
@router.get("/payment-verification/search", dependencies=[admin_limit])
def search(q: Optional[str] = Query(None, max_length=100), status_filter: str = Query("PENDING", alias="status"),
           class_grade: Optional[str] = Query(None, max_length=40), amount: Optional[float] = Query(None, gt=0),
           date_from: Optional[date] = None, date_to: Optional[date] = None,
           utr: Optional[str] = Query(None, max_length=64), limit: int = Query(100, ge=1, le=200),
           admin: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    rows = ops.search_declarations(db, admin.tenant_id, q=q, status_filter=status_filter, class_grade=class_grade,
                                   amount_paise=round(amount * 100) if amount else None, date_from=date_from,
                                   date_to=date_to, utr=utr, limit=limit)
    return {"items": [serialize_declaration(db, d) for d in rows]}


@router.get("/verified-payments/check-reference", dependencies=[admin_limit])
def check_reference(ref: str = Query(..., min_length=1, max_length=64), admin: AdminContext = Depends(require_admin),
                    db: Session = Depends(get_db)):
    return ops.check_reference(db, admin.tenant_id, ref)


# ------------------------------------------------------------------ overpayments
@router.get("/adjustments", dependencies=[admin_limit])
def adjustments(status_filter: str = Query("FLAGGED", alias="status"), admin: AdminContext = Depends(require_admin),
                db: Session = Depends(get_db)):
    rows = (db.query(PaymentAdjustment, Installment)
            .join(Installment, Installment.id == PaymentAdjustment.installment_id)
            .filter(PaymentAdjustment.tenant_id == admin.tenant_id, PaymentAdjustment.status == status_filter)
            .order_by(PaymentAdjustment.created_at.desc()).limit(200).all())
    return {"items": [{"id": a.id, "student_name": i.student.name, "description": i.description,
                       "billing_month": i.billing_month, "amount_paise": a.amount_paise, "status": a.status,
                       "created_at": _iso(a.created_at)} for a, i in rows]}


@router.post("/adjustments/{adjustment_id}/apply", dependencies=[admin_limit])
def apply_excess(adjustment_id: str, body: ApplyExcessRequest,
                 idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
                 admin: AdminContext = Depends(require_writable_admin), db: Session = Depends(get_db)):
    return _idempotent(db, admin, f"apply-excess:{adjustment_id}", idempotency_key, body.model_dump(),
                       lambda: (200, ops.apply_excess(db, admin, adjustment_id, body.installment_id)))


# ------------------------------------------------------------------ parent: another child's fee
@router.post("/pay/{secure_token}/family-link", dependencies=[Depends(rate_limit("public_write", 20))])
def family_link(secure_token: str, body: FamilyLinkRequest, db: Session = Depends(get_db)):
    link = resolve_payment_link(db, secure_token, allow_expired=False)
    out = ops.family_link(db, db.get(Installment, link.installment_id), body.reference)
    db.commit()
    return out
