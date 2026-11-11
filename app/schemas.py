"""Request validation models."""
from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# Hard ceiling on any single amount (Rs. 10,00,000) to catch typos.
MAX_PAISE = 100_000_000

_SENSITIVE_WORDS = ("upi pin", "mpin", "password", "otp", "cvv")


def _no_secrets(v: Optional[str]) -> Optional[str]:
    if v and any(w in v.lower() for w in _SENSITIVE_WORDS):
        raise ValueError("Never share your UPI PIN, OTP or passwords. Please remove them from the note.")
    return v


class DeclarationCreate(BaseModel):
    token: str = Field(min_length=16, max_length=128, description="Secure payment-link token")
    action: Literal["INITIATED", "PROCESSING", "PAID", "FAILED"] = "PAID"
    payment_type: Literal["FULL", "PARTIAL"] = "FULL"
    declared_amount_paise: Optional[int] = Field(None, gt=0, le=MAX_PAISE)
    upi_app: Optional[str] = Field(None, max_length=30)
    approximate_payment_time: Optional[str] = Field(None, max_length=50)
    note: Optional[str] = Field(None, max_length=500)
    idempotency_key: Optional[str] = Field(None, max_length=100)

    _check_note = field_validator("note")(_no_secrets)


class ParentClarification(BaseModel):
    token: str = Field(min_length=16, max_length=128)
    note: str = Field(min_length=2, max_length=500)
    upi_reference: Optional[str] = Field(None, max_length=64, description="Optional - never required")
    approximate_payment_time: Optional[str] = Field(None, max_length=50)
    upi_app: Optional[str] = Field(None, max_length=30)

    _check_note = field_validator("note")(_no_secrets)


class TransactionIn(BaseModel):
    amount_paise: int = Field(gt=0, le=MAX_PAISE)
    bank_reference: Optional[str] = Field(None, max_length=64, description="UTR / bank reference (admin side)")
    paid_at: Optional[datetime] = None
    bank_transaction_id: Optional[str] = Field(None, max_length=36)


class ConfirmPayment(BaseModel):
    confirm: bool = Field(description="Administrator confirms this action")
    bank_transaction_checked: bool = Field(description="Admin checked the actual bank transaction")
    belongs_to_centre: bool = Field(description="Credit was received in the centre's own account")
    transactions: List[TransactionIn] = Field(min_length=1, max_length=5)
    amount_difference_acknowledged: bool = False
    evidence_note: Optional[str] = Field(None, max_length=1000)
    admin_note: Optional[str] = Field(None, max_length=1000)


class ManualPayment(BaseModel):
    confirm: bool
    bank_transaction_checked: bool
    belongs_to_centre: bool
    transactions: List[TransactionIn] = Field(min_length=1, max_length=5)
    evidence_note: Optional[str] = Field(None, max_length=1000)


class RejectDeclaration(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    admin_note: Optional[str] = Field(None, max_length=1000)


class ClarificationRequest(BaseModel):
    message: str = Field(min_length=3, max_length=500)


class ReopenDeclaration(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class LoginRequest(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


class BankTransactionIn(BaseModel):
    bank_reference: str = Field(min_length=4, max_length=64)
    amount_paise: int = Field(gt=0, le=MAX_PAISE)
    credited_at: datetime
    credited_to_vpa: Optional[str] = Field(None, max_length=100)
    payer_vpa: Optional[str] = Field(None, max_length=100)
    narration: Optional[str] = Field(None, max_length=500)


class BankImport(BaseModel):
    source: Literal["STATEMENT_IMPORT", "BANK_FEED", "PROVIDER_API"] = "STATEMENT_IMPORT"
    transactions: List[BankTransactionIn] = Field(min_length=1, max_length=1000)


class ResolveAdjustment(BaseModel):
    status: Literal["REFUNDED", "ADJUSTED"]
    note: str = Field(min_length=3, max_length=500)


class SmsAck(BaseModel):
    id: str = Field(max_length=36)
    status: Literal["SENT", "FAILED"]
    error: Optional[str] = Field(None, max_length=500)


class ReminderAck(BaseModel):
    id: str = Field(max_length=36)
    version: int = Field(ge=1)
    sent_at: Optional[datetime] = None  # set when the device actually sent this reminder


class DeviceAck(BaseModel):
    sms: List[SmsAck] = Field(default_factory=list, max_length=500)
    reminders: List[ReminderAck] = Field(default_factory=list, max_length=500)


class DeviceRegister(BaseModel):
    name: str = Field(min_length=2, max_length=100)


def _normalize_mobile(v: str) -> str:
    digits = "".join(c for c in v if c.isdigit())
    if len(digits) == 10:
        digits = "91" + digits
    elif len(digits) == 11 and digits.startswith("0"):
        digits = "91" + digits[1:]
    if len(digits) != 12 or not digits.startswith("91") or digits[2] not in "6789":
        raise ValueError("Enter a valid 10-digit Indian mobile number")
    return "+" + digits


class StudentCreate(BaseModel):
    student_code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._/-]+$")
    name: str = Field(min_length=2, max_length=200)
    parent_name: Optional[str] = Field(None, max_length=200)
    parent_mobile: str = Field(max_length=20)

    _mobile = field_validator("parent_mobile")(_normalize_mobile)


class InstallmentCreate(BaseModel):
    student_id: str = Field(max_length=36)
    description: str = Field(min_length=2, max_length=200)
    billing_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    total_paise: int = Field(gt=0, le=MAX_PAISE)
    due_date: date
    send_link_sms: bool = True
    schedule_reminders: bool = True
    link_valid_days: int = Field(45, ge=1, le=120)
