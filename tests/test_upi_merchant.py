"""Business (merchant) UPI IDs, pay-by-UPI-ID data and the downloadable PNG QR."""
from urllib.parse import parse_qs, urlparse

from tests.conftest import key


def _params(uri):
    return {k: v[0] for k, v in parse_qs(urlparse(uri).query).items()}


def test_personal_upi_link_has_no_merchant_params(env):
    v = env.view().json()
    u = v["upi"]
    assert u["is_merchant"] is False and u["amount_text"] == "2500.00"
    p = _params(u["uris"]["OTHER_UPI"])
    assert p["pa"] == "brightminds@okaxis" and p["am"] == "2500.00" and p["cu"] == "INR"
    assert "mc" not in p and "tr" not in p
    assert u["note"] == "BM-1001 " + v["fee"]["billing_month"] + " fee"


def test_merchant_upi_adds_mc_and_stable_tr(env):
    r = env.c.patch("/api/v1/workspace", headers=env.admin, json={"upi_is_merchant": True})
    assert r.status_code == 200 and r.json()["upi_is_merchant"] is True and r.json()["merchant_code"] == "8299"
    u = env.view().json()["upi"]
    assert u["is_merchant"] is True
    for app in ("GPAY", "PHONEPE", "PAYTM", "OTHER_UPI", "QR"):
        p = _params(u["uris"][app])
        assert p["mc"] == "8299" and p["tr"].startswith("FF") and p["tr"].isalnum() and len(p["tr"]) <= 35
        assert p["am"] == "2500.00"
    tr1 = _params(u["uris"]["GPAY"])["tr"]
    assert _params(env.view().json()["upi"]["uris"]["GPAY"])["tr"] == tr1  # stable for the same fee
    other = _params(env.view("BM-1002").json()["upi"]["uris"]["GPAY"])["tr"]
    assert other != tr1  # unique per fee

    assert env.c.patch("/api/v1/workspace", headers=env.admin, json={"merchant_code": "82A9"}).status_code == 422
    env.c.patch("/api/v1/workspace", headers=env.admin, json={"merchant_code": "5999"})
    assert _params(env.view().json()["upi"]["uris"]["GPAY"])["mc"] == "5999"
    logs = env.c.get("/api/v1/audit-logs", headers=env.admin).json()["items"]
    assert any(a["action"] == "WORKSPACE_UPDATED" and "upi_is_merchant" in (a["details"] or "") for a in logs)

    env.c.patch("/api/v1/workspace", headers=env.admin, json={"upi_is_merchant": False})
    assert "mc" not in _params(env.view().json()["upi"]["uris"]["GPAY"])


def test_workspace_created_as_merchant_gets_default_code(env):
    r = env.c.post("/api/v1/auth/signup", json={"name": "Biz Owner", "email": "biz@centre.in", "password": "strong-pass-123"})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = env.c.post("/api/v1/workspace", headers=h, json={"name": "Biz Centre", "upi_vpa": "bizcentre@okbizaxis",
                                                         "upi_payee_name": "Biz Centre", "upi_is_merchant": True})
    assert r.status_code == 201 and r.json()["merchant_code"] == "8299"


def test_png_qr_download_for_gallery(env):
    r = env.c.get(f"/api/v1/pay/{env.token()}/qr.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert "content-disposition" not in r.headers
    d = env.c.get(f"/api/v1/pay/{env.token()}/qr.png?download=1")
    assert d.headers["content-disposition"].startswith('attachment; filename="FeeFlow-UPI-QR-BM-1001-')
    # Not offered once the fee is paid
    env.declare(action="PAID")
    env.confirm(env.decl_id(), [{"amount_paise": 250000, "bank_reference": "UTR-QR"}])
    assert env.c.get(f"/api/v1/pay/{env.token()}/qr.png").status_code == 409
    assert env.c.get("/api/v1/pay/" + "x" * 32 + "/qr.png").status_code == 404


def test_copying_upi_id_records_intent_only(env):
    # The page records PAYMENT_INITIATED with OTHER_UPI when the parent copies the UPI ID: never proof of payment.
    r = env.declare(action="INITIATED", upi_app="OTHER_UPI", idem=key())
    assert r.status_code == 201 and r.json()["display_status"] == "PAYMENT_INITIATED"
    assert r.json()["fee"]["paid_paise"] == 0
