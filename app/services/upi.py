"""UPI Intent URI construction (NPCI deep-link format)."""
from typing import Dict, Optional
from urllib.parse import quote

UPI_APPS = ("GPAY", "PHONEPE", "PAYTM", "OTHER_UPI", "QR")

# App-specific schemes open the chosen app directly on Android; all share the
# standard upi://pay query parameters. OTHER_UPI uses the generic chooser.
_APP_PREFIX = {
    "GPAY": "tez://upi/pay",
    "PHONEPE": "phonepe://pay",
    "PAYTM": "paytmmp://pay",
    "OTHER_UPI": "upi://pay",
    "QR": "upi://pay",
}


def amount_str(paise: int) -> str:
    return f"{paise // 100}.{paise % 100:02d}"


# NPCI merchant category code for schools & educational services.
DEFAULT_MERCHANT_CODE = "8299"


def build_query(vpa: str, payee_name: str, amount_paise: Optional[int], note: str,
                merchant_code: Optional[str] = None, txn_ref: Optional[str] = None) -> str:
    """Personal UPI IDs: pa, pn, am, cu, tn. Business (merchant) UPI IDs also carry mc + tr, which UPI apps
    expect for merchant payments (pre-filled links to personal IDs are often blocked by the apps)."""
    parts = [("pa", vpa), ("pn", payee_name)]
    if merchant_code:
        parts.append(("mc", merchant_code))
        if txn_ref:
            parts.append(("tr", txn_ref[:35]))
    if amount_paise:
        parts.append(("am", amount_str(amount_paise)))
    parts += [("cu", "INR"), ("tn", note[:50])]
    return "&".join(f"{k}={quote(v, safe='@.-_')}" for k, v in parts)


def build_uris(vpa: str, payee_name: str, amount_paise: Optional[int], note: str,
               merchant_code: Optional[str] = None, txn_ref: Optional[str] = None) -> Dict[str, str]:
    q = build_query(vpa, payee_name, amount_paise, note, merchant_code, txn_ref)
    return {app: f"{prefix}?{q}" for app, prefix in _APP_PREFIX.items()}
