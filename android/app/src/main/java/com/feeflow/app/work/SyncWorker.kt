package com.feeflow.app.work

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.feeflow.app.core.FileStateStore
import com.feeflow.app.core.SmsEngine
import com.feeflow.app.data.SecurePrefs
import com.feeflow.app.sms.AndroidSmsSender
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * Runs the SIM SMS engine. Scheduled every 15 minutes (WorkManager minimum; survives reboots)
 * and on demand from the app. No network constraint: due reminders are still evaluated from
 * the locally known schedule when the server is unreachable.
 */
class SyncWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val prefs = SecurePrefs(applicationContext)
        if (prefs.deviceToken == null) return Result.success() // this phone is not the SMS device
        val engine = SmsEngine(prefs.client(), stateStore(applicationContext), AndroidSmsSender(applicationContext))
        val report = engine.runOnce()
        // Device token revoked (another phone was registered) -> stop acting as the SMS device.
        if (report.syncErrorCode == 401) {
            prefs.deviceToken = null
            SyncScheduler.cancel(applicationContext)
        }
        return Result.success(workDataOf("synced" to report.synced, "sent" to report.smsSent + report.remindersSent))
    }

    companion object {
        fun stateStore(context: Context) = FileStateStore(File(context.filesDir, "sms_engine_state.json"))
    }
}

object SyncScheduler {
    private const val PERIODIC = "feeflow-sms-engine"
    private const val NOW = "feeflow-sms-engine-now"

    fun schedule(context: Context) {
        val req = PeriodicWorkRequestBuilder<SyncWorker>(15, TimeUnit.MINUTES).build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(PERIODIC, ExistingPeriodicWorkPolicy.KEEP, req)
    }

    fun runNow(context: Context) {
        val req = OneTimeWorkRequestBuilder<SyncWorker>().build()
        WorkManager.getInstance(context).enqueueUniqueWork(NOW, ExistingWorkPolicy.KEEP, req)
    }

    fun cancel(context: Context) {
        WorkManager.getInstance(context).cancelUniqueWork(PERIODIC)
        WorkManager.getInstance(context).cancelUniqueWork(NOW)
    }
}
