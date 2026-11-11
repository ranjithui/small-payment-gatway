package com.feeflow.app.data

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import com.feeflow.app.BuildConfig

/** Tokens are kept in EncryptedSharedPreferences (Android Keystore-backed). */
class SecurePrefs(context: Context) {
    private val prefs: SharedPreferences = run {
        val key = MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
        EncryptedSharedPreferences.create(
            context,
            "feeflow_secure",
            key,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    var serverUrl: String
        get() = prefs.getString("server_url", null) ?: BuildConfig.DEFAULT_SERVER_URL
        set(v) = prefs.edit().putString("server_url", v.trim().trimEnd('/')).apply()

    var adminToken: String?
        get() = prefs.getString("admin_token", null)
        set(v) = prefs.edit().putString("admin_token", v).apply()

    var adminName: String?
        get() = prefs.getString("admin_name", null)
        set(v) = prefs.edit().putString("admin_name", v).apply()

    var centreName: String?
        get() = prefs.getString("centre_name", null)
        set(v) = prefs.edit().putString("centre_name", v).apply()

    /** Present only while this phone is the centre's registered SMS device. */
    var deviceToken: String?
        get() = prefs.getString("device_token", null)
        set(v) = prefs.edit().putString("device_token", v).apply()

    var deviceName: String?
        get() = prefs.getString("device_name", null)
        set(v) = prefs.edit().putString("device_name", v).apply()

    fun client(): FeeFlowClient = FeeFlowClient(serverUrl, { adminToken }, { deviceToken })

    fun signOutAdmin() {
        prefs.edit().remove("admin_token").remove("admin_name").remove("centre_name").apply()
    }
}
