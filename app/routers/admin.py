"""Authenticated administrator endpoints (tenant-scoped)."""
from datetime import datetime, time, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import idempotency
from app.audit import audit
from app.db import get_db, utcnow
from app.models import AdminNotification, AdminUser, AuditLog, Installment, PaymentAdjustment, Student
from app.ratelimit import rate_limit
from app.schemas import (
    InstallmentCreate,
    StudentCreate,
    BankImport,
    ClarificationRequest,
    ConfirmPayment,
    LoginRequest,
    ManualPayment,
    RejectDeclaration,
    ReopenDeclaration,
    ResolveAdjustment,
)
from app.security import AdminContext, issue_admin_token, require_admin, verify_password
from app.services import reconciliation, reports, verification
from app.services.declarations import _iso
from app.services.links import create_payment_link, link_url
from app.services.reminders import schedule_initial_reminders
from app.services.sms import enqueue_sms, format_rupees

router = APIRouter(prefix="/api/v1", tags=["admin"])
admin_limit = Depends(rate_limit("admin", 240))


# ------------------------------------------------------------------ auth
@router.post("/auth/login", dependencies=[Depends(rate_limit("login", 10))], tags=["auth"])
def login(body: LoginRequest, db: Session = Depends(get_db)):
    admin = db.query(AdminUser).filter(AdminUser.email == body.email.strip().lower()).first()
    if not admin or not admin.active or not verify_password(body.password, admin.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    return {"access_token": issue_admin_token(admin), "token_type": "bearer", "name": admin.name}


@router.get("/auth/me", dependencies=[admin_limit], tags=["auth"])
def me(admin: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    from app.models import Tenant

    t = db.get(Tenant, admin.tenant_id)
    return {"admin_id": admin.admin_id, "name": admin.name, "centre": {"name": t.name, "logo_url": t.logo_url}}


# ------------------------------------------------------------------ helpers
def _idempotent(db: Session, admin: AdminContext, scope: str, key: Optional[str], body, op):
    key = idempotency.validate_key(key)
    code, resp = idempotency.run_idempotent(db, f"{admin.tenant_id}:{scope}", key, body, op)
    return JSONResponse(status_code=code, content=resp)


# ------------------------------------------------------------------ declarations / verification
@router.get("/payment-declarations/{declaration_id}", dependencies=[admin_limit])
def get_declaration(declaration_id: str, admin: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    decl = verification.get_declaration(db, admin, declaration_id)
    verification.acknowledge(db, admin, decl)
    return verification.serialize_declaration(db, decl, detail=True)


@router.get("/payment-verification/pending", dependencies=[admin_limit])
def pending(admin: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    return {"items": verification.pending_queue(db, admin)}


@router.post("/payment-verification/{declaration_id}/confirm", dependencies=[admin_limit])
def confirm(
    declaration_id: str,
    body: ConfirmPayment,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    admin: AdminContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    data = body.model_dump()
    data["transactions"] = [t.model_dump() for t in body.transactions]
    return _idempotent(
        db, admin, f"confirm:{declaration_id}", idempotency_key, data,
        lambda: (200, verification.confirm(db, admin, declaration_id, data)),
    )


@router.post("/payment-verification/{declaration_id}/reject", dependencies=[admin_limit])
def reject(
    declaration_id: str,
    body: RejectDeclaration,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    admin: AdminContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _idempotent(
        db, admin, f"reject:{declaration_id}", idempotency_key, body.model_dump(),
        lambda: (200, verification.reject(db, admin, declaration_id, body.reason, body.admin_note)),
    )


@router.post("/payment-verification/{declaration_id}/request-clarification", dependencies=[admin_limit])
def request_clarification(
    declaration_id: str,
    body: ClarificationRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    admin: AdminContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _idempotent(
        db, admin, f"clarify:{declaration_id}", idempotency_key, body.model_dump(),
        lambda: (200, verification.request_clarification(db, admin, declaration_id, body.message)),
    )


@router.post("/payment-verification/{declaration_id}/reopen", dependencies=[admin_limit])
def reopen(
    declaration_id: str,
    body: ReopenDeclaration,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    admin: AdminContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Controlled workflow to reopen a REJECTED declaration (e.g. the credit appeared late)."""
    return _idempotent(
        db, admin, f"reopen:{declaration_id}", idempotency_key, body.model_dump(),
        lambda: (200, verification.reopen(db, admin, declaration_id, body.reason)),
    )


# ------------------------------------------------------------------ students & fees (minimal fee management)
def _student_out(st: Student) -> dict:
    return {"id": st.id, "student_id": st.student_code, "name": st.name, "parent_name": st.parent_name,
            "parent_mobile": st.parent_mobile}


@router.get("/students", dependencies=[admin_limit])
def list_students(admin: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(Student).filter(Student.tenant_id == admin.tenant_id).order_by(Student.name).all()
    return {"items": [_student_out(s) for s in rows]}


@router.post("/students", dependencies=[admin_limit], status_code=201)
def create_student(body: StudentCreate, admin: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    code = body.student_code.strip()
    if db.query(Student).filter(Student.tenant_id == admin.tenant_id, Student.student_code == code).first():
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Student ID {code} already exists")
    st = Student(tenant_id=admin.tenant_id, student_code=code, name=body.name.strip(),
                 parent_name=(body.parent_name or "").strip() or None, parent_mobile=body.parent_mobile)
    db.add(st)
    db.flush()
    audit(db, tenant_id=admin.tenant_id, actor_type="ADMIN", actor_id=admin.admin_id, action="STUDENT_CREATED",
          source="admin", details={"student_id": st.id, "student_code": code})
    db.commit()
    return _student_out(st)


@router.post("/installments", dependencies=[admin_limit], status_code=201)
def create_installment(
    body: InstallmentCreate,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    admin: AdminContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create a fee, its secure payment link, the reminder schedule and (optionally) the link SMS."""

    def op():
        st = db.query(Student).filter(Student.id == body.student_id, Student.tenant_id == admin.tenant_id).first()
        if st is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Student not found")
        inst = Installment(tenant_id=admin.tenant_id, student_id=st.id, description=body.description.strip(),
                           billing_month=body.billing_month, total_paise=body.total_paise, due_date=body.due_date,
                           status="UNPAID")
        db.add(inst)
        db.flush()
        _, token = create_payment_link(db, inst, valid_days=body.link_valid_days)
        url = link_url(token)
        tenant = inst.tenant
        if body.schedule_reminders:
            # First reminder at 9:00 IST (03:30 UTC) the day before the due date, but never in the past.
            first = datetime.combine(body.due_date - timedelta(days=1), time(3, 30))
            schedule_initial_reminders(db, inst, tenant, start=max(first, utcnow() + timedelta(hours=1)))
        if body.send_link_sms:
            enqueue_sms(db, tenant=tenant, to_mobile=st.parent_mobile, kind="FEE_REMINDER",
                        dedupe_key=f"fee-link:{inst.id}", installment_id=inst.id,
                        amount=format_rupees(inst.total_paise), student_name=st.name,
                        description=inst.description, due_date=inst.due_date.strftime("%d %b %Y"),
                        payment_link=url)
        audit(db, tenant_id=admin.tenant_id, actor_type="ADMIN", actor_id=admin.admin_id,
              action="INSTALLMENT_CREATED", source="admin", installment_id=inst.id,
              details={"total_paise": inst.total_paise, "due_date": inst.due_date, "link_sms": body.send_link_sms})
        db.flush()
        return 201, {"id": inst.id, "payment_link": url, "status": inst.status,
                     "link_sms_queued": body.send_link_sms}

    return _idempotent(db, admin, "create-installment", idempotency_key, body.model_dump(), op)


# ------------------------------------------------------------------ installments
@router.get("/installments", dependencies=[admin_limit])
def list_installments(
    status_filter: Optional[str] = Query(None, alias="status"),
    admin: AdminContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(Installment).filter(Installment.tenant_id == admin.tenant_id)
    if status_filter:
        q = q.filter(Installment.status == status_filter)
    items = q.join(Student).order_by(Installment.due_date, Student.name).all()
    return {
        "items": [
            {
                "id": i.id,
                "student_name": i.student.name,
                "student_id": i.student.student_code,
                "parent_mobile": i.student.parent_mobile,
                "description": i.description,
                "billing_month": i.billing_month,
                "due_date": i.due_date.isoformat(),
                "total_paise": i.total_paise,
                "paid_paise": i.paid_paise,
                "outstanding_paise": i.outstanding_paise,
                "excess_paise": i.excess_paise,
                "status": i.status,
            }
            for i in items
        ]
    }


@router.get("/installments/{installment_id}/payment-status", dependencies=[admin_limit])
def installment_payment_status(
    installment_id: str, admin: AdminContext = Depends(require_admin), db: Session = Depends(get_db)
):
    return verification.installment_payment_status(db, admin, installment_id)


@router.post("/installments/{installment_id}/payments", dependencies=[admin_limit])
def record_payment(
    installment_id: str,
    body: ManualPayment,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    admin: AdminContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Record an actual bank credit with no declaration (e.g. an accidental second transfer)."""
    data = body.model_dump()
    data["transactions"] = [t.model_dump() for t in body.transactions]
    return _idempotent(
        db, admin, f"manual-payment:{installment_id}", idempotency_key, data,
        lambda: (201, verification.record_manual_payment(db, admin, installment_id, data)),
    )


@router.post("/installments/{installment_id}/payment-links", dependencies=[admin_limit])
def new_payment_link(
    installment_id: str,
    valid_days: int = Query(30, ge=1, le=120),
    admin: AdminContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    inst = (
        db.query(Installment)
        .filter(Installment.id == installment_id, Installment.tenant_id == admin.tenant_id)
        .first()
    )
    if inst is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Installment not found")
    link, token = create_payment_link(db, inst, valid_days=valid_days)
    audit(
        db, tenant_id=admin.tenant_id, actor_type="ADMIN", actor_id=admin.admin_id,
        action="PAYMENT_LINK_CREATED", source="admin_dashboard", installment_id=inst.id,
        details={"payment_link_id": link.id, "expires_at": link.expires_at},
    )
    db.commit()
    return {"url": link_url(token), "expires_at": _iso(link.expires_at)}


# ------------------------------------------------------------------ notifications / adjustments
@router.get("/notifications", dependencies=[admin_limit])
def notifications(
    unread_only: bool = False, admin: AdminContext = Depends(require_admin), db: Session = Depends(get_db)
):
    q = db.query(AdminNotification).filter(AdminNotification.tenant_id == admin.tenant_id)
    if unread_only:
        q = q.filter(AdminNotification.read_at.is_(None))
    rows = q.order_by(AdminNotification.created_at.desc()).limit(100).all()
    return {
        "items": [
            {
                "id": n.id,
                "kind": n.kind,
                "message": n.message,
                "declaration_id": n.declaration_id,
                "created_at": _iso(n.created_at),
                "read": n.read_at is not None,
            }
            for n in rows
        ]
    }


@router.post("/notifications/mark-read", dependencies=[admin_limit])
def mark_read(admin: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    db.query(AdminNotification).filter(
        AdminNotification.tenant_id == admin.tenant_id, AdminNotification.read_at.is_(None)
    ).update({AdminNotification.read_at: utcnow()}, synchronize_session=False)
    db.commit()
    return {"ok": True}


@router.post("/adjustments/{adjustment_id}/resolve", dependencies=[admin_limit])
def resolve_adjustment(
    adjustment_id: str,
    body: ResolveAdjustment,
    admin: AdminContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    adj = (
        db.query(PaymentAdjustment)
        .filter(PaymentAdjustment.id == adjustment_id, PaymentAdjustment.tenant_id == admin.tenant_id)
        .first()
    )
    if adj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Adjustment not found")
    if adj.status != "FLAGGED":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Adjustment already resolved")
    prev = adj.status
    adj.status = body.status
    adj.note = f"{adj.note or ''}\nResolution: {body.note}".strip()
    adj.resolved_by = admin.admin_id
    adj.resolved_at = utcnow()
    audit(
        db, tenant_id=admin.tenant_id, actor_type="ADMIN", actor_id=admin.admin_id,
        action="EXCESS_PAYMENT_RESOLVED", source="admin_dashboard", installment_id=adj.installment_id,
        previous_status=prev, new_status=adj.status, reason=body.note,
        details={"adjustment_id": adj.id, "amount_paise": adj.amount_paise},
    )
    db.commit()
    return {"id": adj.id, "status": adj.status}


# ------------------------------------------------------------------ reconciliation / reports / audit
@router.post("/reconciliation/bank-transactions/import", dependencies=[admin_limit])
def import_bank_transactions(
    body: BankImport, admin: AdminContext = Depends(require_admin), db: Session = Depends(get_db)
):
    rows = [t.model_dump() for t in body.transactions]
    return reconciliation.import_transactions(db, admin, body.source, rows)


@router.get("/reports/{report_type}", dependencies=[admin_limit])
def report(report_type: str, admin: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    if report_type not in reports.REPORTS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown report. Available: {sorted(reports.REPORTS)}")
    return {"report": report_type, "items": reports.REPORTS[report_type](db, admin.tenant_id)}


@router.get("/audit-logs", dependencies=[admin_limit])
def audit_logs(
    declaration_id: Optional[str] = None,
    installment_id: Optional[str] = None,
    limit: int = Query(200, ge=1, le=1000),
    admin: AdminContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(AuditLog).filter(AuditLog.tenant_id == admin.tenant_id)
    if declaration_id:
        q = q.filter(AuditLog.declaration_id == declaration_id)
    if installment_id:
        q = q.filter(AuditLog.installment_id == installment_id)
    rows = q.order_by(AuditLog.created_at.desc()).limit(limit).all()
    return {
        "items": [
            {
                "actor_type": a.actor_type,
                "actor_id": a.actor_id,
                "action": a.action,
                "tenant_id": a.tenant_id,
                "declaration_id": a.declaration_id,
                "installment_id": a.installment_id,
                "previous_status": a.previous_status,
                "new_status": a.new_status,
                "timestamp": _iso(a.created_at),
                "reason": a.reason,
                "source": a.source,
                "details": a.details_json,
            }
            for a in rows
        ]
    }
