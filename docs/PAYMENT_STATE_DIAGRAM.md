# Payment state diagrams

FeeFlow keeps **two separate state machines**. A declaration is what a parent *said*;
an installment balance changes **only** through verified payment transactions.

## 1. Payment declaration (`payment_declarations.declaration_status`)

```mermaid
stateDiagram-v2
    [*] --> PAYMENT_INITIATED : parent picks GPay / PhonePe / Paytm / Other UPI / QR
    [*] --> PAYMENT_PROCESSING : "Payment is processing"
    [*] --> PAYMENT_REPORTED : "I have paid"
    [*] --> FAILED : "Payment failed"

    PAYMENT_INITIATED --> PAYMENT_INITIATED : switched app
    PAYMENT_INITIATED --> PAYMENT_PROCESSING : parent
    PAYMENT_INITIATED --> PAYMENT_REPORTED : "I have paid"
    PAYMENT_INITIATED --> FAILED : parent
    PAYMENT_INITIATED --> EXPIRED : link expired (system)

    PAYMENT_PROCESSING --> PAYMENT_REPORTED : "I have paid"
    PAYMENT_PROCESSING --> FAILED : parent

    PAYMENT_REPORTED --> PENDING_VERIFICATION : admin opens it / parent adds info
    PENDING_VERIFICATION --> PENDING_VERIFICATION : clarification requested / answered

    PAYMENT_PROCESSING --> VERIFIED : admin confirms (bank checked)
    PAYMENT_REPORTED --> VERIFIED : admin confirms
    PENDING_VERIFICATION --> VERIFIED : admin confirms

    PAYMENT_PROCESSING --> REJECTED : admin - not received
    PAYMENT_REPORTED --> REJECTED : admin - not received
    PENDING_VERIFICATION --> REJECTED : admin - not received
    REJECTED --> PENDING_VERIFICATION : admin reopen (controlled, audited)

    VERIFIED --> [*]
    FAILED --> [*]
    EXPIRED --> [*]
```

* **Active** = `PAYMENT_INITIATED | PAYMENT_PROCESSING | PAYMENT_REPORTED | PENDING_VERIFICATION`.
  A partial unique index allows **one active declaration per installment**.
* Once `PAYMENT_REPORTED`, the parent cannot change the declaration. Another "I have paid"
  tap returns the existing one (`duplicate: true`), and other actions get `409`.
* After `REJECTED` / `FAILED` / `EXPIRED`, a new declaration can be created.
* Only the authenticated admin API can move a declaration to `VERIFIED`. The change is audited.
* Transition tables live in [`app/states.py`](../app/states.py) and are exhaustively tested
  in `tests/test_state_engine.py`.

## 2. Installment balance (`installments.status`)

```mermaid
stateDiagram-v2
    [*] --> UNPAID
    UNPAID --> PARTIALLY_PAID : verified payment < outstanding
    UNPAID --> PAID : verified payment >= outstanding
    PARTIALLY_PAID --> PARTIALLY_PAID : verified payment < outstanding
    PARTIALLY_PAID --> PAID : verified payment >= outstanding
    PAID --> PAID : extra verified credit -> excess flagged (refund / adjust)
```

`paid_paise` never exceeds `total_paise`. Any surplus goes to `excess_paise` and a
`payment_adjustments` row with status `FLAGGED`.

## 3. Display status (parent page and APIs)

Computed, never stored (`app/states.py::display_status`):

1. Installment `PAID` → **PAID**
2. Active declaration exists → its status (`PAYMENT_REPORTED`, `PENDING_VERIFICATION`, ...)
3. Link expired → **EXPIRED**
4. Latest declaration `REJECTED` / `FAILED` → that status
5. Installment `PARTIALLY_PAID` → **PARTIALLY_PAID**
6. Otherwise **UNPAID**
