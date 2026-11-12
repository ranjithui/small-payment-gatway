package com.feeflow.app.core

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// ------------------------------------------------------------------ sign-up + subscription

@Serializable
data class SignupRequest(val name: String, val email: String, val password: String)

@Serializable
data class Subscription(
    val status: String = "TRIAL", // TRIAL / ACTIVE / EXPIRED / SUSPENDED
    @SerialName("trial_ends_at") val trialEndsAt: String? = null,
    @SerialName("trial_days_left") val trialDaysLeft: Int? = null,
    @SerialName("read_only") val readOnly: Boolean = false,
)

// ------------------------------------------------------------------ students

@Serializable
data class LatestFee(
    @SerialName("billing_month") val billingMonth: String,
    val status: String,
    @SerialName("outstanding_paise") val outstandingPaise: Long,
    @SerialName("due_date") val dueDate: String,
)

@Serializable
data class FeeReceipt(
    @SerialName("receipt_number") val receiptNumber: String,
    @SerialName("amount_paise") val amountPaise: Long,
    val url: String,
)

@Serializable
data class StudentFee(
    val id: String,
    val description: String,
    @SerialName("billing_month") val billingMonth: String,
    val kind: String = "ONE_OFF",
    @SerialName("due_date") val dueDate: String,
    @SerialName("total_paise") val totalPaise: Long,
    @SerialName("paid_paise") val paidPaise: Long,
    @SerialName("outstanding_paise") val outstandingPaise: Long,
    val status: String,
    val receipts: List<FeeReceipt> = emptyList(),
)

@Serializable
data class Student(
    val id: String,
    @SerialName("student_id") val studentCode: String,
    val name: String,
    @SerialName("parent_name") val parentName: String? = null,
    @SerialName("parent_mobile") val parentMobile: String,
    @SerialName("parent_email") val parentEmail: String? = null,
    @SerialName("date_of_birth") val dateOfBirth: String? = null,
    val gender: String? = null,
    @SerialName("class_grade") val classGrade: String? = null,
    val school: String? = null,
    val address: String? = null,
    @SerialName("joined_on") val joinedOn: String? = null,
    @SerialName("monthly_fee_paise") val monthlyFeePaise: Long? = null,
    @SerialName("fee_due_day") val feeDueDay: Int? = null,
    val active: Boolean = true,
    @SerialName("latest_fee") val latestFee: LatestFee? = null,
    // detail only
    val fees: List<StudentFee> = emptyList(),
    @SerialName("outstanding_paise") val outstandingPaise: Long = 0,
    @SerialName("sms_phone_active") val smsPhoneActive: Boolean = false,
)

@Serializable
data class StudentList(val items: List<Student>)

/** Create (all fields) and update (fields sent are changed; blank text clears it). */
@Serializable
data class StudentRequest(
    val name: String? = null,
    @SerialName("student_code") val studentCode: String? = null,
    @SerialName("parent_name") val parentName: String? = null,
    @SerialName("parent_mobile") val parentMobile: String? = null,
    @SerialName("parent_email") val parentEmail: String? = null,
    @SerialName("date_of_birth") val dateOfBirth: String? = null,
    val gender: String? = null,
    @SerialName("class_grade") val classGrade: String? = null,
    val school: String? = null,
    val address: String? = null,
    @SerialName("joined_on") val joinedOn: String? = null,
    @SerialName("monthly_fee_paise") val monthlyFeePaise: Long? = null,
    @SerialName("fee_due_day") val feeDueDay: Int? = null,
    val active: Boolean? = null,
    @SerialName("clear_fee_plan") val clearFeePlan: Boolean? = null,
)

@Serializable
data class SendFeeLinkRequest(val channel: String)

@Serializable
data class LinkFee(
    val description: String,
    @SerialName("billing_month") val billingMonth: String,
    @SerialName("outstanding_paise") val outstandingPaise: Long,
    @SerialName("due_date") val dueDate: String,
)

@Serializable
data class SendFeeLinkResponse(
    val channel: String,
    @SerialName("payment_link") val paymentLink: String,
    @SerialName("share_text") val shareText: String,
    @SerialName("sms_queued") val smsQueued: Boolean,
    @SerialName("sms_phone_active") val smsPhoneActive: Boolean,
    val fee: LinkFee,
    @SerialName("parent_mobile") val parentMobile: String,
)

object StudentInput {
    private val DATE = Regex("^\\d{4}-\\d{2}-\\d{2}$")

    /** "" -> null; "2014-06-02" -> ok; anything else -> error. */
    fun date(text: String): Result<String?> {
        val t = text.trim()
        if (t.isEmpty()) return Result.success(null)
        if (!DATE.matches(t)) return Result.failure(IllegalArgumentException("Use the date format YYYY-MM-DD"))
        return try {
            java.time.LocalDate.parse(t)
            Result.success(t)
        } catch (_: Exception) {
            Result.failure(IllegalArgumentException("Not a real date: $t"))
        }
    }

    fun mobileOk(text: String): Boolean {
        var d = text.filter { it.isDigit() }
        if (d.length == 12 && d.startsWith("91")) d = d.substring(2)
        if (d.length == 11 && d.startsWith("0")) d = d.substring(1)
        return d.length == 10 && d[0] in "6789"
    }

    fun ordinal(n: Int): String {
        val suffix = if (n % 100 in 11..13) "th" else when (n % 10) { 1 -> "st"; 2 -> "nd"; 3 -> "rd"; else -> "th" }
        return "$n$suffix"
    }
}
