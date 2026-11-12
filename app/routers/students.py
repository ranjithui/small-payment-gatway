"""Student profiles, monthly fee plans and the "Send fee link" button."""
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app import idempotency
from app.audit import audit
from app.db import get_db, utcnow
from app.models import Installment, Receipt, Student, Tenant
from app.ratelimit import rate_limit
from app.schemas import SendFeeLink, StudentCreate, StudentUpdate
from app.security import AdminContext, require_admin, require_writable_admin
from app.services import fees
from app.services.declarations import _iso
from app.services.receipts import receipt_url
from app.services.sms import enqueue_sms

router = APIRouter(prefix="/api/v1", tags=["students"])
limit = Depends(rate_limit("admin", 240))

PROFILE_FIELDS = ("name", "parent_name", "parent_mobile", "parent_email", "date_of_birth", "gender", "class_grade",
                  "school", "address", "joined_on", "monthly_fee_paise", "fee_due_day")


def _get_student(db: Session, admin: AdminContext, student_id: str) -> Student:
    st = db.query(Student).filter(Student.id == student_id, Student.tenant_id == admin.tenant_id).first()
    if st is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Student not found")
    return st


def student_out(st: Student, latest: Optional[Installment] = None) -> dict:
    out = {
        "id": st.id,
        "student_id": st.student_code,
        "name": st.name,
        "parent_name": st.parent_name,
        "parent_mobile": st.parent_mobile,
        "parent_email": st.parent_email,
        "date_of_birth": st.date_of_birth.isoformat() if st.date_of_birth else None,
        "gender": st.gender,
        "class_grade": st.class_grade,
        "school": st.school,
        "address": st.address,
        "joined_on": st.joined_on.isoformat() if st.joined_on else None,
        "monthly_fee_paise": st.monthly_fee_paise,
        "fee_due_day": st.fee_due_day,
        "active": bool(st.active),
    }
    if latest is not None:
        out["latest_fee"] = {"billing_month": latest.billing_month, "status": latest.status,
                             "outstanding_paise": latest.outstanding_paise, "due_date": latest.due_date.isoformat()}
    return out


def _next_code(db: Session, tenant: Tenant) -> str:
    n = db.query(func.count(Student.id)).filter(Student.tenant_id == tenant.id).scalar() + 1
    while True:
        code = f"{tenant.short_code}-{n:04d}"
        if not db.query(Student.id).filter(Student.tenant_id == tenant.id, Student.student_code == code).first():
            return code
        n += 1


@router.get("/students", dependencies=[limit])
def list_students(
    q: Optional[str] = Query(None, max_length=100),
    include_inactive: bool = False,
    admin: AdminContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(Student).filter(Student.tenant_id == admin.tenant_id)
    if not include_inactive:
        query = query.filter(Student.active.is_(True))
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.filter(or_(func.lower(Student.name).like(like), func.lower(Student.student_code).like(like),
                                 Student.parent_mobile.like(f"%{q.strip()}%"), func.lower(Student.class_grade).like(like)))
    rows = query.order_by(Student.name).limit(500).all()
    out = []
    for st in rows:
        latest = (db.query(Installment).filter(Installment.student_id == st.id)
                  .order_by(Installment.due_date.desc(), Installment.created_at.desc()).first())
        out.append(student_out(st, latest))
    return {"items": out}


@router.post("/students", dependencies=[limit], status_code=201)
def create_student(body: StudentCreate, admin: AdminContext = Depends(require_writable_admin),
                   db: Session = Depends(get_db)):
    tenant = db.get(Tenant, admin.tenant_id)
    code = (body.student_code or "").strip() or _next_code(db, tenant)
    if db.query(Student.id).filter(Student.tenant_id == tenant.id, Student.student_code == code).first():
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Student ID {code} already exists")
    data = body.model_dump(exclude={"student_code"})
    for k in ("name", "parent_name", "class_grade", "school", "address", "parent_email"):
        if isinstance(data.get(k), str):
            data[k] = data[k].strip() or None
    st = Student(tenant_id=tenant.id, student_code=code, active=True,
                 joined_on=data.pop("joined_on") or utcnow().date(), **data)
    db.add(st)
    db.flush()
    audit(db, tenant_id=tenant.id, actor_type="ADMIN", actor_id=admin.admin_id, action="STUDENT_CREATED",
          source="students", details={"student_id": st.id, "student_code": code,
                                      "monthly_fee_paise": st.monthly_fee_paise, "fee_due_day": st.fee_due_day})
    # Saving a student on a monthly plan creates this month's fee straight away.
    fees.create_monthly_fee(db, tenant, st, fees.month_of(utcnow().date()), actor_id=admin.admin_id)
    db.commit()
    return student_detail(db, admin, st)


def student_detail(db: Session, admin: AdminContext, st: Student) -> dict:
    insts = (db.query(Installment).filter(Installment.student_id == st.id)
             .order_by(Installment.due_date.desc(), Installment.created_at.desc()).all())
    receipts = {}
    for r in db.query(Receipt).filter(Receipt.installment_id.in_([i.id for i in insts] or ["-"])).all():
        receipts.setdefault(r.installment_id, []).append(
            {"receipt_number": r.receipt_number, "amount_paise": r.amount_paise, "url": receipt_url(r)})
    out = student_out(st)
    out["fees"] = [{
        "id": i.id, "description": i.description, "billing_month": i.billing_month, "kind": i.kind,
        "due_date": i.due_date.isoformat(), "total_paise": i.total_paise, "paid_paise": i.paid_paise,
        "outstanding_paise": i.outstanding_paise, "status": i.status, "receipts": receipts.get(i.id, []),
    } for i in insts]
    out["outstanding_paise"] = sum(i.outstanding_paise for i in insts)
    out["sms_phone_active"] = fees.sms_phone_active(db, admin.tenant_id)
    return out


@router.get("/students/{student_id}", dependencies=[limit])
def get_student(student_id: str, admin: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    return student_detail(db, admin, _get_student(db, admin, student_id))


@router.patch("/students/{student_id}", dependencies=[limit])
def update_student(student_id: str, body: StudentUpdate, admin: AdminContext = Depends(require_writable_admin),
                   db: Session = Depends(get_db)):
    st = _get_student(db, admin, student_id)
    changes = {}
    data = body.model_dump(exclude_unset=True, exclude={"clear_fee_plan", "active"})
    for field, value in data.items():
        if field not in PROFILE_FIELDS or value is None:
            continue
        if isinstance(value, str):
            value = value.strip() or None
            if value is None and field in ("name", "parent_mobile"):
                continue
        if getattr(st, field) != value:
            changes[field] = {"from": getattr(st, field), "to": value}
            setattr(st, field, value)
    if body.clear_fee_plan and st.monthly_fee_paise is not None:
        changes["monthly_fee_paise"] = {"from": st.monthly_fee_paise, "to": None}
        st.monthly_fee_paise = None
        st.fee_due_day = None
    if (st.monthly_fee_paise is None) != (st.fee_due_day is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Enter both the monthly fee amount and the fee due day (or neither)")
    if body.active is not None and body.active != st.active:
        changes["active"] = {"from": st.active, "to": body.active}
        st.active = body.active
    if changes:
        audit(db, tenant_id=admin.tenant_id, actor_type="ADMIN", actor_id=admin.admin_id,
              action="STUDENT_DEACTIVATED" if changes.get("active", {}).get("to") is False else "STUDENT_UPDATED",
              source="students", details={"student_id": st.id, **changes})
        db.commit()
    return student_detail(db, admin, st)


@router.post("/students/{student_id}/send-fee-link", dependencies=[limit])
def send_fee_link(
    student_id: str,
    body: SendFeeLink,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    admin: AdminContext = Depends(require_writable_admin),
    db: Session = Depends(get_db),
):
    """SMS: queue the payment link for the latest unpaid fee on the centre's SMS phone.
    SHARE: return the link + message so the app can share it (WhatsApp etc.)."""
    key = idempotency.validate_key(idempotency_key)
    st = _get_student(db, admin, student_id)
    tenant = db.get(Tenant, admin.tenant_id)

    def op():
        if not st.active:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="This student is inactive")
        inst, url = fees.prepare_fee_link(db, tenant, st, admin.admin_id)
        if inst is None:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                detail="No unpaid fee for this student. Add a monthly fee plan or a fee first.")
        phone_ready = fees.sms_phone_active(db, tenant.id)
        queued = False
        if body.channel == "SMS":
            msg = enqueue_sms(db, tenant=tenant, to_mobile=st.parent_mobile, kind="FEE_REMINDER",
                              dedupe_key=f"fee-link-send:{inst.id}:{key}", installment_id=inst.id,
                              **fees.link_sms_text_params(inst, url))
            queued = msg is not None
        audit(db, tenant_id=tenant.id, actor_type="ADMIN", actor_id=admin.admin_id,
              action="FEE_LINK_SENT" if body.channel == "SMS" else "FEE_LINK_SHARED", source="students",
              installment_id=inst.id, details={"student_id": st.id, "channel": body.channel})
        return 200, {
            "channel": body.channel,
            "payment_link": url,
            "share_text": fees.share_message(inst, url, tenant.name),
            "sms_queued": queued,
            "sms_phone_active": phone_ready,
            "fee": {"id": inst.id, "description": inst.description, "billing_month": inst.billing_month,
                    "outstanding_paise": inst.outstanding_paise, "due_date": inst.due_date.isoformat()},
            "parent_mobile": st.parent_mobile,
        }

    code, resp = idempotency.run_idempotent(db, f"{admin.tenant_id}:send-link:{st.id}", key, body.model_dump(), op)
    return JSONResponse(status_code=code, content=resp)
