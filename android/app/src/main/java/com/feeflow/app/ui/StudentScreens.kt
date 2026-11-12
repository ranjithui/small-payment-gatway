package com.feeflow.app.ui

import android.content.Intent
import android.net.Uri
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.feeflow.app.core.Money
import com.feeflow.app.core.Student
import com.feeflow.app.core.StudentInput
import com.feeflow.app.core.Subscription

// ------------------------------------------------------------------ sign-up
@Composable
fun SignUpScreen(state: UiState, vm: AppViewModel, snackbar: SnackbarHostState) {
    BackHandler { vm.goToLogin() }
    var url by rememberSaveable { mutableStateOf(state.serverUrl) }
    var name by rememberSaveable { mutableStateOf("") }
    var email by rememberSaveable { mutableStateOf("") }
    var pw by rememberSaveable { mutableStateOf("") }
    var pw2 by rememberSaveable { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    Scaffold(snackbarHost = { SnackbarHost(snackbar) }) { pad ->
        Column(Modifier.padding(pad).padding(24.dp).fillMaxSize().imePadding().verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.Center) {
            Text("FeeFlow", color = Brand, fontWeight = FontWeight.ExtraBold, fontSize = 16.sp)
            Text("Create your account", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
            Text("30-day free trial. Next you'll set up your centre and its UPI ID.", color = Color.Gray)
            Spacer(Modifier.height(16.dp))
            OutlinedTextField(name, { name = it }, label = { Text("Your name") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(email, { email = it }, label = { Text("Email") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email))
            OutlinedTextField(pw, { pw = it }, label = { Text("Password (10+ characters)") }, singleLine = true,
                visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password))
            OutlinedTextField(pw2, { pw2 = it }, label = { Text("Confirm password") }, singleLine = true,
                visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password))
            OutlinedTextField(url, { url = it }, label = { Text("Server URL") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri))
            error?.let { Text(it, color = Bad, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 8.dp)) }
            Spacer(Modifier.height(16.dp))
            Button({ error = vm.signUp(url, name, email, pw, pw2) }, Modifier.fillMaxWidth().height(50.dp), enabled = !state.busy) {
                if (state.busy) CircularProgressIndicator(Modifier.height(20.dp), color = Color.White) else Text("Create account")
            }
            TextButton({ vm.goToLogin() }, Modifier.fillMaxWidth()) { Text("Already have an account? Sign in") }
        }
    }
}

// ------------------------------------------------------------------ trial banner
@Composable
fun TrialBanner(sub: Subscription?) {
    if (sub == null) return
    val (text, fg, bg) = when (sub.status) {
        "TRIAL" -> Triple("Free trial: ${sub.trialDaysLeft ?: 0} day${if (sub.trialDaysLeft == 1) "" else "s"} left",
            if ((sub.trialDaysLeft ?: 0) <= 5) Warn else Brand, if ((sub.trialDaysLeft ?: 0) <= 5) WarnBg else Color(0xFFE0E7FF))
        "EXPIRED" -> Triple("Free trial ended - read-only. Parents can still pay existing links. Contact FeeFlow to activate.", Bad, BadBg)
        else -> return
    }
    Text(text, color = fg, fontSize = 13.sp, fontWeight = FontWeight.SemiBold,
        modifier = Modifier.fillMaxWidth().background(bg).padding(horizontal = 14.dp, vertical = 8.dp))
}

// ------------------------------------------------------------------ students list (tab)
@Composable
fun StudentsTab(state: UiState, vm: AppViewModel) {
    val readOnly = state.subscription?.readOnly == true
    var query by rememberSaveable { mutableStateOf(state.studentQuery) }
    LaunchedEffect(query) {
        if (query != state.studentQuery) {
            kotlinx.coroutines.delay(300)
            vm.setStudentQuery(query)
        }
    }
    Scaffold(
        floatingActionButton = {
            if (!readOnly) FloatingActionButton(onClick = { vm.newStudent() }, containerColor = Brand, contentColor = Color.White) {
                Icon(Icons.Default.Add, "Add student")
            }
        },
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize()) {
            OutlinedTextField(query, { query = it }, placeholder = { Text("Search name, ID, class, mobile") }, singleLine = true,
                leadingIcon = { Icon(Icons.Default.Search, null) },
                modifier = Modifier.fillMaxWidth().padding(start = 12.dp, end = 12.dp, top = 10.dp))
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(start = 4.dp)
                .clickable { vm.setShowInactive(!state.showInactive) }) {
                Checkbox(state.showInactive, { vm.setShowInactive(it) })
                Text("Show inactive students", fontSize = 13.sp)
            }
            if (state.students.isEmpty() && !state.busy) {
                Column(Modifier.fillMaxSize().padding(32.dp), horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center) {
                    Text(if (query.isBlank()) "No students yet" else "No students match", fontWeight = FontWeight.SemiBold)
                    if (query.isBlank() && !readOnly) Text("Tap + to add your first student", color = Color.Gray)
                }
                return@Column
            }
            LazyColumn(contentPadding = PaddingValues(start = 12.dp, end = 12.dp, bottom = 88.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(state.students, key = { it.id }) { s -> StudentRow(s) { vm.openStudent(s.id) } }
            }
        }
    }
}

@Composable
private fun StudentRow(s: Student, onClick: () -> Unit) {
    Card(Modifier.fillMaxWidth().clickable(onClick = onClick), colors = CardDefaults.cardColors(containerColor = Color.White)) {
        Column(Modifier.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(s.name, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                s.latestFee?.let { StatusPill(it.status) }
            }
            Row {
                Text(s.studentCode + (s.classGrade?.let { " · $it" } ?: "") + if (s.active) "" else " · inactive",
                    color = Color.Gray, fontSize = 13.sp, modifier = Modifier.weight(1f))
                Text(feePlan(s), color = Color.Gray, fontSize = 12.sp)
            }
        }
    }
}

private fun feePlan(s: Student) = if (s.monthlyFeePaise != null && s.feeDueDay != null)
    "${Money.format(s.monthlyFeePaise)}/month · due ${StudentInput.ordinal(s.feeDueDay)}" else "No monthly plan"

private val GENDERS = listOf("MALE" to "Male", "FEMALE" to "Female", "OTHER" to "Other")

// ------------------------------------------------------------------ student profile
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun StudentDetailScreen(state: UiState, vm: AppViewModel, snackbar: SnackbarHostState) {
    BackHandler { vm.back() }
    val context = LocalContext.current
    val s = state.student
    val readOnly = state.subscription?.readOnly == true
    var confirmDeactivate by remember { mutableStateOf(false) }

    // Share: hand the message to Android's share sheet (WhatsApp, SMS app, ...).
    LaunchedEffect(state.shareText) {
        val text = state.shareText ?: return@LaunchedEffect
        val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text)
        context.startActivity(Intent.createChooser(send, "Send fee link"))
        vm.shareHandled()
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                title = { Text(s?.name ?: "Student") },
                navigationIcon = { IconButton(onClick = { vm.back() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") } },
                actions = { if (s != null && !readOnly) TextButton({ vm.editStudent() }) { Text("Edit") } },
            )
        },
    ) { pad ->
        if (s == null) {
            Column(Modifier.padding(pad).fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
                CircularProgressIndicator()
            }
            return@Scaffold
        }
        Column(Modifier.padding(pad).fillMaxSize().verticalScroll(rememberScrollState())) {
            if (state.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            TrialBanner(state.subscription)
            Column(Modifier.padding(14.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(s.studentCode, color = Color.Gray, modifier = Modifier.weight(1f))
                    if (!s.active) StatusPill("INACTIVE")
                }
                Spacer(Modifier.height(8.dp))
                Card(colors = CardDefaults.cardColors(containerColor = Color.White)) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        s.classGrade?.let { Kv("Class / grade", it) }
                        s.school?.let { Kv("School", it) }
                        s.dateOfBirth?.let { Kv("Date of birth", it) }
                        s.gender?.let { g -> Kv("Gender", GENDERS.firstOrNull { it.first == g }?.second ?: g) }
                        s.joinedOn?.let { Kv("Joined on", it) }
                        s.address?.let { Kv("Address", it) }
                        Kv("Parent", s.parentName ?: "-")
                        Kv("Parent mobile", s.parentMobile)
                        s.parentEmail?.let { Kv("Parent email", it) }
                        Kv("Monthly fee", feePlan(s))
                        Kv("Outstanding", Money.format(s.outstandingPaise), big = true)
                    }
                }
                Spacer(Modifier.height(14.dp))
                Button({ vm.sendFeeLink("SMS") }, Modifier.fillMaxWidth().height(48.dp), enabled = s.active && !readOnly && !state.busy) {
                    Text("Send fee link (SMS)")
                }
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton({ vm.sendFeeLink("SHARE") }, Modifier.weight(1f), enabled = s.active && !readOnly && !state.busy) {
                        Icon(Icons.Default.Share, null, Modifier.height(18.dp)); Spacer(Modifier.padding(start = 6.dp)); Text("Share link")
                    }
                    OutlinedButton({
                        val link = state.lastLink
                        val text = link?.shareText ?: "Dear Parent, regarding ${s.name}'s fee"
                        val uri = Uri.parse("https://wa.me/${s.parentMobile.removePrefix("+")}?text=" + Uri.encode(text))
                        context.startActivity(Intent(Intent.ACTION_VIEW, uri))
                    }, Modifier.weight(1f), enabled = state.lastLink != null) { Text("WhatsApp parent") }
                }
                if (state.lastLink == null) {
                    Text("Tip: tap 'Share link' first, then 'WhatsApp parent' opens the chat with the message ready.",
                        fontSize = 12.sp, color = Color.Gray, modifier = Modifier.padding(top = 4.dp))
                }
                state.lastLink?.let { r ->
                    Card(colors = CardDefaults.cardColors(containerColor = if (r.smsPhoneActive || r.channel == "SHARE") OkBg else WarnBg),
                        modifier = Modifier.padding(top = 10.dp)) {
                        Column(Modifier.padding(12.dp)) {
                            Text("${r.fee.description} ${r.fee.billingMonth}: ${Money.format(r.fee.outstandingPaise)} due ${r.fee.dueDate}",
                                fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
                            Text(when {
                                r.channel == "SHARE" -> "Link ready to share."
                                r.smsPhoneActive -> "SMS queued for ${r.parentMobile}."
                                else -> "SMS queued, but no SMS phone is registered yet (SMS phone tab)."
                            }, fontSize = 13.sp)
                            Text(r.paymentLink, fontSize = 11.sp, color = Color.Gray)
                        }
                    }
                }
                Text("Fee history", fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 18.dp))
                HorizontalDivider(Modifier.padding(vertical = 6.dp))
                if (s.fees.isEmpty()) Text("No fees yet. Add a monthly fee plan with Edit.", color = Color.Gray, fontSize = 13.sp)
                s.fees.forEach { f ->
                    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text("${f.billingMonth} · ${f.description}", fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                            Text("Due ${f.dueDate} · ${Money.format(f.totalPaise)} · paid ${Money.format(f.paidPaise)}", color = Color.Gray, fontSize = 12.sp)
                            f.receipts.forEach { r ->
                                Text("Receipt ${r.receiptNumber}", color = Brand, fontSize = 12.sp,
                                    modifier = Modifier.clickable { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(r.url))) })
                            }
                        }
                        StatusPill(f.status)
                    }
                }
                Spacer(Modifier.height(18.dp))
                if (!readOnly) {
                    TextButton({ if (s.active) confirmDeactivate = true else vm.setStudentActive(true) }, Modifier.fillMaxWidth()) {
                        Text(if (s.active) "Deactivate student" else "Reactivate student", color = if (s.active) Bad else Brand)
                    }
                }
            }
        }
    }
    if (confirmDeactivate && s != null) {
        AlertDialog(
            onDismissRequest = { confirmDeactivate = false },
            title = { Text("Deactivate ${s.name}?") },
            text = { Text("No new monthly fees will be created. Existing fees and receipts stay.") },
            confirmButton = { Button({ confirmDeactivate = false; vm.setStudentActive(false) },
                colors = ButtonDefaults.buttonColors(containerColor = Bad)) { Text("Deactivate") } },
            dismissButton = { TextButton({ confirmDeactivate = false }) { Text("Cancel") } },
        )
    }
}

// ------------------------------------------------------------------ add / edit student
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun StudentFormScreen(state: UiState, id: String?, vm: AppViewModel, snackbar: SnackbarHostState) {
    BackHandler { vm.back() }
    val start = remember(id) { if (id != null && state.student != null) StudentFormInput.from(state.student) else StudentFormInput() }
    var f by remember(id) { mutableStateOf(start) }
    var error by remember { mutableStateOf<String?>(null) }
    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                title = { Text(if (id == null) "Add student" else "Edit ${start.name}") },
                navigationIcon = { IconButton(onClick = { vm.back() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") } },
            )
        },
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize().imePadding().verticalScroll(rememberScrollState()).padding(14.dp)) {
            if (state.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            FormSection("Student")
            Field("Student name *", f.name) { f = f.copy(name = it) }
            if (id == null) Field("Student ID (blank = automatic)", f.studentCode) { f = f.copy(studentCode = it) }
            Field("Class / grade", f.classGrade) { f = f.copy(classGrade = it) }
            Field("Date of birth (YYYY-MM-DD)", f.dateOfBirth, KeyboardType.Number) { f = f.copy(dateOfBirth = it) }
            Text("Gender", fontSize = 13.sp, color = Color.Gray, modifier = Modifier.padding(top = 6.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                GENDERS.forEach { (code, label) ->
                    FilterChip(selected = f.gender == code, onClick = { f = f.copy(gender = if (f.gender == code) "" else code) },
                        label = { Text(label) })
                }
            }
            Field("School", f.school) { f = f.copy(school = it) }
            Field("Address", f.address, singleLine = false) { f = f.copy(address = it) }
            Field("Joining date (YYYY-MM-DD, blank = today)", f.joinedOn, KeyboardType.Number) { f = f.copy(joinedOn = it) }
            FormSection("Parent")
            Field("Parent name", f.parentName) { f = f.copy(parentName = it) }
            Field("Parent mobile *", f.parentMobile, KeyboardType.Phone) { f = f.copy(parentMobile = it) }
            Field("Parent email", f.parentEmail, KeyboardType.Email) { f = f.copy(parentEmail = it) }
            FormSection("Monthly fee")
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Column(Modifier.weight(1f)) { Field("Amount (Rs.)", f.monthlyFee, KeyboardType.Decimal) { f = f.copy(monthlyFee = it) } }
                Column(Modifier.weight(1f)) { Field("Due day (1-31)", f.feeDueDay, KeyboardType.Number) { f = f.copy(feeDueDay = it) } }
            }
            Text("With a fee plan, this month's fee is created when you save, and each new month's fee on the 1st.",
                fontSize = 12.sp, color = Color.Gray)
            error?.let { Text(it, color = Bad, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 8.dp)) }
            Spacer(Modifier.height(14.dp))
            Button({ error = vm.saveStudent(id, f) }, Modifier.fillMaxWidth().height(50.dp), enabled = !state.busy) {
                Text(if (id == null) "Add student" else "Save changes")
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun FormSection(title: String) {
    Text(title, fontWeight = FontWeight.Bold, color = Brand, modifier = Modifier.padding(top = 12.dp, bottom = 2.dp))
}

@Composable
private fun Field(label: String, value: String, keyboard: KeyboardType = KeyboardType.Text, singleLine: Boolean = true,
                  onChange: (String) -> Unit) {
    OutlinedTextField(value, onChange, label = { Text(label) }, singleLine = singleLine, minLines = if (singleLine) 1 else 2,
        keyboardOptions = KeyboardOptions(keyboardType = keyboard), modifier = Modifier.fillMaxWidth())
}

