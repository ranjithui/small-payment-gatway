"""Request validation models."""
from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

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


GENDERS = ("MALE", "FEMALE", "OTHER")


class _StudentFields(BaseModel):
    date_of_birth: Optional[date] = None
    gender: Optional[Literal["MALE", "FEMALE", "OTHER"]] = None
    class_grade: Optional[str] = Field(None, max_length=40)
    school: Optional[str] = Field(None, max_length=200)
    address: Optional[str] = Field(None, max_length=500)
    parent_name: Optional[str] = Field(None, max_length=200)
    parent_email: Optional[str] = Field(None, max_length=200, pattern=r"^$|^[^@\s]+@[^@\s]+\.[^@\s]+$")
    joined_on: Optional[date] = None
    monthly_fee_paise: Optional[int] = Field(None, gt=0, le=MAX_PAISE, description="Monthly fee in paise")
    fee_due_day: Optional[int] = Field(None, ge=1, le=31, description="Day of month the fee is due")

    @field_validator("date_of_birth")
    @classmethod
    def _dob(cls, v: Optional[date]) -> Optional[date]:
        if v is not None and (v > date.today() or v.year < 1900):
            raise ValueError("Enter a valid date of birth")
        return v


class StudentCreate(_StudentFields):
    student_code: Optional[str] = Field(None, min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._/-]+$",
                                        description="Generated if omitted")
    name: str = Field(min_length=2, max_length=200)
    parent_mobile: str = Field(max_length=20)

    _mobile = field_validator("parent_mobile")(_normalize_mobile)

    @model_validator(mode="after")
    def _plan(self):
        if (self.monthly_fee_paise is None) != (self.fee_due_day is None):
            raise ValueError("Enter both the monthly fee amount and the fee due day (or neither)")
        return self


class StudentUpdate(_StudentFields):
    name: Optional[str] = Field(None, min_length=2, max_length=200)
    parent_mobile: Optional[str] = Field(None, max_length=20)
    active: Optional[bool] = None
    clear_fee_plan: bool = False

    @field_validator("parent_mobile")
    @classmethod
    def _mobile(cls, v: Optional[str]) -> Optional[str]:
        return None if v is None else _normalize_mobile(v)


class SendFeeLink(BaseModel):
    channel: Literal["SMS", "SHARE"] = "SMS"


class SignupRequest(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    email: str = Field(max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=10, max_length=200)


class PlatformExtendTrial(BaseModel):
    days: int = Field(30, ge=1, le=365)


class PlatformSuspend(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class InstallmentCreate(BaseModel):
    student_id: str = Field(max_length=36)
    description: str = Field(min_length=2, max_length=200)
    billing_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    total_paise: int = Field(gt=0, le=MAX_PAISE)
    due_date: date
    send_link_sms: bool = True
    schedule_reminders: bool = True
    link_valid_days: int = Field(45, ge=1, le=120)


UPI_VPA_PATTERN = r"^[A-Za-z0-9.\-_]{2,256}@[A-Za-z][A-Za-z0-9]{1,63}$"


def _clean_vpa(v: Optional[str]) -> Optional[str]:
    return v.strip() if v is not None else v


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200, description="Tuition centre name")
    upi_vpa: str = Field(max_length=100, description="Centre's UPI ID, e.g. brightminds@okaxis")
    upi_payee_name: str = Field(min_length=2, max_length=100, description="Name shown in UPI apps")
    contact_phone: Optional[str] = Field(None, max_length=20)
    contact_email: Optional[str] = Field(None, max_length=200)
    short_code: Optional[str] = Field(None, min_length=2, max_length=10, pattern=r"^[A-Za-z0-9]+$",
                                      description="Receipt number prefix; generated if omitted")

    _vpa_strip = field_validator("upi_vpa", mode="before")(_clean_vpa)

    @field_validator("upi_vpa")
    @classmethod
    def _vpa(cls, v: str) -> str:
        import re

        if not re.match(UPI_VPA_PATTERN, v):
            raise ValueError("Enter a valid UPI ID, e.g. centrename@okaxis")
        return v


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=200)
    upi_vpa: Optional[str] = Field(None, max_length=100)
    upi_payee_name: Optional[str] = Field(None, min_length=2, max_length=100)
    contact_phone: Optional[str] = Field(None, max_length=20)
    contact_email: Optional[str] = Field(None, max_length=200)
    verification_grace_hours: Optional[int] = Field(None, ge=1, le=168)
    reminder_frequency_hours: Optional[int] = Field(None, ge=12, le=720)
    ack_sms_enabled: Optional[bool] = None
    auto_send_links: Optional[bool] = None

    _vpa_strip = field_validator("upi_vpa", mode="before")(_clean_vpa)

    @field_validator("upi_vpa")
    @classmethod
    def _vpa(cls, v: Optional[str]) -> Optional[str]:
        import re

        if v is not None and not re.match(UPI_VPA_PATTERN, v):
            raise ValueError("Enter a valid UPI ID, e.g. centrename@okaxis")
        return v
