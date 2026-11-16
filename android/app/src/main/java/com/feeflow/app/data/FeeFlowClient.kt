package com.feeflow.app.data

import com.feeflow.app.core.AdminRemarkRequest
import com.feeflow.app.core.ClarificationRequest
import com.feeflow.app.core.ConfirmRequest
import com.feeflow.app.core.ConfirmResponse
import com.feeflow.app.core.SmsStatus
import com.feeflow.app.core.AdjustmentItem
import com.feeflow.app.core.AdjustmentList
import com.feeflow.app.core.ApplyExcessRequest
import com.feeflow.app.core.ApplyExcessResult
import com.feeflow.app.core.BatchStatus
import com.feeflow.app.core.BulkFeeRequest
import com.feeflow.app.core.BulkFeeResult
import com.feeflow.app.core.BulkReminderRequest
import com.feeflow.app.core.BulkSendResponse
import com.feeflow.app.core.Dashboard
import com.feeflow.app.core.FeePreview
import com.feeflow.app.core.GenerateResult
import com.feeflow.app.core.Money
import com.feeflow.app.core.PaymentSearch
import com.feeflow.app.core.RefCheck
import com.feeflow.app.core.ReminderPreview
import com.feeflow.app.core.ReportResponse
import com.feeflow.app.core.RetryFailedRequest
import com.feeflow.app.core.RetryResult
import com.feeflow.app.core.SearchResponse
import com.feeflow.app.core.SmsHealth
import com.feeflow.app.core.Declaration
import com.feeflow.app.core.DeviceAckRequest
import com.feeflow.app.core.DeviceApi
import com.feeflow.app.core.DeviceRegisterRequest
import com.feeflow.app.core.DeviceRegisterResponse
import com.feeflow.app.core.DevicesResponse
import com.feeflow.app.core.FeeFlowJson
import com.feeflow.app.core.HttpStatusError
import com.feeflow.app.core.LoginRequest
import com.feeflow.app.core.LoginResponse
import com.feeflow.app.core.Me
import com.feeflow.app.core.PendingResponse
import com.feeflow.app.core.RejectRequest
import com.feeflow.app.core.SendFeeLinkRequest
import com.feeflow.app.core.SendFeeLinkResponse
import com.feeflow.app.core.SignupRequest
import com.feeflow.app.core.Student
import com.feeflow.app.core.StudentList
import com.feeflow.app.core.StudentRequest
import com.feeflow.app.core.SyncResponse
import com.feeflow.app.core.Workspace
import com.feeflow.app.core.WorkspaceCreateRequest
import com.feeflow.app.core.WorkspaceUpdateRequest
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.KSerializer
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.UUID
import java.util.concurrent.TimeUnit

class ApiException(override val code: Int, message: String) : Exception(message), HttpStatusError {
    val isUnauthorized get() = code == 401
}

/**
 * FeeFlow REST client. Admin calls use the bearer token from sign-in; SMS-engine calls use the
 * device token. All mutating admin calls carry an Idempotency-Key supplied by the caller so a
 * retried tap can never verify a payment twice.
 */
class FeeFlowClient(
    baseUrl: String,
    private val adminToken: () -> String? = { null },
    private val deviceToken: () -> String? = { null },
    private val http: OkHttpClient = defaultHttp,
) : DeviceApi {
    private val base = baseUrl.trim().trimEnd('/')

    companion object {
        private val JSON = "application/json; charset=utf-8".toMediaType()
        val defaultHttp: OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .callTimeout(45, TimeUnit.SECONDS)
            .build()

        fun newIdempotencyKey(): String = "android-" + UUID.randomUUID().toString()

        fun errorMessage(code: Int, body: String): String {
            val detail = try {
                when (val d = FeeFlowJson.parseToJsonElement(body).jsonObject["detail"]) {
                    is JsonPrimitive -> d.content
                    is JsonArray -> d.joinToString("; ") { (it as? JsonObject)?.get("msg")?.jsonPrimitive?.content ?: it.toString() }
                    else -> null
                }
            } catch (_: Exception) {
                null
            }
            return detail ?: when (code) {
                401 -> "Please sign in again"
                429 -> "Too many requests - please wait a moment"
                in 500..599 -> "FeeFlow server error ($code)"
                else -> "Request failed ($code)"
            }
        }
    }

    private enum class Auth { NONE, ADMIN, DEVICE }

    private suspend fun <T> call(
        method: String,
        path: String,
        auth: Auth,
        out: KSerializer<T>?,
        body: String? = null,
        idempotencyKey: String? = null,
    ): T? = withContext(Dispatchers.IO) {
        val builder = Request.Builder().url(base + path).header("Accept", "application/json")
        when (auth) {
            Auth.ADMIN -> adminToken()?.let { builder.header("Authorization", "Bearer $it") }
            Auth.DEVICE -> deviceToken()?.let { builder.header("X-Device-Token", it) }
            Auth.NONE -> Unit
        }
        idempotencyKey?.let { builder.header("Idempotency-Key", it) }
        val reqBody = if (method == "GET") null else (body ?: "{}").toRequestBody(JSON)
        builder.method(method, reqBody)
        http.newCall(builder.build()).execute().use { res ->
            val text = res.body?.string().orEmpty()
            if (!res.isSuccessful) throw ApiException(res.code, errorMessage(res.code, text))
            if (out == null) null else FeeFlowJson.decodeFromString(out, text)
        }
    }

    private fun <T> enc(s: KSerializer<T>, v: T) = FeeFlowJson.encodeToString(s, v)

    // ---------------------------------------------------------------- admin
    suspend fun login(email: String, password: String): LoginResponse =
        call("POST", "/api/v1/auth/login", Auth.NONE, LoginResponse.serializer(),
            enc(LoginRequest.serializer(), LoginRequest(email.trim().lowercase(), password)))!!

    suspend fun me(): Me = call("GET", "/api/v1/auth/me", Auth.ADMIN, Me.serializer())!!

    suspend fun createWorkspace(req: WorkspaceCreateRequest): Workspace =
        call("POST", "/api/v1/workspace", Auth.ADMIN, Workspace.serializer(),
            enc(WorkspaceCreateRequest.serializer(), req))!!

    suspend fun workspace(): Workspace = call("GET", "/api/v1/workspace", Auth.ADMIN, Workspace.serializer())!!

    suspend fun updateWorkspace(req: WorkspaceUpdateRequest): Workspace =
        call("PATCH", "/api/v1/workspace", Auth.ADMIN, Workspace.serializer(), enc(WorkspaceUpdateRequest.serializer(), req))!!

    /** New centre owner account (30-day trial starts when the workspace is created). */
    suspend fun signup(name: String, email: String, password: String): LoginResponse =
        call("POST", "/api/v1/auth/signup", Auth.NONE, LoginResponse.serializer(),
            enc(SignupRequest.serializer(), SignupRequest(name.trim(), email.trim().lowercase(), password)))!!

    suspend fun students(query: String? = null, includeInactive: Boolean = false): List<Student> {
        val q = query?.trim()?.takeIf { it.isNotEmpty() }?.let { "&q=" + java.net.URLEncoder.encode(it, "UTF-8") } ?: ""
        return call("GET", "/api/v1/students?include_inactive=$includeInactive$q", Auth.ADMIN, StudentList.serializer())!!.items
    }

    suspend fun student(id: String): Student = call("GET", "/api/v1/students/$id", Auth.ADMIN, Student.serializer())!!

    suspend fun createStudent(req: StudentRequest): Student =
        call("POST", "/api/v1/students", Auth.ADMIN, Student.serializer(), enc(StudentRequest.serializer(), req))!!

    suspend fun updateStudent(id: String, req: StudentRequest): Student =
        call("PATCH", "/api/v1/students/$id", Auth.ADMIN, Student.serializer(), enc(StudentRequest.serializer(), req))!!

    /** channel = "SMS" (queued on the centre's SMS phone) or "SHARE" (returns the message to share). */
    suspend fun sendFeeLink(id: String, channel: String, idempotencyKey: String): SendFeeLinkResponse =
        call("POST", "/api/v1/students/$id/send-fee-link", Auth.ADMIN, SendFeeLinkResponse.serializer(),
            enc(SendFeeLinkRequest.serializer(), SendFeeLinkRequest(channel)), idempotencyKey)!!

    suspend fun pending(): List<Declaration> =
        call("GET", "/api/v1/payment-verification/pending", Auth.ADMIN, PendingResponse.serializer())!!.items

    suspend fun declaration(id: String): Declaration =
        call("GET", "/api/v1/payment-declarations/$id", Auth.ADMIN, Declaration.serializer())!!

    suspend fun confirm(id: String, req: ConfirmRequest, idempotencyKey: String): ConfirmResponse =
        call("POST", "/api/v1/payment-verification/$id/confirm", Auth.ADMIN, ConfirmResponse.serializer(),
            enc(ConfirmRequest.serializer(), req), idempotencyKey)!!

    // ---------------------------------------------------------------- v2.7 owner tools
    suspend fun dashboard(month: String? = null): Dashboard =
        call("GET", "/api/v1/dashboard" + (month?.let { "?month=$it" } ?: ""), Auth.ADMIN, Dashboard.serializer())!!

    suspend fun generateMonthly(idempotencyKey: String): GenerateResult =
        call("POST", "/api/v1/fees/generate-monthly", Auth.ADMIN, GenerateResult.serializer(), null, idempotencyKey)!!

    suspend fun remindersPreview(req: BulkReminderRequest): ReminderPreview =
        call("POST", "/api/v1/bulk/reminders/preview", Auth.ADMIN, ReminderPreview.serializer(), enc(BulkReminderRequest.serializer(), req))!!

    suspend fun remindersSend(req: BulkReminderRequest, idempotencyKey: String): BulkSendResponse =
        call("POST", "/api/v1/bulk/reminders/send", Auth.ADMIN, BulkSendResponse.serializer(),
            enc(BulkReminderRequest.serializer(), req), idempotencyKey)!!

    suspend fun batch(id: String): BatchStatus = call("GET", "/api/v1/bulk/batches/$id", Auth.ADMIN, BatchStatus.serializer())!!

    suspend fun feesPreview(req: BulkFeeRequest): FeePreview =
        call("POST", "/api/v1/bulk/fees/preview", Auth.ADMIN, FeePreview.serializer(), enc(BulkFeeRequest.serializer(), req))!!

    suspend fun feesCreate(req: BulkFeeRequest, idempotencyKey: String): BulkFeeResult =
        call("POST", "/api/v1/bulk/fees/create", Auth.ADMIN, BulkFeeResult.serializer(),
            enc(BulkFeeRequest.serializer(), req), idempotencyKey)!!

    suspend fun smsHealth(): SmsHealth = call("GET", "/api/v1/sms/health", Auth.ADMIN, SmsHealth.serializer())!!

    suspend fun retryFailed(batchId: String?, idempotencyKey: String): RetryResult =
        call("POST", "/api/v1/sms/retry-failed", Auth.ADMIN, RetryResult.serializer(),
            enc(RetryFailedRequest.serializer(), RetryFailedRequest(batchId)), idempotencyKey)!!

    suspend fun searchPayments(s: PaymentSearch): List<Declaration> {
        val q = buildList {
            add("status=" + enc(s.status))
            if (s.query.isNotBlank()) add("q=" + enc(s.query.trim()))
            if (s.classGrade.isNotBlank()) add("class_grade=" + enc(s.classGrade))
            Money.parseToPaise(s.amount)?.let { add("amount=" + (it / 100.0)) }
            if (s.utr.isNotBlank()) add("utr=" + enc(s.utr.trim()))
        }.joinToString("&")
        return call("GET", "/api/v1/payment-verification/search?$q", Auth.ADMIN, SearchResponse.serializer())!!.items
    }

    suspend fun checkReference(ref: String): RefCheck =
        call("GET", "/api/v1/verified-payments/check-reference?ref=" + enc(ref.trim()), Auth.ADMIN, RefCheck.serializer())!!

    suspend fun adjustments(): List<AdjustmentItem> =
        call("GET", "/api/v1/adjustments?status=FLAGGED", Auth.ADMIN, AdjustmentList.serializer())!!.items

    suspend fun applyExcess(id: String, idempotencyKey: String): ApplyExcessResult =
        call("POST", "/api/v1/adjustments/$id/apply", Auth.ADMIN, ApplyExcessResult.serializer(),
            enc(ApplyExcessRequest.serializer(), ApplyExcessRequest()), idempotencyKey)!!

    suspend fun report(name: String): ReportResponse = call("GET", "/api/v1/reports/$name", Auth.ADMIN, ReportResponse.serializer())!!

    private fun enc(v: String): String = java.net.URLEncoder.encode(v, "UTF-8")

    suspend fun smsStatus(id: String): SmsStatus =
        call("GET", "/api/v1/sms/$id", Auth.ADMIN, SmsStatus.serializer())!!

    /** Queue a FAILED message again; returns the new message. */
    suspend fun resendSms(id: String, idempotencyKey: String): SmsStatus =
        call("POST", "/api/v1/sms/$id/resend", Auth.ADMIN, SmsStatus.serializer(), null, idempotencyKey)!!

    suspend fun reject(id: String, reason: String, idempotencyKey: String) {
        call<Unit>("POST", "/api/v1/payment-verification/$id/reject", Auth.ADMIN, null,
            enc(RejectRequest.serializer(), RejectRequest(reason)), idempotencyKey)
    }

    /** Internal remark on a declaration (appended, audited, never shown to parents). */
    suspend fun addRemark(id: String, remark: String, idempotencyKey: String) {
        call<Unit>("POST", "/api/v1/payment-verification/$id/remarks", Auth.ADMIN, null,
            enc(AdminRemarkRequest.serializer(), AdminRemarkRequest(remark)), idempotencyKey)
    }

    suspend fun requestClarification(id: String, message: String, idempotencyKey: String) {
        call<Unit>("POST", "/api/v1/payment-verification/$id/request-clarification", Auth.ADMIN, null,
            enc(ClarificationRequest.serializer(), ClarificationRequest(message)), idempotencyKey)
    }

    suspend fun registerDevice(name: String): DeviceRegisterResponse =
        call("POST", "/api/v1/devices/register", Auth.ADMIN, DeviceRegisterResponse.serializer(),
            enc(DeviceRegisterRequest.serializer(), DeviceRegisterRequest(name)))!!

    suspend fun devices(): DevicesResponse =
        call("GET", "/api/v1/devices", Auth.ADMIN, DevicesResponse.serializer())!!

    // ---------------------------------------------------------------- SMS engine (device token)
    override suspend fun sync(): SyncResponse =
        call("GET", "/api/v1/devices/sync", Auth.DEVICE, SyncResponse.serializer())!!

    override suspend fun ack(request: DeviceAckRequest) {
        call<Unit>("POST", "/api/v1/devices/ack", Auth.DEVICE, null, enc(DeviceAckRequest.serializer(), request))
    }
}
