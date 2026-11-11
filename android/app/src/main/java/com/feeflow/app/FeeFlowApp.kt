package com.feeflow.app

import android.app.Application
import com.feeflow.app.data.SecurePrefs
import com.feeflow.app.work.SyncScheduler

class FeeFlowApp : Application() {
    override fun onCreate() {
        super.onCreate()
        // Keep the SIM SMS engine scheduled while this phone is the registered SMS device.
        if (SecurePrefs(this).deviceToken != null) SyncScheduler.schedule(this)
    }
}
