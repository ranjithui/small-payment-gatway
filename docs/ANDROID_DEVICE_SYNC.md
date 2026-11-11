# Android SIM SMS engine – sync contract

FeeFlow sends every SMS from the centre's registered Android phone and its physical SIM.
**There is no SMS gateway.** The Kotlin app in [`android/`](../android/README.md) implements this
contract (`core/SmsEngine.kt`).

Auth header: `X-Device-Token: <device token>`. The server stores only its SHA-256.

## Registering the phone (admin)

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/devices/register` `{"name": "Pixel 7 office"}` | An admin signed in on the phone registers it. The response has the `device_token`, returned **once**. Any previously active phone for the centre is deactivated: its unacknowledged messages go back to the queue, and the full reminder schedule is marked for sync to the new phone. |
| `GET /api/v1/devices` | Devices, `sms_waiting`, and `reminder_changes_pending_sync` (shown in the app). |
| `POST /api/v1/devices/{id}/revoke` | Deactivates a phone. Its next sync gets 401 and the app stops acting as the SMS device. |

Only one phone per centre is active at a time, so two phones can never send the same message.
Messages that were `DISPATCHED` to one phone are offered again only to that phone.

## `GET /api/v1/devices/sync`

```json
{
  "server_time": "2026-11-11T13:02:11Z",
  "sms": [
    {"id": "…", "to_mobile": "+9198…", "kind": "DECLARATION_ACK", "body": "Dear Parent, we have received…"}
  ],
  "reminder_schedules": [
    {"id": "…", "version": 4, "status": "SCHEDULED|CANCELLED|SENT", "scheduled_for": "…Z",
     "hold_until": "…Z or null", "to_mobile": "+9198…", "body": "Dear Parent, fee of Rs. 1,500…",
     "amount_paise": 150000, "cancel_reason": null}
  ]
}
```

* `sms`: one-off messages. They are re-offered until acknowledged, so **de-duplicate by `id`**
  before sending. The server also guarantees uniqueness per event (`dedupe_key`), so a parent
  never gets two acknowledgements for one declaration.
* `reminder_schedules`: every schedule whose `version` changed since the device last acked it.
  Upsert locally by `id` and keep the highest `version`.

### Rule before sending any reminder

Check the **latest locally known** schedule state:

```
send only if status == SCHEDULED
          and now >= scheduled_for
          and (hold_until == null or now >= hold_until)
```

`hold_until` is set while a payment declaration is awaiting verification (default 24h grace).
Verification sets `status = CANCELLED`. A partial verification rewrites `body`/`amount_paise`
for the remaining balance.

## `POST /api/v1/devices/ack`

```json
{
  "sms": [{"id": "…", "status": "SENT"}, {"id": "…", "status": "FAILED", "error": "No service"}],
  "reminders": [
    {"id": "…", "version": 4},                                  // state received
    {"id": "…", "version": 4, "sent_at": "2026-11-12T04:30:00Z"} // reminder actually sent
  ]
}
```

Acks are idempotent. If an offline device sends a reminder that the server had already
cancelled, the ack is accepted and an audit entry `REMINDER_SENT_AFTER_CANCELLATION` is
written for follow-up. Until the device acks a cancellation, the admin **Fees → Status**
view shows it as `PENDING SYNC`.

## Kotlin sketch

```kotlin
suspend fun syncOnce(api: FeeFlowApi, store: LocalStore, sms: SimSmsSender) {
    val res = api.sync()
    res.reminderSchedules.forEach { store.upsertIfNewer(it) }
    val smsAcks = res.sms.filterNot { store.alreadySent(it.id) }.map { m ->
        val ok = sms.send(m.toMobile, m.body)
        if (ok) store.markSent(m.id)
        SmsAck(m.id, if (ok) "SENT" else "FAILED")
    }
    val now = Instant.now()
    val sent = store.dueReminders(now) // applies the rule above against LOCAL state
        .filter { sms.send(it.toMobile, it.body) }
        .map { store.markReminderSent(it.id); ReminderAck(it.id, it.version, now) }
    api.ack(DeviceAck(smsAcks, res.reminderSchedules.map { ReminderAck(it.id, it.version) } + sent))
}
```
