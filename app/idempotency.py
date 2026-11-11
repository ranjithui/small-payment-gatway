"""Idempotency-key support for mutating endpoints.

A replay with the same key and the same request body returns the stored response.
The same key with a different body is rejected with 422.
"""
import hashlib
import json
from typing import Any, Callable, Optional, Tuple

from fastapi import HTTPException, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import IdempotencyRecord


def _hash(body: Any) -> str:
    return hashlib.sha256(json.dumps(jsonable_encoder(body), sort_keys=True).encode()).hexdigest()


def validate_key(key: Optional[str]) -> str:
    if not key or not (8 <= len(key) <= 100) or not all(c.isalnum() or c in "-_:." for c in key):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="A valid Idempotency-Key (8-100 chars: letters, digits, - _ : .) is required",
        )
    return key


def lookup(db: Session, scope: str, key: str, body: Any) -> Optional[Tuple[int, Any]]:
    rec = db.query(IdempotencyRecord).filter_by(scope=scope, key=key).first()
    if rec is None:
        return None
    if rec.request_hash != _hash(body):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Idempotency-Key was already used with a different request",
        )
    return rec.status_code, json.loads(rec.response_json)


def store(db: Session, scope: str, key: str, body: Any, status_code: int, response: Any) -> None:
    db.add(
        IdempotencyRecord(
            scope=scope,
            key=key,
            request_hash=_hash(body),
            status_code=status_code,
            response_json=json.dumps(jsonable_encoder(response)),
        )
    )


def run_idempotent(
    db: Session, scope: str, key: str, body: Any, operation: Callable[[], Tuple[int, Any]]
) -> Tuple[int, Any]:
    """Execute ``operation`` once per (scope, key); it must NOT commit. Commits on success."""
    cached = lookup(db, scope, key, body)
    if cached is not None:
        return cached
    try:
        code, response = operation()
        store(db, scope, key, body, code, response)
        db.commit()
        return code, response
    except IntegrityError:
        db.rollback()
        cached = lookup(db, scope, key, body)
        if cached is not None:
            return cached
        raise
    except Exception:
        db.rollback()
        raise
