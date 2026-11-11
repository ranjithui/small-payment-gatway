# UI flows

## Parent payment page – `/pay/{secure_token}` (mobile-first)

```mermaid
flowchart TD
    SMS[Parent opens link from SMS] --> LOAD[GET /payment-declarations/public/token]
    LOAD -->|installment PAID| PAID[Show PAID + receipt<br/>payment buttons hidden]
    LOAD -->|link expired| EXP[Link expired - contact centre]
    LOAD -->|declaration awaiting verification| WAIT
    LOAD -->|can pay| PAY[Fee details + GPay / PhonePe / Paytm / Other UPI / QR]
    PAY -->|tap app| INIT[POST action=INITIATED<br/>open UPI intent URI]
    INIT -->|parent returns - visibilitychange| ASK{Have you completed your payment?}
    ASK -->|I have paid - one tap| REP[POST action=PAID]
    ASK -->|Partial payment ticked| PART[enter amount -> POST action=PAID, PARTIAL]
    ASK -->|Payment is processing| PROC[POST action=PROCESSING<br/>'do not repeat payment']
    ASK -->|Payment failed| FAIL[POST action=FAILED<br/>retry allowed]
    PROC --> ASK
    REP --> WAIT[Payment confirmation received<br/>Waiting for tuition centre verification<br/>date/time, contact, do-not-pay-again warning]
    PART --> WAIT
    WAIT -->|admin asks for clarification| CLAR[Show question + reply form<br/>optional time / optional UPI ref]
    CLAR --> WAIT
    WAIT -->|admin verifies full| PAID
    WAIT -->|admin verifies partial| PARTIAL[Payment successfully verified!<br/>remaining balance + receipt + pay options]
    WAIT -->|admin rejects| REJ[Payment could not be verified...<br/>pay options shown again]
```

Notes:
* Returning from the UPI app is **not** treated as proof of payment. It only shows the question.
* The idempotency key is kept per attempt in `sessionStorage`, so repeated taps or network retries
  reuse it. Refreshing re-reads server state.
* No UTR, screenshot or amount is required for a full payment. The UPI reference is offered only
  as an optional field when the centre asks for clarification.
* Notes containing "UPI PIN", "OTP", "password" and similar are rejected by the API.

## Admin – `/admin` → **Payment Verification**

```mermaid
flowchart LR
    Q[Queue: PROCESSING / REPORTED / PENDING_VERIFICATION] --> D[Detail: student, ID, parent mobile,<br/>installment, expected vs declared amount,<br/>declared time, UPI app, history, outstanding,<br/>bank-statement match suggestions]
    D --> C[Confirm payment received]
    D --> R[Payment not received]
    D --> K[Request clarification]
    C --> C1[tick: checked bank txn + credited to centre account<br/>enter amount(s) + UTR or evidence note<br/>acknowledge amount difference if any<br/>browser confirm dialog]
    C1 --> C2[verified_payments + balance + receipt + SMS + reminders cancelled/rebased + audit]
    R --> R1[reason required -> REJECTED, SMS, reminders restored without immediate resend]
    K --> K1[question -> SMS with fresh link -> parent replies]
```

Other tabs: **Fees** (status, reminder sync state, new link, record an extra bank credit),
**Reports** (8 reports, with reopen for rejected items and resolve for excess payments),
**Notifications**, **Audit log**.
