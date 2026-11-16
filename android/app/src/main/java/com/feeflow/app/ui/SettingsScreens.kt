package com.feeflow.app.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
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
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

// ------------------------------------------------------------------ shared pieces
@Composable
private fun ConfigPill(status: String) {
    val (label, fg, bg) = when (status) {
        "READY" -> Triple("Ready", Ok, OkBg)
        "INCOMPLETE" -> Triple("Incomplete", Warn, WarnBg)
        else -> Triple("Check UPI ID", Bad, BadBg)
    }
    Text(label, color = fg, fontSize = 11.sp, fontWeight = FontWeight.Bold,
        modifier = Modifier.background(bg, RoundedCornerShape(50)).padding(horizontal = 10.dp, vertical = 3.dp))
}

@Composable
private fun AccountTypeChoice(selected: String, onSelect: (String) -> Unit) {
    Text("UPI account type", fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 8.dp, bottom = 4.dp))
    AccountCard("PERSONAL", selected, "Personal UPI",
        "For individual tutors and small tuition centres using their personal bank account. No business documents needed. " +
            "Parents pay by copying your UPI ID or scanning the QR.", onSelect)
    Spacer(Modifier.height(8.dp))
    AccountCard("MERCHANT", selected, "Merchant UPI",
        "For registered tuition businesses using a merchant UPI account. Adds one-tap \"Pay using UPI app\".", onSelect)
}

@Composable
private fun AccountCard(value: String, selected: String, title: String, text: String, onSelect: (String) -> Unit) {
    val on = value == selected
    Card(
        Modifier.fillMaxWidth().clickable { onSelect(value) },
        colors = CardDefaults.cardColors(containerColor = Color.White),
        border = BorderStroke(if (on) 2.dp else 1.dp, if (on) Brand else Color(0xFFE5E7EB)),
        shape = RoundedCornerShape(12.dp),
    ) {
        Row(Modifier.padding(10.dp), verticalAlignment = Alignment.Top) {
            RadioButton(selected = on, onClick = { onSelect(value) })
            Column(Modifier.padding(top = 10.dp)) {
                Text(title, fontWeight = FontWeight.Bold)
                Text(text, fontSize = 12.sp, color = Color.Gray)
            }
        }
    }
}

@Composable
private fun Field(label: String, value: String, keyboard: KeyboardType = KeyboardType.Text, onChange: (String) -> Unit) {
    OutlinedTextField(value, onChange, label = { Text(label) }, singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = keyboard), modifier = Modifier.fillMaxWidth())
}

@Composable
private fun Check(checked: Boolean, label: String, onChange: (Boolean) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().clickable { onChange(!checked) }) {
        Checkbox(checked, onChange)
        Text(label, fontSize = 14.sp)
    }
}

/** Account-type specific fields used by both setup and Settings. */
@Composable
private fun UpiFields(f: PaymentForm, confirm: Boolean, update: (PaymentForm) -> Unit) {
    Field(if (f.merchant) "Merchant display name" else "Account holder name", f.payee) { update(f.copy(payee = it)) }
    Field(if (f.merchant) "Merchant UPI ID" else "Personal UPI ID", f.upiVpa, KeyboardType.Email) { update(f.copy(upiVpa = it)) }
    if (confirm) Field("Confirm UPI ID", f.upiVpaConfirm, KeyboardType.Email) { update(f.copy(upiVpaConfirm = it)) }
    if (f.merchant) {
        Field("Business name (optional)", f.businessName) { update(f.copy(businessName = it)) }
        Field("Merchant category code", f.merchantCode, KeyboardType.Number) { update(f.copy(merchantCode = it)) }
        Text("8299 = Schools & educational services.", fontSize = 12.sp, color = Color.Gray)
    } else {
        Field("UPI-linked mobile number (optional)", f.upiMobile, KeyboardType.Phone) { update(f.copy(upiMobile = it)) }
    }
    Field("Bank name (optional)", f.bankName) { update(f.copy(bankName = it)) }
    Text("FeeFlow checks the UPI ID format only. Send yourself a Rs. 1 test payment to be sure.", fontSize = 12.sp,
        color = Color.Gray, modifier = Modifier.padding(top = 4.dp))
}

// ------------------------------------------------------------------ first sign-in: create workspace
@Composable
fun SetupScreen(state: UiState, vm: AppViewModel, snackbar: SnackbarHostState) {
    var f by remember { mutableStateOf(PaymentForm()) }
    var error by remember { mutableStateOf<String?>(null) }
    var confirming by remember { mutableStateOf(false) }
    Scaffold(snackbarHost = { SnackbarHost(snackbar) }) { pad ->
        Column(Modifier.padding(pad).padding(20.dp).fillMaxSize().imePadding().verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("FeeFlow", color = Brand, fontWeight = FontWeight.ExtraBold, fontSize = 16.sp)
            Text("Create your workspace", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
            Text("Parents pay straight to the UPI ID you enter. You can change it later in Settings.", color = Color.Gray, fontSize = 14.sp)
            AccountTypeChoice(f.accountType) { f = f.copy(accountType = it) }
            Field("Centre display name", f.centreName) { f = f.copy(centreName = it) }
            UpiFields(f, confirm = true) { f = it }
            Field("Contact phone for parents (optional)", f.contactPhone, KeyboardType.Phone) { f = f.copy(contactPhone = it) }
            error?.let { Text(it, color = Bad, fontWeight = FontWeight.SemiBold) }
            Spacer(Modifier.height(8.dp))
            Button({
                error = vm.validatePayment(f, checkConfirm = true)
                if (error == null) confirming = true
            }, Modifier.fillMaxWidth().height(50.dp), enabled = !state.busy) {
                if (state.busy) CircularProgressIndicator(Modifier.height(20.dp), color = Color.White) else Text("Create workspace")
            }
            TextButton({ vm.logout() }, Modifier.fillMaxWidth()) { Text("Sign out") }
        }
    }
    if (confirming) {
        AlertDialog(
            onDismissRequest = { confirming = false },
            title = { Text("Is this UPI ID correct?") },
            text = { Text("Parents will pay to ${f.upiVpa.trim()} (${if (f.merchant) "merchant" else "personal"} UPI).") },
            confirmButton = { Button({ confirming = false; error = vm.createWorkspace(f) }) { Text("Yes, create") } },
            dismissButton = { TextButton({ confirming = false }) { Text("Check again") } },
        )
    }
}

// ------------------------------------------------------------------ Settings (Payment Settings + centre)
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(state: UiState, vm: AppViewModel, snackbar: SnackbarHostState) {
    BackHandler { vm.back() }
    val w = state.workspace
    var f by remember(w) { mutableStateOf(w?.let { PaymentForm.from(it) } ?: PaymentForm()) }
    var error by remember { mutableStateOf<String?>(null) }
    var confirmVpa by rememberSaveable { mutableStateOf(false) }
    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(title = { Text("Settings") },
                navigationIcon = { IconButton(onClick = { vm.back() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") } })
        },
    ) { pad ->
        if (w == null) {
            Column(Modifier.padding(pad).fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center) { CircularProgressIndicator() }
            return@Scaffold
        }
        Column(Modifier.padding(pad).fillMaxSize().imePadding().verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp)) {
            if (state.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Payment Settings", fontWeight = FontWeight.Bold, fontSize = 18.sp, modifier = Modifier.weight(1f))
                ConfigPill(w.configStatus.status)
            }
            Text(w.configStatus.message, fontSize = 12.sp, color = Color.Gray)

            AccountTypeChoice(f.accountType) { f = f.copy(accountType = it) }
            UpiFields(f, confirm = false) { f = it }

            Text("Payment methods shown to parents", fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 10.dp))
            if (f.merchant) Check(f.payUpiApp, "Pay using UPI app (one-tap)") { f = f.copy(payUpiApp = it) }
            Check(f.payQr, if (f.merchant) "QR code (with amount)" else "QR code") { f = f.copy(payQr = it) }
            Check(f.payCopy, "Copy UPI ID") { f = f.copy(payCopy = it) }
            if (!f.merchant) {
                Text("One-tap \"Pay using UPI app\" isn't offered for personal UPI IDs - UPI apps block it.",
                    fontSize = 12.sp, color = Color.Gray)
            }

            Text("Centre", fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 10.dp))
            Field("Centre display name", f.centreName) { f = f.copy(centreName = it) }
            Field("Contact phone", f.contactPhone, KeyboardType.Phone) { f = f.copy(contactPhone = it) }
            Field("Contact email", f.contactEmail, KeyboardType.Email) { f = f.copy(contactEmail = it) }

            error?.let { Text(it, color = Bad, fontWeight = FontWeight.SemiBold) }
            Spacer(Modifier.height(8.dp))
            Button({
                error = vm.validatePayment(f, checkConfirm = false)
                if (error == null) {
                    if (!f.upiVpa.trim().equals(w.upiVpa, ignoreCase = true)) confirmVpa = true
                    else error = vm.saveSettings(f)
                }
            }, Modifier.fillMaxWidth().height(50.dp), enabled = !state.busy) { Text("Save settings") }
            Text("Receipt prefix: ${w.shortCode}. Reminders and SMS options are on the website Settings page.",
                fontSize = 12.sp, color = Color.Gray)
            Spacer(Modifier.height(24.dp))
        }
    }
    if (confirmVpa) {
        AlertDialog(
            onDismissRequest = { confirmVpa = false },
            title = { Text("Change the UPI ID?") },
            text = { Text("From now on every parent pays to ${f.upiVpa.trim()}. Open payment links update immediately.") },
            confirmButton = { Button({ confirmVpa = false; error = vm.saveSettings(f) }) { Text("Yes, change") } },
            dismissButton = { TextButton({ confirmVpa = false }) { Text("Cancel") } },
        )
    }
}
