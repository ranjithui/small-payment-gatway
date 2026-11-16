package com.feeflow.app.core

import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

@OptIn(ExperimentalSerializationApi::class)
/** Shared JSON configuration for the FeeFlow API (snake_case fields, tolerant of additions). */
val FeeFlowJson = Json {
    ignoreUnknownKeys = true
    explicitNulls = false
    encodeDefaults = true
    coerceInputValues = true
}

// ------------------------------------------------------------------ device sync (SIM SMS engine)

@Serializable
data class SyncResponse(
    @SerialName("server_time") val serverTime: String? = null,
    val sms: List<OutboundSms> = emptyList(),
    @SerialName("reminder_schedules") val reminderSchedules: List<RemoteSchedule> = emptyList(),
)

@Serializable
data class OutboundSms(
    val id: String,
    @SerialName("to_mobile") val toMobile: String,
    val body: String,
    val kind: String = "",
)

@Serializable
data class RemoteSchedule(
    val id: String,
    val version: Int,
    val status: String,
    @SerialName("scheduled_for") val scheduledFor: String,
    @SerialName("hold_until") val holdUntil: String? = null,
    @SerialName("to_mobile") val toMobile: String,
    val body: String,
    @SerialName("amount_paise") val amountPaise: Long = 0,
    @SerialName("cancel_reason") val cancelReason: String? = null,
)

@Serializable
data class SmsAck(val id: String, val status: String, val error: String? = null)

@Serializable
data class ReminderAck(val id: String, val version: Int, @SerialName("sent_at") val sentAt: String? = null)

@Serializable
data class DeviceAckRequest(val sms: List<SmsAck>, val reminders: List<ReminderAck>)

// ------------------------------------------------------------------ admin API

@Serializable
data class LoginRequest(val email: String, val password: String)

@Serializable
data class LoginResponse(@SerialName("access_token") val accessToken: String, val name: String = "")

@Serializable
data class Centre(val name: String, @SerialName("logo_url") val logoUrl: String? = null)

@Serializable
data class Workspace(
    val name: String,
    @SerialName("short_code") val shortCode: String = "",
    @SerialName("upi_vpa") val upiVpa: String,
    @SerialName("upi_payee_name") val upiPayeeName: String,
    @SerialName("contact_phone") val contactPhone: String? = null,
    @SerialName("contact_email") val contactEmail: String? = null,
    @SerialName("auto_send_links") val autoSendLinks: Boolean = false,
    val subscription: Subscription = Subscription(),
    /** PERSONAL (default, no business documents) or MERCHANT (optional). */
    @SerialName("account_type") val accountType: String = "PERSONAL",
    @SerialName("upi_bank_name") val upiBankName: String? = null,
    @SerialName("business_name") val businessName: String? = null,
    @SerialName("merchant_code") val merchantCode: String? = null,
    @SerialName("upi_mobile") val upiMobile: String? = null,
    /** Switches as saved. */
    @SerialName("payment_methods_settings") val methodSettings: PaymentMethods = PaymentMethods(),
    /** What parents actually see (one-tap is never offered for personal UPI IDs). */
    @SerialName("payment_methods") val methods: PaymentMethods = PaymentMethods(),
    @SerialName("upi_config_status") val configStatus: UpiConfigStatus = UpiConfigStatus(),
)

@Serializable
data class PaymentMethods(
    @SerialName("upi_app") val upiApp: Boolean = true,
    val qr: Boolean = true,
    val copy: Boolean = true,
)

/** Format/completeness check only - FeeFlow cannot confirm the account can receive money. */
@Serializable
data class UpiConfigStatus(val status: String = "READY", val message: String = "")

/** Fields sent are changed; null fields are left as they are. Blank text removes optional values. */
@Serializable
data class WorkspaceUpdateRequest(
    val name: String? = null,
    @SerialName("account_type") val accountType: String? = null,
    @SerialName("upi_vpa") val upiVpa: String? = null,
    @SerialName("upi_payee_name") val upiPayeeName: String? = null,
    @SerialName("upi_bank_name") val upiBankName: String? = null,
    @SerialName("business_name") val businessName: String? = null,
    @SerialName("merchant_code") val merchantCode: String? = null,
    @SerialName("upi_mobile") val upiMobile: String? = null,
    @SerialName("pay_intent_enabled") val payIntentEnabled: Boolean? = null,
    @SerialName("pay_qr_enabled") val payQrEnabled: Boolean? = null,
    @SerialName("pay_manual_enabled") val payManualEnabled: Boolean? = null,
    @SerialName("contact_phone") val contactPhone: String? = null,
    @SerialName("contact_email") val contactEmail: String? = null,
)

@Serializable
data class Me(
    @SerialName("admin_id") val adminId: String,
    val name: String,
    val email: String? = null,
    @SerialName("has_workspace") val hasWorkspace: Boolean = true,
    @SerialName("is_platform_admin") val isPlatformAdmin: Boolean = false,
    val centre: Centre? = null,
    val workspace: Workspace? = null,
)

@Serializable
data class WorkspaceCreateRequest(
    val name: String,
    @SerialName("upi_vpa") val upiVpa: String,
    @SerialName("upi_payee_name") val upiPayeeName: String,
    @SerialName("contact_phone") val contactPhone: String? = null,
    @SerialName("account_type") val accountType: String = "PERSONAL",
    @SerialName("upi_bank_name") val upiBankName: String? = null,
    @SerialName("business_name") val businessName: String? = null,
    @SerialName("upi_mobile") val upiMobile: String? = null,
)

/** Same rule as the server: name@bank handle. */
val UPI_VPA_REGEX = Regex("^[A-Za-z0-9.\\-_]{2,256}@[A-Za-z][A-Za-z0-9]{1,63}$")

@Serializable
data class InstallmentInfo(
    val id: String,
    val description: String,
    @SerialName("billing_month") val billingMonth: String,
    @SerialName("due_date") val dueDate: String,
    @SerialName("total_paise") val totalPaise: Long,
    @SerialName("paid_paise") val paidPaise: Long,
    @SerialName("excess_paise") val excessPaise: Long = 0,
    val status: String,
)

@Serializable
data class VerifiedPaymentInfo(
    val id: String = "",
    @SerialName("amount_paise") val amountPaise: Long,
    @SerialName("bank_reference") val bankReference: String? = null,
    @SerialName("verified_at") val verifiedAt: String? = null,
)

@Serializable
data class PreviousDeclaration(
    val reference: String,
    val status: String,
    @SerialName("declared_amount_paise") val declaredAmountPaise: Long,
    @SerialName("declared_at") val declaredAt: String? = null,
)

@Serializable
data class HistoryItem(
    val installment: String,
    @SerialName("billing_month") val billingMonth: String,
    @SerialName("amount_paise") val amountPaise: Long,
    @SerialName("verified_at") val verifiedAt: String? = null,
)

@Serializable
data class BankMatch(
    @SerialName("bank_transaction_id") val bankTransactionId: String,
    @SerialName("bank_reference") val bankReference: String,
    @SerialName("amount_paise") val amountPaise: Long,
    @SerialName("credited_at") val creditedAt: String,
    @SerialName("payer_vpa") val payerVpa: String? = null,
    val narration: String? = null,
)

@Serializable
data class Declaration(
    val id: String,
    val reference: String,
    @SerialName("student_name") val studentName: String,
    @SerialName("student_id") val studentId: String,
    @SerialName("parent_mobile") val parentMobile: String,
    val installment: InstallmentInfo,
    @SerialName("expected_amount_paise") val expectedAmountPaise: Long,
    @SerialName("declared_amount_paise") val declaredAmountPaise: Long,
    @SerialName("payment_type") val paymentType: String = "FULL",
    @SerialName("declared_at") val declaredAt: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("upi_app") val upiApp: String? = null,
    @SerialName("approximate_payment_time") val approximatePaymentTime: String? = null,
    @SerialName("parent_notes") val parentNotes: String? = null,
    @SerialName("parent_upi_reference") val parentUpiReference: String? = null,
    @SerialName("outstanding_paise") val outstandingPaise: Long,
    @SerialName("verification_status") val verificationStatus: String,
    @SerialName("clarification_request") val clarificationRequest: String? = null,
    @SerialName("verification_overdue") val verificationOverdue: Boolean = false,
    /** Fee's payment reference (UPI note), e.g. "BM-1001-2026-11": match it in the bank statement. */
    @SerialName("payment_reference") val paymentReference: String? = null,
    /** Internal remarks (never shown to parents). */
    @SerialName("admin_note") val adminNote: String? = null,
    // detail-only fields
    @SerialName("payment_attempts") val paymentAttempts: List<PaymentAttemptInfo> = emptyList(),
    @SerialName("verified_payments") val verifiedPayments: List<VerifiedPaymentInfo> = emptyList(),
    @SerialName("previous_declarations") val previousDeclarations: List<PreviousDeclaration> = emptyList(),
    @SerialName("student_payment_history") val studentPaymentHistory: List<HistoryItem> = emptyList(),
    @SerialName("suggested_bank_matches") val suggestedBankMatches: List<BankMatch> = emptyList(),
)

/** How the parent tried to start the payment (diagnostics only - not proof of payment). */
@Serializable
data class PaymentAttemptInfo(
    val method: String, // UPI_INTENT / QR / MANUAL
    val event: String,
    val device: String = "OTHER",
    val declared: Boolean = false,
    val detail: String? = null,
    val at: String? = null,
)

@Serializable
data class AdminRemarkRequest(val remark: String)

@Serializable
data class PendingResponse(val items: List<Declaration>)

@Serializable
data class TransactionIn(
    @SerialName("amount_paise") val amountPaise: Long,
    @SerialName("bank_reference") val bankReference: String? = null,
    @SerialName("bank_transaction_id") val bankTransactionId: String? = null,
)

@Serializable
data class ConfirmRequest(
    val confirm: Boolean,
    @SerialName("bank_transaction_checked") val bankTransactionChecked: Boolean,
    @SerialName("belongs_to_centre") val belongsToCentre: Boolean,
    val transactions: List<TransactionIn>,
    @SerialName("amount_difference_acknowledged") val amountDifferenceAcknowledged: Boolean = false,
    @SerialName("evidence_note") val evidenceNote: String? = null,
    @SerialName("admin_note") val adminNote: String? = null,
)

@Serializable
data class ReceiptRef(@SerialName("receipt_number") val receiptNumber: String, val url: String)

@Serializable
data class ConfirmResponse(
    @SerialName("installment_status") val installmentStatus: String,
    @SerialName("outstanding_paise") val outstandingPaise: Long,
    val receipt: ReceiptRef,
    @SerialName("excess_flagged_paise") val excessFlaggedPaise: Long = 0,
    @SerialName("student_name") val studentName: String? = null,
    @SerialName("amount_paise") val amountPaise: Long? = null,
    /** The thank-you message sent to the parent (SMS from the centre's phone; WhatsApp link with the same text). */
    @SerialName("thank_you") val thankYou: ThankYou? = null,
)

@Serializable
data class ThankYou(
    val id: String,
    val status: String,
    val error: String? = null,
    @SerialName("to_mobile") val toMobile: String = "",
    val message: String = "",
    @SerialName("whatsapp_url") val whatsappUrl: String = "",
)

/** Delivery state of one SMS: QUEUED (waiting for the SMS phone), DISPATCHED, SENT or FAILED. */
@Serializable
data class SmsStatus(val id: String, val status: String, val error: String? = null)

@Serializable
data class RejectRequest(val reason: String)

@Serializable
data class ClarificationRequest(val message: String)

@Serializable
data class DeviceRegisterRequest(val name: String)

@Serializable
data class DeviceInfo(
    val id: String,
    val name: String,
    val active: Boolean,
    @SerialName("last_seen_at") val lastSeenAt: String? = null,
)

@Serializable
data class DeviceRegisterResponse(
    val device: DeviceInfo,
    @SerialName("device_token") val deviceToken: String,
    @SerialName("deactivated_devices") val deactivatedDevices: Int = 0,
)

@Serializable
data class DevicesResponse(
    val items: List<DeviceInfo>,
    @SerialName("sms_waiting") val smsWaiting: Int = 0,
    @SerialName("reminder_changes_pending_sync") val reminderChangesPendingSync: Int = 0,
)
