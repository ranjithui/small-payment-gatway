package com.feeflow.app.ui

import android.app.Application
import android.os.Build
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.feeflow.app.core.ConfirmRequest
import com.feeflow.app.core.Declaration
import com.feeflow.app.core.Money
import com.feeflow.app.core.RunReport
import com.feeflow.app.core.SmsEngine
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
    data class Main(val tab: Tab = Tab.VERIFY) : Screen
    data class Detail(val id: String) : Screen
    /** First sign-in: the admin creates their own workspace (centre + UPI ID). */
    data object Setup : Screen
}

enum class Tab { VERIFY, SMS_DEVICE }

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
        _state.update { it.copy(screen = if (it.screen is Screen.Main) it.screen else Screen.Main(), adminName = me.email ?: me.name, centreName = ws.name) }
        refreshPending()
    }

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
        if (tab == Tab.VERIFY) refreshPending() else refreshDevice()
    }

    fun back() {
        if (_state.value.screen is Screen.Detail) {
            _state.update { it.copy(screen = Screen.Main(Tab.VERIFY), detail = null) }
            refreshPending()
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
