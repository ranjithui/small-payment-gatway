"""Public (parent-facing) endpoints. Authenticated only by the secure payment-link token."""
import io
from typing import Optional

import segno
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import idempotency
from app.db import get_db
from app.models import Receipt
from app.ratelimit import rate_limit
from app.schemas import DeclarationCreate, ParentClarification
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
    uri = view["upi"]["uris_any_amount" if any_amount else "uris"]["QR"]
    buf = io.BytesIO()
    segno.make(uri, error="m").save(buf, kind="svg", scale=6, border=2, dark="#111827")
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@router.get("/pay/{secure_token}/qr.png", dependencies=[Depends(rate_limit("public_read", 60))])
def upi_qr_png(secure_token: str, download: bool = False, db: Session = Depends(get_db)):
    """PNG so parents can save it to the gallery and use 'Scan QR -> upload from gallery' in any UPI app."""
    link = resolve_payment_link(db, secure_token, allow_expired=False)
    view = svc.public_view(db, link)
    db.commit()
    if not view["can_pay"] or not view["upi"]:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Payment is not available for this link")
    buf = io.BytesIO()
    segno.make(view["upi"]["uris"]["QR"], error="m").save(buf, kind="png", scale=10, border=4)
    headers = {"Cache-Control": "no-store"}
    if download:
        safe = "".join(c for c in f"{view['student']['student_id']}-{view['fee']['billing_month']}" if c.isalnum() or c in "-_")
        headers["Content-Disposition"] = f'attachment; filename="FeeFlow-UPI-QR-{safe}.png"'
    return Response(buf.getvalue(), media_type="image/png", headers=headers)


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
