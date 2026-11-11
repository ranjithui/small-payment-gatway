import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import Base, SessionLocal, get_engine, init_engine
from app.main import create_app
from app.ratelimit import limiter
from app.seed import DEMO_ADMIN_EMAIL, DEMO_ADMIN_PASSWORD, DEMO_DEVICE_TOKEN, OTHER_ADMIN_EMAIL, OTHER_ADMIN_PASSWORD, seed


@pytest.fixture()
def env():
    settings.rate_limit_enabled = False
    limiter.reset()
    # FEEFLOW_TEST_DATABASE_URL=postgresql+psycopg://... runs the suite on real PostgreSQL.
    url = os.getenv("FEEFLOW_TEST_DATABASE_URL", "sqlite://")
    if url != "sqlite://":
        from app import models  # noqa: F401

        init_engine(url)
        Base.metadata.drop_all(get_engine())
        get_engine().dispose()
    app = create_app(url, run_jobs=False)
    db = SessionLocal()
    data = seed(db)
    db.close()
    with TestClient(app) as client:
        yield Env(client, data)
    settings.rate_limit_enabled = True


def key() -> str:
    return "k-" + uuid.uuid4().hex


class Env:
    def __init__(self, client: TestClient, data):
        self.c = client
        self.data = data
        self.admin = self._login(DEMO_ADMIN_EMAIL, DEMO_ADMIN_PASSWORD)
        self.other_admin = self._login(OTHER_ADMIN_EMAIL, OTHER_ADMIN_PASSWORD)
        self.device = {"X-Device-Token": DEMO_DEVICE_TOKEN}

    def _login(self, email, pw):
        r = self.c.post("/api/v1/auth/login", json={"email": email, "password": pw})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def token(self, code="BM-1001"):
        return self.data["links"][code]

    def inst(self, code="BM-1001"):
        return self.data["installments"][code]

    # parent helpers
    def view(self, code="BM-1001"):
        return self.c.get(f"/api/v1/payment-declarations/public/{self.token(code)}")

    def declare(self, code="BM-1001", action="PAID", idem=None, **kw):
        body = {"token": self.token(code), "action": action, **kw}
        return self.c.post("/api/v1/payment-declarations", json=body, headers={"Idempotency-Key": idem or key()})

    # admin helpers
    def pending(self, headers=None):
        r = self.c.get("/api/v1/payment-verification/pending", headers=headers or self.admin)
        assert r.status_code == 200, r.text
        return r.json()["items"]

    def decl_id(self, code="BM-1001"):
        items = [d for d in self.pending() if d["installment"]["id"] == self.inst(code)]
        assert items, "no pending declaration"
        return items[0]["id"]

    def confirm(self, decl_id, transactions, idem=None, **kw):
        body = {
            "confirm": True,
            "bank_transaction_checked": True,
            "belongs_to_centre": True,
            "transactions": transactions,
            **kw,
        }
        return self.c.post(
            f"/api/v1/payment-verification/{decl_id}/confirm",
            json=body,
            headers={**self.admin, "Idempotency-Key": idem or key()},
        )

    def reject(self, decl_id, reason="No matching credit in bank statement", headers=None):
        return self.c.post(
            f"/api/v1/payment-verification/{decl_id}/reject",
            json={"reason": reason},
            headers={**(headers or self.admin), "Idempotency-Key": key()},
        )

    def status(self, code="BM-1001"):
        r = self.c.get(f"/api/v1/installments/{self.inst(code)}/payment-status", headers=self.admin)
        assert r.status_code == 200, r.text
        return r.json()

    def sync(self):
        r = self.c.get("/api/v1/devices/sync", headers=self.device)
        assert r.status_code == 200, r.text
        return r.json()
