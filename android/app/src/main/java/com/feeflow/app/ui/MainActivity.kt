package com.feeflow.app.ui

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels

class MainActivity : ComponentActivity() {
    private val vm: AppViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { FeeFlowTheme { FeeFlowRoot(vm) } }
    }

    override fun onResume() {
        super.onResume()
        vm.refreshDeviceLocal()
    }
}
