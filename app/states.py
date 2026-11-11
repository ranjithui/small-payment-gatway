"""Payment state engine.

Two independent state machines are modelled:

* ``DeclarationStatus`` – what the parent *said* and what the admin decided about
  one payment declaration. A declaration is NOT a financial transaction.
* ``InstallmentStatus`` – the financial balance of an installment, driven only by
  verified payment transactions.

``display_status`` combines both for the UI without merging them in storage.
"""
from enum import Enum
from typing import Dict, Optional, Set


class DeclarationStatus(str, Enum):
    PAYMENT_INITIATED = "PAYMENT_INITIATED"
    PAYMENT_PROCESSING = "PAYMENT_PROCESSING"
    PAYMENT_REPORTED = "PAYMENT_REPORTED"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class InstallmentStatus(str, Enum):
    UNPAID = "UNPAID"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"


class DisplayStatus(str, Enum):
    UNPAID = "UNPAID"
    PAYMENT_INITIATED = "PAYMENT_INITIATED"
    PAYMENT_PROCESSING = "PAYMENT_PROCESSING"
    PAYMENT_REPORTED = "PAYMENT_REPORTED"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    VERIFIED = "VERIFIED"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    PAID = "PAID"


D = DeclarationStatus

# Declarations that block a new declaration for the same installment.
ACTIVE_STATUSES: Set[DeclarationStatus] = {
    D.PAYMENT_INITIATED,
    D.PAYMENT_PROCESSING,
    D.PAYMENT_REPORTED,
    D.PENDING_VERIFICATION,
}
ACTIVE_STATUS_VALUES = tuple(s.value for s in ACTIVE_STATUSES)

# Declarations shown in the admin Payment Verification queue.
REVIEWABLE_STATUSES: Set[DeclarationStatus] = {D.PAYMENT_PROCESSING, D.PAYMENT_REPORTED, D.PENDING_VERIFICATION}

# Parent-driven transitions (None = no active declaration yet).
PARENT_TRANSITIONS: Dict[Optional[DeclarationStatus], Set[DeclarationStatus]] = {
    None: {D.PAYMENT_INITIATED, D.PAYMENT_PROCESSING, D.PAYMENT_REPORTED, D.FAILED},
    D.PAYMENT_INITIATED: {D.PAYMENT_INITIATED, D.PAYMENT_PROCESSING, D.PAYMENT_REPORTED, D.FAILED},
    D.PAYMENT_PROCESSING: {D.PAYMENT_PROCESSING, D.PAYMENT_REPORTED, D.FAILED},
    # Once reported, the parent cannot change the declaration; only the admin can.
    D.PAYMENT_REPORTED: set(),
    D.PENDING_VERIFICATION: set(),
}

# Admin / system transitions.
ADMIN_TRANSITIONS: Dict[str, Dict[str, object]] = {
    "acknowledge": {"from": {D.PAYMENT_REPORTED}, "to": D.PENDING_VERIFICATION},
    "confirm": {"from": set(ACTIVE_STATUSES), "to": D.VERIFIED},
    "reject": {"from": set(ACTIVE_STATUSES), "to": D.REJECTED},
    "request_clarification": {"from": set(ACTIVE_STATUSES), "to": D.PENDING_VERIFICATION},
    "parent_clarification": {"from": {D.PAYMENT_REPORTED, D.PENDING_VERIFICATION}, "to": D.PENDING_VERIFICATION},
    "reopen": {"from": {D.REJECTED}, "to": D.PENDING_VERIFICATION},
    "expire": {"from": {D.PAYMENT_INITIATED}, "to": D.EXPIRED},
}


class InvalidTransition(Exception):
    def __init__(self, current: Optional[str], target: str, message: Optional[str] = None):
        self.current = current
        self.target = target
        super().__init__(message or f"Cannot move declaration from {current} to {target}")


def check_parent_transition(current: Optional[DeclarationStatus], target: DeclarationStatus) -> None:
    allowed = PARENT_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidTransition(current.value if current else None, target.value)


def check_admin_transition(action: str, current: DeclarationStatus) -> DeclarationStatus:
    rule = ADMIN_TRANSITIONS[action]
    if current not in rule["from"]:  # type: ignore[operator]
        raise InvalidTransition(current.value, rule["to"].value)  # type: ignore[union-attr]
    return rule["to"]  # type: ignore[return-value]


def installment_status_for(total_paise: int, paid_paise: int) -> InstallmentStatus:
    if paid_paise >= total_paise:
        return InstallmentStatus.PAID
    if paid_paise > 0:
        return InstallmentStatus.PARTIALLY_PAID
    return InstallmentStatus.UNPAID


def display_status(
    installment_status: InstallmentStatus,
    active_declaration: Optional[DeclarationStatus],
    latest_declaration: Optional[DeclarationStatus],
    link_expired: bool,
) -> DisplayStatus:
    if installment_status == InstallmentStatus.PAID:
        return DisplayStatus.PAID
    if active_declaration is not None:
        return DisplayStatus(active_declaration.value)
    if link_expired:
        return DisplayStatus.EXPIRED
    if latest_declaration in (D.REJECTED, D.FAILED):
        return DisplayStatus(latest_declaration.value)
    if installment_status == InstallmentStatus.PARTIALLY_PAID:
        return DisplayStatus.PARTIALLY_PAID
    return DisplayStatus.UNPAID
