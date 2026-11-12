package com.feeflow.app.ui

import android.app.Application
import android.os.Build
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.feeflow.app.core.ConfirmRequest
import com.feeflow.app.core.Declaration
import com.feeflow.app.core.Money
import com.feeflow.app.core.RunReport
import com.feeflow.app.core.SendFeeLinkResponse
import com.feeflow.app.core.SmsEngine
import com.feeflow.app.core.Student
import com.feeflow.app.core.StudentInput
import com.feeflow.app.core.StudentRequest
import com.feeflow.app.core.Subscription
import com.feeflow.app.core.TransactionIn
import com.feeflow.app.core.UPI_VPA_REGEX
import com.feeflow.app.core.WorkspaceCreateRequest
import com.feeflow.app.data.ApiException
import com.feeflow.app.data.FeeFlowClient
import com.feeflow.app.data.SecurePrefs
import com.feeflow.app.sms.AndroidSmsSender
import com.feeflow.app.work.SyncScheduler
import com.feeflow.app.work.SyncWorker
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

sealed interface Screen {
    data object Login : Screen
    data object SignUp : Screen
    data class Main(val tab: Tab = Tab.STUDENTS) : Screen
    data class Detail(val id: String) : Screen
    /** First sign-in: the admin creates their own workspace (centre + UPI ID). */
    data object Setup : Screen
    data class StudentDetail(val id: String) : Screen
    /** id == null: add a new student. */
    data class StudentForm(val id: String?) : Screen
}

enum class Tab { STUDENTS, VERIFY, SMS_DEVICE }

/** Text fields of the student form (validated in [AppViewModel.saveStudent]). */
data class StudentFormInput(
    val name: String = "",
    val studentCode: String = "",
    val classGrade: String = "",
    val dateOfBirth: String = "",
    val gender: String = "",
    val school: String = "",
    val address: String = "",
    val joinedOn: String = "",
    val parentName: String = "",
    val parentMobile: String = "",
    val parentEmail: String = "",
    val monthlyFee: String = "",
    val feeDueDay: String = "",
) {
    companion object {
        fun from(s: Student) = StudentFormInput(
            name = s.name, studentCode = s.studentCode, classGrade = s.classGrade.orEmpty(),
            dateOfBirth = s.dateOfBirth.orEmpty(), gender = s.gender.orEmpty(), school = s.school.orEmpty(),
            address = s.address.orEmpty(), joinedOn = s.joinedOn.orEmpty(), parentName = s.parentName.orEmpty(),
            parentMobile = s.parentMobile, parentEmail = s.parentEmail.orEmpty(),
            monthlyFee = s.monthlyFeePaise?.let { Money.toInput(it) }.orEmpty(), feeDueDay = s.feeDueDay?.toString().orEmpty(),
        )
    }
}

data class DeviceUi(
    val registered: Boolean = false,
    val deviceName: String? = null,
    val permissionGranted: Boolean = false,
    val lastSyncAtMs: Long? = null,
    val lastRunAtMs: Long? = null,
    val lastSyncError: String? = null,
    val pendingAcks: Int = 0,
    val scheduledReminders: Int = 0,
    val heldUntilVerified: Int = 0,
    val serverSmsWaiting: Int? = null,
    val serverPendingSync: Int? = null,
    val lastReport: RunReport? = null,
    val syncing: Boolean = false,
)

data class UiState(
    val screen: Screen = Screen.Login,
    val serverUrl: String = "",
    val adminName: String? = null,
    val centreName: String? = null,
    val busy: Boolean = false,
    val error: String? = null,
    val message: String? = null,
    val pending: List<Declaration> = emptyList(),
    val detail: Declaration? = null,
    val device: DeviceUi = DeviceUi(),
    val subscription: Subscription? = null,
    val students: List<Student> = emptyList(),
    val studentQuery: String = "",
    val showInactive: Boolean = false,
    val student: Student? = null,
    val lastLink: SendFeeLinkResponse? = null,
    /** One-shot: text the UI should hand to Android's share sheet (WhatsApp etc.). */
    val shareText: String? = null,
)

/** Collected confirm-form input (validated in [AppViewModel.confirm]). */
data class ConfirmInput(
    val transactions: List<Pair<String, String>>, // amount text, bank reference
    val bankTransactionId: String? = null,
    val evidenceNote: String,
    val adminNote: String,
    val checkedBank: Boolean,
    val belongsToCentre: Boolean,
    val differenceAcknowledged: Boolean,
)

class AppViewModel(app: Application) : AndroidViewModel(app) {
    private val prefs = SecurePrefs(app)
    private val sender = AndroidSmsSender(app)
    private val _state = MutableStateFlow(
        UiState(
            screen = if (prefs.adminToken != null) Screen.Main() else Screen.Login,
            serverUrl = prefs.serverUrl,
            adminName = prefs.adminName,
            centreName = prefs.centreName,
        )
    )
    val state: StateFlow<UiState> = _state.asStateFlow()

    private fun client() = prefs.client()

    init {
        refreshDeviceLocal()
        if (prefs.adminToken != null) launchBusy { routeAfterSignIn() }
    }

    fun clearMessages() = _state.update { it.copy(error = null, message = null) }

    private fun launchBusy(block: suspend () -> Unit) = viewModelScope.launch {
        _state.update { it.copy(busy = true, error = null) }
        try {
            block()
        } catch (e: ApiException) {
            if (e.isUnauthorized && prefs.adminToken != null) logout("Your session expired. Please sign in again.")
            else _state.update { it.copy(error = e.message) }
        } catch (e: IllegalArgumentException) {
            _state.update { it.copy(error = e.message) }
        } catch (e: Exception) {
            _state.update { it.copy(error = "Can't reach FeeFlow server: ${e.message ?: e.javaClass.simpleName}") }
        } finally {
            _state.update { it.copy(busy = false) }
        }
    }

    // ------------------------------------------------------------- auth
    fun login(serverUrl: String, email: String, password: String) = launchBusy {
        val url = serverUrl.trim().trimEnd('/')
        require(url.startsWith("http://") || url.startsWith("https://")) { "Server URL must start with https://" }
        val c = FeeFlowClient(url)
        val res = c.login(email, password)
        prefs.serverUrl = url
        prefs.adminToken = res.accessToken
        _state.update { it.copy(serverUrl = url) }
        routeAfterSignIn()
    }

    /** Workspace exists -> main screen; otherwise -> workspace setup. */
    private suspend fun routeAfterSignIn() {
        val me = client().me()
        prefs.adminName = me.email ?: me.name
        val ws = me.workspace
        if (!me.hasWorkspace || ws == null) {
            prefs.centreName = null
            _state.update { it.copy(screen = Screen.Setup, adminName = me.email ?: me.name, centreName = null) }
            return
        }
        prefs.centreName = ws.name
        _state.update {
            it.copy(screen = if (it.screen is Screen.Main) it.screen else Screen.Main(), adminName = me.email ?: me.name,
                centreName = ws.name, subscription = ws.subscription)
        }
        refreshStudents()
        refreshPending()
    }

    // ------------------------------------------------------------- sign-up
    fun goToSignUp() = _state.update { it.copy(screen = Screen.SignUp, error = null) }
    fun goToLogin() = _state.update { it.copy(screen = Screen.Login, error = null) }

    /** Returns a validation error, or null when the request was sent. */
    fun signUp(serverUrl: String, name: String, email: String, password: String, confirm: String): String? {
        val url = serverUrl.trim().trimEnd('/')
        if (!(url.startsWith("http://") || url.startsWith("https://"))) return "Server URL must start with https://"
        if (name.trim().length < 2) return "Enter your name"
        if (!email.contains("@") || !email.substringAfter("@").contains(".")) return "Enter a valid email"
        if (password.length < 10) return "Password must be at least 10 characters"
        if (password != confirm) return "The passwords do not match"
        launchBusy {
            val res = FeeFlowClient(url).signup(name, email, password)
            prefs.serverUrl = url
            prefs.adminToken = res.accessToken
            _state.update { it.copy(serverUrl = url) }
            routeAfterSignIn() // -> Create your workspace
        }
        return null
    }

    // ------------------------------------------------------------- students
    fun refreshStudents() = launchBusy {
        val s = _state.value
        val items = client().students(s.studentQuery, s.showInactive)
        _state.update { it.copy(students = items) }
    }

    fun setStudentQuery(q: String) {
        _state.update { it.copy(studentQuery = q) }
        refreshStudents()
    }

    fun setShowInactive(v: Boolean) {
        _state.update { it.copy(showInactive = v) }
        refreshStudents()
    }

    fun openStudent(id: String) {
        _state.update { it.copy(screen = Screen.StudentDetail(id), student = null, lastLink = null) }
        launchBusy {
            val st = client().student(id)
            _state.update { it.copy(student = st) }
        }
    }

    fun newStudent() = _state.update { it.copy(screen = Screen.StudentForm(null), student = null) }

    fun editStudent() {
        val st = _state.value.student ?: return
        _state.update { it.copy(screen = Screen.StudentForm(st.id)) }
    }

    /** Returns a validation error, or null when the request was sent. */
    fun saveStudent(id: String?, f: StudentFormInput): String? {
        if (f.name.trim().length < 2) return "Enter the student's name"
        if (!StudentInput.mobileOk(f.parentMobile)) return "Enter a valid 10-digit parent mobile number"
        val dob = StudentInput.date(f.dateOfBirth).getOrElse { return "Date of birth: ${it.message}" }
        val joined = StudentInput.date(f.joinedOn).getOrElse { return "Joining date: ${it.message}" }
        val fee = if (f.monthlyFee.isBlank()) null else (Money.parseToPaise(f.monthlyFee) ?: return "Enter a valid monthly fee amount")
        val day = if (f.feeDueDay.isBlank()) null else (f.feeDueDay.trim().toIntOrNull()?.takeIf { it in 1..31 }
            ?: return "Fee due day must be between 1 and 31")
        if ((fee == null) != (day == null)) return "Enter both the monthly fee and the due day (or neither)"
        val existing = _state.value.student
        val req = StudentRequest(
            name = f.name.trim(), studentCode = if (id == null) f.studentCode.trim().ifEmpty { null } else null,
            parentName = f.parentName.trim(), parentMobile = f.parentMobile.trim(), parentEmail = f.parentEmail.trim(),
            dateOfBirth = dob, gender = f.gender.ifEmpty { null }, classGrade = f.classGrade.trim(), school = f.school.trim(),
            address = f.address.trim(), joinedOn = joined, monthlyFeePaise = fee, feeDueDay = day,
            clearFeePlan = if (id != null && fee == null && existing?.monthlyFeePaise != null) true else null,
        )
        launchBusy {
            val saved = if (id == null) client().createStudent(req) else client().updateStudent(id, req)
            val note = if (id == null && saved.fees.isNotEmpty()) " This month's fee was created." else ""
            _state.update { it.copy(screen = Screen.StudentDetail(saved.id), student = saved, message = "Saved ${saved.name}.$note") }
        }
        return null
    }

    fun setStudentActive(active: Boolean) {
        val st = _state.value.student ?: return
        launchBusy {
            val saved = client().updateStudent(st.id, StudentRequest(active = active))
            _state.update { it.copy(student = saved, message = if (active) "${saved.name} is active again." else "${saved.name} deactivated.") }
        }
    }

    /** "SMS": queue on the centre's SMS phone. "SHARE": open Android's share sheet with the message. */
    fun sendFeeLink(channel: String) {
        val st = _state.value.student ?: return
        launchBusy {
            val r = client().sendFeeLink(st.id, channel, FeeFlowClient.newIdempotencyKey())
            val msg = when {
                channel == "SHARE" -> null
                r.smsPhoneActive -> "Fee link queued for ${r.parentMobile}. The SMS phone sends it on its next sync."
                else -> "Fee link queued, but no SMS phone is registered yet. Use 'Share link', or register a phone in the SMS phone tab."
            }
            _state.update { it.copy(lastLink = r, message = msg, shareText = if (channel == "SHARE") r.shareText else null) }
            if (channel == "SMS" && prefs.deviceToken != null) syncNow() // this phone IS the SMS phone: send right away
        }
    }

    fun shareHandled() = _state.update { it.copy(shareText = null) }

    /** Returns a validation error, or null when the request was sent. */
    fun createWorkspace(name: String, upiVpa: String, upiVpaConfirm: String, payee: String, phone: String): String? {
        val vpa = upiVpa.trim()
        if (name.trim().length < 2) return "Enter the tuition centre name"
        if (!UPI_VPA_REGEX.matches(vpa)) return "Enter a valid UPI ID, e.g. centrename@okaxis"
        if (!vpa.equals(upiVpaConfirm.trim(), ignoreCase = true)) return "The two UPI IDs do not match"
        if (payee.trim().length < 2) return "Enter the payee name shown in UPI apps"
        launchBusy {
            val ws = client().createWorkspace(WorkspaceCreateRequest(name.trim(), vpa, payee.trim(), phone.trim().ifEmpty { null }))
            _state.update { it.copy(message = "Workspace created. Parents will pay to ${ws.upiVpa}.") }
            routeAfterSignIn()
        }
        return null
    }

    fun logout(reason: String? = null) {
        prefs.signOutAdmin()
        // The SMS engine keeps running with its own device token even when no admin is signed in.
        _state.update { UiState(screen = Screen.Login, serverUrl = prefs.serverUrl, error = reason, device = it.device) }
    }

    // ------------------------------------------------------------- navigation
    fun selectTab(tab: Tab) {
        _state.update { it.copy(screen = Screen.Main(tab)) }
        when (tab) {
            Tab.STUDENTS -> refreshStudents()
            Tab.VERIFY -> refreshPending()
            Tab.SMS_DEVICE -> refreshDevice()
        }
    }

    fun back() {
        when (val s = _state.value.screen) {
            is Screen.Detail -> {
                _state.update { it.copy(screen = Screen.Main(Tab.VERIFY), detail = null) }
                refreshPending()
            }
            is Screen.StudentDetail -> {
                _state.update { it.copy(screen = Screen.Main(Tab.STUDENTS), student = null, lastLink = null) }
                refreshStudents()
            }
            is Screen.StudentForm -> {
                if (s.id != null) _state.update { it.copy(screen = Screen.StudentDetail(s.id)) }
                else _state.update { it.copy(screen = Screen.Main(Tab.STUDENTS)) }
            }
            Screen.SignUp -> goToLogin()
            else -> Unit
        }
    }

    // ------------------------------------------------------------- verification
    fun refreshPending() = launchBusy {
        val items = client().pending()
        _state.update { it.copy(pending = items) }
    }

    fun openDetail(id: String) {
        _state.update { it.copy(screen = Screen.Detail(id), detail = null) }
        launchBusy {
            val d = client().declaration(id) // also marks it PENDING_VERIFICATION on the server
            _state.update { it.copy(detail = d) }
        }
    }

    /** Returns a validation error, or null when the request was sent. */
    fun confirm(d: Declaration, input: ConfirmInput, idempotencyKey: String): String? {
        if (!input.checkedBank || !input.belongsToCentre) {
            return "Tick both boxes after checking the credit in the centre's bank account."
        }
        val txs = input.transactions.mapIndexed { i, (amountText, ref) ->
            val paise = Money.parseToPaise(amountText) ?: return "Enter a valid amount for transaction ${i + 1}"
            TransactionIn(paise, ref.trim().ifEmpty { null }, if (i == 0) input.bankTransactionId else null)
        }
        if (txs.isEmpty()) return "Add at least one bank transaction"
        if (txs.any { it.bankReference == null && it.bankTransactionId == null } && input.evidenceNote.isBlank()) {
            return "Enter the bank reference (UTR) or an evidence note"
        }
        val total = txs.sumOf { it.amountPaise }
        if (total != d.declaredAmountPaise && !input.differenceAcknowledged) {
            return "Verified total ${Money.format(total)} differs from declared ${Money.format(d.declaredAmountPaise)}. Tick the acknowledgement to continue."
        }
        launchBusy {
            val res = client().confirm(
                d.id,
                ConfirmRequest(
                    confirm = true,
                    bankTransactionChecked = true,
                    belongsToCentre = true,
                    transactions = txs,
                    amountDifferenceAcknowledged = input.differenceAcknowledged,
                    evidenceNote = input.evidenceNote.trim().ifEmpty { null },
                    adminNote = input.adminNote.trim().ifEmpty { null },
                ),
                idempotencyKey,
            )
            val excess = if (res.excessFlaggedPaise > 0) " Excess ${Money.format(res.excessFlaggedPaise)} flagged for refund/adjustment." else ""
            _state.update {
                it.copy(
                    message = "Verified. Fee is ${res.installmentStatus.replace('_', ' ')}, outstanding ${Money.format(res.outstandingPaise)}. Receipt ${res.receipt.receiptNumber}.$excess",
                )
            }
            back()
        }
        return null
    }

    fun reject(d: Declaration, reason: String, idempotencyKey: String): String? {
        if (reason.trim().length < 3) return "Enter a reason"
        launchBusy {
            client().reject(d.id, reason.trim(), idempotencyKey)
            _state.update { it.copy(message = "Marked as not received. The parent has been notified.") }
            back()
        }
        return null
    }

    fun requestClarification(d: Declaration, message: String, idempotencyKey: String): String? {
        if (message.trim().length < 3) return "Enter a question for the parent"
        launchBusy {
            client().requestClarification(d.id, message.trim(), idempotencyKey)
            _state.update { it.copy(message = "Clarification request sent to the parent by SMS.") }
            openDetail(d.id)
        }
        return null
    }

    // ------------------------------------------------------------- SMS device
    fun refreshDeviceLocal() {
        val s = SyncWorker.stateStore(getApplication()).load()
        val now = System.currentTimeMillis()
        val scheduled = s.schedules.values.filter { it.status == "SCHEDULED" && it.localSentAtMs == null }
        _state.update {
            it.copy(
                device = it.device.copy(
                    registered = prefs.deviceToken != null,
                    deviceName = prefs.deviceName,
                    permissionGranted = sender.hasPermission(),
                    lastSyncAtMs = s.lastSyncAtMs,
                    lastRunAtMs = s.lastRunAtMs,
                    lastSyncError = s.lastSyncError,
                    pendingAcks = s.pendingSmsAcks.size + s.pendingReminderAcks.size,
                    scheduledReminders = scheduled.size,
                    heldUntilVerified = scheduled.count { r -> (r.holdUntilMs ?: 0) > now },
                )
            )
        }
    }

    fun refreshDevice() {
        refreshDeviceLocal()
        if (prefs.adminToken == null) return
        viewModelScope.launch {
            runCatching { client().devices() }.onSuccess { d ->
                _state.update {
                    it.copy(device = it.device.copy(serverSmsWaiting = d.smsWaiting, serverPendingSync = d.reminderChangesPendingSync))
                }
            }
        }
    }

    fun onPermissionResult() = refreshDeviceLocal()

    fun registerThisPhone() = launchBusy {
        val name = "${Build.MANUFACTURER} ${Build.MODEL}".trim()
        val res = client().registerDevice(name)
        prefs.deviceToken = res.deviceToken
        prefs.deviceName = res.device.name
        SyncScheduler.schedule(getApplication())
        val replaced = if (res.deactivatedDevices > 0) " The previous SMS phone was deactivated." else ""
        _state.update { it.copy(message = "This phone is now the centre's SMS device.$replaced") }
        syncNow()
    }

    fun stopBeingSmsDevice() {
        prefs.deviceToken = null
        prefs.deviceName = null
        SyncScheduler.cancel(getApplication())
        refreshDevice()
    }

    fun syncNow() {
        if (prefs.deviceToken == null) return
        viewModelScope.launch {
            _state.update { it.copy(device = it.device.copy(syncing = true)) }
            val report = SmsEngine(client(), SyncWorker.stateStore(getApplication()), sender).runOnce()
            if (report.syncErrorCode == 401) {
                prefs.deviceToken = null
                SyncScheduler.cancel(getApplication())
                _state.update { it.copy(error = "This phone is no longer the registered SMS device.") }
            }
            _state.update { it.copy(device = it.device.copy(syncing = false, lastReport = report)) }
            refreshDevice()
        }
    }
}
