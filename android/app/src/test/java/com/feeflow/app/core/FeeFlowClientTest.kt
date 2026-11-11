package com.feeflow.app.core

import com.feeflow.app.data.ApiException
import com.feeflow.app.data.FeeFlowClient
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

class FeeFlowClientTest {
    private lateinit var server: MockWebServer
    private lateinit var client: FeeFlowClient

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        client = FeeFlowClient(server.url("/").toString(), adminToken = { "ADMIN.TOKEN" }, deviceToken = { "DEVICE-TOKEN" })
    }

    @After
    fun tearDown() = server.shutdown()

    private fun json(body: String, code: Int = 200) = MockResponse().setResponseCode(code).setHeader("Content-Type", "application/json").setBody(body)

    @Test
    fun loginNormalisesEmailAndNeedsNoAuth() = runTest {
        server.enqueue(json("""{"access_token":"abc","token_type":"bearer","name":"Priya"}"""))
        val res = client.login("  Admin@BrightMinds.test ", "pw")
        assertEquals("abc", res.accessToken)
        val req = server.takeRequest()
        assertEquals("/api/v1/auth/login", req.path)
        assertNull(req.getHeader("Authorization"))
        assertTrue(req.body.readUtf8().contains("\"email\":\"admin@brightminds.test\""))
    }

    @Test
    fun parsesPendingQueueShapeFromBackend() = runTest {
        server.enqueue(json("""{"items":[{"id":"d1","reference":"PD-ABC","student_name":"Aarav Sharma","student_id":"BM-1001",
            "parent_mobile":"+919800000001","installment":{"id":"i1","description":"Monthly tuition fee","billing_month":"2026-11",
            "due_date":"2026-11-16","total_paise":250000,"paid_paise":0,"excess_paise":0,"status":"UNPAID"},
            "expected_amount_paise":250000,"declared_amount_paise":250000,"payment_type":"FULL","declared_at":"2026-11-11T13:02:11.123000Z",
            "created_at":"2026-11-11T13:01:00Z","upi_app":"GPAY","approximate_payment_time":null,"parent_notes":null,
            "parent_upi_reference":null,"outstanding_paise":250000,"verification_status":"PAYMENT_REPORTED",
            "clarification_request":null,"clarification_requested_at":null,"admin_note":null,"rejection_reason":null,
            "reviewed_at":null,"grace_expires_at":"2026-11-12T13:02:11Z","verification_overdue":false,"future_field":42}]}"""))
        val items = client.pending()
        assertEquals("Aarav Sharma", items.single().studentName)
        assertEquals("GPAY", items.single().upiApp)
        assertEquals("Bearer ADMIN.TOKEN", server.takeRequest().getHeader("Authorization"))
    }

    @Test
    fun confirmSendsIdempotencyKeyAndSnakeCaseBody() = runTest {
        server.enqueue(json("""{"installment_status":"PAID","outstanding_paise":0,"receipt":{"receipt_number":"BMTC-000001","url":"http://x/receipt/t"},"excess_flagged_paise":0,"payments":[]}"""))
        val res = client.confirm(
            "d1",
            ConfirmRequest(true, true, true, listOf(TransactionIn(250000, "UTR1"))),
            "android-key-123",
        )
        assertEquals("BMTC-000001", res.receipt.receiptNumber)
        val req = server.takeRequest()
        assertEquals("android-key-123", req.getHeader("Idempotency-Key"))
        val body = req.body.readUtf8()
        assertTrue(body, body.contains("\"bank_transaction_checked\":true"))
        assertTrue(body, body.contains("\"amount_paise\":250000"))
        assertTrue(body, body.contains("\"bank_reference\":\"UTR1\""))
    }

    @Test
    fun surfacesServerErrorDetail() = runTest {
        server.enqueue(json("""{"detail":"Bank transaction UTR1 has already been credited."}""", 409))
        try {
            client.reject("d1", "nope", "k-1")
            fail()
        } catch (e: ApiException) {
            assertEquals(409, e.code)
            assertEquals("Bank transaction UTR1 has already been credited.", e.message)
        }
        server.enqueue(json("""{"detail":[{"loc":["body","reason"],"msg":"String should have at least 3 characters"}]}""", 422))
        try {
            client.reject("d1", "x", "k-2")
            fail()
        } catch (e: ApiException) {
            assertEquals("String should have at least 3 characters", e.message)
        }
    }

    @Test
    fun deviceSyncUsesDeviceTokenAndParsesSchedules() = runTest {
        server.enqueue(json("""{"server_time":"2026-11-11T13:02:11.123000Z",
            "sms":[{"id":"m1","to_mobile":"+919800000001","body":"Dear Parent","kind":"DECLARATION_ACK"}],
            "reminder_schedules":[{"id":"r1","version":4,"status":"SCHEDULED","scheduled_for":"2026-11-12T10:00:00Z",
              "hold_until":null,"to_mobile":"+919800000001","body":"Fee reminder","amount_paise":150000,"cancel_reason":null}]}"""))
        val s = client.sync()
        assertEquals("DECLARATION_ACK", s.sms.single().kind)
        assertEquals(4, s.reminderSchedules.single().version)
        val req = server.takeRequest()
        assertEquals("DEVICE-TOKEN", req.getHeader("X-Device-Token"))
        assertNull(req.getHeader("Authorization"))

        server.enqueue(json("""{"sms_acknowledged":1,"reminders_acknowledged":1}"""))
        client.ack(DeviceAckRequest(listOf(SmsAck("m1", "SENT")), listOf(ReminderAck("r1", 4))))
        val body = server.takeRequest().body.readUtf8()
        assertTrue(body, body.contains("\"status\":\"SENT\""))
        assertTrue(body, body.contains("\"version\":4"))
    }

    @Test
    fun revokedDeviceIs401() = runTest {
        server.enqueue(json("""{"detail":"Unknown or disabled device"}""", 401))
        try {
            client.sync()
            fail()
        } catch (e: ApiException) {
            assertTrue(e.isUnauthorized)
            assertEquals(401, (e as HttpStatusError).code)
        }
    }
}
