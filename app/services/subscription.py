"""Workspace subscription state: 30-day trial, active, expired (derived), suspended.

* TRIAL / ACTIVE      - everything works.
* EXPIRED             - trial over and not activated: read-only for the centre. Parents' existing
                        links, payment verification and receipts keep working so no parent is stuck.
* SUSPENDED           - blocked by the platform owner (e.g. misuse): the centre cannot sign in,
                        its payment pages are closed and its SMS phone stops syncing.
"""
from datetime import datetime
from typing import Optional

from fastapi import HTTPException, status

from app.db import utcnow
from app.models import Tenant

WRITABLE = {"TRIAL", "ACTIVE"}


def effective_status(t: Tenant, now: Optional[datetime] = None) -> str:
    now = now or utcnow()
    if t.status == "SUSPENDED":
        return "SUSPENDED"
    if t.status == "ACTIVE":
        return "ACTIVE"
    if t.trial_ends_at is not None and t.trial_ends_at <= now:
        return "EXPIRED"
    return "TRIAL"


def trial_days_left(t: Tenant, now: Optional[datetime] = None) -> Optional[int]:
    if effective_status(t, now) != "TRIAL" or t.trial_ends_at is None:
        return None
    seconds = (t.trial_ends_at - (now or utcnow())).total_seconds()
    return max(0, int((seconds + 86399) // 86400))  # round up: "1 day left" on the last day


def subscription_out(t: Tenant) -> dict:
    st = effective_status(t)
    return {
        "status": st,
        "trial_ends_at": t.trial_ends_at.isoformat() + "Z" if t.trial_ends_at else None,
        "trial_days_left": trial_days_left(t),
        "read_only": st not in WRITABLE,
    }


def ensure_writable(t: Tenant) -> None:
    st = effective_status(t)
    if st == "EXPIRED":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Your 30-day free trial has ended. Contact FeeFlow to activate your workspace.",
            headers={"X-FeeFlow-Error": "TRIAL_EXPIRED"},
        )
    if st == "SUSPENDED":
        raise suspended_error()


def suspended_error() -> HTTPException:
    return HTTPException(
        status.HTTP_403_FORBIDDEN,
        detail="This workspace has been suspended. Contact FeeFlow support.",
        headers={"X-FeeFlow-Error": "WORKSPACE_SUSPENDED"},
    )
