"""Public (parent-facing) endpoints. Authenticated only by the secure payment-link token."""
import io
from typing import Optional

import segno
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import idempotency
from app.db import get_db
from app.models import Receipt
from app.ratelimit import rate_limit
from app.models import Installment, PaymentAttempt, Tenant
from app.schemas import DeclarationCreate, ParentClarification, PaymentAttemptIn
from app.services.references import ensure_reference
from app.services.upi import validate_upi_uri
from app.security import resolve_payment_link
from app.services import declarations as svc
from app.services.receipts import receipt_url

router = APIRouter(prefix="/api/v1", tags=["parent"])


@router.get(
    "/payment-declarations/public/{secure_token}",
    dependencies=[Depends(rate_limit("public_read", 60))],
)
def get_public_payment_state(secure_token: str, db: Session = Depends(get_db)):
    """Current fee + declaration state for the parent payment page (survives refresh)."""
    link = resolve_payment_link(db, secure_token)
    view = svc.public_view(db, link)
    db.commit()
    return view


@router.post(
    "/payment-declarations",
    dependencies=[Depends(rate_limit("public_write", 20))],
)
def create_payment_declaration(
    body: DeclarationCreate,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    """One-tap parent actions: INITIATED (app chosen), PROCESSING, PAID ("I have paid"), FAILED."""
    key = idempotency.validate_key(idempotency_key or body.idempotency_key)
    link = resolve_payment_link(db, body.token)
    payload = body.model_dump(exclude={"idempotency_key"})

    def op():
        view = svc.submit_parent_action(
            db,
            link,
            action=body.action,
            payment_type=body.payment_type,
            declared_amount_paise=body.declared_amount_paise,
            upi_app=body.upi_app,
            approximate_payment_time=body.approximate_payment_time,
            note=body.note,
            idempotency_key=key,
        )
        return (200 if view.get("duplicate") else 201), view

    scope = f"parent-declaration:{link.id}"
    try:
        code, resp = idempotency.run_idempotent(db, scope, key, payload, op)
    except IntegrityError:
        # A concurrent tap won the race on the one-active-declaration index; replay against it.
        code, resp = idempotency.run_idempotent(db, scope, key, payload, op)
    return JSONResponse(status_code=code, content=resp)


@router.post(
    "/payment-declarations/{declaration_ref}/clarification",
    dependencies=[Depends(rate_limit("public_write", 20))],
)
def add_parent_clarification(
    declaration_ref: str,
    body: ParentClarification,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    """Parent adds optional information (note, approximate time, optional UPI reference)."""
    key = idempotency.validate_key(idempotency_key)
    link = resolve_payment_link(db, body.token)

    def op():
        return 200, svc.submit_parent_clarification(
            db,
            link,
            declaration_ref,
            note=body.note,
            upi_reference=body.upi_reference,
            approximate_payment_time=body.approximate_payment_time,
            upi_app=body.upi_app,
        )

    code, resp = idempotency.run_idempotent(db, f"parent-clarify:{link.id}", key, body.model_dump(), op)
    return JSONResponse(status_code=code, content=resp)


@router.get("/pay/{secure_token}/qr.svg", dependencies=[Depends(rate_limit("public_read", 60))])
def upi_qr(secure_token: str, any_amount: bool = False, db: Session = Depends(get_db)):
    link = resolve_payment_link(db, secure_token, allow_expired=False)
    view = svc.public_view(db, link)
    db.commit()
    if not view["can_pay"] or not view["upi"]:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Payment is not available for this link")
    uri = _validated_qr_uri(db, link, view, any_amount)
    buf = io.BytesIO()
    segno.make(uri, error="m").save(buf, kind="svg", scale=6, border=2, dark="#111827")
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


def _validated_qr_uri(db: Session, link, view: dict, any_amount: bool = False) -> str:
    """The QR encodes exactly the link shown on the page, re-checked against server-side fee records."""
    uri = view["upi"]["uris_any_amount" if any_amount else "uris"]["QR"]
    tenant = db.get(Tenant, link.tenant_id)
    problems = validate_upi_uri(uri, vpa=tenant.upi_vpa, payee_name=tenant.upi_payee_name,
                                amount_paise=None if any_amount else view["fee"]["outstanding_paise"],
                                reference=view["payment_reference"])
    if problems:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Payment details are not available. Please contact the tuition centre.")
    return uri


# QR sizes: "standard" (~370 px) for the page, "large" (~1000 px) for printing / gallery scanning.
QR_SCALES = {"standard": 10, "large": 26}


@router.get("/pay/{secure_token}/qr.png", dependencies=[Depends(rate_limit("public_read", 60))])
def upi_qr_png(secure_token: str, download: bool = False, size: str = "standard", any_amount: bool = False,
               db: Session = Depends(get_db)):
    """PNG so parents can save it to the gallery and use 'Scan QR -> upload from gallery' in any UPI app."""
    if size not in QR_SCALES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="size must be standard or large")
    link = resolve_payment_link(db, secure_token, allow_expired=False)
    view = svc.public_view(db, link)
    db.commit()
    if not view["can_pay"] or not view["upi"]:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Payment is not available for this link")
    # any_amount=1: QR without a pre-filled amount - most reliable for personal UPI IDs (parent types the amount).
    uri = _validated_qr_uri(db, link, view, any_amount)
    buf = io.BytesIO()
    segno.make(uri, error="m").save(buf, kind="png", scale=QR_SCALES[size], border=4)
    headers = {"Cache-Control": "no-store"}
    if download:
        safe = "".join(c for c in view["payment_reference"] if c.isalnum() or c in "-_") + ("-no-amount" if any_amount else "")
        headers["Content-Disposition"] = f'attachment; filename="FeeFlow-UPI-QR-{safe}.png"'
    return Response(buf.getvalue(), media_type="image/png", headers=headers)


def device_category(user_agent: Optional[str]) -> str:
    """Coarse device class only; the user-agent string itself is never stored."""
    ua = (user_agent or "").lower()
    if "android" in ua:
        return "ANDROID"
    if any(k in ua for k in ("iphone", "ipad", "ipod")):
        return "IOS"
    if any(k in ua for k in ("windows", "macintosh", "x11", "linux", "cros")):
        return "DESKTOP"
    return "OTHER"


MAX_ATTEMPTS_PER_FEE = 300


@router.post("/pay/{secure_token}/attempts", status_code=202, dependencies=[Depends(rate_limit("pay_attempts", 30))])
def record_payment_attempt(secure_token: str, body: PaymentAttemptIn, request: Request, db: Session = Depends(get_db)):
    """Diagnostics for payment initiation (UPI intent / QR / manual). Not proof of payment."""
    link = resolve_payment_link(db, secure_token, allow_expired=False)
    inst = db.get(Installment, link.installment_id)
    count = db.query(PaymentAttempt.id).filter(PaymentAttempt.installment_id == inst.id).count()
    if count >= MAX_ATTEMPTS_PER_FEE:  # abuse guard: silently stop recording
        return {"recorded": False}
    ref = ensure_reference(db, inst)
    db.add(PaymentAttempt(
        tenant_id=inst.tenant_id, installment_id=inst.id, payment_link_id=link.id, payment_reference=ref,
        method=body.method, event=body.event, device=device_category(request.headers.get("user-agent")),
        error_detail=(body.detail or None),
    ))
    db.commit()
    return {"recorded": True}


@router.get("/receipts/public/{receipt_token}", dependencies=[Depends(rate_limit("public_read", 60))])
def public_receipt(receipt_token: str, db: Session = Depends(get_db)):
    return receipt_payload(db, receipt_token)


def receipt_payload(db: Session, receipt_token: str) -> dict:
    if not receipt_token or len(receipt_token) > 64:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Receipt not found")
    r = db.query(Receipt).filter(Receipt.public_token == receipt_token).first()
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Receipt not found")
    from app.models import Installment

    inst = db.get(Installment, r.installment_id)
    return {
        "receipt_number": r.receipt_number,
        "issued_at": r.issued_at.isoformat() + "Z",
        "amount_paise": r.amount_paise,
        "total_paid_after_paise": r.total_paid_after_paise,
        "balance_after_paise": r.balance_after_paise,
        "centre": {"name": inst.tenant.name, "contact_phone": inst.tenant.contact_phone, "logo_url": inst.tenant.logo_url},
        "student": {"name": inst.student.name, "student_id": inst.student.student_code},
        "fee": {
            "description": inst.description,
            "billing_month": inst.billing_month,
            "total_paise": inst.total_paise,
        },
        "url": receipt_url(r),
    }
