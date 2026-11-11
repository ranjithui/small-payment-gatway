"""Real concurrency against PostgreSQL (row locks + partial unique index). Skipped on SQLite.

  FEEFLOW_TEST_DATABASE_URL=postgresql+psycopg://postgres@localhost:5433/feeflow_test pytest tests/test_concurrency_postgres.py
"""
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.db import SessionLocal
from app.models import PaymentDeclaration, SmsOutbox, VerifiedPayment
from tests.conftest import key

pytestmark = pytest.mark.skipif(
    not os.getenv("FEEFLOW_TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="needs PostgreSQL (FEEFLOW_TEST_DATABASE_URL)",
)


def test_simultaneous_taps_create_one_declaration(env):
    def tap(_):
        return env.declare(action="PAID").status_code

    with ThreadPoolExecutor(max_workers=10) as pool:
        codes = list(pool.map(tap, range(10)))
    assert all(c in (200, 201) for c in codes), codes
    assert codes.count(201) == 1  # exactly one created, the rest returned the existing one
    db = SessionLocal()
    assert db.query(PaymentDeclaration).filter_by(installment_id=env.inst()).count() == 1
    assert db.query(SmsOutbox).filter_by(kind="DECLARATION_ACK").count() == 1
    db.close()


def test_simultaneous_admin_confirms_credit_once(env):
    env.declare(action="PAID")
    decl_id = env.decl_id()

    def confirm(i):
        return env.confirm(decl_id, [{"amount_paise": 250000, "bank_reference": f"UTR-RACE-{i}"}]).status_code

    with ThreadPoolExecutor(max_workers=5) as pool:
        codes = list(pool.map(confirm, range(5)))
    assert codes.count(200) == 1, codes
    assert all(c == 409 for c in codes if c != 200), codes
    db = SessionLocal()
    assert db.query(VerifiedPayment).count() == 1
    db.close()
    assert env.status()["paid_paise"] == 250000
