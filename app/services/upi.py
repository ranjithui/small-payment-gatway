"""UPI Intent URI construction (NPCI deep-link format)."""
import re
from typing import Dict, List, Optional
from urllib.parse import parse_qs, quote, urlparse

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


VPA_RE = re.compile(r"^[A-Za-z0-9.\-_]{2,256}@[A-Za-z][A-Za-z0-9]{1,63}$")


def validate_upi_uri(uri: str, *, vpa: str, payee_name: str, amount_paise: Optional[int], reference: str) -> List[str]:
    """Check a generated UPI link carries exactly the server-side payment details. Returns problems."""
    problems: List[str] = []
    parsed = urlparse(uri)
    if parsed.scheme != "upi" or parsed.netloc != "pay":
        problems.append("not a upi://pay link")
    q = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
    if not VPA_RE.match(q.get("pa", "")):
        problems.append("invalid payee UPI ID")
    if q.get("pa") != vpa:
        problems.append("payee UPI ID does not match the centre's settings")
    if not q.get("pn") or q.get("pn") != payee_name:
        problems.append("payee name missing or different")
    if q.get("cu") != "INR":
        problems.append("currency must be INR")
    if amount_paise is not None and q.get("am") != amount_str(amount_paise):
        problems.append("amount does not match the fee")
    if amount_paise is None and "am" in q:
        problems.append("open-amount link must not carry an amount")
    if q.get("tn") != reference[:50]:
        problems.append("payment reference missing or different")
    return problems


def build_uris(vpa: str, payee_name: str, amount_paise: Optional[int], note: str,
               merchant_code: Optional[str] = None, txn_ref: Optional[str] = None) -> Dict[str, str]:
    q = build_query(vpa, payee_name, amount_paise, note, merchant_code, txn_ref)
    return {app: f"{prefix}?{q}" for app, prefix in _APP_PREFIX.items()}
