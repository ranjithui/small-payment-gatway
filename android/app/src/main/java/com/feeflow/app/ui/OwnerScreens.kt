package com.feeflow.app.ui

import android.content.Intent
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardColors
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.feeflow.app.BuildConfig
import com.feeflow.app.core.BulkFeeRequest
import com.feeflow.app.core.Money
import com.feeflow.app.core.PaymentSearch
import com.feeflow.app.core.Times
import com.feeflow.app.data.FeeFlowClient
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.longOrNull
import java.time.LocalDate
import java.time.YearMonth

private val White: CardColors @Composable get() = CardDefaults.cardColors(containerColor = Color.White)

@Composable
private fun Stat(label: String, value: String, color: Color = Color.Unspecified, modifier: Modifier = Modifier, onClick: (() -> Unit)? = null) {
    Card(modifier.then(if (onClick != null) Modifier.clickable { onClick() } else Modifier), colors = White) {
        Column(Modifier.padding(12.dp)) {
            Text(label, fontSize = 12.sp, color = Color.Gray, maxLines = 1)
            Text(value, fontSize = 20.sp, fontWeight = FontWeight.ExtraBold, color = color, maxLines = 1)
        }
    }
}

// ================================================================== Home
@Composable
fun HomeTab(state: UiState, vm: AppViewModel) {
    val d = state.dashboard
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (d == null) { Text("Loading…", color = Color.Gray); return@Column }
        val s = d.stats
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton({ vm.changeMonth(-1) }) { Icon(Icons.AutoMirrored.Filled.KeyboardArrowLeft, "Previous month") }
            Text(d.monthLabel, fontWeight = FontWeight.Bold, fontSize = 17.sp, modifier = Modifier.weight(1f))
            IconButton({ vm.changeMonth(1) }) { Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, "Next month") }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Stat("Collected", Money.format(s.collectedPaise), Ok, Modifier.weight(1f))
            Stat("Pending", Money.format(s.outstandingPaise), if (s.outstandingPaise > 0) Warn else Color.Unspecified, Modifier.weight(1f))
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Stat("Collected today", Money.format(s.collectedTodayPaise), Ok, Modifier.weight(1f))
            Stat("Fees generated", "${Money.format(s.feesGeneratedPaise)} (${s.feesCount})", modifier = Modifier.weight(1f))
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Stat("To verify", "${s.awaitingVerification}", if (s.awaitingVerification > 0) Bad else Color.Unspecified, Modifier.weight(1f)) { vm.selectTab(Tab.PAYMENTS) }
            Stat("Overdue", "${s.overdueStudents}", if (s.overdueStudents > 0) Bad else Color.Unspecified, Modifier.weight(1f)) { vm.openBulkReminders("OVERDUE") }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Stat("Students", "${s.studentsTotal}", modifier = Modifier.weight(1f)) { vm.selectTab(Tab.STUDENTS) }
            Stat("Failed SMS", "${s.smsFailed}", if (s.smsFailed > 0) Bad else Color.Unspecified, Modifier.weight(1f)) { vm.openSms() }
        }

        Card(colors = White) {
            Column(Modifier.padding(14.dp)) {
                Text("Pending actions", fontWeight = FontWeight.Bold, fontSize = 16.sp)
                if (d.pendingActions.isEmpty()) Text("All caught up. Nothing needs your attention right now.", color = Color.Gray, fontSize = 13.sp)
                d.pendingActions.forEachIndexed { i, a ->
                    if (i > 0) HorizontalDivider()
                    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text("●", color = when (a.tone) { "bad" -> Bad; "info" -> Brand; else -> Warn }, fontSize = 12.sp)
                        Spacer(Modifier.width(8.dp))
                        Text(a.text, Modifier.weight(1f), fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                        TextButton({ vm.runPendingAction(a.kind) }, enabled = !state.busy) { Text(a.button) }
                    }
                }
            }
        }

        Text("Quick actions", fontWeight = FontWeight.Bold, fontSize = 16.sp)
        val quick = listOf(
            "+ Add student" to { vm.newStudent() },
            "Generate fee" to { vm.openBulkFees() },
            "Collect fee" to { vm.selectTab(Tab.STUDENTS) },
            "Send reminders" to { vm.openBulkReminders("OVERDUE") },
            "Verify payments" to { vm.selectTab(Tab.PAYMENTS) },
            "View reports" to { vm.selectTab(Tab.REPORTS) },
        )
        quick.chunked(2).forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                row.forEach { (label, go) -> OutlinedButton(onClick = go, modifier = Modifier.weight(1f)) { Text(label, maxLines = 1) } }
            }
        }

        if (d.recentPayments.isNotEmpty()) {
            Card(colors = White) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text("Recent payments", fontWeight = FontWeight.Bold, fontSize = 16.sp)
                    d.recentPayments.forEach { p ->
                        Row {
                            Column(Modifier.weight(1f)) {
                                Text(p.studentName, fontWeight = FontWeight.SemiBold)
                                Text("${p.description} · ${p.billingMonth}", fontSize = 12.sp, color = Color.Gray)
                            }
                            Text(Money.format(p.amountPaise), fontWeight = FontWeight.Bold, color = Ok)
                        }
                    }
                }
            }
        }
    }
}

// ================================================================== Payments: search + queue + overpayments
@Composable
fun PaymentsTab(state: UiState, vm: AppViewModel) {
    var q by remember { mutableStateOf(state.paymentSearch.query) }
    var status by remember { mutableStateOf(state.paymentSearch.status) }
    var cls by remember { mutableStateOf(state.paymentSearch.classGrade) }
    var amount by remember { mutableStateOf(state.paymentSearch.amount) }
    var utr by remember { mutableStateOf(state.paymentSearch.utr) }
    var more by remember { mutableStateOf(false) }
    val search = { vm.searchPayments(PaymentSearch(q, status, cls, amount, utr)) }
    val list = state.searchResults ?: state.pending
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedTextField(q, { q = it }, label = { Text("Search name, ID, mobile or reference") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            listOf("PENDING" to "To verify", "VERIFIED" to "Confirmed", "REJECTED" to "Not received", "ALL" to "All").forEach { (v, l) ->
                FilterChip(status == v, { status = v; vm.searchPayments(PaymentSearch(q, v, cls, amount, utr)) }, label = { Text(l, fontSize = 12.sp) })
            }
        }
        TextButton({ more = !more }) { Text(if (more) "Fewer filters" else "More filters (class, amount, UTR)") }
        if (more) {
            val classes = state.dashboard?.classes.orEmpty()
            if (classes.isNotEmpty()) {
                Row(Modifier.horizontalScrollRow(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    FilterChip(cls.isBlank(), { cls = "" }, label = { Text("All classes") })
                    classes.forEach { c -> FilterChip(cls == c, { cls = c }, label = { Text(c) }) }
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(amount, { amount = it }, label = { Text("Amount (Rs.)") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal), modifier = Modifier.weight(1f))
                OutlinedTextField(utr, { utr = it }, label = { Text("Txn ID / UTR") }, singleLine = true, modifier = Modifier.weight(1f))
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(search, Modifier.weight(1f)) { Text("Search") }
            OutlinedButton({ q = ""; status = "PENDING"; cls = ""; amount = ""; utr = ""; vm.searchPayments(PaymentSearch()) }, Modifier.weight(1f)) { Text("Clear") }
        }

        if (state.adjustments.isNotEmpty() && state.searchResults == null) {
            Card(colors = White) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text("Overpayments to sort out", fontWeight = FontWeight.Bold)
                    state.adjustments.forEach { a ->
                        val key = remember(a.id) { FeeFlowClient.newIdempotencyKey() }
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text("${a.studentName} paid ${Money.format(a.amountPaise)} extra", fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                                Text("${a.description} · ${a.billingMonth}", fontSize = 12.sp, color = Color.Gray)
                            }
                            TextButton({ vm.applyExcess(a.id, key) }, enabled = !state.busy) { Text("Apply to next fee") }
                        }
                    }
                    Text("Refunds can be recorded on the website (Fees tab).", fontSize = 12.sp, color = Color.Gray)
                }
            }
        }

        Text(if (state.searchResults == null) "Waiting to verify (${state.pending.size})" else "${list.size} result(s)",
            fontWeight = FontWeight.Bold, fontSize = 15.sp)
        if (list.isEmpty() && !state.busy) {
            Text(if (state.searchResults == null) "Nothing to verify right now." else "No payments match.", color = Color.Gray)
        }
        list.forEach { d -> PaymentCard(d, vm) }
    }
}

@Composable
private fun Modifier.horizontalScrollRow(): Modifier = this.then(Modifier.horizontalScroll(rememberScrollState()))

// ================================================================== Reports
private val REPORTS = listOf(
    "outstanding-fees" to "Outstanding fees",
    "verified-payments" to "Confirmed payments",
    "pending-verification" to "Waiting to verify",
    "partial-payments" to "Part payments",
    "excess-payments" to "Overpayments",
    "rejected-declarations" to "Not received",
)

private fun JsonObject.str(k: String): String? = (this[k] as? JsonPrimitive)?.contentOrNull
private fun JsonObject.paise(k: String): Long? = (this[k] as? JsonPrimitive)?.longOrNull

@Composable
fun ReportsTab(state: UiState, vm: AppViewModel) {
    val context = LocalContext.current
    val d = state.dashboard
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (d != null) {
            Text("${d.monthLabel} summary", fontWeight = FontWeight.Bold, fontSize = 16.sp)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Stat("Generated", Money.format(d.stats.feesGeneratedPaise), modifier = Modifier.weight(1f))
                Stat("Collected", Money.format(d.stats.collectedPaise), Ok, Modifier.weight(1f))
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Stat("Pending", Money.format(d.stats.outstandingPaise), Warn, Modifier.weight(1f))
                Stat("Fully paid", "${d.stats.paidCount} of ${d.stats.feesCount}", modifier = Modifier.weight(1f))
            }
        }
        Row(Modifier.horizontalScrollRow(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            REPORTS.forEach { (k, l) -> FilterChip(state.reportName == k, { vm.loadReport(k) }, label = { Text(l, fontSize = 12.sp) }) }
        }
        val rows = state.reportRows
        if (rows == null) { Text("Loading…", color = Color.Gray); return@Column }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("${rows.size} record(s)", Modifier.weight(1f), color = Color.Gray)
            if (rows.isNotEmpty()) TextButton({
                val text = toCsv(rows)
                val send = Intent(Intent.ACTION_SEND).setType("text/plain")
                    .putExtra(Intent.EXTRA_SUBJECT, "FeeFlow ${state.reportName}").putExtra(Intent.EXTRA_TEXT, text)
                context.startActivity(Intent.createChooser(send, "Share report"))
            }) { Text("Share (CSV)") }
        }
        rows.take(200).forEach { r ->
            Card(Modifier.fillMaxWidth(), colors = White) {
                Column(Modifier.padding(12.dp)) {
                    Row {
                        Text(r.str("student_name") ?: "-", Modifier.weight(1f), fontWeight = FontWeight.SemiBold)
                        val amt = r.paise("outstanding_paise") ?: r.paise("amount_paise") ?: r.paise("declared_amount_paise")
                        amt?.let { Text(Money.format(it), fontWeight = FontWeight.Bold) }
                    }
                    Text(listOfNotNull(r.str("installment"), r.str("billing_month"), r.str("status")?.replace('_', ' '),
                        r.str("parent_mobile")).joinToString(" · "), fontSize = 12.sp, color = Color.Gray)
                }
            }
        }
    }
}

private fun toCsv(rows: List<JsonObject>): String {
    val cols = rows.first().keys.toList()
    fun cell(v: String) = if (v.any { it == ',' || it == '"' || it == '\n' }) "\"" + v.replace("\"", "\"\"") + "\"" else v
    val head = cols.joinToString(",") { it.removeSuffix("_paise").let { c -> if (c != it) "${c}_rs" else c } }
    val body = rows.map { r ->
        cols.joinToString(",") { c ->
            val p = r[c] as? JsonPrimitive
            val raw = p?.contentOrNull ?: ""
            cell(if (c.endsWith("_paise") && p?.longOrNull != null) "%.2f".format(p.longOrNull!! / 100.0) else raw)
        }
    }
    return (listOf(head) + body).joinToString("\n")
}

// ================================================================== Settings
@Composable
fun SettingsTab(state: UiState, vm: AppViewModel) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        SettingRow("Payment settings", "UPI ID, personal or merchant, payment options, centre details") { vm.openSettings() }
        val failed = state.dashboard?.stats?.smsFailed ?: 0
        SettingRow("SMS service", if (failed > 0) "$failed message(s) failed - tap to retry" else "SMS phone, sent today, failed messages",
            if (failed > 0) Bad else Color.Gray) { vm.openSms() }
        SettingRow("Sign out", state.adminName ?: "") { vm.logout() }
        Text("FeeFlow ${BuildConfig.VERSION_NAME}", fontSize = 12.sp, color = Color.Gray, modifier = Modifier.padding(4.dp))
    }
}

@Composable
private fun SettingRow(title: String, subtitle: String, subColor: Color = Color.Gray, onClick: () -> Unit) {
    Card(Modifier.fillMaxWidth().clickable { onClick() }, colors = White) {
        Column(Modifier.padding(16.dp)) {
            Text(title, fontWeight = FontWeight.Bold, fontSize = 16.sp)
            if (subtitle.isNotBlank()) Text(subtitle, fontSize = 13.sp, color = subColor)
        }
    }
}

// ================================================================== Bulk reminders
private val TARGETS = listOf("OVERDUE" to "Overdue", "MONTH" to "Unpaid this month", "CLASS" to "A class", "ALL_UNPAID" to "All unpaid")

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BulkRemindersScreen(state: UiState, target: String, vm: AppViewModel, snackbar: SnackbarHostState) {
    BackHandler { vm.back() }
    var cls by remember { mutableStateOf<String?>(null) }
    val excluded = remember(state.reminderPreview) { mutableStateListOf<String>() }
    var key by remember(state.reminderPreview) { mutableStateOf(FeeFlowClient.newIdempotencyKey()) }
    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = { TopAppBar(title = { Text("Send fee reminders") }, navigationIcon = { IconButton({ vm.back() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") } }) },
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (state.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            Row(Modifier.horizontalScrollRow(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                TARGETS.forEach { (t, l) -> FilterChip(target == t, { vm.previewReminders(t, if (t == "CLASS") cls else null) }, label = { Text(l) }) }
            }
            if (target == "CLASS") {
                val classes = state.dashboard?.classes.orEmpty()
                if (classes.isEmpty()) Text("No classes yet. Add a class to your students' profiles.", color = Color.Gray)
                Row(Modifier.horizontalScrollRow(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    classes.forEach { c -> FilterChip(cls == c, { cls = c; vm.previewReminders("CLASS", c) }, label = { Text(c) }) }
                }
            }
            Text("Parents who have already reported paying are left out. One message per student, sent from the SMS phone.",
                fontSize = 12.sp, color = Color.Gray)

            val b = state.batch
            if (b != null) {
                Card(colors = White) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text(if (b.done) "Finished" else "Sending…", fontWeight = FontWeight.Bold)
                        LinearProgressIndicator(progress = { b.progressPercent / 100f }, modifier = Modifier.fillMaxWidth())
                        Text("${b.sent} sent · ${b.queued + b.dispatched} waiting · ${b.failed} failed (of ${b.total})", fontSize = 13.sp)
                        if (b.failed > 0) {
                            Text(b.failedItems.joinToString { it.studentName ?: it.toMobile }, fontSize = 12.sp, color = Bad)
                            Button({ vm.retryBatch(b.batchId) }, enabled = !state.busy) { Text("Retry failed") }
                        }
                        if (state.device.registered.not()) {
                            Text("This phone is not the SMS phone. Messages are sent when the centre's SMS phone syncs.", fontSize = 12.sp, color = Color.Gray)
                        }
                    }
                }
                return@Column
            }

            val p = state.reminderPreview ?: return@Column
            if (p.count == 0) { Text("No students to remind for this choice.", color = Color.Gray); return@Column }
            val chosen = p.count - excluded.size
            Text("$chosen student(s) will get a reminder · about ${p.smsParts} SMS part(s) each", fontWeight = FontWeight.SemiBold)
            Card(colors = White) {
                Column(Modifier.padding(6.dp)) {
                    p.recipients.forEach { r ->
                        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().clickable {
                            if (r.studentId in excluded) excluded.remove(r.studentId) else excluded.add(r.studentId)
                        }) {
                            Checkbox(r.studentId !in excluded, { on -> if (on) excluded.remove(r.studentId) else excluded.add(r.studentId) })
                            Column(Modifier.weight(1f)) {
                                Text(r.studentName, fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                                Text(listOfNotNull(r.classGrade, r.parentMobile).joinToString(" · "), fontSize = 12.sp, color = Color.Gray)
                            }
                            Column(horizontalAlignment = Alignment.End) {
                                Text(Money.format(r.amountPaise), fontWeight = FontWeight.Bold, fontSize = 14.sp)
                                if (r.overdue) Text("overdue", color = Bad, fontSize = 11.sp)
                            }
                        }
                    }
                }
            }
            p.sampleMessage?.let {
                Text("Message preview", fontWeight = FontWeight.Bold)
                Card(colors = CardDefaults.cardColors(containerColor = Color(0xFFF8FAFC))) { Text(it, Modifier.padding(10.dp), fontSize = 13.sp) }
            }
            if (!p.smsPhoneActive) Text("No SMS phone is connected. Messages will wait until it opens the FeeFlow app.", color = Warn, fontSize = 13.sp)
            Button({ vm.sendReminders(target, cls, excluded.toList(), key) }, enabled = chosen > 0 && !state.busy, modifier = Modifier.fillMaxWidth()) {
                Text("Send $chosen reminder(s)")
            }
        }
    }
}

// ================================================================== Bulk one-off fees
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BulkFeesScreen(state: UiState, vm: AppViewModel, snackbar: SnackbarHostState) {
    BackHandler { vm.back() }
    var desc by remember { mutableStateOf("") }
    var amount by remember { mutableStateOf("") }
    var month by remember { mutableStateOf(YearMonth.now().toString()) }
    var due by remember { mutableStateOf(LocalDate.now().plusDays(7).toString()) }
    var cls by remember { mutableStateOf<String?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    val excluded = remember(state.feePreview) { mutableStateListOf<String>() }
    val key = remember(state.feePreview) { FeeFlowClient.newIdempotencyKey() }
    fun request(): BulkFeeRequest? {
        val paise = Money.parseToPaise(amount)
        error = when {
            desc.trim().length < 2 -> "Enter what the fee is for"
            paise == null || paise <= 0 -> "Enter a valid amount"
            !Regex("""\d{4}-\d{2}""").matches(month) -> "Fee month must look like 2026-12"
            runCatching { LocalDate.parse(due) }.isFailure -> "Due date must look like 2026-12-10"
            else -> null
        }
        if (error != null) return null
        return BulkFeeRequest(desc.trim(), paise!!, month, due, if (cls == null) "ALL" else "CLASS", cls, excluded.toList().ifEmpty { null })
    }
    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = { TopAppBar(title = { Text("Fee for many students") }, navigationIcon = { IconButton({ vm.back() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") } }) },
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (state.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            Text("For exam fees, book fees, trips and so on. Monthly tuition fees are created automatically.", fontSize = 12.sp, color = Color.Gray)
            OutlinedTextField(desc, { desc = it }, label = { Text("What is the fee for?") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(amount, { amount = it }, label = { Text("Amount per student (Rs.)") }, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal), modifier = Modifier.fillMaxWidth())
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(month, { month = it }, label = { Text("Fee month (YYYY-MM)") }, singleLine = true, modifier = Modifier.weight(1f))
                OutlinedTextField(due, { due = it }, label = { Text("Due date (YYYY-MM-DD)") }, singleLine = true, modifier = Modifier.weight(1f))
            }
            Text("Students", fontWeight = FontWeight.SemiBold)
            Row(Modifier.horizontalScrollRow(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                FilterChip(cls == null, { cls = null }, label = { Text("All active students") })
                state.dashboard?.classes.orEmpty().forEach { c -> FilterChip(cls == c, { cls = c }, label = { Text(c) }) }
            }
            error?.let { Text(it, color = Bad, fontWeight = FontWeight.SemiBold) }
            OutlinedButton({ request()?.let { vm.previewFees(it.copy(excludeIds = null)) } }, Modifier.fillMaxWidth(), enabled = !state.busy) { Text("Preview") }

            state.feeResult?.let { r ->
                Card(colors = CardDefaults.cardColors(containerColor = Color(0xFFD1FAE5))) {
                    Column(Modifier.padding(12.dp)) {
                        Text("${r.created} fee(s) created" + if (r.skippedDuplicates > 0) ", ${r.skippedDuplicates} skipped" else "", fontWeight = FontWeight.Bold, color = Ok)
                        TextButton({ vm.openBulkReminders("ALL_UNPAID") }) { Text("Send reminders for them") }
                    }
                }
            }
            val p = state.feePreview ?: return@Column
            Text("${p.count - excluded.size} new fee(s) · total ${Money.format((p.count - excluded.size) * (p.rows.firstOrNull()?.amountPaise ?: 0))}" +
                if (p.duplicates > 0) " · ${p.duplicates} already have this fee" else "", fontWeight = FontWeight.SemiBold)
            Card(colors = White) {
                Column(Modifier.padding(6.dp)) {
                    p.rows.forEach { r ->
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Checkbox(!r.duplicate && r.studentId !in excluded, { on -> if (on) excluded.remove(r.studentId) else excluded.add(r.studentId) },
                                enabled = !r.duplicate)
                            Text(r.studentName + (r.classGrade?.let { " · $it" } ?: ""), Modifier.weight(1f), fontSize = 14.sp)
                            Text(if (r.duplicate) "already added" else Money.format(r.amountPaise), fontSize = 13.sp,
                                color = if (r.duplicate) Color.Gray else Color.Unspecified)
                        }
                    }
                }
            }
            Button({ request()?.let { vm.createFees(it, key) } }, enabled = p.count - excluded.size > 0 && !state.busy, modifier = Modifier.fillMaxWidth()) {
                Text("Create ${p.count - excluded.size} fee(s)")
            }
        }
    }
}

// ================================================================== SMS service
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SmsScreen(state: UiState, vm: AppViewModel, snackbar: SnackbarHostState) {
    BackHandler { vm.back() }
    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = { TopAppBar(title = { Text("SMS service") }, navigationIcon = { IconButton({ vm.back() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") } },
            actions = { TextButton({ vm.refreshSmsHealth(); vm.refreshDevice() }) { Text("Refresh") } }) },
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize()) {
            if (state.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            state.smsHealth?.let { h ->
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Stat("SMS phone", if (!h.phone.connected) "None" else if (h.phone.online) "Online" else "Offline",
                            if (h.phone.online) Ok else Bad, Modifier.weight(1f))
                        Stat("Sent today", "${h.sentToday}", Ok, Modifier.weight(1f))
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Stat("Waiting", "${h.waiting}", if (h.waiting > 0) Warn else Color.Unspecified, Modifier.weight(1f))
                        Stat("Failed", "${h.failedCount}", if (h.failedCount > 0) Bad else Color.Unspecified, Modifier.weight(1f))
                    }
                    Text(h.advice, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = if (h.failedCount > 0 || !h.phone.online) Warn else Ok)
                    if (h.failedCount > 0) {
                        h.failed.take(3).forEach { f ->
                            Text("${f.studentName ?: f.toMobile}: ${f.error ?: "failed"}", fontSize = 12.sp, color = Bad)
                        }
                        Button({ vm.retryFailedSms(null) }, enabled = !state.busy, modifier = Modifier.fillMaxWidth()) { Text("Retry all ${h.failedCount}") }
                    }
                    h.phone.lastSeenAt?.let { Text("${h.phone.name ?: "SMS phone"} last seen ${Times.display(it)}", fontSize = 12.sp, color = Color.Gray) }
                }
                HorizontalDivider()
            }
            DeviceScreen(state, vm)
        }
    }
}
