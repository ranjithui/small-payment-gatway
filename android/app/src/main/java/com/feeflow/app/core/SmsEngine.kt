package com.feeflow.app.core

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.time.Duration
import java.time.Instant

/** Device-side API (implemented by [com.feeflow.app.data.FeeFlowClient]). */
interface DeviceApi {
    suspend fun sync(): SyncResponse
    suspend fun ack(request: DeviceAckRequest)
}

data class SendResult(val success: Boolean, val error: String? = null)

/** Implemented by API errors that carry an HTTP status code. */
interface HttpStatusError {
    val code: Int
}

/** Sends an SMS from the phone's physical SIM. */
interface SmsSender {
    suspend fun send(toMobile: String, body: String): SendResult
}

data class EngineConfig(
    /** Don't send reminders if the device hasn't synced for this long (it may have missed a cancellation). */
    val maxScheduleStaleness: Duration = Duration.ofHours(24),
    /** Don't send reminders that are this far overdue (e.g. phone was off for a week). */
    val maxReminderLateness: Duration = Duration.ofHours(48),
    val maxReminderAttempts: Int = 3,
    val historyRetention: Duration = Duration.ofDays(30),
)

data class RunReport(
    val synced: Boolean,
    val syncError: String? = null,
    /** HTTP status of a failed sync, e.g. 401 when this phone's device token was revoked. */
    val syncErrorCode: Int? = null,
    val smsSent: Int = 0,
    val smsFailed: Int = 0,
    val smsSkippedDuplicate: Int = 0,
    val remindersSent: Int = 0,
    val remindersFailed: Int = 0,
    val remindersHeldStale: Int = 0,
    val acked: Boolean = false,
    val ackError: String? = null,
)

object ReminderRules {
    /** The rule from the spec: check the latest LOCALLY known state right before sending. */
    fun isDue(s: LocalSchedule, now: Instant): Boolean =
        s.status == "SCHEDULED" &&
            s.localSentAtMs == null &&
            now.toEpochMilli() >= s.scheduledForMs &&
            (s.holdUntilMs == null || now.toEpochMilli() >= s.holdUntilMs)

    fun isTooLate(s: LocalSchedule, now: Instant, cfg: EngineConfig): Boolean =
        now.toEpochMilli() - s.scheduledForMs > cfg.maxReminderLateness.toMillis()

    fun scheduleIsFresh(state: EngineState, now: Instant, cfg: EngineConfig): Boolean {
        val last = state.lastSyncAtMs ?: return false
        return now.toEpochMilli() - last <= cfg.maxScheduleStaleness.toMillis()
    }

    /** Apply a server schedule only if it is newer; never forget that this device already sent it. */
    fun merge(local: LocalSchedule?, remote: RemoteSchedule): LocalSchedule? {
        if (local != null && remote.version < local.version) return null
        val scheduledFor = Times.parse(remote.scheduledFor)?.toEpochMilli() ?: return null
        return LocalSchedule(
            id = remote.id,
            version = remote.version,
            status = remote.status,
            scheduledForMs = scheduledFor,
            holdUntilMs = Times.parse(remote.holdUntil)?.toEpochMilli(),
            toMobile = remote.toMobile,
            body = remote.body,
            amountPaise = remote.amountPaise,
            localSentAtMs = local?.localSentAtMs,
            attempts = local?.attempts ?: 0,
            lastError = local?.lastError,
        )
    }
}

/**
 * One pass of the SIM SMS engine:
 *  1. sync with the server (new SMS + changed reminder schedules),
 *  2. send one-off SMS not already sent by this device (de-duplicated by id),
 *  3. send reminders that are due according to the latest LOCAL schedule state,
 *  4. acknowledge results (kept and retried if the server is unreachable).
 *
 * State is persisted after every send so a crash can never cause a duplicate SMS.
 */
class SmsEngine(
    private val api: DeviceApi,
    private val store: StateStore,
    private val sender: SmsSender,
    private val clock: () -> Instant = { Instant.now() },
    private val config: EngineConfig = EngineConfig(),
) {
    companion object {
        /** Process-wide lock: the worker and "Sync now" must never run concurrently. */
        val lock = Mutex()
    }

    suspend fun runOnce(): RunReport = lock.withLock { runLocked() }

    private suspend fun runLocked(): RunReport {
        var state = store.load()
        val now = clock()
        var report = RunReport(synced = false)

        // 1. Sync
        val sync = try {
            api.sync()
        } catch (e: Exception) {
            report = report.copy(syncError = e.message ?: e.javaClass.simpleName, syncErrorCode = (e as? HttpStatusError)?.code)
            null
        }
        if (sync != null) {
            val schedules = state.schedules.toMutableMap()
            val reminderAcks = state.pendingReminderAcks.toMutableMap()
            for (remote in sync.reminderSchedules) {
                val merged = ReminderRules.merge(schedules[remote.id], remote)
                if (merged != null) schedules[remote.id] = merged
                // Acknowledge receipt of this version (unless a "sent" ack is already queued).
                val existing = reminderAcks[remote.id]
                if (existing?.sentAt == null) reminderAcks[remote.id] = ReminderAck(remote.id, remote.version)
            }
            state = state.copy(
                schedules = schedules,
                pendingReminderAcks = reminderAcks,
                lastSyncAtMs = now.toEpochMilli(),
                lastSyncError = null,
            )
            report = report.copy(synced = true)
            store.save(state)

            // 2. One-off SMS
            for (msg in sync.sms) {
                val already = state.sentSms[msg.id]
                if (already != null) {
                    report = report.copy(smsSkippedDuplicate = report.smsSkippedDuplicate + 1)
                    // A send interrupted by a crash has unknown delivery: report FAILED, never resend.
                    val ack = if (already.status == "SENDING") {
                        SmsAck(msg.id, "FAILED", "Interrupted during send - delivery unknown")
                    } else {
                        SmsAck(msg.id, already.status, already.error)
                    }
                    state = state.copy(pendingSmsAcks = state.pendingSmsAcks + (msg.id to ack))
                    continue
                }
                // Record intent BEFORE sending: if we crash mid-send we prefer a missed SMS over a duplicate.
                state = state.copy(sentSms = state.sentSms + (msg.id to SentSms("SENDING", now.toEpochMilli())))
                store.save(state)
                val result = safeSend(msg.toMobile, msg.body)
                val status = if (result.success) "SENT" else "FAILED"
                state = state.copy(
                    sentSms = state.sentSms + (msg.id to SentSms(status, clock().toEpochMilli(), result.error)),
                    pendingSmsAcks = state.pendingSmsAcks + (msg.id to SmsAck(msg.id, status, result.error)),
                )
                store.save(state)
                report = if (result.success) report.copy(smsSent = report.smsSent + 1) else report.copy(smsFailed = report.smsFailed + 1)
            }
        } else {
            state = state.copy(lastSyncError = report.syncError)
        }

        // 3. Reminders from LOCAL state
        val fresh = ReminderRules.scheduleIsFresh(state, now, config)
        val due = state.schedules.values.filter { ReminderRules.isDue(it, now) }.sortedBy { it.scheduledForMs }
        for (s in due) {
            if (!fresh) {
                report = report.copy(remindersHeldStale = report.remindersHeldStale + 1)
                continue
            }
            if (ReminderRules.isTooLate(s, now, config) || s.attempts >= config.maxReminderAttempts) continue
            val sending = s.copy(localSentAtMs = now.toEpochMilli(), attempts = s.attempts + 1)
            state = state.copy(schedules = state.schedules + (s.id to sending))
            store.save(state)
            val result = safeSend(s.toMobile, s.body)
            if (result.success) {
                val sentAt = clock()
                state = state.copy(
                    pendingReminderAcks = state.pendingReminderAcks + (s.id to ReminderAck(s.id, s.version, sentAt.toString())),
                )
                report = report.copy(remindersSent = report.remindersSent + 1)
            } else {
                // Roll back so it is retried on the next run (up to maxReminderAttempts).
                state = state.copy(schedules = state.schedules + (s.id to sending.copy(localSentAtMs = null, lastError = result.error)))
                report = report.copy(remindersFailed = report.remindersFailed + 1)
            }
            store.save(state)
        }

        // 4. Acknowledge (only when the sync worked: the server is reachable)
        if (sync != null && (state.pendingSmsAcks.isNotEmpty() || state.pendingReminderAcks.isNotEmpty())) {
            try {
                api.ack(DeviceAckRequest(state.pendingSmsAcks.values.toList(), state.pendingReminderAcks.values.toList()))
                state = state.copy(pendingSmsAcks = emptyMap(), pendingReminderAcks = emptyMap())
                report = report.copy(acked = true)
            } catch (e: Exception) {
                report = report.copy(ackError = e.message ?: e.javaClass.simpleName)
            }
        }

        state = prune(state, now).copy(lastRunAtMs = now.toEpochMilli())
        store.save(state)
        return report
    }

    private suspend fun safeSend(to: String, body: String): SendResult = try {
        sender.send(to, body)
    } catch (e: Exception) {
        SendResult(false, e.message ?: e.javaClass.simpleName)
    }

    private fun prune(state: EngineState, now: Instant): EngineState {
        val cutoff = now.toEpochMilli() - config.historyRetention.toMillis()
        return state.copy(
            sentSms = state.sentSms.filterValues { it.atMs >= cutoff },
            schedules = state.schedules.filterValues {
                it.scheduledForMs >= cutoff || (it.status == "SCHEDULED" && it.localSentAtMs == null)
            },
        )
    }
}
