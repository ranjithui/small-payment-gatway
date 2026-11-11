package com.feeflow.app.core

import com.feeflow.app.data.FeeFlowClient
import kotlinx.coroutines.runBlocking
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.util.UUID

/**
 * End-to-end against a REAL running FeeFlow backend (skipped unless FEEFLOW_E2E_URL is set).
 * Uses the app's own HTTP client and SMS engine, with a fake SIM sender.
 *
 *   FEEFLOW_E2E_URL=http://localhost:8000 FEEFLOW_E2E_PAY_TOKEN=<link token> gradlew testDebugUnitTest
 */
class LiveBackendE2ETest {
    private val url = System.getenv("FEEFLOW_E2E_URL")
    private val payToken = System.getenv("FEEFLOW_E2E_PAY_TOKEN")

    @Test
    fun adminVerifiesOnPhoneAndSimEngineSendsTheSms() = runBlocking {
        assumeTrue("FEEFLOW_E2E_URL not set", url != null && payToken != null)
        var adminToken: String? = null
        var deviceToken: String? = null
        val client = FeeFlowClient(url!!, { adminToken }, { deviceToken })

        // 1. Admin signs in on the phone (seeded local test account)
        adminToken = client.login("admin@brightminds.test", "feeflow-demo-123").accessToken
        assertEquals("Bright Minds Tuition Centre", client.me().workspace!!.name)

        // 2. Phone becomes the SMS device
        deviceToken = client.registerDevice("E2E test phone").deviceToken
        val store = InMemoryStateStore()
        val sim = FakeSender()
        val engine = SmsEngine(client, store, sim)
        engine.runOnce() // drain anything already queued

        // 3. Parent taps "I have paid" (public API, as the web page does)
        val http = OkHttpClient()
        val body = """{"token":"$payToken","action":"PAID"}""".toRequestBody("application/json".toMediaType())
        http.newCall(
            Request.Builder().url("$url/api/v1/payment-declarations").post(body)
                .header("Idempotency-Key", "e2e-" + UUID.randomUUID()).build()
        ).execute().use { assertTrue(it.body?.string(), it.code in 200..201) }

        // 4. SIM engine sends the acknowledgement SMS
        val sentBefore = sim.sent.size
        val r1 = engine.runOnce()
        assertTrue(r1.synced && r1.acked)
        assertTrue(sim.sent.drop(sentBefore).any { it.second.contains("received your payment confirmation") })

        // 5. Admin verifies from the phone
        val pending = client.pending()
        val decl = pending.first { it.verificationStatus == "PAYMENT_REPORTED" || it.verificationStatus == "PENDING_VERIFICATION" }
        val detail = client.declaration(decl.id)
        assertEquals("PENDING_VERIFICATION", detail.verificationStatus)
        val key = FeeFlowClient.newIdempotencyKey()
        val utr = "E2E" + System.currentTimeMillis()
        val req = ConfirmRequest(true, true, true, listOf(TransactionIn(detail.declaredAmountPaise, utr)))
        val res = client.confirm(decl.id, req, key)
        val replay = client.confirm(decl.id, req, key) // same key -> same answer, no double credit
        assertEquals(res, replay)

        // 6. SIM engine sends the "verified" SMS with the receipt link; reminders are cancelled locally
        val sentMid = sim.sent.size
        engine.runOnce()
        val verifiedSms = sim.sent.drop(sentMid).map { it.second }
        assertTrue(verifiedSms.toString(), verifiedSms.any { it.contains("verified successfully") && it.contains("/receipt/") })
        assertTrue(store.state.schedules.values.filter { it.toMobile == decl.parentMobile && it.status == "SCHEDULED" && res.outstandingPaise == 0L }.isEmpty())

        // 7. Nothing left to do on the next run
        val r3 = engine.runOnce()
        assertEquals(0, r3.smsSent)
        println("E2E OK: ${decl.studentName} ${res.installmentStatus} receipt ${res.receipt.receiptNumber}, SMS sent by fake SIM: ${sim.sent.size}")
    }
}
