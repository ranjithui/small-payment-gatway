"""First-start bootstrap for a fresh deployment (no shell needed on the host).

If the database has no tenants, create the first centre and its admin from env vars:

  FEEFLOW_BOOTSTRAP_CENTRE_NAME      e.g. "Bright Minds Tuition Centre"
  FEEFLOW_BOOTSTRAP_CENTRE_CODE      short code used in receipt numbers, e.g. "BMTC"
  FEEFLOW_BOOTSTRAP_UPI_VPA          the centre's UPI ID, e.g. "brightminds@okaxis"
  FEEFLOW_BOOTSTRAP_UPI_NAME         payee name shown in UPI apps
  FEEFLOW_BOOTSTRAP_CONTACT_PHONE    optional
  FEEFLOW_BOOTSTRAP_ADMIN_EMAIL
  FEEFLOW_BOOTSTRAP_ADMIN_PASSWORD   at least 10 characters; change/remove after first login

With FEEFLOW_SEED_DEMO=1 (and an empty database) the demo data from app/seed.py is loaded instead.
Runs only when the tenants table is empty, so it is safe on every restart.
"""
import logging
import os

from sqlalchemy.orm import Session

from app.config import settings
from app.models import AdminUser, Tenant
from app.security import hash_password

log = logging.getLogger("feeflow.bootstrap")


def bootstrap(db: Session) -> str:
    if db.query(Tenant).count() > 0:
        return "existing"
    if settings.seed_demo:
        from app.seed import seed

        seed(db)
        log.warning("Loaded DEMO data (FEEFLOW_SEED_DEMO=1). Do not use demo logins for real centres.")
        return "demo"

    env = {k: os.getenv(f"FEEFLOW_BOOTSTRAP_{k}", "").strip() for k in (
        "CENTRE_NAME", "CENTRE_CODE", "UPI_VPA", "UPI_NAME", "CONTACT_PHONE", "ADMIN_EMAIL", "ADMIN_PASSWORD")}
    required = ["CENTRE_NAME", "CENTRE_CODE", "UPI_VPA", "UPI_NAME", "ADMIN_EMAIL", "ADMIN_PASSWORD"]
    missing = [k for k in required if not env[k]]
    if missing:
        log.warning("Empty database and no bootstrap settings (missing %s); nobody can sign in yet.", missing)
        return "empty"
    if len(env["ADMIN_PASSWORD"]) < 10:
        raise RuntimeError("FEEFLOW_BOOTSTRAP_ADMIN_PASSWORD must be at least 10 characters")

    tenant = Tenant(
        name=env["CENTRE_NAME"],
        short_code=env["CENTRE_CODE"].upper()[:16],
        upi_vpa=env["UPI_VPA"],
        upi_payee_name=env["UPI_NAME"],
        contact_phone=env["CONTACT_PHONE"] or None,
    )
    db.add(tenant)
    db.flush()
    db.add(AdminUser(
        tenant_id=tenant.id,
        email=env["ADMIN_EMAIL"].lower(),
        name="Administrator",
        password_hash=hash_password(env["ADMIN_PASSWORD"]),
    ))
    db.commit()
    log.warning("Created centre %r and admin %s", tenant.name, env["ADMIN_EMAIL"].lower())
    return "created"
