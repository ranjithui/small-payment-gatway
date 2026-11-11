package com.feeflow.app.sms

import android.Manifest
import android.app.Activity
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.os.Build
import android.telephony.SmsManager
import androidx.core.content.ContextCompat
import com.feeflow.app.core.SendResult
import com.feeflow.app.core.SmsSender
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import java.util.UUID
import kotlin.coroutines.resume

/**
 * Sends SMS from the phone's own SIM (default SMS subscription) and waits for the radio's
 * "sent" result for every part of a multipart message. No SMS gateway is involved.
 */
class AndroidSmsSender(private val context: Context) : SmsSender {

    fun hasPermission(): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.SEND_SMS) == PackageManager.PERMISSION_GRANTED

    private fun smsManager(): SmsManager =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) context.getSystemService(SmsManager::class.java)
        else @Suppress("DEPRECATION") SmsManager.getDefault()

    override suspend fun send(toMobile: String, body: String): SendResult {
        if (!hasPermission()) return SendResult(false, "SMS permission not granted on the device")
        val result = withTimeoutOrNull(60_000) {
            suspendCancellableCoroutine { cont ->
                val action = "com.feeflow.app.SMS_SENT." + UUID.randomUUID()
                val manager = smsManager()
                val parts = manager.divideMessage(body)
                var remaining = parts.size
                var error: String? = null
                val receiver = object : BroadcastReceiver() {
                    override fun onReceive(ctx: Context, intent: Intent) {
                        if (resultCode != Activity.RESULT_OK && error == null) error = describe(resultCode)
                        remaining--
                        if (remaining <= 0) {
                            runCatching { context.unregisterReceiver(this) }
                            if (cont.isActive) cont.resume(SendResult(error == null, error))
                        }
                    }
                }
                ContextCompat.registerReceiver(context, receiver, IntentFilter(action), ContextCompat.RECEIVER_NOT_EXPORTED)
                cont.invokeOnCancellation { runCatching { context.unregisterReceiver(receiver) } }
                val sentIntents = ArrayList(parts.indices.map { i ->
                    PendingIntent.getBroadcast(
                        context, i, Intent(action).setPackage(context.packageName),
                        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_ONE_SHOT,
                    )
                })
                try {
                    if (parts.size == 1) manager.sendTextMessage(toMobile, null, body, sentIntents[0], null)
                    else manager.sendMultipartTextMessage(toMobile, null, parts, sentIntents, null)
                } catch (e: Exception) {
                    runCatching { context.unregisterReceiver(receiver) }
                    if (cont.isActive) cont.resume(SendResult(false, e.message ?: "Send failed"))
                }
            }
        }
        // On timeout the SMS may still go out later; reporting FAILED never causes a resend.
        return result ?: SendResult(false, "No confirmation from SIM within 60s - delivery unknown")
    }

    private fun describe(code: Int): String = when (code) {
        SmsManager.RESULT_ERROR_GENERIC_FAILURE -> "Generic failure"
        SmsManager.RESULT_ERROR_NO_SERVICE -> "No mobile service"
        SmsManager.RESULT_ERROR_NULL_PDU -> "Null PDU"
        SmsManager.RESULT_ERROR_RADIO_OFF -> "Radio off / airplane mode"
        else -> "SIM error code $code"
    }
}
