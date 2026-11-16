# -*- coding: utf-8 -*-
"""
notifier_service.py — خدمة أندرويد (python-for-android) تعمل في الخلفية حتى والتطبيق مغلق.
تفحص Worker API للإشعارات دورياً وتعرض إشعاراً أصلياً لكل رسالة جديدة تخص المستخدم.
لا تستورد main.py (لأنه يحمّل واجهة Kivy)؛ تقرأ الإعدادات من notifier_cfg.json
الذي يكتبه التطبيق، وتحتفظ بحالتها في notifier_state.json.
"""
import os
import re
import json
import time
import urllib.request
import urllib.parse
import urllib.error


def _data_dir():
    try:
        from android.storage import app_storage_path
        return app_storage_path()
    except Exception:
        return os.path.dirname(os.path.abspath(__file__))


class NetError(Exception):
    pass


DATA = _data_dir()
CFG = os.path.join(DATA, "notifier_cfg.json")
STATE = os.path.join(DATA, "notifier_state.json")


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def fetch_notifications(cfg, after):
    base = str(cfg.get("worker_url") or "https://conarkrzqpapuueynitc.supabase.co/functions/v1/proxy").rstrip("/")
    url = base + "/v1/app/notifications?after=" + urllib.parse.quote(str(after))
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + str(cfg.get("session_token") or "")})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            obj = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try: detail = json.loads(e.read().decode("utf-8"))
        except Exception: detail = {}
        raise NetError(str(detail.get("error") or "HTTP %s" % e.code))
    except Exception as e:
        raise NetError(str(e))
    return obj.get("notifications", []) if isinstance(obj, dict) else []


def notify(title, text, nid):
    from jnius import autoclass
    PS = autoclass("org.kivy.android.PythonService")
    ctx = PS.mService
    Context = autoclass("android.content.Context")
    nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE)
    sdk = autoclass("android.os.Build$VERSION").SDK_INT
    NB = autoclass("android.app.Notification$Builder")
    if sdk >= 26:
        NC = autoclass("android.app.NotificationChannel")
        nm.createNotificationChannel(NC("notes", "إشعارات دفتر المحاسب", 4))
        b = NB(ctx, "notes")
    else:
        b = NB(ctx)
    PI = autoclass("android.app.PendingIntent")
    intent = ctx.getPackageManager().getLaunchIntentForPackage(ctx.getPackageName())
    flags = PI.FLAG_UPDATE_CURRENT | (PI.FLAG_IMMUTABLE if sdk >= 23 else 0)
    b.setContentTitle(title)
    b.setContentText(text[:120])
    b.setStyle(autoclass("android.app.Notification$BigTextStyle")().bigText(text))
    b.setSmallIcon(ctx.getApplicationInfo().icon)
    b.setContentIntent(PI.getActivity(ctx, 0, intent, flags))
    b.setAutoCancel(True)
    nm.notify(nid, b.build())


def app_is_open(cfg):
    try:
        with open(cfg.get("alive", ""), encoding="utf-8") as f:
            return time.time() - float(f.read().strip()) < 75
    except Exception:
        return False


def run_once():
    cfg = _read(CFG, {})
    if not cfg.get("session_token") or not cfg.get("email") or not cfg.get("enabled", True):
        return int(cfg.get("poll", 60))
    st = _read(STATE, {})
    after = max(int(st.get("last", 0)), int(cfg.get("last", 0)))
    try:
        events = fetch_notifications(cfg, after)
    except NetError:
        return int(cfg.get("poll", 60))
    events = sorted(events, key=lambda e: int(e.get("id", 0)))
    for event in events:
        try: eid = int(event.get("id", after))
        except Exception: eid = after
        if eid <= after: continue
        text = str(event.get("message") or "").strip()
        if text and not app_is_open(cfg):
            try: notify("دفتر المحاسب", text, eid % 2147483000)
            except Exception as e: print("[NOTIFY]", e)
        after = max(after, eid)
    st["last"] = after
    _write(STATE, st)
    return int(cfg.get("poll", 60))


def main():
    while True:
        try:
            wait = run_once()
        except Exception as e:
            print("[SERVICE]", e)
            wait = 60
        time.sleep(max(60, wait))


if __name__ == "__main__":
    main()
