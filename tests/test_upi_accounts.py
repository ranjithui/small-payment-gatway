"""Personal UPI (default) and Merchant UPI (optional) accounts, payment-method switches, platform view."""
from urllib.parse import parse_qs, urlparse

from app.db import SessionLocal
from app.models import AdminUser, Tenant
from app.security import hash_password
from tests.conftest import key


def q(uri):
    return {k: v[0] for k, v in parse_qs(urlparse(uri).query).items()}


def owner(env, email):
    r = env.c.post("/api/v1/auth/signup", json={"name": "Owner", "email": email, "password": "strong-pass-123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_register_with_personal_upi_no_business_details(env):
    h = owner(env, "tutor@home.in")
    r = env.c.post("/api/v1/workspace", headers=h, json={
        "name": "Lakshmi Home Tuition", "upi_payee_name": "LAKSHMI R", "upi_vpa": "lakshmi.r@okhdfcbank",
        "account_type": "PERSONAL", "upi_bank_name": "HDFC Bank"})
    assert r.status_code == 201, r.text
    w = r.json()
    assert w["account_type"] == "PERSONAL" and w["upi_bank_name"] == "HDFC Bank" and w["business_name"] is None
    # One-tap is not offered for personal UPI IDs (UPI apps block it); QR + copy are
    assert w["payment_methods"] == {"upi_app": False, "qr": True, "copy": True}
    assert w["upi_config_status"]["status"] == "READY"
    assert "can't confirm" in w["upi_config_status"]["message"]  # never claims bank verification


def test_register_with_merchant_upi(env):
    h = owner(env, "biz@centre.in")
    r = env.c.post("/api/v1/workspace", headers=h, json={
        "name": "Excel Coaching", "upi_payee_name": "Excel Coaching", "upi_vpa": "excelcoaching@okbizaxis",
        "account_type": "MERCHANT", "business_name": "Excel Coaching Pvt Ltd"})
    w = r.json()
    assert r.status_code == 201 and w["account_type"] == "MERCHANT" and w["merchant_code"] == "8299"
    assert w["business_name"] == "Excel Coaching Pvt Ltd"
    assert w["payment_methods"] == {"upi_app": True, "qr": True, "copy": True}


def test_existing_centres_default_to_personal_with_all_methods(env):
    w = env.c.get("/api/v1/workspace", headers=env.admin).json()
    assert w["account_type"] == "PERSONAL" and w["payment_methods_settings"] == {"upi_app": True, "qr": True, "copy": True}
    v = env.view().json()["upi"]
    assert v["methods"] == {"upi_app": False, "qr": True, "copy": True} and v["qr_has_amount"] is False


def test_payment_page_follows_account_type_and_methods(env):
    # Personal: no one-tap, QR without amount
    u = env.view().json()["upi"]
    assert u["account_type"] == "PERSONAL" and not u["methods"]["upi_app"]
    # Switch to merchant: one-tap on, QR with amount
    r = env.c.patch("/api/v1/workspace", headers=env.admin, json={"account_type": "MERCHANT"})
    assert r.json()["account_type"] == "MERCHANT"
    u = env.view().json()["upi"]
    assert u["methods"]["upi_app"] and u["qr_has_amount"] and q(u["uris"]["OTHER_UPI"])["mc"] == "8299"
    # Disable one-tap + QR: only copy remains
    r = env.c.patch("/api/v1/workspace", headers=env.admin, json={"pay_intent_enabled": False, "pay_qr_enabled": False})
    assert r.json()["payment_methods"] == {"upi_app": False, "qr": False, "copy": True}
    assert env.view().json()["upi"]["methods"] == {"upi_app": False, "qr": False, "copy": True}
    # Cannot turn everything off
    r = env.c.patch("/api/v1/workspace", headers=env.admin, json={"pay_manual_enabled": False})
    assert r.status_code == 422 and "at least one" in r.json()["detail"]
    assert env.c.get("/api/v1/workspace", headers=env.admin).json()["payment_methods_settings"]["copy"] is True
    # Back to personal: one-tap switch is ignored for personal IDs, so personal with only one-tap enabled is refused
    env.c.patch("/api/v1/workspace", headers=env.admin, json={"pay_qr_enabled": True})
    r = env.c.patch("/api/v1/workspace", headers=env.admin,
                    json={"account_type": "PERSONAL", "pay_qr_enabled": False, "pay_manual_enabled": False})
    assert r.status_code == 422


def test_update_upi_id_audited_and_links_follow(env):
    r = env.c.patch("/api/v1/workspace", headers=env.admin, json={"upi_vpa": "newid@okicici", "upi_bank_name": "ICICI"})
    assert r.status_code == 200 and r.json()["upi_bank_name"] == "ICICI"
    assert env.view().json()["upi"]["payee_vpa"] == "newid@okicici"
    assert env.c.patch("/api/v1/workspace", headers=env.admin, json={"upi_vpa": "not valid"}).status_code == 422
    logs = env.c.get("/api/v1/audit-logs", headers=env.admin).json()["items"]
    assert any(a["action"] == "UPI_ID_CHANGED" for a in logs)


def test_invalid_stored_upi_id_shows_check_status(env):
    db = SessionLocal()
    db.query(Tenant).filter_by(short_code="BMTC").one().upi_vpa = "broken-id"
    db.commit()
    db.close()
    w = env.c.get("/api/v1/workspace", headers=env.admin).json()
    assert w["upi_config_status"]["status"] == "CHECK_UPI_ID"
    assert env.view().json()["upi"] is None  # never shows a broken payment link


def test_payment_settings_are_private_per_centre(env):
    mine = env.c.get("/api/v1/workspace", headers=env.admin).json()
    other = env.c.get("/api/v1/workspace", headers=env.other_admin).json()
    assert mine["upi_vpa"] != other["upi_vpa"] and other["name"] == "Sunrise Academy"


def test_platform_shows_account_type_masked_upi_and_payment_counts(env):
    db = SessionLocal()
    db.add(AdminUser(tenant_id=None, email="boss@feeflow.in", name="Boss", is_platform_admin=True,
                     password_hash=hash_password("boss-password-1")))
    db.commit()
    db.close()
    p = {"Authorization": "Bearer " + env.c.post("/api/v1/auth/login", json={"email": "boss@feeflow.in",
                                                                               "password": "boss-password-1"}).json()["access_token"]}
    env.declare("BM-1001", action="PAID")
    env.confirm(env.decl_id("BM-1001"), [{"amount_paise": 250000, "bank_reference": "UTR-PLAT-1"}])
    env.declare("BM-1002", action="PAID")
    env.reject(env.decl_id("BM-1002"))
    env.declare("BM-1003", action="PAID")
    env.c.patch("/api/v1/workspace", headers=env.other_admin, json={"account_type": "MERCHANT"})

    data = env.c.get("/api/v1/platform/workspaces", headers=p).json()
    bm = next(w for w in data["items"] if w["short_code"] == "BMTC")
    sun = next(w for w in data["items"] if w["short_code"] == "SUNA")
    assert bm["account_type"] == "PERSONAL" and sun["account_type"] == "MERCHANT"
    assert bm["upi_vpa"] == "bri…@okaxis" and "brightminds@" not in str(data)  # masked
    assert (bm["pending_verification"], bm["confirmed_payments"], bm["rejected_declarations"]) == (1, 1, 1)
    assert data["summary"]["PERSONAL"] == 1 and data["summary"]["MERCHANT"] == 1
