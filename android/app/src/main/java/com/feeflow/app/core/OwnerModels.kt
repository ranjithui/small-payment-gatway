package com.feeflow.app.core

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

// ------------------------------------------------------------------ v2.7 owner tools (server: /api/v1/dashboard etc.)

@Serializable
data class Dashboard(
    val month: String,
    @SerialName("month_label") val monthLabel: String,
    val stats: DashStats,
    @SerialName("sms_phone") val smsPhone: SmsPhone = SmsPhone(),
    @SerialName("pending_actions") val pendingActions: List<PendingAction> = emptyList(),
    @SerialName("recent_payments") val recentPayments: List<RecentPayment> = emptyList(),
    val classes: List<String> = emptyList(),
)

@Serializable
data class DashStats(
    @SerialName("students_total") val studentsTotal: Int = 0,
    @SerialName("fees_generated_paise") val feesGeneratedPaise: Long = 0,
    @SerialName("fees_count") val feesCount: Int = 0,
    @SerialName("collected_paise") val collectedPaise: Long = 0,
    @SerialName("outstanding_paise") val outstandingPaise: Long = 0,
    @SerialName("paid_count") val paidCount: Int = 0,
    @SerialName("awaiting_verification") val awaitingVerification: Int = 0,
    @SerialName("overdue_students") val overdueStudents: Int = 0,
    @SerialName("overdue_paise") val overduePaise: Long = 0,
    @SerialName("collected_today_paise") val collectedTodayPaise: Long = 0,
    @SerialName("sms_failed") val smsFailed: Int = 0,
    @SerialName("sms_waiting") val smsWaiting: Int = 0,
)

/** One line of "Pending actions": [kind] decides what the button does. */
@Serializable
data class PendingAction(val kind: String, val count: Int, val text: String, val button: String, val tone: String = "warn")

@Serializable
data class RecentPayment(
    @SerialName("student_name") val studentName: String,
    val description: String,
    @SerialName("billing_month") val billingMonth: String,
    @SerialName("amount_paise") val amountPaise: Long,
    @SerialName("verified_at") val verifiedAt: String? = null,
)

@Serializable
data class SmsPhone(
    val connected: Boolean = false,
    val name: String? = null,
    val online: Boolean = false,
    @SerialName("last_seen_at") val lastSeenAt: String? = null,
)

@Serializable
data class FailedSms(
    val id: String,
    val kind: String = "",
    val status: String = "",
    val error: String? = null,
    @SerialName("to_mobile") val toMobile: String = "",
    @SerialName("student_name") val studentName: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
)

@Serializable
data class SmsHealth(
    val phone: SmsPhone = SmsPhone(),
    @SerialName("sent_today") val sentToday: Int = 0,
    val waiting: Int = 0,
    @SerialName("failed_count") val failedCount: Int = 0,
    val failed: List<FailedSms> = emptyList(),
    val advice: String = "",
    @SerialName("limit_note") val limitNote: String = "",
)

@Serializable
data class RetryFailedRequest(@SerialName("batch_id") val batchId: String? = null)

@Serializable
data class RetryResult(val resent: Int)

@Serializable
data class GenerateResult(val created: Int, @SerialName("billing_month") val billingMonth: String)

// ---------------- bulk reminders
@Serializable
data class BulkReminderRequest(
    val target: String,
    @SerialName("class_grade") val classGrade: String? = null,
    val month: String? = null,
    @SerialName("exclude_ids") val excludeIds: List<String>? = null,
)

@Serializable
data class Recipient(
    @SerialName("student_id") val studentId: String,
    @SerialName("student_name") val studentName: String,
    @SerialName("class_grade") val classGrade: String? = null,
    @SerialName("parent_mobile") val parentMobile: String = "",
    val description: String = "",
    @SerialName("billing_month") val billingMonth: String = "",
    @SerialName("amount_paise") val amountPaise: Long = 0,
    val overdue: Boolean = false,
)

@Serializable
data class ReminderPreview(
    val count: Int,
    val recipients: List<Recipient> = emptyList(),
    @SerialName("sample_message") val sampleMessage: String? = null,
    @SerialName("sms_parts") val smsParts: Int = 0,
    @SerialName("sms_phone_active") val smsPhoneActive: Boolean = false,
)

@Serializable
data class BulkSendResponse(@SerialName("batch_id") val batchId: String, val queued: Int)

@Serializable
data class BatchStatus(
    @SerialName("batch_id") val batchId: String,
    val total: Int = 0,
    val queued: Int = 0,
    val dispatched: Int = 0,
    val sent: Int = 0,
    val failed: Int = 0,
    val done: Boolean = false,
    @SerialName("progress_percent") val progressPercent: Int = 0,
    @SerialName("failed_items") val failedItems: List<FailedSms> = emptyList(),
)

// ---------------- bulk one-off fees
@Serializable
data class BulkFeeRequest(
    val description: String,
    @SerialName("amount_paise") val amountPaise: Long,
    @SerialName("billing_month") val billingMonth: String,
    @SerialName("due_date") val dueDate: String,
    val target: String = "ALL",
    @SerialName("class_grade") val classGrade: String? = null,
    @SerialName("exclude_ids") val excludeIds: List<String>? = null,
)

@Serializable
data class FeeRow(
    @SerialName("student_id") val studentId: String,
    @SerialName("student_name") val studentName: String,
    @SerialName("class_grade") val classGrade: String? = null,
    @SerialName("amount_paise") val amountPaise: Long = 0,
    val duplicate: Boolean = false,
)

@Serializable
data class FeePreview(
    val count: Int,
    val duplicates: Int = 0,
    @SerialName("total_paise") val totalPaise: Long = 0,
    val rows: List<FeeRow> = emptyList(),
)

@Serializable
data class BulkFeeResult(val created: Int, @SerialName("skipped_duplicates") val skippedDuplicates: Int = 0)

// ---------------- verification extras
@Serializable
data class VerificationWarning(val level: String, val text: String)

@Serializable
data class SearchResponse(val items: List<Declaration> = emptyList())

@Serializable
data class RefCheck(val used: Boolean, val message: String? = null)

@Serializable
data class AdjustmentItem(
    val id: String,
    @SerialName("student_name") val studentName: String,
    val description: String = "",
    @SerialName("billing_month") val billingMonth: String = "",
    @SerialName("amount_paise") val amountPaise: Long = 0,
)

@Serializable
data class AdjustmentList(val items: List<AdjustmentItem> = emptyList())

@Serializable
data class ApplyExcessRequest(@SerialName("installment_id") val installmentId: String? = null)

@Serializable
data class AppliedTo(
    val description: String = "",
    @SerialName("billing_month") val billingMonth: String = "",
    @SerialName("outstanding_paise") val outstandingPaise: Long = 0,
)

@Serializable
data class ApplyExcessResult(val status: String, @SerialName("applied_to") val appliedTo: AppliedTo, val receipt: ReceiptRef)

// ---------------- reports (rows differ per report: kept as JSON)
@Serializable
data class ReportResponse(val items: List<JsonObject> = emptyList())

/** Payments tab search (all optional; empty = the waiting queue). */
data class PaymentSearch(
    val query: String = "",
    val status: String = "PENDING",
    val classGrade: String = "",
    val amount: String = "",
    val utr: String = "",
) {
    val active: Boolean get() = query.isNotBlank() || status != "PENDING" || classGrade.isNotBlank() || amount.isNotBlank() || utr.isNotBlank()
}
