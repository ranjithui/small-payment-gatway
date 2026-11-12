package com.feeflow.app.data

import com.feeflow.app.core.ClarificationRequest
import com.feeflow.app.core.ConfirmRequest
import com.feeflow.app.core.ConfirmResponse
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

    suspend fun reject(id: String, reason: String, idempotencyKey: String) {
        call<Unit>("POST", "/api/v1/payment-verification/$id/reject", Auth.ADMIN, null,
            enc(RejectRequest.serializer(), RejectRequest(reason)), idempotencyKey)
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
