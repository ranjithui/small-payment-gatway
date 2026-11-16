package com.feeflow.app.ui

import android.content.Intent
import android.net.Uri
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.feeflow.app.core.Money
import com.feeflow.app.data.FeeFlowClient

private val Green = Color(0xFF047857)

/** Shown after the admin verifies a payment: the parent's thank-you message, its SMS status, WhatsApp and receipt. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConfirmedScreen(state: UiState, vm: AppViewModel, snackbar: SnackbarHostState) {
    BackHandler { vm.closeConfirmed() }
    val c = state.confirmed ?: return
    val context = LocalContext.current
    var resendKey by remember(c.smsId) { mutableStateOf(FeeFlowClient.newIdempotencyKey()) }
    fun open(url: String) = runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = { TopAppBar(title = { Text("Payment confirmed") }) },
    ) { pad ->
        Column(
            Modifier.padding(pad).fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Box(Modifier.size(64.dp).background(Green, CircleShape), contentAlignment = Alignment.Center) {
                Icon(Icons.Filled.Check, contentDescription = null, tint = Color.White, modifier = Modifier.size(38.dp))
            }
            Spacer(Modifier.height(10.dp))
            Text("${Money.format(c.amountPaise)} received", fontSize = 24.sp, fontWeight = FontWeight.Bold)
            Text(c.studentName, fontSize = 16.sp, color = Color.Gray)
            Spacer(Modifier.height(4.dp))
            Text(
                if (c.outstandingPaise == 0L) "Fee fully paid · Receipt ${c.receiptNumber}"
                else "Balance due ${Money.format(c.outstandingPaise)} · Receipt ${c.receiptNumber}",
                fontSize = 13.sp, color = Color.Gray, textAlign = TextAlign.Center,
            )
            if (c.excessFlaggedPaise > 0) {
                Text("Excess ${Money.format(c.excessFlaggedPaise)} flagged for refund/adjustment.", fontSize = 13.sp, color = Warn)
            }
            Spacer(Modifier.height(16.dp))

            val t = c.thankYou
            if (t != null) {
                Card(Modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = Color.White)) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("Thank-you message to the parent", fontWeight = FontWeight.SemiBold)
                        Text(t.message, fontSize = 14.sp)
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            SmsStatusLine(c)
                            Spacer(Modifier.weight(1f))
                            if (c.checking) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                            else TextButton(onClick = { vm.refreshThankYouStatus() }) { Text("Refresh") }
                        }
                        if (c.smsStatus == "FAILED") {
                            Button(onClick = { vm.resendThankYou(resendKey); resendKey = FeeFlowClient.newIdempotencyKey() },
                                enabled = !state.busy, modifier = Modifier.fillMaxWidth()) { Text("Send SMS again") }
                        }
                        Button(
                            onClick = { open(t.whatsappUrl) },
                            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF25D366)),
                            modifier = Modifier.fillMaxWidth(),
                        ) { Text("Send on WhatsApp", color = Color.White, fontWeight = FontWeight.SemiBold) }
                    }
                }
                Spacer(Modifier.height(12.dp))
            }

            Row(Modifier.fillMaxWidth()) {
                OutlinedButton(onClick = { open(c.receiptUrl) }, modifier = Modifier.weight(1f)) { Text("View receipt") }
                Spacer(Modifier.width(10.dp))
                OutlinedButton(
                    onClick = {
                        val text = t?.message ?: "Receipt ${c.receiptNumber}: ${c.receiptUrl}"
                        val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text)
                        context.startActivity(Intent.createChooser(send, "Share receipt"))
                    },
                    modifier = Modifier.weight(1f),
                ) { Text("Share receipt") }
            }
            Spacer(Modifier.height(16.dp))
            Button(onClick = { vm.closeConfirmed() }, modifier = Modifier.fillMaxWidth()) { Text("Done") }
        }
    }
}

@Composable
private fun SmsStatusLine(c: ConfirmedUi) {
    val (text, color) = when (c.smsStatus) {
        "SENT" -> "SMS sent ✓" to Green
        "FAILED" -> "SMS failed${c.smsError?.let { ": $it" } ?: ""}" to Color(0xFFB91C1C)
        "DISPATCHED" -> "SMS sending…" to Warn
        "QUEUED" -> (if (c.thisPhoneSends) "SMS sending…" else "SMS waiting for the centre's SMS phone") to Warn
        null -> "SMS status unknown" to Color.Gray
        else -> "SMS ${c.smsStatus}" to Color.Gray
    }
    Text(text, color = color, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
}
