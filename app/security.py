"""Authentication helpers: admin sessions, device tokens and secure payment-link tokens."""
import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db, utcnow
from app.models import AdminUser, Device, PaymentLink

PBKDF2_ROUNDS = 200_000


# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- tokens
def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_secret_token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def issue_admin_token(admin: AdminUser) -> str:
    # The workspace is looked up from the database on every request, so a token stays valid
    # when the admin creates their workspace after signing in.
    payload = {"sub": admin.id, "exp": int(time.time()) + settings.admin_token_ttl_seconds}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def decode_admin_token(token: str) -> Optional[dict]:
    try:
        body, sig = token.split(".")
        expected = _b64(hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_unb64(body))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


# ---------------------------------------------------------------- dependencies
@dataclass
class AdminContext:
    admin_id: str
    tenant_id: str
    name: str


def _unauthorized(detail: str = "Authentication required") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"})


@dataclass
class SessionContext:
    admin_id: str
    tenant_id: Optional[str]
    name: str
    email: str


def require_session(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)) -> SessionContext:
    """Signed-in admin, with or without a workspace yet."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorized()
    payload = decode_admin_token(authorization[7:].strip())
    if not payload:
        raise _unauthorized("Invalid or expired session")
    admin = db.get(AdminUser, payload["sub"])
    if not admin or not admin.active:
        raise _unauthorized("Invalid or expired session")
    return SessionContext(admin_id=admin.id, tenant_id=admin.tenant_id, name=admin.name, email=admin.email)


def require_admin(session: SessionContext = Depends(require_session)) -> AdminContext:
    """Signed-in admin of a workspace (every tenant-scoped endpoint)."""
    if session.tenant_id is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Create your workspace first",
            headers={"X-FeeFlow-Error": "WORKSPACE_REQUIRED"},
        )
    return AdminContext(admin_id=session.admin_id, tenant_id=session.tenant_id, name=session.name)


@dataclass
class DeviceContext:
    device_id: str
    tenant_id: str


def require_device(x_device_token: Optional[str] = Header(None), db: Session = Depends(get_db)) -> DeviceContext:
    if not x_device_token:
        raise _unauthorized("Device token required")
    device = db.query(Device).filter(Device.token_hash == sha256_hex(x_device_token)).first()
    if not device or not device.active:
        raise _unauthorized("Unknown or disabled device")
    device.last_seen_at = utcnow()
    db.commit()
    return DeviceContext(device_id=device.id, tenant_id=device.tenant_id)


class LinkError(HTTPException):
    pass


def resolve_payment_link(db: Session, token: str, *, allow_expired: bool = True) -> PaymentLink:
    """Validate a secure payment-link token. Raises 404 for unknown/revoked links (no oracle)."""
    if not token or len(token) < 16 or len(token) > 128:
        raise LinkError(status.HTTP_404_NOT_FOUND, detail="Payment link not found")
    link = db.query(PaymentLink).filter(PaymentLink.token_hash == sha256_hex(token)).first()
    if not link or link.revoked_at is not None:
        raise LinkError(status.HTTP_404_NOT_FOUND, detail="Payment link not found")
    if not allow_expired and link.expires_at <= utcnow():
        raise LinkError(status.HTTP_410_GONE, detail="This payment link has expired. Please contact your tuition centre.")
    return link
