# FeeFlow Android app

The FeeFlow Android app is one Kotlin app (Jetpack Compose, minSdk 26, targetSdk 34) with two roles:

1. **Payment Verification (admin):** sign in, see payment confirmations waiting for review,
   then **Confirm payment received** (with bank evidence), mark **Not received** (with a reason),
   or **Ask parent** (clarification by SMS). It uses the same backend rules as the web dashboard:
   idempotency keys, required "checked the bank" confirmations, and amount-difference acknowledgement.
2. **SIM SMS engine:** the centre's registered phone sends every parent SMS from its own SIM.
   This covers acknowledgements, verified and rejected messages, clarifications and fee reminders.
   No SMS gateway is used.

## How the SMS engine works

* WorkManager runs `SyncWorker` every 15 minutes, and also when you tap **Sync & send now**.
* Each run ([`core/SmsEngine.kt`](app/src/main/java/com/feeflow/app/core/SmsEngine.kt)):
  1. `GET /api/v1/devices/sync` returns new messages and changed reminder schedules
     (newer versions only).
  2. Sends one-off messages not already sent by this phone. Messages are de-duplicated by server id,
     and state is saved before and after every send, so a crash never causes a duplicate.
  3. Sends reminders that are due according to the **latest locally known schedule**: status
     `SCHEDULED`, the scheduled time has passed, `hold_until` has passed (it is set while a payment
     is being verified), and the phone hasn't already sent it.
  4. `POST /api/v1/devices/ack` uploads the results. If the server is unreachable, results are
     queued and uploaded on the next sync.
* **Offline safety:** if the phone hasn't synced for 24h, reminders are held, because a
  cancellation (parent already paid) might not have arrived yet. Reminders more than 48h overdue
  are skipped. A failed send is retried up to 3 times.
* **One SMS phone per centre:** registering a phone retires the previous one, and the server
  resends the full schedule to the new phone. A revoked phone gets HTTP 401 and stops automatically.
* State is kept in `files/sms_engine_state.json`, written crash-safely with a `.bak` copy.
  Tokens are stored in `EncryptedSharedPreferences`.

## Build

The toolchain used here is installed in `E:\android-toolchain` (JDK 17, Android SDK 34, Gradle 8.7).
From this folder:

```bash
JAVA_HOME=E:/android-toolchain/jdk17 ./gradlew assembleDebug
```

The APK is written to `app/build/outputs/apk/debug/app-debug.apk`. You can also open the folder in
Android Studio (Koala or newer).

### Signed builds (what to install on phones)

| Build | Command | Output | Network |
|---|---|---|---|
| **pilot** | `./gradlew assemblePilot` | `app/build/outputs/apk/pilot/app-pilot.apk` | Allows plain HTTP. Default server `http://192.168.31.225:8000`; override with `-PpilotServerUrl=http://<ip>:8000` |
| **release** | `./gradlew assembleRelease` | `app/build/outputs/apk/release/app-release.apk` | HTTPS only (production) |

Both are minified and signed with the FeeFlow release key. Certificate SHA-256:
`1cf79d6a83da44d730f7e77b9d036941cbc51804749308380d543348da31326b`.

* The key is in `keystore/feeflow-release.jks` and its passwords are in `keystore.properties`.
  Both are git-ignored. **Back them up somewhere safe.** If they are lost, updates can never be
  installed over the existing app; every phone would have to uninstall and reinstall.
* Debug, pilot and release builds share one package name. Switching from the debug-signed build to
  a signed build needs an uninstall first, because the signatures differ.
* Play Protect may still warn the first time, because the app is new, sideloaded and uses SEND_SMS.
  Tap **More details → Install anyway**, or submit `app-release.apk` for Google's review through
  the Play Protect appeals form so it stops warning for everyone.

Unit tests (engine, money, store, HTTP client against MockWebServer):

```bash
JAVA_HOME=E:/android-toolchain/jdk17 ./gradlew testDebugUnitTest
```

Live end-to-end test against the running backend, using the app's real client and engine with a fake SIM:

```bash
FEEFLOW_E2E_URL=http://localhost:8000 FEEFLOW_E2E_PAY_TOKEN=<parent link token> JAVA_HOME=E:/android-toolchain/jdk17 ./gradlew testDebugUnitTest --tests '*LiveBackendE2ETest*'
```

## Running on a phone

1. Start the backend so the phone can reach it (same Wi-Fi). Bind to all interfaces, and allow
   Python through Windows Firewall when it asks:
   `python -m uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000`.
   Then set `FEEFLOW_PUBLIC_BASE_URL` to the PC's LAN address so SMS links work on the phone.
2. Install `app/build/outputs/apk/pilot/app-pilot.apk` (copy it to the phone, or `adb install`).
3. Sign in. The server URL is `http://10.0.2.2:8000` on the emulator, or `http://<PC LAN IP>:8000`
   on a real phone (the pilot build pre-fills this PC). Debug and pilot builds allow plain HTTP; release requires HTTPS.
4. Go to the **SMS phone** tab and tap **Use this phone as the SMS device**, then allow the SMS permission.

## Notes

* SMS go out from the default SMS SIM. Choosing a SIM on dual-SIM phones is not implemented yet.
* `SEND_SMS` is a restricted permission on Google Play. Distribute this app privately to centres
  (sideload or managed Play), or submit a permissions declaration.
* Battery optimisation can delay WorkManager on some OEM phones. Exempt FeeFlow in the battery settings.
