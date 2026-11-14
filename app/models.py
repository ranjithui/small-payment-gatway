"""SQLAlchemy models. Mirrors migrations/001_feeflow_payment_workflow.sql (Supabase/Postgres).

All money is stored as integer paise to avoid floating point errors.
"""
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, utcnow
from app.states import ACTIVE_STATUS_VALUES

_active_in = "declaration_status IN (" + ", ".join(f"'{s}'" for s in sorted(ACTIVE_STATUS_VALUES)) + ")"


def new_id() -> str:
    return str(uuid.uuid4())


TRIAL_DAYS = 30


def default_trial_end() -> datetime:
    return utcnow() + timedelta(days=TRIAL_DAYS)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    short_code: Mapped[str] = mapped_column(String(16), unique=True)
    logo_url: Mapped[Optional[str]] = mapped_column(String(500))
    contact_phone: Mapped[Optional[str]] = mapped_column(String(20))
    contact_email: Mapped[Optional[str]] = mapped_column(String(200))
    upi_vpa: Mapped[str] = mapped_column(String(100))
    upi_payee_name: Mapped[str] = mapped_column(String(100))
    # Business (merchant) UPI ID: payment links then include the merchant code + a transaction reference.
    upi_is_merchant: Mapped[bool] = mapped_column(Boolean, default=False)
    merchant_code: Mapped[Optional[str]] = mapped_column(String(4))
    verification_grace_hours: Mapped[int] = mapped_column(Integer, default=24)
    reminder_frequency_hours: Mapped[int] = mapped_column(Integer, default=72)
    ack_sms_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Subscription: TRIAL (until trial_ends_at) / ACTIVE / SUSPENDED. "EXPIRED" is derived.
    status: Mapped[str] = mapped_column(String(20), default="TRIAL")
    trial_ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=default_trial_end)
    suspended_reason: Mapped[Optional[str]] = mapped_column(Text)
    # Send the fee link by SMS automatically when a monthly fee is created (off = manual button).
    auto_send_links: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # NULL until the admin creates their workspace on first sign-in.
    tenant_id: Mapped[Optional[str]] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Platform owner (FEEFLOW_ADMIN_EMAIL): manages all workspaces at /platform.
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Student(Base):
    __tablename__ = "students"
    __table_args__ = (UniqueConstraint("tenant_id", "student_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    student_code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    parent_name: Mapped[Optional[str]] = mapped_column(String(200))
    parent_mobile: Mapped[str] = mapped_column(String(20))
    parent_email: Mapped[Optional[str]] = mapped_column(String(200))
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date)
    gender: Mapped[Optional[str]] = mapped_column(String(20))
    class_grade: Mapped[Optional[str]] = mapped_column(String(40))
    school: Mapped[Optional[str]] = mapped_column(String(200))
    address: Mapped[Optional[str]] = mapped_column(Text)
    joined_on: Mapped[Optional[date]] = mapped_column(Date)
    # Monthly fee plan: amount + day of month it is due (1-31, clamped to the month's last day).
    monthly_fee_paise: Mapped[Optional[int]] = mapped_column(Integer)
    fee_due_day: Mapped[Optional[int]] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=utcnow)


class Installment(Base):
    __tablename__ = "installments"
    __table_args__ = (
        CheckConstraint("total_paise > 0", name="ck_installment_total_positive"),
        CheckConstraint("paid_paise >= 0", name="ck_installment_paid_nonneg"),
        CheckConstraint("excess_paise >= 0", name="ck_installment_excess_nonneg"),
        CheckConstraint("status IN ('UNPAID','PARTIALLY_PAID','PAID')", name="ck_installment_status"),
        # One automatically generated monthly fee per student per month.
        Index("uq_monthly_fee_per_student_month", "student_id", "billing_month", unique=True,
              sqlite_where=text("kind = 'MONTHLY'"), postgresql_where=text("kind = 'MONTHLY'")),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"), index=True)
    description: Mapped[str] = mapped_column(String(200))
    billing_month: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    kind: Mapped[str] = mapped_column(String(10), default="ONE_OFF")  # MONTHLY (auto) / ONE_OFF
    total_paise: Mapped[int] = mapped_column(Integer)
    paid_paise: Mapped[int] = mapped_column(Integer, default=0)  # verified, applied to this installment
    excess_paise: Mapped[int] = mapped_column(Integer, default=0)  # verified money above the total
    due_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="UNPAID")
    reminders_paused_until: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    student: Mapped[Student] = relationship()
    tenant: Mapped[Tenant] = relationship()

    @property
    def outstanding_paise(self) -> int:
        return max(self.total_paise - self.paid_paise, 0)


class PaymentLink(Base):
    __tablename__ = "payment_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    installment_id: Mapped[str] = mapped_column(ForeignKey("installments.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 of the secret URL token
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    installment: Mapped[Installment] = relationship()


class PaymentDeclaration(Base):
    """What the parent reported. Never a financial transaction by itself."""

    __tablename__ = "payment_declarations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_declaration_idempotency"),
        CheckConstraint("declared_amount_paise > 0", name="ck_declared_amount_positive"),
        CheckConstraint(
            "declaration_status IN ('PAYMENT_INITIATED','PAYMENT_PROCESSING','PAYMENT_REPORTED',"
            "'PENDING_VERIFICATION','VERIFIED','REJECTED','FAILED','EXPIRED')",
            name="ck_declaration_status",
        ),
        CheckConstraint("payment_type IN ('FULL','PARTIAL')", name="ck_declaration_payment_type"),
        # At most ONE active declaration per installment (duplicate-tap protection at DB level).
        Index(
            "uq_one_active_declaration_per_installment",
            "installment_id",
            unique=True,
            sqlite_where=text(_active_in),
            postgresql_where=text(_active_in),
        ),
        Index("ix_declarations_tenant_status", "tenant_id", "declaration_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    public_ref: Mapped[str] = mapped_column(String(32), unique=True)  # safe to show parents
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    installment_id: Mapped[str] = mapped_column(ForeignKey("installments.id"), index=True)
    payment_link_id: Mapped[str] = mapped_column(ForeignKey("payment_links.id"))
    parent_identifier: Mapped[Optional[str]] = mapped_column(String(100))
    declared_amount_paise: Mapped[int] = mapped_column(Integer)
    payment_type: Mapped[str] = mapped_column(String(10), default="FULL")
    payment_mode: Mapped[str] = mapped_column(String(20), default="UPI")
    upi_app: Mapped[Optional[str]] = mapped_column(String(30))
    declaration_status: Mapped[str] = mapped_column(String(30))
    declared_at: Mapped[Optional[datetime]] = mapped_column(DateTime)  # when "I have paid" was tapped
    approximate_payment_time: Mapped[Optional[str]] = mapped_column(String(50))
    clarification_note: Mapped[Optional[str]] = mapped_column(Text)  # parent-provided notes
    parent_upi_reference: Mapped[Optional[str]] = mapped_column(String(64))  # optional, never required
    clarification_request: Mapped[Optional[str]] = mapped_column(Text)  # admin question to the parent
    clarification_requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    admin_note: Mapped[Optional[str]] = mapped_column(Text)
    verified_payment_id: Mapped[Optional[str]] = mapped_column(String(36))
    reviewed_by: Mapped[Optional[str]] = mapped_column(ForeignKey("admin_users.id"))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text)
    grace_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    grace_expiry_notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    installment: Mapped[Installment] = relationship()


class VerifiedPayment(Base):
    """A real bank transaction confirmed by an administrator (or authorized reconciliation)."""

    __tablename__ = "verified_payments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "bank_reference", name="uq_verified_bank_reference"),
        UniqueConstraint("bank_transaction_id", name="uq_verified_bank_transaction"),
        CheckConstraint("amount_paise > 0", name="ck_verified_amount_positive"),
        CheckConstraint("applied_paise + excess_paise = amount_paise", name="ck_verified_split"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    installment_id: Mapped[str] = mapped_column(ForeignKey("installments.id"), index=True)
    declaration_id: Mapped[Optional[str]] = mapped_column(ForeignKey("payment_declarations.id"), index=True)
    bank_transaction_id: Mapped[Optional[str]] = mapped_column(ForeignKey("bank_transactions.id"))
    amount_paise: Mapped[int] = mapped_column(Integer)
    applied_paise: Mapped[int] = mapped_column(Integer)
    excess_paise: Mapped[int] = mapped_column(Integer, default=0)
    bank_reference: Mapped[Optional[str]] = mapped_column(String(64))  # UTR, admin-side only
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    payment_mode: Mapped[str] = mapped_column(String(20), default="UPI")
    evidence_note: Mapped[Optional[str]] = mapped_column(Text)
    reconciliation_source: Mapped[str] = mapped_column(String(30), default="ADMIN_MANUAL")
    verified_by: Mapped[Optional[str]] = mapped_column(ForeignKey("admin_users.id"))
    verified_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    receipt_id: Mapped[Optional[str]] = mapped_column(String(36))


class PaymentAdjustment(Base):
    __tablename__ = "payment_adjustments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    installment_id: Mapped[str] = mapped_column(ForeignKey("installments.id"), index=True)
    verified_payment_id: Mapped[str] = mapped_column(ForeignKey("verified_payments.id"))
    amount_paise: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20), default="EXCESS")
    status: Mapped[str] = mapped_column(String(20), default="FLAGGED")  # FLAGGED / REFUNDED / ADJUSTED
    note: Mapped[Optional[str]] = mapped_column(Text)
    resolved_by: Mapped[Optional[str]] = mapped_column(ForeignKey("admin_users.id"))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Receipt(Base):
    __tablename__ = "receipts"
    __table_args__ = (UniqueConstraint("tenant_id", "receipt_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    installment_id: Mapped[str] = mapped_column(ForeignKey("installments.id"), index=True)
    declaration_id: Mapped[Optional[str]] = mapped_column(ForeignKey("payment_declarations.id"))
    receipt_number: Mapped[str] = mapped_column(String(40))
    public_token: Mapped[str] = mapped_column(String(64), unique=True)
    amount_paise: Mapped[int] = mapped_column(Integer)
    total_paid_after_paise: Mapped[int] = mapped_column(Integer)
    balance_after_paise: Mapped[int] = mapped_column(Integer)
    issued_by: Mapped[Optional[str]] = mapped_column(ForeignKey("admin_users.id"))
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ReminderSchedule(Base):
    __tablename__ = "reminder_schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    installment_id: Mapped[str] = mapped_column(ForeignKey("installments.id"), index=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime, index=True)
    amount_paise: Mapped[int] = mapped_column(Integer)
    to_mobile: Mapped[str] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text)  # pre-rendered SMS text the device sends
    status: Mapped[str] = mapped_column(String(20), default="SCHEDULED")  # SCHEDULED / CANCELLED / SENT
    cancel_reason: Mapped[Optional[str]] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, default=1)
    sync_status: Mapped[str] = mapped_column(String(20), default="PENDING_SYNC")  # PENDING_SYNC / SYNCED
    synced_version: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Device(Base):
    """Registered Android phone whose physical SIM sends SMS."""

    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class SmsOutbox(Base):
    __tablename__ = "sms_outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    to_mobile: Mapped[str] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(40))
    dedupe_key: Mapped[str] = mapped_column(String(150), unique=True)  # prevents duplicate messages
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")  # QUEUED/DISPATCHED/SENT/FAILED
    installment_id: Mapped[Optional[str]] = mapped_column(String(36))
    declaration_id: Mapped[Optional[str]] = mapped_column(String(36))
    reminder_id: Mapped[Optional[str]] = mapped_column(String(36))
    device_id: Mapped[Optional[str]] = mapped_column(String(36))
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    dispatched_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class AdminNotification(Base):
    __tablename__ = "admin_notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    declaration_id: Mapped[Optional[str]] = mapped_column(String(36))
    installment_id: Mapped[Optional[str]] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class AuditLog(Base):
    """Append-only. The Postgres migration installs a trigger that blocks UPDATE/DELETE."""

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    actor_type: Mapped[str] = mapped_column(String(20))  # PARENT / ADMIN / SYSTEM / DEVICE
    actor_id: Mapped[Optional[str]] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(60))
    declaration_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    installment_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    previous_status: Mapped[Optional[str]] = mapped_column(String(30))
    new_status: Mapped[Optional[str]] = mapped_column(String(30))
    reason: Mapped[Optional[str]] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(40))
    details_json: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("scope", "key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scope: Mapped[str] = mapped_column(String(120))
    key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    response_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BankTransaction(Base):
    """Future-ready: credits from an authorized bank statement import / feed."""

    __tablename__ = "bank_transactions"
    __table_args__ = (UniqueConstraint("tenant_id", "source", "bank_reference"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    source: Mapped[str] = mapped_column(String(40))  # STATEMENT_IMPORT / BANK_FEED / PROVIDER_API
    bank_reference: Mapped[str] = mapped_column(String(64))
    amount_paise: Mapped[int] = mapped_column(Integer)
    credited_at: Mapped[datetime] = mapped_column(DateTime)
    credited_to_vpa: Mapped[Optional[str]] = mapped_column(String(100))
    payer_vpa: Mapped[Optional[str]] = mapped_column(String(100))
    narration: Mapped[Optional[str]] = mapped_column(Text)
    imported_by: Mapped[Optional[str]] = mapped_column(String(36))
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
