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


def build_query(vpa: str, payee_name: str, amount_paise: Optional[int], note: str) -> str:
    parts = [("pa", vpa), ("pn", payee_name)]
    if amount_paise:
        parts.append(("am", amount_str(amount_paise)))
    parts += [("cu", "INR"), ("tn", note[:50])]
    return "&".join(f"{k}={quote(v, safe='@.-_')}" for k, v in parts)


def build_uris(vpa: str, payee_name: str, amount_paise: Optional[int], note: str) -> Dict[str, str]:
    q = build_query(vpa, payee_name, amount_paise, note)
    return {app: f"{prefix}?{q}" for app, prefix in _APP_PREFIX.items()}
