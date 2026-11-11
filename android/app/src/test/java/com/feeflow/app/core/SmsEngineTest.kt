package com.feeflow.app.core

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Duration
import java.time.Instant

class FakeApi : DeviceApi {
    var next = SyncResponse()
    var syncError: Exception? = null
    var ackError: Exception? = null
    val acks = mutableListOf<DeviceAckRequest>()
    var syncCalls = 0

    override suspend fun sync(): SyncResponse {
        syncCalls++
        syncError?.let { throw it }
        return next
    }

    override suspend fun ack(request: DeviceAckRequest) {
        ackError?.let { throw it }
        acks += request
    }
}

class FakeSender(var fail: Boolean = false) : SmsSender {
    val sent = mutableListOf<Pair<String, String>>()
    override suspend fun send(toMobile: String, body: String): SendResult {
        if (fail) return SendResult(false, "No mobile service")
        sent += toMobile to body
        return SendResult(true)
    }
}

class HttpError(override val code: Int) : Exception("HTTP $code"), HttpStatusError

class SmsEngineTest {
    private var now: Instant = Instant.parse("2026-11-11T10:00:00Z")
    private val api = FakeApi()
    private val sender = FakeSender()
    private val store = InMemoryStateStore()
    private val engine = SmsEngine(api, store, sender, clock = { now })

    private fun sms(id: String = "m1") = OutboundSms(id, "+919800000001", "Dear Parent, we have received...", "DECLARATION_ACK")

    private fun schedule(
        id: String = "r1",
        version: Int = 1,
        status: String = "SCHEDULED",
        at: String = "2026-11-11T09:00:00Z",
        hold: String? = null,
    ) = RemoteSchedule(id, version, status, at, hold, "+919800000001", "Fee reminder $id v$version", 250000)

    @Test
    fun `one-off sms is sent exactly once even when re-offered`() = runTest {
        api.next = SyncResponse(sms = listOf(sms()))
        val r1 = engine.runOnce()
        assertEquals(1, r1.smsSent)
        assertEquals(listOf(SmsAck("m1", "SENT")), api.acks.last().sms)

        // Server re-offers (e.g. our ack was lost) -> not sent again, but re-acknowledged
        val r2 = engine.runOnce()
        assertEquals(0, r2.smsSent)
        assertEquals(1, r2.smsSkippedDuplicate)
        assertEquals(1, sender.sent.size)
        assertEquals("SENT", api.acks.last().sms.single().status)
    }

    @Test
    fun `send interrupted by a crash is reported failed and never resent`() = runTest {
        store.state = EngineState(sentSms = mapOf("m1" to SentSms("SENDING", now.toEpochMilli())))
        api.next = SyncResponse(sms = listOf(sms()))
        engine.runOnce()
        assertTrue(sender.sent.isEmpty())
        assertEquals("FAILED", api.acks.last().sms.single().status)
    }

    @Test
    fun `due reminder is sent and acknowledged with sent_at`() = runTest {
        api.next = SyncResponse(reminderSchedules = listOf(schedule()))
        val r = engine.runOnce()
        assertEquals(1, r.remindersSent)
        assertEquals("Fee reminder r1 v1", sender.sent.single().second)
        val ack = api.acks.last().reminders.single()
        assertEquals("r1", ack.id)
        assertEquals(now.toString(), ack.sentAt)
        // Next run: nothing more to send
        api.next = SyncResponse()
        assertEquals(0, engine.runOnce().remindersSent)
    }

    @Test
    fun `future, held and cancelled reminders are not sent`() = runTest {
        api.next = SyncResponse(
            reminderSchedules = listOf(
                schedule("future", at = "2026-11-12T09:00:00Z"),
                schedule("held", hold = "2026-11-12T09:00:00Z"),
                schedule("cancelled", status = "CANCELLED"),
            )
        )
        val r = engine.runOnce()
        assertEquals(0, r.remindersSent)
        assertTrue(sender.sent.isEmpty())
        // received-acks for all three versions so the server can mark them synced
        assertEquals(setOf("future", "held", "cancelled"), api.acks.last().reminders.map { it.id }.toSet())
        assertTrue(api.acks.last().reminders.all { it.sentAt == null })

        // Hold expires (grace period over, no verification) -> reminder goes out
        now = Instant.parse("2026-11-12T09:30:00Z")
        api.next = SyncResponse()
        val r2 = engine.runOnce()
        assertEquals(setOf("Fee reminder future v1", "Fee reminder held v1"), sender.sent.map { it.second }.toSet())
        assertEquals(2, r2.remindersSent)
    }

    @Test
    fun `cancellation received in the same sync wins over the stale local copy`() = runTest {
        // Device knew the reminder from an earlier sync (but it was not due then)
        api.next = SyncResponse(reminderSchedules = listOf(schedule(at = "2026-11-11T10:30:00Z")))
        engine.runOnce()
        // Parent paid; admin verified -> server cancelled it (v2). Now it would be due.
        now = Instant.parse("2026-11-11T11:00:00Z")
        api.next = SyncResponse(reminderSchedules = listOf(schedule(version = 2, status = "CANCELLED", at = "2026-11-11T10:30:00Z")))
        engine.runOnce()
        assertTrue(sender.sent.isEmpty())
        assertEquals("CANCELLED", store.state.schedules["r1"]!!.status)
    }

    @Test
    fun `older schedule versions are ignored`() = runTest {
        api.next = SyncResponse(reminderSchedules = listOf(schedule(version = 3, status = "CANCELLED")))
        engine.runOnce()
        api.next = SyncResponse(reminderSchedules = listOf(schedule(version = 2, status = "SCHEDULED")))
        engine.runOnce()
        assertEquals(3, store.state.schedules["r1"]!!.version)
        assertTrue(sender.sent.isEmpty())
    }

    @Test
    fun `reminder already sent locally is not resent when the server re-offers a newer version`() = runTest {
        api.next = SyncResponse(reminderSchedules = listOf(schedule()))
        api.ackError = RuntimeException("timeout") // our "sent" ack does not reach the server
        engine.runOnce()
        api.ackError = null
        api.next = SyncResponse(reminderSchedules = listOf(schedule(version = 2)))
        engine.runOnce()
        assertEquals(1, sender.sent.size)
        // The queued "sent" ack is delivered on the next successful sync
        assertTrue(api.acks.last().reminders.any { it.id == "r1" && it.sentAt != null })
    }

    @Test
    fun `offline device still sends due reminders from fresh local state and uploads results later`() = runTest {
        api.next = SyncResponse(reminderSchedules = listOf(schedule(at = "2026-11-11T12:00:00Z")))
        engine.runOnce() // synced at 10:00
        now = Instant.parse("2026-11-11T12:05:00Z")
        api.syncError = RuntimeException("Unable to resolve host")
        val r = engine.runOnce()
        assertFalse(r.synced)
        assertEquals(1, r.remindersSent)
        assertEquals(1, store.state.pendingReminderAcks.values.count { it.sentAt != null })
        api.syncError = null
        api.next = SyncResponse()
        engine.runOnce()
        assertTrue(store.state.pendingReminderAcks.isEmpty())
    }

    @Test
    fun `reminders are held when the schedule is stale (possible missed cancellation)`() = runTest {
        api.next = SyncResponse(reminderSchedules = listOf(schedule(at = "2026-11-12T12:00:00Z")))
        engine.runOnce() // synced at 10:00 on the 11th
        now = Instant.parse("2026-11-12T12:30:00Z") // 26.5h later, server unreachable
        api.syncError = RuntimeException("offline")
        val r = engine.runOnce()
        assertEquals(1, r.remindersHeldStale)
        assertTrue(sender.sent.isEmpty())
    }

    @Test
    fun `failed reminder send is retried up to the attempt limit`() = runTest {
        sender.fail = true
        api.next = SyncResponse(reminderSchedules = listOf(schedule()))
        repeat(5) { engine.runOnce() }
        assertEquals(3, store.state.schedules["r1"]!!.attempts)
        assertNull(store.state.schedules["r1"]!!.localSentAtMs)
        sender.fail = false
        engine.runOnce()
        assertTrue(sender.sent.isEmpty()) // gave up after 3 attempts
    }

    @Test
    fun `very late reminders are skipped`() = runTest {
        api.next = SyncResponse(reminderSchedules = listOf(schedule(at = "2026-11-08T09:00:00Z")))
        engine.runOnce()
        assertTrue(sender.sent.isEmpty())
    }

    @Test
    fun `failed one-off sms is acknowledged as FAILED with the radio error`() = runTest {
        sender.fail = true
        api.next = SyncResponse(sms = listOf(sms()))
        val r = engine.runOnce()
        assertEquals(1, r.smsFailed)
        assertEquals(SmsAck("m1", "FAILED", "No mobile service"), api.acks.last().sms.single())
    }

    @Test
    fun `ack failure keeps results and retries next time`() = runTest {
        api.next = SyncResponse(sms = listOf(sms()))
        api.ackError = RuntimeException("502")
        val r = engine.runOnce()
        assertFalse(r.acked)
        assertEquals(1, store.state.pendingSmsAcks.size)
        api.ackError = null
        api.next = SyncResponse()
        assertTrue(engine.runOnce().acked)
        assertTrue(store.state.pendingSmsAcks.isEmpty())
        assertEquals(1, sender.sent.size)
    }

    @Test
    fun `revoked device token surfaces as 401 and nothing is sent`() = runTest {
        api.syncError = HttpError(401)
        val r = engine.runOnce()
        assertEquals(401, r.syncErrorCode)
        assertTrue(sender.sent.isEmpty())
        assertTrue(api.acks.isEmpty())
    }

    @Test
    fun `history older than retention is pruned`() = runTest {
        api.next = SyncResponse(sms = listOf(sms()))
        engine.runOnce()
        now = now.plus(Duration.ofDays(31))
        api.next = SyncResponse()
        engine.runOnce()
        assertTrue(store.state.sentSms.isEmpty())
    }
}
