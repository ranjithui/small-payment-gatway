"""Start-up bootstrap: make sure the configured admin can sign in.

Only two settings are needed on the host:

  FEEFLOW_ADMIN_EMAIL      the admin's sign-in email
  FEEFLOW_ADMIN_PASSWORD   at least 10 characters

The admin signs in and creates the workspace (centre name, UPI ID, ...) in the app; see
app/routers/workspace.py. These env values stay the source of truth: changing the password
on the host and restarting changes the admin's password.

(The older FEEFLOW_BOOTSTRAP_ADMIN_EMAIL / _PASSWORD names are still accepted.)
With FEEFLOW_SEED_DEMO=1 and an empty database, the demo data from app/seed.py is loaded.
"""
import logging
import os

from sqlalchemy.orm import Session

from app.config import settings
from app.models import AdminUser, Tenant
from app.security import hash_password, verify_password

log = logging.getLogger("feeflow.bootstrap")


def _env(name: str) -> str:
    return (os.getenv(f"FEEFLOW_{name}") or os.getenv(f"FEEFLOW_BOOTSTRAP_{name}") or "").strip()


def bootstrap(db: Session) -> str:
    if settings.seed_demo and db.query(Tenant).count() == 0:
        from app.seed import seed

        seed(db)
        log.warning("Loaded DEMO data (FEEFLOW_SEED_DEMO=1). Do not use demo logins for real centres.")
        return "demo"

    email = _env("ADMIN_EMAIL").lower()
    password = _env("ADMIN_PASSWORD")
    if not email or not password:
        if db.query(AdminUser).count() == 0:
            log.warning("No admin yet: set FEEFLOW_ADMIN_EMAIL and FEEFLOW_ADMIN_PASSWORD and restart.")
        return "unconfigured"
    if "@" not in email:
        raise RuntimeError("FEEFLOW_ADMIN_EMAIL must be an email address")
    if len(password) < 10:
        raise RuntimeError("FEEFLOW_ADMIN_PASSWORD must be at least 10 characters")

    admin = db.query(AdminUser).filter(AdminUser.email == email).first()
    if admin is None:
        db.add(AdminUser(tenant_id=None, email=email, name="Administrator", password_hash=hash_password(password)))
        db.commit()
        log.warning("Created admin %s - sign in to create your workspace.", email)
        return "created"
    changed = False
    if not verify_password(password, admin.password_hash):
        admin.password_hash = hash_password(password)
        changed = True
    if not admin.active:
        admin.active = True
        changed = True
    if changed:
        db.commit()
        log.warning("Updated admin %s from configuration.", email)
        return "updated"
    return "existing"
