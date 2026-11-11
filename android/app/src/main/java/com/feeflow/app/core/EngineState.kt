package com.feeflow.app.core

import kotlinx.serialization.Serializable
import java.io.File

/** Locally known copy of one reminder schedule (the device's source of truth when offline). */
@Serializable
data class LocalSchedule(
    val id: String,
    val version: Int,
    val status: String,
    val scheduledForMs: Long,
    val holdUntilMs: Long? = null,
    val toMobile: String,
    val body: String,
    val amountPaise: Long,
    /** Set when THIS device sent it; never send twice even if the server re-offers it. */
    val localSentAtMs: Long? = null,
    val attempts: Int = 0,
    val lastError: String? = null,
)

@Serializable
data class SentSms(val status: String, val atMs: Long, val error: String? = null)

@Serializable
data class EngineState(
    val schedules: Map<String, LocalSchedule> = emptyMap(),
    /** One-off SMS handled by this device, by server message id (de-duplication). */
    val sentSms: Map<String, SentSms> = emptyMap(),
    /** Results not yet acknowledged to the server (survive restarts / offline periods). */
    val pendingSmsAcks: Map<String, SmsAck> = emptyMap(),
    val pendingReminderAcks: Map<String, ReminderAck> = emptyMap(),
    val lastSyncAtMs: Long? = null,
    val lastSyncError: String? = null,
    val lastRunAtMs: Long? = null,
)

interface StateStore {
    fun load(): EngineState
    fun save(state: EngineState)
}

class InMemoryStateStore(var state: EngineState = EngineState()) : StateStore {
    var saves = 0
    override fun load() = state
    override fun save(state: EngineState) {
        this.state = state
        saves++
    }
}

/**
 * JSON file store with crash-safe writes: write to a temp file, keep the previous file as
 * ".bak", then move the temp file into place. Loading falls back to the backup so the
 * de-duplication history is never silently lost (which could cause duplicate SMS).
 */
class FileStateStore(private val file: File) : StateStore {
    private val backup = File(file.path + ".bak")
    private val tmp = File(file.path + ".tmp")

    @Synchronized
    override fun load(): EngineState {
        for (f in listOf(file, backup)) {
            if (!f.exists()) continue
            try {
                return FeeFlowJson.decodeFromString(EngineState.serializer(), f.readText())
            } catch (_: Exception) {
                // try the next candidate
            }
        }
        return EngineState()
    }

    @Synchronized
    override fun save(state: EngineState) {
        file.parentFile?.mkdirs()
        tmp.writeText(FeeFlowJson.encodeToString(EngineState.serializer(), state))
        if (file.exists()) {
            backup.delete()
            file.renameTo(backup)
        }
        if (!tmp.renameTo(file)) {
            tmp.copyTo(file, overwrite = true)
            tmp.delete()
        }
    }
}
