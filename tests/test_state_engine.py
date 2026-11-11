"""Unit tests for every transition in the payment state engine."""
import pytest

from app.states import (
    ACTIVE_STATUSES,
    ADMIN_TRANSITIONS,
    PARENT_TRANSITIONS,
    DeclarationStatus as D,
    DisplayStatus,
    InstallmentStatus as I,
    InvalidTransition,
    check_admin_transition,
    check_parent_transition,
    display_status,
    installment_status_for,
)

ALL = list(D)


@pytest.mark.parametrize("current", [None] + ALL)
@pytest.mark.parametrize("target", ALL)
def test_parent_transition_matrix(current, target):
    allowed = PARENT_TRANSITIONS.get(current, set())
    if target in allowed:
        check_parent_transition(current, target)
    else:
        with pytest.raises(InvalidTransition):
            check_parent_transition(current, target)


def test_parent_cannot_self_verify_or_change_reported():
    for cur in (None, D.PAYMENT_INITIATED, D.PAYMENT_PROCESSING):
        for forbidden in (D.VERIFIED, D.REJECTED, D.PENDING_VERIFICATION, D.EXPIRED):
            with pytest.raises(InvalidTransition):
                check_parent_transition(cur, forbidden)
    for cur in (D.PAYMENT_REPORTED, D.PENDING_VERIFICATION):
        for t in ALL:
            with pytest.raises(InvalidTransition):
                check_parent_transition(cur, t)


@pytest.mark.parametrize("action", list(ADMIN_TRANSITIONS))
@pytest.mark.parametrize("current", ALL)
def test_admin_transition_matrix(action, current):
    rule = ADMIN_TRANSITIONS[action]
    if current in rule["from"]:
        assert check_admin_transition(action, current) == rule["to"]
    else:
        with pytest.raises(InvalidTransition):
            check_admin_transition(action, current)


def test_terminal_states_cannot_be_verified_again():
    for terminal in (D.VERIFIED, D.REJECTED, D.FAILED, D.EXPIRED):
        assert terminal not in ACTIVE_STATUSES
        with pytest.raises(InvalidTransition):
            check_admin_transition("confirm", terminal)


def test_installment_status_from_balance():
    assert installment_status_for(1000, 0) == I.UNPAID
    assert installment_status_for(1000, 400) == I.PARTIALLY_PAID
    assert installment_status_for(1000, 1000) == I.PAID


def test_display_status_keeps_models_separate():
    assert display_status(I.PAID, D.PAYMENT_REPORTED, None, False) == DisplayStatus.PAID
    assert display_status(I.UNPAID, D.PAYMENT_REPORTED, None, False) == DisplayStatus.PAYMENT_REPORTED
    assert display_status(I.UNPAID, None, None, True) == DisplayStatus.EXPIRED
    assert display_status(I.UNPAID, None, D.REJECTED, False) == DisplayStatus.REJECTED
    assert display_status(I.PARTIALLY_PAID, None, D.VERIFIED, False) == DisplayStatus.PARTIALLY_PAID
    assert display_status(I.UNPAID, None, None, False) == DisplayStatus.UNPAID
