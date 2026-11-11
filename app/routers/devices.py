"""Android SIM-based SMS engine synchronisation.

The registered Android device polls ``GET /api/v1/devices/sync`` (existing device
synchronisation architecture). It receives:

* ``sms``: queued one-off messages (acknowledgements, verified, rejected, clarification)
* ``reminder_schedules``: every reminder whose state changed since the device last
  acknowledged it (new, rescheduled, paused via ``hold_until``, cancelled)

Before sending a reminder the device MUST check its latest locally known state:
``status == SCHEDULED`` and ``now >= scheduled_for`` and ``now >= hold_until``.
It then reports results with ``POST /api/v1/devices/ack``.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.audit import audit
from app.db import get_db, to_naive_utc, utcnow
from app.models import Device, Installment, ReminderSchedule, SmsOutbox
from app.ratelimit import rate_limit
from app.schemas import DeviceAck, DeviceRegister
from app.security import AdminContext, DeviceContext, new_secret_token, require_admin, require_device, sha256_hex
from app.services.declarations import _iso
from app.services.reminders import process_grace_expiry

router = APIRouter(prefix="/api/v1/devices", tags=["android-device"])
device_limit = Depends(rate_limit("device", 120))


@router.get("/sync", dependencies=[device_limit])
def sync(device: DeviceContext = Depends(require_device), db: Session = Depends(get_db)):
    now = utcnow()
    process_grace_expiry(db, now)

    # DISPATCHED-but-unacknowledged messages are re-offered until acked; the device
    # de-duplicates by message id so a crash between send and ack never double-sends.
    msgs = (
        db.query(SmsOutbox)
        .filter(
            SmsOutbox.tenant_id == device.tenant_id,
            (SmsOutbox.status == "QUEUED")
            | ((SmsOutbox.status == "DISPATCHED") & (SmsOutbox.device_id == device.device_id)),
        )
        .order_by(SmsOutbox.created_at)
        .limit(100)
        .all()
    )
    for m in msgs:
        if m.status == "QUEUED":
            m.status = "DISPATCHED"
            m.dispatched_at = now
            m.device_id = device.device_id

    rems = (
        db.query(ReminderSchedule)
        .filter(ReminderSchedule.tenant_id == device.tenant_id, ReminderSchedule.sync_status == "PENDING_SYNC")
        .order_by(ReminderSchedule.updated_at)
        .limit(500)
        .all()
    )
    rem_out = []
    for r in rems:
        inst = db.get(Installment, r.installment_id)
        rem_out.append(
            {
                "id": r.id,
                "version": r.version,
                "status": r.status,
                "scheduled_for": _iso(r.scheduled_for),
                "hold_until": _iso(inst.reminders_paused_until),
                "to_mobile": r.to_mobile,
                "body": r.body,
                "amount_paise": r.amount_paise,
                "cancel_reason": r.cancel_reason,
            }
        )
    db.commit()
    return {
        "server_time": _iso(now),
        "sms": [{"id": m.id, "to_mobile": m.to_mobile, "body": m.body, "kind": m.kind} for m in msgs],
        "reminder_schedules": rem_out,
    }


@router.post("/ack", dependencies=[device_limit])
def ack(body: DeviceAck, device: DeviceContext = Depends(require_device), db: Session = Depends(get_db)):
    now = utcnow()
    sms_done = rem_done = 0
    for a in body.sms:
        m = db.query(SmsOutbox).filter(SmsOutbox.id == a.id, SmsOutbox.tenant_id == device.tenant_id).first()
        if m is None or m.status in ("SENT",):
            continue  # idempotent: a repeated ack never re-sends or double counts
        m.status = a.status
        m.error = a.error
        m.sent_at = now if a.status == "SENT" else None
        sms_done += 1

    for a in body.reminders:
        r = (
            db.query(ReminderSchedule)
            .filter(ReminderSchedule.id == a.id, ReminderSchedule.tenant_id == device.tenant_id)
            .first()
        )
        if r is None:
            continue
        if a.sent_at is not None:
            if r.status == "SCHEDULED":
                r.status = "SENT"
                r.sent_at = to_naive_utc(a.sent_at)
                r.version += 1
                r.synced_version = r.version
                r.sync_status = "SYNCED"
            elif r.status == "CANCELLED":
                # Device was offline and sent before it received the cancellation.
                audit(
                    db, tenant_id=device.tenant_id, actor_type="DEVICE", actor_id=device.device_id,
                    action="REMINDER_SENT_AFTER_CANCELLATION", source="android_sync",
                    installment_id=r.installment_id, reason="Device sent using stale schedule (was offline)",
                    details={"reminder_id": r.id, "device_version": a.version, "server_version": r.version},
                )
            rem_done += 1
            continue
        if a.version == r.version:
            r.synced_version = a.version
            r.sync_status = "SYNCED"
            rem_done += 1
    db.commit()
    return {"sms_acknowledged": sms_done, "reminders_acknowledged": rem_done}


# ------------------------------------------------------------------ admin: device management
def _device_out(d: Device) -> dict:
    return {"id": d.id, "name": d.name, "active": d.active, "last_seen_at": _iso(d.last_seen_at)}


@router.post("/register", dependencies=[Depends(rate_limit("admin", 240))], status_code=201)
def register_device(body: DeviceRegister, admin: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    """Register this phone as the centre's SMS device. Returns the device token ONCE.

    Only one active SMS device per centre: older devices are deactivated so two phones
    can never send the same message. The new device receives the full reminder schedule.
    """
    now = utcnow()
    old = db.query(Device).filter(Device.tenant_id == admin.tenant_id, Device.active.is_(True)).all()
    for d in old:
        d.active = False
    old_ids = [d.id for d in old]
    if old_ids:
        # Unacknowledged messages of the retired phone go back to the queue (a possible
        # duplicate notification is preferred over a lost one).
        db.query(SmsOutbox).filter(SmsOutbox.status == "DISPATCHED", SmsOutbox.device_id.in_(old_ids)).update(
            {SmsOutbox.status: "QUEUED", SmsOutbox.device_id: None}, synchronize_session=False
        )
    for r in db.query(ReminderSchedule).filter(
        ReminderSchedule.tenant_id == admin.tenant_id, ReminderSchedule.status == "SCHEDULED"
    ):
        r.sync_status = "PENDING_SYNC"
        r.synced_version = 0
    token = new_secret_token(32)
    dev = Device(tenant_id=admin.tenant_id, name=body.name, token_hash=sha256_hex(token), last_seen_at=now)
    db.add(dev)
    db.flush()
    audit(
        db, tenant_id=admin.tenant_id, actor_type="ADMIN", actor_id=admin.admin_id, action="SMS_DEVICE_REGISTERED",
        source="android_app", details={"device_id": dev.id, "name": body.name, "deactivated": old_ids},
    )
    db.commit()
    return {"device": _device_out(dev), "device_token": token, "deactivated_devices": len(old_ids)}


@router.get("", dependencies=[Depends(rate_limit("admin", 240))])
def list_devices(admin: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    devices = db.query(Device).filter(Device.tenant_id == admin.tenant_id).order_by(Device.active.desc()).all()
    queued = db.query(SmsOutbox).filter(
        SmsOutbox.tenant_id == admin.tenant_id, SmsOutbox.status.in_(["QUEUED", "DISPATCHED"])
    ).count()
    pending_sync = db.query(ReminderSchedule).filter(
        ReminderSchedule.tenant_id == admin.tenant_id, ReminderSchedule.sync_status == "PENDING_SYNC"
    ).count()
    return {"items": [_device_out(d) for d in devices], "sms_waiting": queued, "reminder_changes_pending_sync": pending_sync}


@router.post("/{device_id}/revoke", dependencies=[Depends(rate_limit("admin", 240))])
def revoke_device(device_id: str, admin: AdminContext = Depends(require_admin), db: Session = Depends(get_db)):
    dev = db.query(Device).filter(Device.id == device_id, Device.tenant_id == admin.tenant_id).first()
    if dev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Device not found")
    dev.active = False
    audit(
        db, tenant_id=admin.tenant_id, actor_type="ADMIN", actor_id=admin.admin_id, action="SMS_DEVICE_REVOKED",
        source="admin", details={"device_id": dev.id},
    )
    db.commit()
    return _device_out(dev)
