package com.feeflow.app.ui

import android.Manifest
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.automirrored.filled.ExitToApp
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.feeflow.app.core.BankMatch
import com.feeflow.app.core.Declaration
import com.feeflow.app.core.Money
import com.feeflow.app.core.Times
import com.feeflow.app.data.FeeFlowClient
import java.time.Instant

@Composable
fun FeeFlowRoot(vm: AppViewModel) {
    val state by vm.state.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    LaunchedEffect(state.error, state.message) {
        val text = state.error ?: state.message
        if (text != null) {
            snackbar.showSnackbar(text)
            vm.clearMessages()
        }
    }
    when (val s = state.screen) {
        Screen.Login -> LoginScreen(state, vm, snackbar)
        is Screen.Main -> MainScreen(state, s.tab, vm, snackbar)
        is Screen.Detail -> DetailScreen(state, vm, snackbar)
    }
}

// ------------------------------------------------------------------ login
@Composable
private fun LoginScreen(state: UiState, vm: AppViewModel, snackbar: SnackbarHostState) {
    var url by rememberSaveable { mutableStateOf(state.serverUrl) }
    var email by rememberSaveable { mutableStateOf("") }
    var password by rememberSaveable { mutableStateOf("") }
    Scaffold(snackbarHost = { SnackbarHost(snackbar) }) { pad ->
        Column(
            Modifier.padding(pad).padding(24.dp).fillMaxSize().imePadding().verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.Center,
        ) {
            Text("FeeFlow", color = Brand, fontWeight = FontWeight.ExtraBold, fontSize = 16.sp)
            Text("Admin sign in", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
            Text("Verify parent payments and run the centre's SMS phone.", color = Color.Gray)
            Spacer(Modifier.height(20.dp))
            OutlinedTextField(url, { url = it }, label = { Text("Server URL") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri))
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(email, { email = it }, label = { Text("Email") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email))
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(password, { password = it }, label = { Text("Password") }, singleLine = true,
                visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password))
            Spacer(Modifier.height(16.dp))
            Button(
                onClick = { vm.login(url, email, password) },
                enabled = !state.busy && email.isNotBlank() && password.isNotBlank(),
                modifier = Modifier.fillMaxWidth().height(50.dp),
            ) { if (state.busy) CircularProgressIndicator(Modifier.width(20.dp), color = Color.White) else Text("Sign in") }
        }
    }
}

// ------------------------------------------------------------------ main (tabs)
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun MainScreen(state: UiState, tab: Tab, vm: AppViewModel, snackbar: SnackbarHostState) {
    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(state.centreName ?: "FeeFlow", fontWeight = FontWeight.Bold, fontSize = 18.sp)
                        Text(state.adminName ?: "", fontSize = 12.sp, color = Color.White.copy(alpha = .8f))
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = Brand, titleContentColor = Color.White, actionIconContentColor = Color.White),
                actions = {
                    IconButton(onClick = { if (tab == Tab.VERIFY) vm.refreshPending() else vm.refreshDevice() }) {
                        Icon(Icons.Default.Refresh, "Refresh")
                    }
                    IconButton(onClick = { vm.logout() }) { Icon(Icons.AutoMirrored.Filled.ExitToApp, "Sign out") }
                },
            )
        },
        bottomBar = {
            NavigationBar {
                NavigationBarItem(
                    selected = tab == Tab.VERIFY, onClick = { vm.selectTab(Tab.VERIFY) },
                    icon = { Icon(Icons.AutoMirrored.Filled.List, null) },
                    label = { Text("Verify (${state.pending.size})") },
                )
                NavigationBarItem(
                    selected = tab == Tab.SMS_DEVICE, onClick = { vm.selectTab(Tab.SMS_DEVICE) },
                    icon = { Icon(Icons.AutoMirrored.Filled.Send, null) },
                    label = { Text("SMS phone") },
                )
            }
        },
    ) { pad ->
        Column(Modifier.padding(pad)) {
            if (state.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            when (tab) {
                Tab.VERIFY -> PendingList(state, vm)
                Tab.SMS_DEVICE -> DeviceScreen(state, vm)
            }
        }
    }
}

@Composable
private fun PendingList(state: UiState, vm: AppViewModel) {
    if (state.pending.isEmpty() && !state.busy) {
        Column(Modifier.fillMaxSize().padding(32.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
            Icon(Icons.Default.CheckCircle, null, tint = Ok, modifier = Modifier.width(48.dp).height(48.dp))
            Text("Nothing to verify right now", fontWeight = FontWeight.SemiBold)
        }
        return
    }
    LazyColumn(contentPadding = PaddingValues(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        items(state.pending, key = { it.id }) { d ->
            Card(
                Modifier.fillMaxWidth().clickable { vm.openDetail(d.id) },
                colors = CardDefaults.cardColors(containerColor = Color.White),
            ) {
                Column(Modifier.padding(14.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(d.studentName, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                        StatusPill(d.verificationStatus)
                    }
                    Text("${d.studentId} · ${d.installment.description} ${d.installment.billingMonth}", color = Color.Gray, fontSize = 13.sp)
                    Row(Modifier.padding(top = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text("Declared ", fontSize = 14.sp)
                        Text(Money.format(d.declaredAmountPaise), fontWeight = FontWeight.Bold)
                        if (d.paymentType == "PARTIAL") Text(" (partial)", color = Warn, fontSize = 13.sp)
                        Spacer(Modifier.weight(1f))
                        Text(Times.display(d.declaredAt ?: d.createdAt), color = Color.Gray, fontSize = 12.sp)
                    }
                    if (d.verificationOverdue) Text("Past verification grace period", color = Bad, fontSize = 12.sp)
                    if (d.clarificationRequest != null) Text("Clarification requested", color = Warn, fontSize = 12.sp)
                }
            }
        }
    }
}

// ------------------------------------------------------------------ detail
private enum class Action { NONE, CONFIRM, REJECT, CLARIFY }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DetailScreen(state: UiState, vm: AppViewModel, snackbar: SnackbarHostState) {
    BackHandler { vm.back() }
    var action by remember { mutableStateOf(Action.NONE) }
    var match by remember { mutableStateOf<BankMatch?>(null) }
    val d = state.detail
    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                title = { Text(d?.studentName ?: "Payment confirmation") },
                navigationIcon = { IconButton(onClick = { vm.back() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") } },
            )
        },
    ) { pad ->
        if (d == null) {
            Column(Modifier.padding(pad).fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
                CircularProgressIndicator()
            }
            return@Scaffold
        }
        Column(Modifier.padding(pad).fillMaxSize().verticalScroll(rememberScrollState()).padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("${d.studentId} · ${d.reference}", color = Color.Gray, modifier = Modifier.weight(1f))
                StatusPill(d.verificationStatus)
            }
            Spacer(Modifier.height(8.dp))
            Card(colors = CardDefaults.cardColors(containerColor = Color.White)) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Kv("Parent mobile", d.parentMobile)
                    Kv("Installment", "${d.installment.description} · ${d.installment.billingMonth}")
                    Kv("Due date", d.installment.dueDate)
                    Kv("Expected amount", Money.format(d.expectedAmountPaise))
                    Kv("Declared amount", Money.format(d.declaredAmountPaise) + if (d.paymentType == "PARTIAL") " (partial)" else "", big = true)
                    Kv("Declared at", Times.display(d.declaredAt))
                    Kv("UPI app", d.upiApp ?: "Not specified")
                    Kv("Approx. payment time", d.approximatePaymentTime ?: "-")
                    d.parentUpiReference?.let { Kv("Parent's UPI ref", it) }
                    Kv("Total fee / paid", "${Money.format(d.installment.totalPaise)} / ${Money.format(d.installment.paidPaise)}")
                    Kv("Outstanding", Money.format(d.outstandingPaise))
                }
            }
            d.parentNotes?.let { Section("Parent notes") { Text(it, fontSize = 13.sp) } }
            d.clarificationRequest?.let {
                Spacer(Modifier.height(10.dp))
                Text("Waiting for parent: $it", color = Warn, fontWeight = FontWeight.SemiBold)
            }
            if (d.suggestedBankMatches.isNotEmpty()) {
                Section("Possible matches from bank statement") {
                    d.suggestedBankMatches.forEach { m ->
                        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 4.dp)) {
                            Column(Modifier.weight(1f)) {
                                Text("${Money.format(m.amountPaise)} · ${m.bankReference}", fontWeight = FontWeight.SemiBold)
                                Text("${Times.display(m.creditedAt)} ${m.payerVpa ?: ""}", fontSize = 12.sp, color = Color.Gray)
                            }
                            TextButton(onClick = { match = m; action = Action.CONFIRM }) { Text("Use") }
                        }
                    }
                }
            }
            Section("Payment history") {
                if (d.verifiedPayments.isEmpty() && d.studentPaymentHistory.isEmpty()) Text("No verified payments yet.", color = Color.Gray, fontSize = 13.sp)
                d.verifiedPayments.forEach { Kv("This fee ${it.bankReference ?: ""}", Money.format(it.amountPaise)) }
                d.studentPaymentHistory.forEach { Kv("${it.installment} ${it.billingMonth}", Money.format(it.amountPaise)) }
                if (d.previousDeclarations.isNotEmpty()) {
                    Text("Earlier confirmations: " + d.previousDeclarations.joinToString { "${it.status.replace('_', ' ')} ${Money.format(it.declaredAmountPaise)}" },
                        fontSize = 12.sp, color = Color.Gray)
                }
            }
            Spacer(Modifier.height(16.dp))
            Button({ match = null; action = Action.CONFIRM }, Modifier.fillMaxWidth().height(48.dp), enabled = !state.busy,
                colors = ButtonDefaults.buttonColors(containerColor = Ok)) { Text("Confirm payment received") }
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button({ action = Action.REJECT }, Modifier.weight(1f), enabled = !state.busy,
                    colors = ButtonDefaults.buttonColors(containerColor = Bad)) { Text("Not received") }
                OutlinedButton({ action = Action.CLARIFY }, Modifier.weight(1f), enabled = !state.busy) { Text("Ask parent") }
            }
            Spacer(Modifier.height(24.dp))
        }
    }
    when (action) {
        Action.CONFIRM -> ConfirmDialog(d!!, match, vm) { action = Action.NONE }
        Action.REJECT -> TextActionDialog(
            title = "Payment not received",
            hint = "Reason (internal), e.g. no matching credit in bank statement",
            explain = "The parent will be told the payment could not be verified and can submit a new confirmation.",
            confirmLabel = "Mark not received",
            initial = "",
            onSubmit = { text, key -> vm.reject(d!!, text, key) },
            onDismiss = { action = Action.NONE },
        )
        Action.CLARIFY -> TextActionDialog(
            title = "Ask the parent",
            hint = "Question (sent by SMS)",
            explain = "The installment stays unpaid until you verify the payment.",
            confirmLabel = "Send",
            initial = "Could you tell us which UPI app you used and roughly what time you paid?",
            onSubmit = { text, key -> vm.requestClarification(d!!, text, key) },
            onDismiss = { action = Action.NONE },
        )
        Action.NONE -> Unit
    }
}

@Composable
private fun Kv(label: String, value: String, big: Boolean = false) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = Color.Gray, fontSize = 14.sp, modifier = Modifier.weight(1f))
        Text(value, fontWeight = FontWeight.SemiBold, fontSize = if (big) 20.sp else 14.sp)
    }
}

@Composable
private fun Section(title: String, content: @Composable () -> Unit) {
    Spacer(Modifier.height(14.dp))
    Text(title, fontWeight = FontWeight.Bold)
    HorizontalDivider(Modifier.padding(vertical = 6.dp))
    content()
}

@Composable
private fun ConfirmDialog(d: Declaration, match: BankMatch?, vm: AppViewModel, onDismiss: () -> Unit) {
    // One idempotency key per opened form: a double tap or retry can't verify twice.
    val key = remember { FeeFlowClient.newIdempotencyKey() }
    val amounts = remember { mutableStateListOf(Money.toInput(match?.amountPaise ?: d.declaredAmountPaise)) }
    val refs = remember { mutableStateListOf(match?.bankReference ?: "") }
    var evidence by remember { mutableStateOf("") }
    var note by remember { mutableStateOf("") }
    var checked by remember { mutableStateOf(false) }
    var mine by remember { mutableStateOf(false) }
    var diffAck by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var reallySure by remember { mutableStateOf(false) }
    val total = amounts.sumOf { Money.parseToPaise(it) ?: 0L }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Confirm payment received") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                Text("Open your bank / UPI merchant app and find the actual credit first.", fontSize = 13.sp, color = Color.Gray)
                amounts.indices.forEach { i ->
                    Spacer(Modifier.height(6.dp))
                    Text("Transaction ${i + 1}", fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
                    OutlinedTextField(amounts[i], { amounts[i] = it }, label = { Text("Amount received (Rs.)") }, singleLine = true,
                        readOnly = match != null && i == 0, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal), modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(refs[i], { refs[i] = it }, label = { Text("Bank reference / UTR") }, singleLine = true,
                        readOnly = match != null && i == 0, modifier = Modifier.fillMaxWidth())
                }
                if (amounts.size < 5) TextButton({ amounts.add(""); refs.add("") }) { Text("+ Parent paid more than once") }
                OutlinedTextField(evidence, { evidence = it }, label = { Text("Evidence note (if no UTR)") }, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(note, { note = it }, label = { Text("Internal note (optional)") }, modifier = Modifier.fillMaxWidth())
                CheckRow(checked, { checked = it }, "I checked the actual bank transaction")
                CheckRow(mine, { mine = it }, "The money was received in our centre's account")
                if (total != d.declaredAmountPaise) {
                    CheckRow(diffAck, { diffAck = it },
                        "Verified ${Money.format(total)} differs from declared ${Money.format(d.declaredAmountPaise)} - the verified amount is correct")
                }
                error?.let { Text(it, color = Bad, fontWeight = FontWeight.SemiBold) }
                if (reallySure) {
                    Text("Verify ${Money.format(total)} for ${d.studentName}? This issues a receipt and notifies the parent.",
                        color = Ok, fontWeight = FontWeight.SemiBold)
                }
            }
        },
        confirmButton = {
            Button(colors = ButtonDefaults.buttonColors(containerColor = Ok), onClick = {
                if (!reallySure) {
                    reallySure = true
                    return@Button
                }
                error = vm.confirm(
                    d,
                    ConfirmInput(amounts.zip(refs), match?.bankTransactionId, evidence, note, checked, mine, diffAck),
                    key,
                )
                if (error == null) onDismiss() else reallySure = false
            }) { Text(if (reallySure) "Yes, verify" else "Verify payment") }
        },
        dismissButton = { TextButton(onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun CheckRow(checked: Boolean, onChange: (Boolean) -> Unit, label: String) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.clickable { onChange(!checked) }) {
        Checkbox(checked, onChange)
        Text(label, fontSize = 13.sp)
    }
}

@Composable
private fun TextActionDialog(
    title: String,
    hint: String,
    explain: String,
    confirmLabel: String,
    initial: String,
    onSubmit: (String, String) -> String?,
    onDismiss: () -> Unit,
) {
    val key = remember { FeeFlowClient.newIdempotencyKey() }
    var text by remember { mutableStateOf(initial) }
    var error by remember { mutableStateOf<String?>(null) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            Column {
                OutlinedTextField(text, { text = it }, label = { Text(hint) }, minLines = 2, modifier = Modifier.fillMaxWidth())
                Text(explain, fontSize = 12.sp, color = Color.Gray, modifier = Modifier.padding(top = 6.dp))
                error?.let { Text(it, color = Bad) }
            }
        },
        confirmButton = {
            Button({
                error = onSubmit(text, key)
                if (error == null) onDismiss()
            }) { Text(confirmLabel) }
        },
        dismissButton = { TextButton(onDismiss) { Text("Cancel") } },
    )
}

// ------------------------------------------------------------------ SMS device
@Composable
private fun DeviceScreen(state: UiState, vm: AppViewModel) {
    val dev = state.device
    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { vm.onPermissionResult() }
    var confirmRegister by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(14.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Card(colors = CardDefaults.cardColors(containerColor = Color.White)) {
            Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("SIM SMS engine", fontWeight = FontWeight.Bold, fontSize = 17.sp)
                Text("Parents' acknowledgement, verified/rejected messages and fee reminders are sent from this phone's own SIM. No SMS gateway is used.",
                    fontSize = 13.sp, color = Color.Gray)
                Kv("This phone", if (dev.registered) "Registered SMS device" else "Not the SMS device")
                dev.deviceName?.let { Kv("Device name", it) }
                Kv("SMS permission", if (dev.permissionGranted) "Granted" else "Not granted")
                if (dev.registered) {
                    Kv("Last successful sync", if (dev.lastSyncAtMs != null) Times.display(Instant.ofEpochMilli(dev.lastSyncAtMs)) else "Never")
                    Kv("Reminders scheduled on phone", dev.scheduledReminders.toString())
                    Kv("Paused (payment being verified)", dev.heldUntilVerified.toString())
                    Kv("Results waiting to upload", dev.pendingAcks.toString())
                    dev.lastSyncError?.let { Text("Last sync failed: $it", color = Bad, fontSize = 13.sp) }
                }
                dev.serverSmsWaiting?.let { Kv("Server: SMS waiting for phone", it.toString()) }
                dev.serverPendingSync?.let { Kv("Server: schedule changes not yet on phone", it.toString()) }
            }
        }
        if (!dev.permissionGranted) {
            Button({ permissionLauncher.launch(Manifest.permission.SEND_SMS) }, Modifier.fillMaxWidth()) { Text("Allow sending SMS") }
        }
        if (dev.registered) {
            Button({ vm.syncNow() }, Modifier.fillMaxWidth().height(48.dp), enabled = !dev.syncing) {
                Text(if (dev.syncing) "Syncing…" else "Sync & send now")
            }
            dev.lastReport?.let { r ->
                Card(colors = CardDefaults.cardColors(containerColor = if (r.synced) OkBg else BadBg)) {
                    Column(Modifier.padding(12.dp)) {
                        Text(if (r.synced) "Synced with FeeFlow" else "Could not reach FeeFlow: ${r.syncError}", fontWeight = FontWeight.SemiBold)
                        Text("Messages sent: ${r.smsSent} · failed: ${r.smsFailed} · reminders sent: ${r.remindersSent}", fontSize = 13.sp)
                        if (r.remindersHeldStale > 0) Text("${r.remindersHeldStale} reminder(s) held: schedule not synced in 24h (might have been cancelled).", fontSize = 13.sp, color = Warn)
                        r.ackError?.let { Text("Upload of results failed, will retry: $it", fontSize = 13.sp, color = Warn) }
                    }
                }
            }
            OutlinedButton({ vm.stopBeingSmsDevice() }, Modifier.fillMaxWidth()) { Text("Stop using this phone for SMS") }
        } else if (state.adminName != null) {
            Button({ confirmRegister = true }, Modifier.fillMaxWidth().height(48.dp), enabled = !state.busy) { Text("Use this phone as the SMS device") }
        }
        Text("The phone syncs every 15 minutes in the background. Before sending any reminder it re-checks the latest schedule it knows, so reminders for fees already being verified or paid are not sent.",
            fontSize = 12.sp, color = Color.Gray)
    }
    if (confirmRegister) {
        AlertDialog(
            onDismissRequest = { confirmRegister = false },
            title = { Text("Use this phone for SMS?") },
            text = { Text("Messages will be sent from this phone's SIM (normal SMS charges apply). Any previously registered SMS phone for your centre will stop sending.") },
            confirmButton = {
                Button({
                    confirmRegister = false
                    if (!dev.permissionGranted) permissionLauncher.launch(Manifest.permission.SEND_SMS)
                    vm.registerThisPhone()
                }) { Text("Register") }
            },
            dismissButton = { TextButton({ confirmRegister = false }) { Text("Cancel") } },
        )
    }
}
