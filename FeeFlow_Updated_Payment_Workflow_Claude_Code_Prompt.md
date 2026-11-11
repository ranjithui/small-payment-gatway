# FEEFLOW – UPDATED PAYMENT EXPERIENCE
## Claude Code Development Specification – Version 2.1

**Product:** FeeFlow  
**Scope:** Replace mandatory parent UTR submission with one-tap payment declaration and administrator verification.  
**Platform:** Existing FeeFlow web application, Kotlin Android application, FastAPI backend and Supabase PostgreSQL database.

---

# 1. Objective

Update the existing FeeFlow application to implement a parent-friendly, one-tap UPI payment confirmation workflow.

Remove mandatory UTR/reference number entry from the parent payment experience.

Parents must not be forced to enter a UTR number or upload a screenshot after completing a UPI payment.

Instead, introduce a simple **“I have paid”** confirmation button.

The payment must still be verified by the tuition centre administrator before the installment is marked as PAID.

Do not compromise financial accuracy or payment security.

This specification updates the payment workflow; it does not replace the existing FeeFlow multi-tenant architecture, web application, Android application, direct UPI payment initiation, or Android SIM-based SMS engine.

# 2. Updated parent payment experience

The parent payment process must be simple.

1. Parent opens the unique payment URL received through SMS.
2. Display:
   - Tuition centre name and logo
   - Student name
   - Student ID
   - Fee description
   - Billing month
   - Total fee
   - Amount already paid
   - Outstanding amount
   - Due date
   - Payment status
3. Display UPI payment options:
   - Google Pay
   - PhonePe
   - Paytm
   - Other compatible UPI applications
   - UPI QR code
4. Parent selects their preferred UPI application.
5. Open the UPI payment application using a correctly constructed UPI Intent URI.
6. When the parent returns to the FeeFlow payment page, display:

   **“Have you completed your payment?”**

   Actions:
   - **I have paid**
   - Payment is processing
   - Payment failed

Do not automatically interpret returning from a UPI app as proof of payment.

# 3. One-tap “I have paid” workflow

When the parent taps **“I have paid”**:

1. Validate the secure payment link.
2. Confirm the installment is still outstanding.
3. Prevent duplicate active declarations.
4. Create a payment declaration record.
5. Record the declaration timestamp.
6. Record the selected UPI application if known.
7. Record the declared payment amount.
8. Set the declaration status to `PAYMENT_REPORTED`.
9. Notify the tuition centre administrator.
10. Display a confirmation message to the parent.

Parent confirmation message:

> Thank you! Your payment confirmation has been submitted.
>
> The tuition centre is checking the transaction. You will receive confirmation once the payment has been verified.
>
> Please do not make another payment while this confirmation is being checked.

Do not mark the installment PAID at this stage.

Do not generate a final paid receipt at this stage.

# 4. Optional parent inputs

Make the process one-tap by default.

For a full payment:
- Do not require UTR.
- Do not require a screenshot.
- Do not require manual amount entry if the payment page already specifies the exact amount.

For partial payments:
- Allow the parent to select **“Partial payment”**.
- Ask for the amount actually paid.
- Validate the amount against the outstanding balance.

Optional additional information:
- UPI application used
- Approximate payment time
- Payment note

These fields must not complicate the standard full-payment workflow.

Never ask for sensitive UPI PINs, bank credentials or passwords.

# 5. Payment status engine

Keep payment attempt/declaration status separate from the installment's overall financial status.

Implement the following statuses:

| Status | Meaning |
|---|---|
| `UNPAID` | No payment declaration has been submitted. |
| `PAYMENT_INITIATED` | Parent selected a UPI payment option. This is not proof of payment. |
| `PAYMENT_PROCESSING` | Parent reports that the payment is processing. |
| `PAYMENT_REPORTED` | Parent tapped “I have paid”. |
| `PENDING_VERIFICATION` | Declaration is awaiting administrator review. |
| `VERIFIED` | Administrator confirmed the payment against actual bank records. |
| `PARTIALLY_PAID` | Only part of the installment has been verified. |
| `REJECTED` | Administrator could not confirm the payment. |
| `FAILED` | Parent reported that the payment failed. |
| `EXPIRED` | Payment link expired. |
| `PAID` | The entire installment has been verified and settled. |

Do not use one status field to represent both a payment declaration and an installment balance. Model these separately.

# 6. Admin payment verification dashboard

Create a dedicated section named **Payment Verification**.

Display:
- Student name
- Student ID
- Parent mobile number
- Installment
- Expected amount
- Declared amount
- Payment declaration time
- UPI application used
- Previous payment history
- Outstanding amount
- Verification status

Admin actions:
1. Confirm payment received
2. Payment not received
3. Request clarification

When the admin selects **Confirm payment received**:

1. Require administrator confirmation.
2. Require checking the actual bank transaction.
3. Verify the amount.
4. Verify that the payment belongs to the centre.
5. Check that the transaction has not already been recorded.
6. Create a verified payment transaction.
7. Update the installment balance.
8. Mark the installment PAID only when the full outstanding balance has been settled.
9. Generate the receipt.
10. Notify the parent.
11. Cancel future reminders for the settled amount.
12. Record administrator ID and verification timestamp.
13. Record the reconciliation evidence/reference available to the administrator.

If the admin selects **Payment not received**:
- Ask for a rejection reason.
- Update the declaration status to `REJECTED`.
- Keep the installment outstanding.
- Notify the parent.
- Allow a new declaration when appropriate.

If the admin selects **Request clarification**:
- Record the clarification request.
- Notify the parent.
- Keep the installment unpaid.
- Allow the parent to provide additional information.

A declaration must never become a verified payment merely through a frontend action without backend authorization and audit logging.

# 7. Duplicate payment protection

Implement protection against duplicate payment declarations and duplicate financial records.

Rules:
- Multiple taps must not create multiple active declarations.
- Use idempotency keys.
- A parent must see the current payment status after refreshing the page.
- If a declaration is already pending, display the existing status.
- Do not show an unrestricted second payment action while a declaration is pending.
- Allow administrators to reopen a rejected declaration through a controlled workflow.
- A verified bank transaction must never be credited twice.

If a parent accidentally pays twice:
- Allow the administrator to record both actual transactions separately.
- Flag the excess amount for refund or account adjustment.
- Do not silently discard a genuine second payment.

# 8. Parent payment page UI

Design a mobile-first payment page.

Header:
- FeeFlow branding
- Tuition centre logo
- Tuition centre name

Fee details:
- Student name
- Fee month
- Total fee
- Amount paid
- Outstanding balance
- Due date

Payment section:
- GPay
- PhonePe
- Paytm
- Other UPI
- QR code

Post-payment section:

**Question:** “Have you completed your payment?”

Main button: **I have paid**

Secondary options:
- Payment is processing
- Payment failed

After submission, replace payment controls with:

**Payment confirmation received**

Status: **Waiting for tuition centre verification**

Show:
- Declaration date and time
- Tuition centre contact information
- Payment status
- A warning not to pay again while verification is pending

If already verified:
- Display “Payment successfully verified!”
- Show the receipt link.

If rejected:
- Display “Payment could not be verified. Please contact your tuition centre or submit a new confirmation after checking your bank transaction.”

If the installment is fully paid:
- Hide payment buttons.
- Display PAID.
- Show the receipt.

# 9. Payment declaration data model

Create a dedicated `payment_declarations` table.

Suggested fields:

| Field | Purpose |
|---|---|
| `id` | Internal primary key |
| `tenant_id` | Tuition centre |
| `installment_id` | Related fee installment |
| `payment_link_id` | Related secure payment link |
| `parent_identifier` | Optional parent reference |
| `declared_amount` | Amount parent says was paid |
| `payment_mode` | UPI or supported payment mode |
| `upi_app` | App selected, if known |
| `declaration_status` | Current declaration state |
| `declared_at` | Declaration timestamp |
| `approximate_payment_time` | Optional parent-provided time |
| `clarification_note` | Optional parent note |
| `admin_note` | Internal administrator note |
| `verified_payment_id` | Linked verified transaction, if any |
| `reviewed_by` | Administrator who reviewed it |
| `reviewed_at` | Review timestamp |
| `rejection_reason` | Reason for rejection |
| `idempotency_key` | Duplicate submission protection |
| `created_at` | Creation timestamp |
| `updated_at` | Last update timestamp |

Create appropriate indexes, foreign keys, tenant constraints and uniqueness rules.

Do not store sensitive bank credentials or UPI PINs.

Keep payment declarations separate from verified payment transactions. A declaration is not a financial transaction.

# 10. Backend API endpoints

Implement:

```text
POST /api/v1/payment-declarations

GET /api/v1/payment-declarations/{declaration_id}

GET /api/v1/payment-declarations/public/{secure_token}

POST /api/v1/payment-declarations/{declaration_id}/clarification

GET /api/v1/payment-verification/pending

POST /api/v1/payment-verification/{declaration_id}/confirm

POST /api/v1/payment-verification/{declaration_id}/reject

POST /api/v1/payment-verification/{declaration_id}/request-clarification

GET /api/v1/installments/{installment_id}/payment-status
```

All endpoints must implement:
- Authentication where required
- Public-link token validation where appropriate
- Tenant isolation
- Authorization
- Request validation
- Rate limiting
- Idempotency
- Audit logs

Do not expose internal IDs or sensitive information through public APIs.

# 11. SMS integration

Integrate this workflow with the existing Android SIM-based SMS engine.

When a parent clicks **I have paid**, send an acknowledgement SMS if enabled:

> Dear Parent, we have received your payment confirmation for {student_name}. Our team is verifying the transaction. Please wait for confirmation. - {centre_name}

When payment is verified:

> Dear Parent, your payment of Rs. {amount} for {student_name} has been verified successfully. Your receipt: {receipt_link}. - {centre_name}

When payment is rejected:

> Dear Parent, we could not verify your payment for {student_name}. Please contact {centre_name} for assistance.

Important:
- Use the registered Android device and physical SIM.
- Do not introduce an SMS gateway.
- Use the existing device synchronisation architecture.
- Avoid duplicate acknowledgement messages.
- Do not send payment-success SMS before actual verification.

# 12. Reminder automation

When a declaration is submitted:
- Pause normal fee reminders for a configurable verification grace period.
- Default grace period: 24 hours.
- Display the installment as `PAYMENT_REPORTED` or `PENDING_VERIFICATION`.
- Do not mark the fee as paid.

When payment is verified:
- Cancel future reminders for the settled amount.
- Synchronise cancellation to Android devices.

When payment is rejected:
- Restore normal reminder eligibility.
- Avoid sending an immediate duplicate reminder.
- Respect the centre's configured reminder frequency.

When partial payment is verified:
- Update the outstanding balance.
- Schedule reminders only for the remaining balance.

If the grace period expires without verification:
- Notify the administrator.
- Restore reminder eligibility according to the centre's settings.
- Do not assume the payment failed.

If the Android device is offline, show cancellation as pending synchronisation. The device cannot receive a newly issued cancellation until it reconnects. Before sending, it must check the latest locally known schedule state.

# 13. Automatic reconciliation – future-ready design

Keep the system ready for authorized bank reconciliation.

Potential future integrations:
- Authorized bank transaction feeds
- Bank statement import
- Merchant UPI integrations
- Authorized payment-provider APIs

A reconciliation service may match incoming transactions against:
- Centre UPI account
- Amount
- Transaction reference
- Payment date and time
- Installment reference where available

When a transaction is reliably matched:
- Create or update the verified payment through a secure backend process.
- Prevent duplicate matching.
- Record the reconciliation source.
- Maintain audit history.

Do not implement unauthorized bank scraping, Gmail scraping or SMS interception as the default solution.

UPI Intent and parent confirmation are not authoritative settlement signals.

# 14. Audit and reporting

Maintain separate reports for:
1. Payment declarations
2. Pending verification
3. Verified payments
4. Rejected declarations
5. Partial payments
6. Duplicate payment reports
7. Excess payments
8. Outstanding fees

Audit log fields:
- Actor
- Action
- Tenant
- Related declaration
- Related installment
- Previous status
- New status
- Timestamp
- Reason
- Source

Financial records must not be silently overwritten.

# 15. Acceptance tests

## Scenario A – Normal payment
1. Parent opens link.
2. Parent selects UPI app.
3. Parent completes payment.
4. Parent returns to FeeFlow.
5. Parent taps “I have paid”.
6. System records declaration.
7. Admin checks bank transaction.
8. Admin confirms.
9. Installment becomes PAID.
10. Receipt is generated.
11. Reminders stop.

## Scenario B – Parent did not actually pay
1. Parent taps “I have paid”.
2. Admin checks bank.
3. No transaction exists.
4. Admin rejects.
5. Installment remains outstanding.
6. Parent receives notification.

## Scenario C – Payment processing
1. Parent taps “Payment is processing”.
2. System records processing status.
3. Parent is instructed not to repeat payment immediately.
4. Admin can later review.

## Scenario D – Duplicate click
1. Parent taps multiple times.
2. Only one active declaration exists.
3. No duplicate payment record is created.

## Scenario E – Partial payment
1. Parent declares partial payment.
2. Admin verifies actual amount.
3. Remaining balance is calculated.
4. Installment becomes PARTIALLY_PAID.
5. Reminders continue for the outstanding amount.

## Scenario F – Already paid
1. Parent opens old payment link.
2. System detects PAID status.
3. Payment options are hidden.
4. Receipt is displayed.

## Scenario G – Duplicate actual transfer
1. Parent makes two genuine transfers.
2. Admin records both separately.
3. Excess payment is flagged for adjustment or refund.

# 16. Implementation instructions

Integrate this workflow into the existing FeeFlow architecture. Do not create a disconnected payment system.

Replace the old mandatory UTR submission flow.

Keep UTR as an optional internal/admin reconciliation field or optional additional information only where necessary. It must not be compulsory for parents.

Preserve the existing:
- Multi-tenant architecture
- Web application
- Kotlin Android application
- FastAPI backend
- Supabase PostgreSQL
- Direct UPI payment flow
- Android SIM-based SMS engine
- Fee management
- Receipt generation
- Subscription management

Before coding:
1. Inspect existing payment models.
2. Inspect existing payment APIs.
3. Inspect the parent payment page.
4. Inspect reminder cancellation logic.
5. Prepare database migration.
6. Prepare updated payment state diagram.
7. Prepare updated UI flow.

Implement changes without breaking existing working functionality.

Write automated tests for all payment state transitions.

At completion, report modified files, migrations, tests, test results and outstanding issues.

**Final requirement:** Provide a frictionless parent experience while preserving accurate financial records and administrator control.
