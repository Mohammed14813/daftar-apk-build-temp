# -*- coding: utf-8 -*-
"""
accounting_app.py  (الكود الثاني)  —  تطبيق المحاسب المتكامل
يُستدعى من main.py:  accounting_app.run()

التخزين: Worker API:
  ملف الحساب: بيانات الاشتراك والملف الشخصي +
                  بيانات الحساب الفردية.
  البيانات المحاسبية محفوظة عبر Worker API.
"""
import sys
import os
import json
import re
import time
import threading
import socket
import base64
import zlib
import hashlib
import traceback
import textwrap
import urllib.request
import urllib.parse
import urllib.error
import copy
import math
from datetime import datetime, timedelta

_core = sys.modules.get("__main__")
if _core is None or not hasattr(_core, "UI_KIT_READY"):
    import main as _core
globals().update({k: v for k, v in vars(_core).items() if not k.startswith("__")})
from kivy.graphics import Line, Color, Ellipse, Rectangle, RoundedRectangle, PushMatrix, PopMatrix, Translate
from kivy.graphics import Mesh, StencilPush, StencilUse, StencilUnUse, StencilPop
from kivy.properties import NumericProperty

# إعدادات Worker API المشتركة
WORKER_URL = "https://conarkrzqpapuueynitc.supabase.co/functions/v1/proxy"
SESSION_TOKEN = ""

# دورة الفحص الخلفي (التحديثات والإشعارات والباقة والمزامنة)
TICK_SECONDS = 60      # فحص كل 60 ثانية لتقليل استهلاك الاستدعاءات
SYNC_EVERY = 3         # مزامنة/تحديث الحساب كل 3 دورات (~60 ثانية). يجب أن تكون 2 فأكثر

# التسجيل/الدخول يبقيان على عقد المصادقة الحالي.
FIXED_CODES = ()
CODES_SINGLE_USE = False
ADMIN_PHONE_SHOW = "779308631"
ADMIN_PHONE_WA = "967779308631"
FREE_START_INVOICES = 500
CHUNK_SIZE = 3600
DATA_FILE = os.path.join(DATA_DIR, "acc_data.json")

TERMS_TEXT = """شروط الاستخدام – تطبيق المحاسب

باستخدامك للتطبيق أو التسجيل فيه فإنك توافق على الشروط التالية:
١- التطبيق أداة مساعدة لتنظيم الحسابات والفواتير والمخزون، وليس جهة مالية.
٢- المستخدم مسؤول عن صحة البيانات المدخلة وسرية كلمة المرور.
٣- تُخزَّن البيانات على جهازك ونسخة منها في السحابة عند المزامنة.
٤- لا نبيع البيانات ولا نشاركها مع أي طرف.
٥- يحق للإدارة إيقاف أي حساب مخالف.
٦- يُقدَّم التطبيق كما هو دون ضمان، والمستخدم مسؤول عن أخذ نسخة احتياطية.
٧- بالضغط على أوافق تقر بأنك قرأت الشروط ووافقت عليها.
"""

class ApiError(Exception):
    pass


import http.client as _httpc
import ssl as _ssl
import urllib.parse as _urlparse

_WORKER_HOST = _urlparse.urlsplit(WORKER_URL).netloc
_WORKER_BASE = _urlparse.urlsplit(WORKER_URL).path.rstrip("/")   # مثل /functions/v1/proxy عند استخدام وسيط Supabase
_CONN = threading.local()       # اتصال HTTPS مستمر (keep-alive) لكل خيط: يوفّر مصافحة TLS في كل طلب
_NET_MSG = "تعذر الاتصال بالخادم. تحقق من الانترنت (أو جرّب VPN) ثم أعد المحاولة."


def _worker_conn(fresh=False):
    c = getattr(_CONN, "c", None)
    if c is not None and not fresh:
        return c
    if c is not None:
        try: c.close()
        except Exception: pass
    try:
        import certifi
        ctx = _ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = _ssl.create_default_context()
    c = _httpc.HTTPSConnection(_WORKER_HOST, timeout=25, context=ctx)
    _CONN.c = c
    return c


def worker_request(method, path, payload=None, authenticated=True):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "User-Agent": "Integrated-Accountant-Android/1.0.9",
    }
    if authenticated and SESSION_TOKEN:
        headers["Authorization"] = "Bearer " + SESSION_TOKEN
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last = None
    for attempt in range(2):
        # المحاولة الثانية باتصال جديد: يعالج انقطاع اتصال keep-alive القديم.
        conn = _worker_conn(fresh=attempt > 0)
        sent = False
        try:
            conn.request(method, _WORKER_BASE + path, body=body, headers=headers)
            sent = True
            r = conn.getresponse()
            raw = r.read().decode("utf-8", "replace")
            status = r.status
            if r.getheader("Connection", "").lower() == "close":
                _CONN.c = None
            break
        except (OSError, _httpc.HTTPException) as e:
            last = e
            _CONN.c = None
            try: conn.close()
            except Exception: pass
            # لا نعيد إرسال طلب كُتب بالفعل إن انتهت مهلته (قد يكون نُفّذ على الخادم) إلا القراءة.
            if sent and isinstance(e, socket.timeout) and method != "GET":
                raise ApiError(_NET_MSG)
            continue
    else:
        raise ApiError(_NET_MSG)
    try: data = json.loads(raw) if raw else {}
    except ValueError: data = {}
    if status >= 400:
        raise ApiError(str((data or {}).get("error") or (data or {}).get("message") or "HTTP %s" % status))
    # الـ Worker يلف نتائج المصادقة داخل {"ok":true,"result":{...}}؛ نفكّها هنا ليقرأها التطبيق مباشرة.
    if isinstance(data, dict) and isinstance(data.get("result"), dict):
        merged = dict(data["result"]); merged.setdefault("ok", data.get("ok", True))
        return merged
    return data


def worker_auth(path, payload):
    return worker_request("POST", path, payload, authenticated=False)


def hash_password(p): return hashlib.sha256(("acc|" + p).encode("utf-8")).hexdigest()
def hash_code(c): return hashlib.sha256(("acc-sc|" + str(c)).encode("utf-8")).hexdigest()

def eval_quota(expr):
    try: return int(str(expr).strip())
    except Exception: return None

class RestDB:
    """واجهة بيانات متوافقة مع Worker API."""
    @staticmethod
    def _unwrap(obj, key):
        return obj.get(key) if isinstance(obj, dict) and isinstance(obj.get(key), dict) else (obj or {})
    def get_user(self, email):
        r = worker_request("GET", "/v1/app/profile")
        rec = self._unwrap(r, "profile")
        rec.setdefault("email", email)
        entry = r.get("entry") if isinstance(r, dict) else None
        entry = entry if isinstance(entry, dict) else {}
        entry = dict(entry)
        entry.setdefault("plan", rec.get("plan", "free"))
        entry.setdefault("expr", entry.get("quota", rec.get("quota", FREE_START_INVOICES)))
        entry["expr"] = str(entry["expr"])
        return rec, None, entry
    def save_user(self, rec, mid=None):
        fields = ("name", "phone", "password", "code", "old_password", "old_code", "devices", "revoked")
        payload = {k: rec[k] for k in fields if k in rec}
        if payload: worker_request("PATCH", "/v1/app/profile", payload)
        return mid
    def delete_account(self, rec, mid=None):
        return worker_request("DELETE", "/v1/app/profile", {"code": rec})
    def read_clients(self, rec):
        r = worker_request("GET", "/v1/app/data")
        return (r.get("data") or {}), r.get("version")
    def write_clients(self, rec, mid, data, old_texts=None):
        worker_request("PUT", "/v1/app/data", {"data": data, "version": old_texts})
        return rec
    def sub_update(self, email, plan=None, expr=None, mid=None):
        profile, _mid, entry = self.get_user(email)
        return {"email": email, "plan": entry.get("plan", profile.get("plan", "free")), "expr": str(entry.get("expr", profile.get("quota", FREE_START_INVOICES)))}
    def consume(self, count): return worker_request("POST", "/v1/app/quota/consume", {"count": int(count)})
    def call(self, *a, **kw): raise ApiError("هذه الخدمة غير متاحة في هذا المسار")
    @staticmethod
    def _gone(r): return False
    @staticmethod
    def _err(r): return str(r)[:120]
DB = RestDB()
UPDATES_DIR = os.path.join(DATA_DIR, "updates")
_SCAN_LOCK = threading.Lock()
UPDATE_NAME_RE = re.compile(r"[A-Za-z0-9_.\-]+\.py")
def dl_probe_msg(mid): return None
def dl_probe(mid): return None
def _msg_date(msg): return ""
def scan_downloads(catalog, tolerance=25, verify=True, gap=None):
    """يحدّث الإشعارات وكتالوج التحديثات والخطوط من الخادم (يرفعها المدير من لوحة الإدارة)."""
    if not SESSION_TOKEN: return catalog
    try:
        after = max([int(x) for x in (catalog.get("notes") or {}).keys()] or [0])
        result = worker_request("GET", "/v1/app/notifications?after=" + urllib.parse.quote(str(after)))
        for n in result.get("notifications", []) if isinstance(result, dict) else []:
            nid = str(n.get("id", ""))
            if nid:
                catalog.setdefault("notes", {})[nid] = {"t": str(n.get("message", "")), "d": str(n.get("created_at", ""))}
    except Exception:
        pass
    try:
        cat = worker_request("GET", "/v1/app/catalog")
        if isinstance(cat, dict) and isinstance(cat.get("updates"), dict):
            catalog["updates"] = dict(cat.get("updates") or {})
            catalog["items"] = dict(cat.get("items") or {})
            catalog["gone"] = list(cat.get("gone") or [])
    except Exception:
        pass            # عند انقطاع الاتصال نُبقي آخر كتالوج معروف بدل تفريغه
    catalog.setdefault("items", {})
    catalog.setdefault("updates", {})
    return catalog
def download_channel_file(name, item, dest_dir, min_size=1000):
    """ينزّل ملف تحديث (.py) أو خط (.otf) من الخادم ويحفظه في dest_dir."""
    if os.path.basename(name) != name or not (UPDATE_NAME_RE.fullmatch(name) or FONT_NAME_RE.fullmatch(name)):
        raise ApiError("اسم الملف غير صالح")
    kind = "font" if FONT_NAME_RE.fullmatch(name) else "update"
    r = worker_request("GET", "/v1/app/file?kind=" + kind + "&name=" + urllib.parse.quote(name))
    try:
        raw = base64.b64decode(str((r or {}).get("content_b64") or ""))
    except Exception:
        raise ApiError("الملف المستلم تالف")
    if len(raw) < min_size:
        raise ApiError("الملف المستلم فارغ أو تالف")
    if kind == "font":
        # لا نحفظ HTML/JSON أو رد خطأ الشبكة ثم نحاول تسجيله داخل Kivy.
        if raw[:4] not in (b"OTTO", b"\x00\x01\x00\x00", b"true", b"typ1"):
            raise ApiError("ملف الخط غير صالح")
    else:
        # تحقق نحوي قبل وضع ملف Python في مجلد التحديثات.
        try:
            compile(raw.decode("utf-8-sig"), name, "exec")
        except (UnicodeError, SyntaxError) as e:
            raise ApiError("ملف التحديث غير صالح: " + str(e).splitlines()[0][:100])
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, name)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(raw)
    os.replace(tmp, path)
    return path
def download_font(name, item):
    download_channel_file(name, item, FONT_STORE, min_size=1000)
    register_user_fonts()
# ---- الإشعارات ----
def note_title(text):
    """أول كلمتين ثم ...... (مثل: السلام عليكم......)"""
    words = " ".join(str(text).split()).split(" ")
    if len(words) > 2:
        return " ".join(words[:2]) + "......"
    return " ".join(words)


def wrap_lines(text, width=30):
    out = []
    for para in str(text).split("\n"):
        out.extend(textwrap.wrap(para.strip(), width=width) or [""])
    return out


NOTE_TARGET_RE = re.compile(r"^\s*([A-Za-z0-9._+\-]+@gmail\.com)[\s:،,\-]*(.*)$", re.S | re.I)


def note_split(text):
    """رسالة خاصة: تبدأ بـ  بريد@gmail.com ثم النص. يرجع (البريد المستهدف أو None، النص)."""
    m = NOTE_TARGET_RE.match(str(text or ""))
    if m and m.group(2).strip():
        return m.group(1).lower(), m.group(2).strip()
    return None, str(text or "")


def note_visible(text, email):
    tgt, _body = note_split(text)
    return tgt is None or tgt == (email or "").strip().lower()


def visible_notes(app):
    """الإشعارات التي يراها المستخدم الحالي فقط (العامة + الموجهة لبريده) بعد إزالة سطر البريد."""
    email = (app.data.get("user") or {}).get("email", "")
    out = {}
    for k, n in ((app.data.get("dl") or {}).get("notes") or {}).items():
        if note_visible(n.get("t", ""), email):
            out[k] = dict(n, t=note_split(n.get("t", ""))[1])
    return out


# ---- الأجهزة المتصلة بالحساب ----
MAX_DEVICES = 8


def device_name():
    try:
        if platform == "android":
            from jnius import autoclass
            B = autoclass("android.os.Build")
            nm = (str(B.MANUFACTURER) + " " + str(B.MODEL)).strip()
            if nm:
                return nm[:28]
    except Exception:
        pass
    return (str(platform).capitalize() or "Device")[:28]


def is_revoked(rec, did):
    return did in (rec.get("revoked") or [])


def touch_device(rec, did, name, force=False):
    """يسجّل هذا الجهاز في سجل الحساب. يرجع True لو تغيّر السجل ويلزم حفظه."""
    if not did:
        return False
    devs = [d for d in (rec.get("devices") or []) if isinstance(d, dict)]
    now = now_str()
    changed = False
    cur = next((d for d in devs if d.get("id") == did), None)
    if cur is None:
        devs.append({"id": did, "name": name, "last": now})
        changed = True
    else:
        if cur.get("name") != name:
            cur["name"] = name
            changed = True
        if force or str(cur.get("last", ""))[:13] != now[:13]:
            cur["last"] = now
            changed = True
    if force and did in (rec.get("revoked") or []):      # دخول جديد بكلمة المرور يلغي الإخراج
        rec["revoked"] = [x for x in rec["revoked"] if x != did]
        changed = True
    if len(devs) > MAX_DEVICES:
        devs.sort(key=lambda d: str(d.get("last", "")), reverse=True)
        devs = devs[:MAX_DEVICES]
        changed = True
    if changed:
        rec["devices"] = devs
    return changed


# ---- ملفات التحديث (py) ----
def load_update_file(app, path):
    """ينفّذ ملف تحديث. الملف يستطيع تعريف  apply(app)  ويستخدم كل أدوات التطبيق مباشرة.
    يرجع (نجاح، نص الخطأ)."""
    import types
    name = os.path.basename(path)
    app._loading_update = name
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        mod = types.ModuleType("acc_update_" + re.sub(r"\W", "_", name))
        mod.__dict__.update({k: v for k, v in globals().items() if not k.startswith("__")})
        mod.__dict__["__file__"] = path
        mod.__dict__["APP"] = app
        mod.__dict__["ACC"] = sys.modules.get(__name__)
        exec(compile(src, path, "exec"), mod.__dict__)
        app.update_modules[name] = mod
        fn = mod.__dict__.get("apply")
        if callable(fn):
            fn(app)
        return True, ""
    except Exception as e:
        _write_crash("UPDATE %s: %s" % (name, traceback.format_exc()))
        try:
            app.unload_update(name)          # ما سجّله التحديث قبل الخطأ يُزال
        except Exception:
            pass
        return False, (str(e) or e.__class__.__name__)[:120]
    finally:
        app._loading_update = None


# ============================================================
# 2) النموذج المحاسبي (منطق صرف)
# ============================================================
COLLS = ["customers", "suppliers", "workers", "warehouses", "products",
         "sales", "purchases", "vouchers", "cash"]


def new_db():
    d = {c: [] for c in COLLS}
    d["warehouses"].append({"id": "wh_main", "name": "المخزن الرئيسي", "mod": "2000-01-01 00:00:00"})
    d["deleted"] = {}
    d["meta"] = {"shop": "", "mod": ""}
    return d


def find(lst, rid):
    for r in lst:
        if r.get("id") == rid:
            return r
    return None


def touch(rec):
    rec["mod"] = now_full()
    return rec


def delete_rec(db, coll, rid):
    db[coll] = [r for r in db[coll] if r.get("id") != rid]
    db["deleted"][rid] = now_full()


def merge_db(server, local):
    server, local = server or {}, local or {}
    dele = dict(server.get("deleted") or {})
    dele.update(local.get("deleted") or {})
    if len(dele) > 4000:
        dele = dict(sorted(dele.items(), key=lambda kv: kv[1])[-4000:])
    out = {}
    for c in COLLS:
        m = {}
        for r in list(server.get(c) or []) + list(local.get(c) or []):
            rid = r.get("id")
            if not rid or rid in dele:
                continue
            old = m.get(rid)
            if old is None or r.get("mod", "") >= old.get("mod", ""):
                m[rid] = r
        out[c] = sorted(m.values(), key=lambda r: (r.get("date") or r.get("mod") or ""))
    if not out["warehouses"]:
        out["warehouses"] = new_db()["warehouses"]
    metas = [x for x in (server.get("meta"), local.get("meta")) if x]
    out["meta"] = max(metas, key=lambda x: x.get("mod", "")) if metas else {"shop": "", "mod": ""}
    out["deleted"] = dele
    return out


def line_total(it):
    return max(0.0, it.get("qty", 0) * it.get("price", 0) - it.get("disc", 0))


PAY_LABEL = {"cash": "نقد", "credit": "آجل", "return": "مرتجع"}


def is_return(v):
    return v.get("pay") == "return"


def sale_total(v):
    """إجمالي الفاتورة؛ المرتجع يُحسب بالسالب (يخصم من حساب العميل ويعيد البضاعة للمخزن)."""
    t = sum(line_total(i) for i in v.get("items", []))
    return -t if is_return(v) else t


def sale_cost(v):
    c = sum(i.get("qty", 0) * i.get("cost", 0) for i in v.get("items", []))
    return -c if is_return(v) else c


def purchase_total(p):
    return sum(i.get("qty", 0) * i.get("cost", 0) for i in p.get("items", []))


def cust_balance(db, cid):
    debt = sum(sale_total(v) for v in db["sales"] if v.get("cust") == cid and v.get("pay") in ("credit", "return"))
    paid = sum(x["amount"] for x in db["vouchers"] if x.get("pt") == "c" and x.get("pid") == cid)
    return debt - paid


def sup_balance(db, sid):
    debt = sum(purchase_total(p) for p in db["purchases"] if p.get("sup") == sid and p.get("pay") == "credit")
    paid = sum(x["amount"] for x in db["vouchers"] if x.get("pt") == "s" and x.get("pid") == sid)
    return debt - paid


def stock_map(db, exclude=None):
    """المخزون لكل (صنف، مخزن) محسوباً بالترتيب الزمني؛ لا ينزل عن الصفر أبداً.
    exclude: رقم فاتورة تُستبعد من الحساب (عند تعديلها)."""
    ev = []
    for p in db["purchases"]:
        if exclude and p.get("id") == exclude:
            continue
        for it in p.get("items", []):
            ev.append((p.get("date", ""), 0, p.get("wh"), it["pid"], it["qty"] * it.get("pack", 1)))
    for v in db["sales"]:
        if exclude and v.get("id") == exclude:
            continue
        sg = 1 if is_return(v) else -1          # المرتجع يعود للمخزن
        for it in v.get("items", []):
            ev.append((v.get("date", ""), 1, v.get("wh"), it["pid"], sg * it["qty"] * it.get("pack", 1)))
    ev.sort(key=lambda e: (e[0], e[1]))
    s = {}
    for _d, _o, wh, pid, q in ev:
        k = (pid, wh)
        s[k] = max(0, s.get(k, 0) + q)
    return s


def stock_value(db, smap=None):
    smap = smap or stock_map(db)
    pm = {p.get("id"): p for p in db["products"]}
    total = 0.0
    for (pid, _w), q in smap.items():
        p = pm.get(pid)
        if p and q > 0:
            total += q * p.get("pcost", 0)
    return total


def piece_price(p):
    """سعر بيع الحبة الواحدة للصنف."""
    us = p.get("units") or []
    for u in us:
        if u.get("pack") == 1:
            return u.get("price", 0)
    return (us[0].get("price", 0) / (us[0].get("pack") or 1)) if us else 0


def stock_value_sale(db, smap=None):
    """قيمة البضاعة الحالية بسعر البيع."""
    smap = smap or stock_map(db)
    pm = {p.get("id"): p for p in db["products"]}
    total = 0.0
    for (pid, _w), q in smap.items():
        p = pm.get(pid)
        if p and q > 0:
            total += q * piece_price(p)
    return total


def product_report(db, d1=None, d2=None, sm=None):
    """لكل صنف: المشترى والمباع (بالحبة) خلال الفترة، والمتبقي الآن وقيمته.
    يمرّ على الفواتير مرة واحدة فقط (كان يعيد المرور لكل صنف فيتجمد التطبيق مع كثرة البيانات)."""
    ok = lambda d: (not d1 or d[:10] >= d1) and (not d2 or d[:10] <= d2)
    sm = sm if sm is not None else stock_map(db)
    bought, sold, rem = {}, {}, {}
    for pu in db["purchases"]:
        if ok(pu.get("date", "")):
            for it in pu.get("items", []):
                k = it.get("pid")
                bought[k] = bought.get(k, 0.0) + it["qty"] * it.get("pack", 1)
    for v in db["sales"]:
        if ok(v.get("date", "")):
            sg = -1 if is_return(v) else 1
            for it in v.get("items", []):
                k = it.get("pid")
                sold[k] = sold.get(k, 0.0) + sg * it["qty"] * it.get("pack", 1)
    for (pid, _w), q in sm.items():
        rem[pid] = rem.get(pid, 0) + q
    rows = []
    for p in db["products"]:
        r_ = rem.get(p["id"], 0)
        rows.append({"name": p["name"], "bought": bought.get(p["id"], 0.0),
                     "sold": sold.get(p["id"], 0.0), "rem": r_,
                     "val_cost": r_ * p.get("pcost", 0), "val_sale": r_ * piece_price(p)})
    rows.sort(key=lambda r: r["name"])
    return rows


def cash_ledger(db):
    _cm = {c.get("id"): c.get("name", "") for c in db["customers"]}
    _sm = {c.get("id"): c.get("name", "") for c in db["suppliers"]}
    cn = lambda cid: _cm.get(cid, "")
    sn = lambda sid: _sm.get(sid, "")
    out = []
    for v in db["sales"]:
        if v.get("pay") == "cash":
            out.append((v["date"], "فاتورة نقد - " + cn(v.get("cust")), sale_total(v)))
    for p in db["purchases"]:
        if p.get("pay") == "cash":
            out.append((p["date"], "شراء نقد" + (" - " + sn(p["sup"]) if p.get("sup") else ""),
                        -purchase_total(p)))
    for x in db["vouchers"]:
        if x.get("pt") == "c":
            out.append((x["date"], "سند قبض - " + cn(x["pid"]), x["amount"]))
        else:
            out.append((x["date"], "سند صرف - " + sn(x["pid"]), -x["amount"]))
    for e in db["cash"]:
        out.append((e["date"], e.get("note") or ("إيداع" if e["kind"] == "in" else "سحب"),
                    e["amount"] if e["kind"] == "in" else -e["amount"]))
    out.sort(key=lambda t: t[0], reverse=True)
    return out


def cash_balance(db):
    return sum(a for _d, _l, a in cash_ledger(db))


def summary(db, d1=None, d2=None, sm=None):
    ok = lambda d: (not d1 or d[:10] >= d1) and (not d2 or d[:10] <= d2)
    sales = [v for v in db["sales"] if ok(v["date"])]
    purs = [p for p in db["purchases"] if ok(p["date"])]
    vs = [x for x in db["vouchers"] if ok(x["date"])]
    cs = [e for e in db["cash"] if ok(e["date"])]
    r = {}
    r["sales_n"] = len(sales)
    r["sales_total"] = sum(sale_total(v) for v in sales)
    r["sales_cash"] = sum(sale_total(v) for v in sales if v["pay"] == "cash")
    r["sales_credit"] = r["sales_total"] - r["sales_cash"]
    r["profit"] = r["sales_total"] - sum(sale_cost(v) for v in sales)
    r["purch_total"] = sum(purchase_total(p) for p in purs)
    r["purch_credit"] = sum(purchase_total(p) for p in purs if p["pay"] == "credit")
    r["purch_cash"] = r["purch_total"] - r["purch_credit"]
    r["cust_receipts"] = sum(x["amount"] for x in vs if x["pt"] == "c")
    r["sup_payments"] = sum(x["amount"] for x in vs if x["pt"] == "s")
    r["cash_in"] = sum(e["amount"] for e in cs if e["kind"] == "in")
    r["cash_out"] = sum(e["amount"] for e in cs if e["kind"] == "out")
    sm = sm if sm is not None else stock_map(db)
    r["stock_value"] = stock_value(db, sm)
    r["stock_sale"] = stock_value_sale(db, sm)
    r["cash"] = cash_balance(db)
    cb, sb = {}, {}
    for v in db["sales"]:
        if v.get("pay") in ("credit", "return"):
            cb[v.get("cust")] = cb.get(v.get("cust"), 0) + sale_total(v)
    for p_ in db["purchases"]:
        if p_.get("pay") == "credit":
            sb[p_.get("sup")] = sb.get(p_.get("sup"), 0) + purchase_total(p_)
    for x in db["vouchers"]:
        d_ = cb if x.get("pt") == "c" else sb
        d_[x.get("pid")] = d_.get(x.get("pid"), 0) - x["amount"]
    r["cust_debts"] = sum(max(0, cb.get(c["id"], 0)) for c in db["customers"])
    r["sup_owed"] = sum(max(0, sb.get(s_["id"], 0)) for s_ in db["suppliers"])
    r["net"] = r["stock_value"] + r["cash"] + r["cust_debts"] - r["sup_owed"]
    return r


def party_ops(db, kind, pid):
    ops = []
    if kind == "c":
        for v in db["sales"]:
            if v.get("cust") == pid:
                ops.append({"date": v["date"],
                            "label": "مرتجع" if is_return(v) else "فاتورة" + (" (نقد)" if v["pay"] == "cash" else ""),
                            "amount": sale_total(v),
                            "eff": sale_total(v) if v["pay"] in ("credit", "return") else 0,
                            "type": "sale", "rec": v})
    else:
        for p in db["purchases"]:
            if p.get("sup") == pid:
                ops.append({"date": p["date"], "label": "فاتورة شراء" + (" (نقد)" if p["pay"] == "cash" else ""),
                            "amount": purchase_total(p), "eff": purchase_total(p) if p["pay"] == "credit" else 0,
                            "type": "purchase", "rec": p})
    for x in db["vouchers"]:
        if x.get("pt") == kind and x.get("pid") == pid:
            ops.append({"date": x["date"], "label": "سند قبض" if kind == "c" else "سند صرف",
                        "amount": x["amount"], "eff": -x["amount"], "type": "voucher", "rec": x})
    ops.sort(key=lambda o: o["date"])
    return ops


# ============================================================
# 3) أدوات الواجهة المشتركة
# ============================================================
def set_text(lbl, text, color=None):
    if color is not None:
        lbl._color = tuple(color)
    lbl.text = text


import weakref
_PICKERS = weakref.WeakSet()          # كل حقول الاختيار (لنعرف أي حقل ضُغط عليه)


def picker_btn(text, color=None, h=dp(48), fs=dp(15)):
    b = mk_btn(text, height=h, background_color=color or T.SOFT_BLUE,
               color=T.PRIMARY if color is None else WHITE, font_size=fs)
    try:
        _PICKERS.add(b)
    except Exception:
        pass
    return b


def lab_in(label, hint="", flt=None, text="", **kw):
    box = BoxLayout(orientation="vertical", spacing=dp(4), size_hint_y=None, height=dp(72))
    box.add_widget(mk_text(label, dp(12), T.MUTED, height=dp(20)))
    inp = mk_input(hint_text=hint, input_filter=flt, size_hint_y=None, height=dp(46), **kw)
    if text != "":
        inp.text = str(text)
    box.add_widget(inp)
    return box, inp


def two_cols(a, b):
    row = BoxLayout(size_hint_y=None, height=dp(72), spacing=dp(10))
    row.add_widget(a)
    row.add_widget(b)
    return row


def kv_row(label, value, color=None, h=dp(48)):
    row = CardBox(orientation="horizontal", size_hint_y=None, height=h,
                  padding=[dp(14), dp(4)], radius=dp(12))
    row.add_widget(mk_text(value, dp(17), color or T.PRIMARY, halign="left", hint_x=0.5))
    row.add_widget(mk_text(label, dp(14), T.TEXT, hint_x=0.5))
    return row


def party_row(name, phone, bal, kind, on_tap):
    if kind == "c":
        col, st = (T.RED, "عليه دين") if bal > 0 else (T.GREEN, "لا يوجد دين")
    elif kind == "w":
        col, st = T.ORANGE, "إجمالي ما صُرف له"
    else:
        col, st = (T.ORANGE, "عليك له") if bal > 0 else (T.GREEN, "لا يوجد")
    row = CardBox(orientation="horizontal", size_hint_y=None, height=dp(78),
                  padding=[dp(12), dp(8)], spacing=dp(10), on_tap=on_tap)
    left = BoxLayout(orientation="vertical", size_hint_x=None, width=dp(120))
    left.add_widget(mk_text(f"{money_signed(bal)} ريال", dp(16), col, halign="left", hint_y=0.55))
    left.add_widget(mk_text(st, dp(12), T.MUTED, halign="left", hint_y=0.45))
    row.add_widget(left)
    mid = BoxLayout(orientation="vertical")
    mid.add_widget(mk_text(name, dp(17), T.TEXT, hint_y=0.55))
    mid.add_widget(mk_text(phone or "بدون رقم", dp(12), T.MUTED, hint_y=0.45))
    row.add_widget(mid)
    av = Avatar(name)
    av.pos_hint = {"center_y": 0.5}
    row.add_widget(av)
    return row


# ============================================================
# 3-ب) ودجات الهيدر والقائمة الجانبية
# ============================================================
class ThemeToggle(Widget):
    """زر الوضع الليلي: هلال في الوضع النهاري، وشمس في الوضع الليلي."""

    def __init__(self, app, bg, confirm=False, **kw):
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("size", (dp(42), dp(42)))
        super().__init__(**kw)
        self.app = app
        self._confirm = confirm
        self._bgc = tuple(bg)
        self.bind(pos=self._draw, size=self._draw)
        self._draw()

    def _draw(self, *a):
        self.canvas.clear()
        cx, cy = self.center_x, self.center_y
        r = min(self.width, self.height) * 0.25
        with self.canvas:
            if is_dark():
                Color(1, 0.84, 0.28, 1)
                Ellipse(pos=(cx - r * 0.65, cy - r * 0.65), size=(r * 1.3, r * 1.3))
                for i in range(8):
                    ang = i * math.pi / 4.0
                    Line(points=[cx + math.cos(ang) * r * 1.05, cy + math.sin(ang) * r * 1.05,
                                 cx + math.cos(ang) * r * 1.55, cy + math.sin(ang) * r * 1.55],
                         width=dp(1.7), cap="round")
            else:
                Color(1, 1, 1, 1)
                Ellipse(pos=(cx - r * 1.05, cy - r), size=(2 * r, 2 * r))
                Color(*self._bgc)
                rr = r * 0.88
                Ellipse(pos=(cx + r * 0.45 - rr, cy + r * 0.28 - rr), size=(2 * rr, 2 * rr))

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            touch.grab(self)
            return True
        return False

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            if self.collide_point(*touch.pos):
                if self._confirm:
                    Clock.schedule_once(lambda dt: confirm_popup(
                        "الوضع الليلي", "سيُعاد رسم الصفحة وقد تفقد ما لم تحفظه بعد. هل تريد المتابعة؟",
                        self.app.toggle_theme, yes_text="متابعة", color=T.BLUE), 0)
                else:
                    Clock.schedule_once(lambda dt: self.app.toggle_theme(), 0)
            return True
        return False


class HamburgerBtn(Widget):
    """ثلاثة خطوط فوق بعض (زر القائمة الجانبية)."""

    def __init__(self, on_press, **kw):
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("size", (dp(42), dp(42)))
        super().__init__(**kw)
        self._cb = on_press
        self.bind(pos=self._draw, size=self._draw)
        self._draw()

    def _draw(self, *a):
        self.canvas.clear()
        cx, cy = self.center_x, self.center_y
        half = dp(11)
        with self.canvas:
            Color(1, 1, 1, 1)
            for dy in (-dp(7), 0, dp(7)):
                Line(points=[cx - half, cy + dy, cx + half, cy + dy], width=dp(1.8), cap="round")

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            touch.grab(self)
            return True
        return False

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            if self.collide_point(*touch.pos):
                cb = self._cb
                Clock.schedule_once(lambda dt: cb(), 0)
            return True
        return False


class SwitchBtn(Widget):
    """مفتاح تشغيل/إيقاف مرسوم (أخضر = مفعّل)."""

    def __init__(self, on=True, on_change=None, enabled=True, **kw):
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("size", (dp(52), dp(30)))
        super().__init__(**kw)
        self.on = bool(on)
        self._cb = on_change
        self.enabled = enabled
        self.bind(pos=self._draw, size=self._draw)
        self._draw()

    def _draw(self, *a):
        self.canvas.clear()
        h = self.height
        r = h / 2 - dp(3)
        with self.canvas:
            if self.on and self.enabled:
                Color(*T.GREEN)
            else:
                Color(*T.GRAY_BTN)
            RoundedRectangle(pos=self.pos, size=self.size, radius=[h / 2])
            Color(1, 1, 1, 1 if self.enabled else 0.6)
            kx = (self.right - h / 2) if self.on else (self.x + h / 2)
            Ellipse(pos=(kx - r, self.center_y - r), size=(2 * r, 2 * r))

    def on_touch_down(self, touch):
        if self.enabled and self.collide_point(*touch.pos):
            touch.grab(self)
            return True
        return False

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            if self.collide_point(*touch.pos):
                self.on = not self.on
                self._draw()
                cb = self._cb
                if cb:
                    Clock.schedule_once(lambda dt: cb(self.on), 0)
            return True
        return False


def section_title(page, text):
    page.add(mk_text(text, dp(14), T.PRIMARY, halign="right", height=dp(30)))


def menu_row(title, sub="", on_tap=None, color=None):
    """صف قائمة أنيق (عنوان + سطر توضيحي صغير)."""
    row = CardBox(orientation="horizontal", size_hint_y=None, height=dp(58) if sub else dp(50),
                  padding=[dp(14), dp(6)], spacing=dp(8), radius=dp(12), on_tap=on_tap)
    c = BoxLayout(orientation="vertical")
    c.add_widget(mk_text(title, dp(15), color or T.TEXT, hint_y=0.55 if sub else 1))
    if sub:
        c.add_widget(mk_text(sub, dp(11), T.MUTED, hint_y=0.45))
    row.add_widget(c)
    return row


def switch_row(title, sub, on, on_change, enabled=True, locked=None):
    """صف فيه مفتاح تشغيل/إيقاف على اليسار والعنوان على اليمين."""
    row = CardBox(orientation="horizontal", size_hint_y=None, height=dp(66),
                  padding=[dp(14), dp(8)], spacing=dp(10), radius=dp(12),
                  on_tap=(None if enabled else locked))
    sw = SwitchBtn(on, on_change, enabled)
    sw.pos_hint = {"center_y": 0.5}
    row.add_widget(sw)
    c = BoxLayout(orientation="vertical")
    c.add_widget(mk_text(title, dp(15), T.TEXT, hint_y=0.5))
    c.add_widget(mk_text(sub, dp(11), T.MUTED, hint_y=0.5))
    row.add_widget(c)
    return row, sw


class BellBtn(Widget):
    """زر الإشعارات (جرس) مع نقطة حمراء عند وجود إشعار غير مقروء."""

    def __init__(self, on_press, count=0, **kw):
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("size", (dp(42), dp(42)))
        super().__init__(**kw)
        self._cb = on_press
        self.count = count
        self.bind(pos=self._draw, size=self._draw)
        self._draw()

    def set_count(self, n):
        self.count = n
        self._draw()

    def _draw(self, *a):
        self.canvas.clear()
        cx, cy = self.center_x, self.center_y
        r = dp(8)
        pts = [cx - r * 1.35, cy - r * 0.8, cx - r * 0.95, cy - r * 0.55, cx - r * 0.95, cy + r * 0.1]
        for i in range(13):
            ang = math.pi - i * math.pi / 12.0
            pts += [cx + math.cos(ang) * r * 0.95, cy + r * 0.1 + math.sin(ang) * r * 0.95]
        pts += [cx + r * 0.95, cy - r * 0.55, cx + r * 1.35, cy - r * 0.8]
        with self.canvas:
            Color(1, 1, 1, 1)
            Line(points=pts, width=dp(1.6), close=True, joint="round")
            Ellipse(pos=(cx - r * 0.3, cy - r * 1.3), size=(r * 0.6, r * 0.5))
            if self.count > 0:
                Color(0.93, 0.22, 0.22, 1)
                Ellipse(pos=(cx + r * 0.3, cy + r * 0.55), size=(dp(11), dp(11)))

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            touch.grab(self)
            return True
        return False

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            if self.collide_point(*touch.pos):
                cb = self._cb
                Clock.schedule_once(lambda dt: cb(), 0)
            return True
        return False


class SideDrawer(FloatLayout):
    """قائمة جانبية بنصف الشاشة تظهر فوق الصفحة من اليسار.
    تُغلق بالسحب نحو اليسار أو بالضغط خارجها."""

    def __init__(self, title, items, bottom=None, on_close=None, **kw):
        kw.setdefault("size_hint", (1, 1))
        super().__init__(**kw)
        self._on_close = on_close
        self._closing = False
        self.pw = max(dp(210), Window.width * 0.55)
        with self.canvas.before:
            self._scol = Color(0, 0, 0, 0)
            self._srect = Rectangle(pos=self.pos, size=self.size)
        panel = BoxLayout(orientation="vertical", size_hint=(None, 1), width=self.pw,
                          padding=[dp(10), dp(18), dp(10), dp(16)], spacing=dp(8))
        panel.x = -self.pw
        with panel.canvas.before:
            Color(*T.CARD)
            self._prect = RoundedRectangle(pos=panel.pos, size=panel.size,
                                           radius=[0, dp(22), dp(22), 0])
        self.panel = panel
        panel.bind(pos=self._upd_panel, size=self._upd_panel, x=self._on_px)
        panel.add_widget(mk_text(title, dp(18), T.PRIMARY, halign="center", height=dp(36)))
        panel.add_widget(self._line())
        for label, fn in items:
            panel.add_widget(self._item(label, fn))
        panel.add_widget(Widget())
        if bottom:
            panel.add_widget(self._line())
            panel.add_widget(self._item(bottom[0], bottom[1], T.PRIMARY))
        self.add_widget(panel)
        self.bind(pos=self._upd_scrim, size=self._upd_scrim)

    # ---- رسم ----
    def _line(self):
        w = Widget(size_hint_y=None, height=dp(2))
        with w.canvas:
            Color(*T.BORDER)
            r = Rectangle(pos=w.pos, size=w.size)

        def _u(*a):
            r.pos, r.size = w.pos, w.size
        w.bind(pos=_u, size=_u)
        return w

    def _item(self, label, fn, color=None):
        row = CardBox(orientation="horizontal", size_hint_y=None, height=dp(52),
                      padding=[dp(12), dp(4)], radius=dp(12), on_tap=lambda: self._tap(fn))
        row.add_widget(mk_text(label, dp(15), color or T.TEXT))
        return row

    def _tap(self, fn):
        self.close(immediate=True)
        fn()

    def _upd_panel(self, *a):
        self._prect.pos, self._prect.size = self.panel.pos, self.panel.size

    def _upd_scrim(self, *a):
        self._srect.pos, self._srect.size = self.pos, self.size
        self.panel.y = self.y

    def _on_px(self, *a):
        frac = max(0.0, min(1.0, 1.0 + self.panel.x / float(self.pw)))
        self._scol.a = 0.45 * frac

    # ---- فتح وإغلاق ----
    def open(self):
        Animation(x=0, duration=0.22, t="out_cubic").start(self.panel)

    def close(self, immediate=False):
        if self._closing:
            return
        self._closing = True

        def done(*a):
            if self.parent is not None:
                self.parent.remove_widget(self)
            if self._on_close:
                try:
                    self._on_close()
                except Exception:
                    pass
        if immediate:
            Animation.cancel_all(self.panel)
            done()
        else:
            anim = Animation(x=-self.pw, duration=0.18, t="in_cubic")
            anim.bind(on_complete=done)
            anim.start(self.panel)

    # ---- اللمس ----
    def on_touch_down(self, touch):
        if self._closing:
            return True
        if not self.panel.collide_point(*touch.pos):
            touch.ud["dr_scrim"] = True
            return True
        touch.ud["dr_start"] = (touch.x, self.panel.x)
        super().on_touch_down(touch)
        return True

    def on_touch_move(self, touch):
        if self._closing:
            return True
        st = touch.ud.get("dr_start")
        if st is None:
            return True
        super().on_touch_move(touch)
        dx = touch.x - st[0]
        if touch.ud.get("dr_drag") or abs(dx) > dp(14):
            touch.ud["dr_drag"] = True
            Animation.cancel_all(self.panel)
            self.panel.x = min(0, st[1] + dx)
        return True

    def on_touch_up(self, touch):
        if self._closing:
            return True
        if touch.ud.get("dr_scrim"):
            self.close()
            return True
        super().on_touch_up(touch)
        if touch.ud.get("dr_drag"):
            if self.panel.x < -self.pw * 0.3:
                self.close()
            else:
                Animation(x=0, duration=0.15).start(self.panel)
        return True


def shop_name_popup(app):
    """نافذة تغيير اسم الدكان (يظهر في الفواتير)."""
    if not app.need_edit():
        return
    box = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(10))
    b1, nm = lab_in("اسم الدكان", "", None, (app.db.get("meta") or {}).get("shop", ""))
    box.add_widget(b1)
    box.add_widget(Widget())
    bb = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(8))
    sv = mk_btn("حفظ", background_color=T.GREEN)
    cn = mk_btn("إلغاء", background_color=T.GRAY_BTN)
    bb.add_widget(sv)
    bb.add_widget(cn)
    box.add_widget(bb)
    pop = make_arabic_popup("اسم الدكان", box, size_hint=(0.92, 0.42))

    def save(*a):
        app.db["meta"] = {"shop": nm.text.strip(), "mod": now_full()}
        app.save()
        pop.dismiss()
    sv.bind(on_release=save)
    cn.bind(on_release=lambda *a: pop.dismiss())
    pop.open()


def all_ops(db):
    """كل الفواتير والسندات مرتبة من الأحدث للأقدم (للصفحة الرئيسية)."""
    _cm = {c.get("id"): c.get("name", "") for c in db["customers"]}
    _sm = {c.get("id"): c.get("name", "") for c in db["suppliers"]}
    cn = lambda cid: _cm.get(cid, "")
    sn = lambda sid: _sm.get(sid, "")
    out = []
    for v in db["sales"]:
        out.append({"type": "sale", "id": v["id"], "date": v.get("date", ""),
                    "amount": sale_total(v), "color": T.RED if is_return(v) else T.ORANGE,
                    "party": cn(v.get("cust")),
                    "label": {"cash": "فاتورة (نقد)", "return": "مرتجع"}.get(v.get("pay"), "فاتورة (آجل)")})
    for p in db["purchases"]:
        out.append({"type": "purchase", "id": p["id"], "date": p.get("date", ""),
                    "amount": purchase_total(p), "color": T.BLUE, "party": sn(p.get("sup")),
                    "label": "فاتورة شراء" + (" (نقد)" if p.get("pay") == "cash" else " (آجل)")})
    for x in db["vouchers"]:
        isc = x.get("pt") == "c"
        out.append({"type": "voucher", "id": x["id"], "date": x.get("date", ""),
                    "amount": x.get("amount", 0), "color": T.GREEN if isc else T.TEAL,
                    "party": (cn if isc else sn)(x.get("pid")),
                    "label": "سند قبض" if isc else "سند صرف"})
    out.sort(key=lambda o: o["date"], reverse=True)
    return out


def add_glow(screen):
    """توهّج أخضر مزرق ناعم في خلفية الصفحة (الوضع الليلي) مثل التصميم المرجعي."""
    if not is_dark():
        return
    layers = []
    with screen.canvas.before:
        for i in range(8):
            Color(T.GLOW[0], T.GLOW[1], T.GLOW[2], 0.032)
            layers.append(Ellipse(pos=(0, 0), size=(1, 1)))

    def upd(*a):
        w, h = screen.size
        cx, cy = screen.x + w / 2.0, screen.y + h * 0.60
        for i, e in enumerate(layers):
            k = (8 - i) / 8.0
            rw, rh = w * 1.05 * k, h * 0.46 * k
            e.pos, e.size = (cx - rw / 2.0, cy - rh / 2.0), (rw, rh)
    screen.bind(pos=upd, size=upd)
    upd()


class Page(Screen):
    """صفحة قياسية: هيدر + محتوى قابل للتمرير + شريط سفلي."""

    def __init__(self, app, title, color=None, header=None, right=None, theme_toggle=False,
                 theme_confirm=False, **kw):
        super().__init__(**kw)
        self.app = app
        root = BoxLayout(orientation="vertical", spacing=0)
        if header is None:
            hc = color or T.HEADER
            if right is None and theme_toggle:
                right = ThemeToggle(app, hc, confirm=theme_confirm)
            _nb = None if self.NAV in ("settings", "reports", "search") else (lambda *a: app.back())
            header = make_header(title, hc, on_back=_nb, right_widget=right)
        root.add_widget(header)
        self.pinned = BoxLayout(orientation="vertical", size_hint_y=None, height=0,
                                padding=[dp(12), dp(6), dp(12), dp(2)], spacing=dp(6))
        self.pinned.bind(minimum_height=self._pin_h)
        root.add_widget(self.pinned)
        self.scroll, self.body = make_scroll_list(spacing=dp(8))
        self.body.padding = [dp(12), dp(8), dp(12), dp(16)]
        root.add_widget(self.scroll)
        self.footer = BoxLayout(size_hint_y=None, height=0,
                                padding=[dp(12), dp(6), dp(12), dp(10)], spacing=dp(8))
        root.add_widget(self.footer)
        self.add_widget(root)
        add_glow(self)

    # ---- الشريط السفلي والزر العائم (للصفحات التي تحدد NAV) ----
    NAV = None

    def on_pre_enter(self, *a):
        if self.NAV and not getattr(self, "_nav_done", False):
            self._nav_done = True
            try:
                self.attach_nav(self.NAV)
            except Exception:
                _write_crash("NAV: " + traceback.format_exc())

    def attach_nav(self, active):
        app = self.app
        acts = [] if app.data.get("role") == "viewer" else fab_actions(app)
        self.footer.clear_widgets()
        self.footer.padding = [0, 0, 0, 0]
        self.footer.spacing = 0
        self.footer.height = NAV_H
        self.footer.add_widget(BottomNav(app, active, has_fab=bool(acts)))
        self.fab = None
        if acts:
            self.fab = FabLayer(acts)
            self.add_widget(self.fab)

    def add(self, w):
        self.body.add_widget(w)
        return w

    def _pin_h(self, w, h):
        w.height = h if len(w.children) else 0

    def add_pinned(self, w):
        """عنصر ثابت أعلى الصفحة لا يتحرك مع التمرير."""
        self.pinned.add_widget(w)
        return w

    def label(self, text, size=13, color=None, h=dp(26), halign="right"):
        return self.add(mk_text(text, dp(size), color or T.MUTED, halign=halign, height=h))

    def field(self, label, hint="", text="", flt=None, **kw):
        box, inp = lab_in(label, hint, flt, text, **kw)
        self.add(box)
        return inp

    def add_footer(self, *btns):
        self.footer.height = dp(66)
        for b in btns:
            self.footer.add_widget(b)

    def msg(self, text, color=None):
        if not hasattr(self, "_msg"):
            self._msg = mk_text("", dp(13), T.RED, halign="center", height=dp(28))
            self.add(self._msg)
        set_text(self._msg, text, color or T.RED)


# ============================================================
# 4) الدخول والتسجيل
# ============================================================
def pick_google(cb, fail):
    """يعرض كل حسابات قوقل في الجهاز ليختار منها (أندرويد)."""
    if platform != "android":
        fail()
        return
    try:
        from jnius import autoclass
        from android import activity, mActivity
        AM = autoclass("android.accounts.AccountManager")
        intent = AM.newChooseAccountIntent(None, None, ["com.google"], None, None, None, None)

        def on_result(req, res, data):
            if req != 7731:
                return
            try:
                activity.unbind(on_activity_result=on_result)
            except Exception:
                pass
            if res == -1 and data is not None:
                name = str(data.getStringExtra(AM.KEY_ACCOUNT_NAME) or "")
                if name:
                    Clock.schedule_once(lambda dt: cb(name.lower()), 0)
                    return
            Clock.schedule_once(lambda dt: fail(), 0)

        activity.bind(on_activity_result=on_result)
        mActivity.startActivityForResult(intent, 7731)
    except Exception as e:
        print(f"[GOOGLE] {e}")
        fail()


GMAIL = "@gmail.com"


class GmailInput(BoxLayout):
    """حقل بريد: اللاحقة @gmail.com ثابتة والمستخدم يكتب ما قبلها فقط."""

    def __init__(self, hint="اسم البريد", **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(50))
        super().__init__(orientation="horizontal", spacing=dp(6), **kw)
        self.inp = mk_input(hint_text=hint, size_hint_y=None, height=dp(50))
        self.add_widget(self.inp)
        self.add_widget(mk_text(GMAIL, dp(14), T.MUTED, halign="center", height=dp(50),
                                width=dp(104)))

    @staticmethod
    def _local(s):
        s = (s or "").strip().lower().replace(" ", "")
        return s.split("@")[0]

    @property
    def text(self):
        loc = self._local(self.inp.text)
        return loc + GMAIL if loc else ""

    @text.setter
    def text(self, v):
        self.inp.text = self._local(v)

    def lock(self):
        """تعطيل الكتابة مع إبقاء النص."""
        self.opacity = 0.7
        try:
            self.inp._input.disabled = True
        except Exception:
            self.disabled = True


def tg_diagnose():
    """يفحص اتصال الجهاز بالانترنت ويرجع سطراً واحداً: متصل / غير متصل."""
    import socket
    for host, port in (("8.8.8.8", 53), ("1.1.1.1", 53)):
        try:
            sk = socket.create_connection((host, port), timeout=4)
            sk.close()
            return ["✓ أنت متصل بالانترنت"]
        except Exception:
            continue
    return ["✗ أنت غير متصل بالانترنت"]


def diagnose_popup():
    pop_lbl = mk_text("جاري الفحص...", dp(14), T.MUTED, halign="center", height=dp(40))
    box = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(8))
    sc, lay = make_scroll_list(spacing=dp(2))
    lay.add_widget(pop_lbl)
    box.add_widget(sc)
    ok = mk_btn("إغلاق", height=dp(46), background_color=T.GRAY_BTN)
    box.add_widget(ok)
    pop = make_arabic_popup("فحص الاتصال بالانترنت", box, size_hint=(0.9, 0.38))
    ok.bind(on_release=lambda *a: pop.dismiss())
    pop.open()

    def w():
        try:
            lines = tg_diagnose()
        except Exception as e:
            lines = ["✗ خطأ: " + str(e)[:80]]

        def done(dt):
            lay.clear_widgets()
            for ln in lines:
                bad = ln.startswith("✗")
                lay.add_widget(mk_text(ln, dp(14), T.RED if bad else (T.GREEN if ln.startswith("✓")
                                                                    else T.MUTED), height=dp(30)))
            schedule_refresh(0.1)
        Clock.schedule_once(done, 0)
    threading.Thread(target=w, daemon=True).start()


# ============================================================
# الشعار المتحرك + شاشة البداية
# ============================================================
SPLASH_SECONDS = 3.6
_NAVY = (10 / 255., 42 / 255., 99 / 255., 1)


def _lg_hex(h, a=1.0):
    h = h.lstrip("#")
    return (int(h[0:2], 16) / 255., int(h[2:4], 16) / 255., int(h[4:6], 16) / 255., a)


def _lg_p(t, start, dur):
    return 0.0 if t <= start else 1.0 if t >= start + dur else (t - start) / dur


def _lg_back(p, c=1.70158):
    p -= 1
    return 1 + (c + 1) * p ** 3 + c * p ** 2


def _lg_out(p):
    return 1 - (1 - p) ** 3


class AnimatedLogo(FloatLayout):
    """الشعار المتحرك. full=True: الأيقونة + الاسم + الجملة + نقاط التحميل. full=False: الأيقونة فقط."""

    def __init__(self, full=True, bg=False, **kw):
        super().__init__(**kw)
        self.full, self.bg, self.t, self._seen = full, bg, 0.0, False
        self.art = Widget(size_hint=(1, 1))
        self.add_widget(self.art)
        if full:
            self.name_w = DrawnText(text="دفتر المحاسب", font_size=dp(30), color=WHITE, halign="center",
                                    size_hint=(None, None))
            self.sub_w = DrawnText(text="حساباتك بدقة وأمان", font_size=dp(14), color=_lg_hex("#9CC3F5"),
                                   halign="center", size_hint=(None, None))
            for w in (self.name_w, self.sub_w):
                w.disabled = True
                w.opacity = 0
                self.add_widget(w)
        self.bind(size=lambda *a: self.draw(), pos=lambda *a: self.draw())
        self._ev = Clock.schedule_interval(self._tick, 1 / 45.)

    def restart(self):
        self.t = 0.0

    def _tick(self, dt):
        if self.parent is not None:
            self._seen = True
        elif self._seen:
            return False                       # أُزيل من الشاشة: نوقف الحركة
        self.t += dt
        self.draw()

    def draw(self):
        t, W, H = self.t, self.width, self.height
        if W < 4 or H < 4:
            return
        if self.full:
            S = min(W / 380.0, H / 440.0) * 0.95
            cx, cy = self.x + W / 2.0, self.y + H / 2.0 + 38 * S
            idle = 3.6
        else:
            S = min(W, H) / 200.0
            cx, cy = self.x + W / 2.0, self.y + H / 2.0
            idle = 2.6
        br = 1 + 0.035 * (0.5 - 0.5 * math.cos((t - idle) * 2.2)) if t > idle else 1.0
        K = S * br

        def px(x):
            return cx + (x - 190) * K

        def py(y):
            return cy + (190 - y) * K

        def rect(x, y, w, h, sx=1.0, sy=1.0, ax=0.5, ay=0.5):
            nw, nh = w * sx, h * sy
            nx, ny = x + ax * w - ax * nw, y + ay * h - ay * nh
            return (px(nx), py(ny + nh)), (nw * K, nh * K)

        def rr(x, y, w, h, r, **k):
            pos, size = rect(x, y, w, h, **k)
            if size[0] > 0.5 and size[1] > 0.5:
                RoundedRectangle(pos=pos, size=size, radius=[min(r * K, size[0] / 2, size[1] / 2)])

        c = self.art.canvas
        c.clear()
        with c:
            if self.bg:
                Color(*_NAVY)
                Rectangle(pos=self.pos, size=self.size)
            k = _lg_back(_lg_p(t, 0.0, 0.7))
            Color(*_lg_hex("#1B62BE"))
            rr(100, 100, 180, 180, 44, sx=k, sy=k)
            Color(*_lg_hex("#2B6CC4", 0.55))
            rr(100, 100, 180, 90, 44, sx=k, sy=k, ay=0.0)
            k = _lg_back(_lg_p(t, 0.35, 0.6))
            Color(1, 1, 1, 1)
            rr(138, 136, 104, 104, 17, sx=k, sy=k)
            Color(*_lg_hex("#CFD6E0"))
            rr(150.5, 149, 54, 5, 2.5, sx=_lg_out(_lg_p(t, 0.8, 0.5)), ax=0.0)
            rr(150.5, 159, 31, 5, 2.5, sx=_lg_out(_lg_p(t, 0.95, 0.5)), ax=0.0)
            for (x, y, w, h, col, st) in ((154, 202, 18, 24, "#5FAEF0", 1.1), (179, 186, 18, 40, "#1C78DC", 1.3),
                                          (204, 166, 18, 60, "#FFB300", 1.5)):
                Color(*_lg_hex(col))
                rr(x, y, w, h, 4, sy=_lg_back(_lg_p(t, st, 0.6)), ay=1.0)
            Color(*_lg_hex("#B0BEC5"))
            rr(149, 226, 64, 3, 1.5, sx=_lg_out(_lg_p(t, 1.0, 0.5)), ax=0.0)
            k = _lg_back(_lg_p(t, 2.0, 0.55), 2.4)
            if k > 0.01:
                if t > 3.0:
                    p = ((t - 3.0) % 2.0) / 2.0
                    Color(*_lg_hex("#4CD27A", 0.55 * (1 - p)))
                    Line(circle=(px(233), py(233), 20 * K * (1 + 0.9 * p)), width=max(1.0, 1.5 * K))
                Color(1, 1, 1, 1)
                Ellipse(pos=(px(233 - 24 * k), py(233 + 24 * k)), size=(48 * k * K,) * 2)
                Color(*_lg_hex("#2EBB62"))
                Ellipse(pos=(px(233 - 20 * k), py(233 + 20 * k)), size=(40 * k * K,) * 2)
                d = _lg_p(t, 2.35, 0.45)
                if d > 0:
                    a, b, e = (223, 233), (230.5, 240.5), (243.5, 225)
                    l1, l2 = 10.6, 20.5
                    run = d * (l1 + l2)
                    if run <= l1:
                        f = run / l1
                        pts = [a, (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)]
                    else:
                        f = (run - l1) / l2
                        pts = [a, b, (b[0] + (e[0] - b[0]) * f, b[1] + (e[1] - b[1]) * f)]
                    flat = []
                    for (x, y) in pts:
                        flat += [px(x), py(y)]
                    Color(1, 1, 1, 1)
                    Line(points=flat, width=max(1.5, 3.1 * K), cap="round", joint="round")
            sp = _lg_p(t, 2.7, 1.1)
            if 0 < sp < 1:
                sh = -140 + 340 * (sp * sp * (3 - 2 * sp))
                al = 0.28 * math.sin(sp * math.pi)
                pos, size = rect(100, 100, 180, 180)
                StencilPush()
                RoundedRectangle(pos=pos, size=size, radius=[44 * K])
                StencilUse()
                Color(1, 1, 1, al)
                vs = []
                for (x, y) in ((100 + sh, 280), (135 + sh, 280), (175 + sh, 100), (140 + sh, 100)):
                    vs += [px(x), py(y), 0, 0]
                Mesh(vertices=vs, indices=[0, 1, 2, 3], mode="triangle_fan")
                StencilUnUse()
                RoundedRectangle(pos=pos, size=size, radius=[44 * K])
                StencilPop()
            if self.full and t > 3.3:
                for i, x in enumerate((176, 190, 204)):
                    a = 0.25 + 0.75 * (0.5 + 0.5 * math.sin((t - 3.3 - i * 0.2) * 5.2))
                    Color(*_lg_hex("#9CC3F5", a))
                    Ellipse(pos=(px(x) - 3.5 * S, py(400) - 3.5 * S), size=(7 * S, 7 * S))
        if self.full:
            for w, y, hgt, start in ((self.name_w, 336, dp(46), 2.8), (self.sub_w, 366, dp(26), 3.05)):
                p = _lg_out(_lg_p(t, start, 0.7))
                w.size = (W, hgt)
                w.pos = (self.x, cy + (190 - y) * S - hgt / 2.0 - (1 - p) * dp(8))
                w.opacity = p


class SplashScreen(Screen):
    """شاشة البداية: الشعار المتحرك على خلفية زرقاء داكنة."""

    def __init__(self, **kw):
        super().__init__(**kw)
        with self.canvas.before:
            Color(*_NAVY)
            self._bgr = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._upd, size=self._upd)
        self.add_widget(AnimatedLogo(full=True))

    def _upd(self, *a):
        self._bgr.pos, self._bgr.size = self.pos, self.size


class CircleBtn(Widget):
    """زر دائري: kind = arrow (سهم التالي) أو eye (مشاهدة فقط)."""

    def __init__(self, kind="arrow", color=None, side=dp(46), on_tap=None, **kw):
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("size", (side, side))
        super().__init__(**kw)
        self.kind, self.color, self.on_tap, self.on, self._down = kind, color or T.GREEN, on_tap, True, False
        self.bind(pos=self._draw, size=self._draw)
        self._draw()

    def set_state(self, on=None, color=None):
        if on is not None:
            self.on = on
        if color is not None:
            self.color = color
        self._draw()

    def set_visible(self, v):
        self.opacity = 1 if v else 0
        self.disabled = not v

    def _draw(self, *a):
        self.canvas.clear()
        w, h = self.size
        r = min(w, h) / 2.0
        cx, cy = self.x + w / 2.0, self.y + h / 2.0
        with self.canvas:
            col = list(self.color)
            if self._down:
                col = [v * 0.8 for v in col[:3]] + [1]
            Color(*col)
            Ellipse(pos=(cx - r, cy - r), size=(2 * r, 2 * r))
            Color(1, 1, 1, 1)
            lw = max(dp(1.6), r * 0.1)
            if self.kind == "up":
                Line(points=[cx, cy - r * 0.38, cx, cy + r * 0.42], width=lw, cap="round")
                Line(points=[cx - r * 0.36, cy + r * 0.06, cx, cy + r * 0.42, cx + r * 0.36, cy + r * 0.06],
                     width=lw, cap="round", joint="round")
            elif self.kind == "arrow":
                Line(points=[cx + r * 0.38, cy, cx - r * 0.42, cy], width=lw, cap="round")
                Line(points=[cx - r * 0.05, cy + r * 0.36, cx - r * 0.42, cy, cx - r * 0.05, cy - r * 0.36],
                     width=lw, cap="round", joint="round")
            else:
                Line(ellipse=(cx - r * 0.55, cy - r * 0.3, r * 1.1, r * 0.6), width=lw)
                Ellipse(pos=(cx - r * 0.17, cy - r * 0.17), size=(r * 0.34, r * 0.34))
                if not self.on:
                    Line(points=[cx - r * 0.5, cy - r * 0.46, cx + r * 0.5, cy + r * 0.46], width=lw, cap="round")

    def on_touch_down(self, touch):
        if self.disabled or not self.collide_point(*touch.pos):
            return False
        touch.grab(self)
        self._down = True
        self._draw()
        return True

    def on_touch_up(self, touch):
        if touch.grab_current is not self:
            return False
        touch.ungrab(self)
        self._down = False
        self._draw()
        if self.collide_point(*touch.pos) and self.on_tap:
            self.on_tap()
        return True


class LinkText(DrawnText):
    """نص قابل للضغط (رابط) بدل الزر."""

    def __init__(self, text, cb, color=None, size=dp(14), halign="center", **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(34))
        super().__init__(text=text, font_size=size, color=color or T.BLUE, halign=halign, **kw)
        self._cb = cb

    def on_touch_down(self, touch):
        if self.disabled or not self.collide_point(*touch.pos):
            return False
        touch.grab(self)
        return True

    def on_touch_up(self, touch):
        if touch.grab_current is not self:
            return False
        touch.ungrab(self)
        if self.collide_point(*touch.pos):
            self._cb()
        return True


def terms_popup(on_agree=None):
    box = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(6))
    sc, lay = make_scroll_list(spacing=dp(2))
    for para in TERMS_TEXT.strip().split("\n"):
        para = para.strip()
        if not para:
            lay.add_widget(Widget(size_hint_y=None, height=dp(10)))
            continue
        for ln in textwrap.wrap(para, width=34) or [""]:
            lay.add_widget(mk_text(ln, dp(15), T.TEXT, height=dp(28)))
    box.add_widget(sc)
    bb = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
    ok = mk_btn("أوافق", background_color=T.GREEN)
    no = mk_btn("إغلاق", background_color=T.GRAY_BTN)
    bb.add_widget(ok)
    bb.add_widget(no)
    box.add_widget(bb)
    pop = make_arabic_popup("شروط الاستخدام", box, size_hint=(0.95, 0.9))
    ok.bind(on_release=lambda *x: (pop.dismiss(), on_agree() if on_agree else None))
    no.bind(on_release=lambda *x: pop.dismiss())
    pop.open()


_LOGIN_BLUE = (0.10, 0.27, 0.80, 1)


def _login_head(app, height, adding=False):
    """رأس تسجيل الدخول: خلفية زرقاء بموجات خفيفة وترحيب، والبطاقة البيضاء تغطي أسفله."""
    head = FloatLayout(size_hint_y=None, height=height)
    with head.canvas.before:
        Color(*_LOGIN_BLUE)
        bg = Rectangle(pos=head.pos, size=head.size)
        Color(1, 1, 1, 0.07)
        e1 = Ellipse(pos=head.pos, size=(dp(300), dp(300)))
        Color(1, 1, 1, 0.05)
        e2 = Ellipse(pos=head.pos, size=(dp(220), dp(220)))

    def _u(*a):
        bg.pos, bg.size = head.pos, head.size
        e1.pos = (head.x - dp(90), head.y + head.height * 0.25)
        e2.pos = (head.right - dp(130), head.y - dp(40))
    head.bind(pos=_u, size=_u)
    side = dp(64)
    head.add_widget(AnimatedLogo(full=False, size_hint=(None, None), size=(side, side),
                                 pos_hint={"x": 0.04, "top": 0.93}))
    for txt, sz, y, col in (("أهلاً بك!", dp(30), 0.42, WHITE),
                            ("سجّل دخولك بأمان لإدارة دفتر دكانك", dp(14), 0.27, (1, 1, 1, 0.82))):
        t = DrawnText(text=txt, font_size=sz, color=col, halign="right", padding_x=dp(24),
                      size_hint=(1, None), height=dp(44) if sz > dp(20) else dp(28),
                      pos_hint={"x": 0, "y": y})
        t.disabled = True
        head.add_widget(t)
    if adding:
        bk = mk_btn("رجوع", size_hint=(None, None), size=(dp(76), dp(38)), font_size=dp(14),
                    background_color=GLASS, pos_hint={"right": 0.97, "top": 0.94})
        bk.bind(on_release=lambda *a: app.back())
        head.add_widget(bk)
    return head


def _auth_head(app, height, adding=False, title=True):
    """رأس الصفحات: خلفية داكنة بحواف سفلية دائرية + الشعار المتحرك."""
    head = FloatLayout(size_hint_y=None, height=height)
    with head.canvas.before:
        Color(*_NAVY)
        hr = RoundedRectangle(pos=head.pos, size=head.size, radius=[0, 0, dp(30), dp(30)])

    def _u(*a):
        hr.pos, hr.size = head.pos, head.size
    head.bind(pos=_u, size=_u)
    side = min(height * (0.58 if title else 0.7), dp(150))
    logo = AnimatedLogo(full=False, size_hint=(None, None), size=(side, side),
                        pos_hint={"center_x": 0.5, "center_y": 0.6 if title else 0.5})
    head.add_widget(logo)
    if title:
        t = DrawnText(text="دفتر المحاسب", font_size=dp(24), color=WHITE, halign="center",
                      size_hint=(1, None), height=dp(36), pos_hint={"x": 0, "y": 0.05})
        t.disabled = True
        head.add_widget(t)
    if adding:
        bk = mk_btn("رجوع", size_hint=(None, None), size=(dp(76), dp(38)), font_size=dp(14),
                    background_color=GLASS, pos_hint={"x": 0.03, "top": 0.94})
        bk.bind(on_release=lambda *a: app.back())
        head.add_widget(bk)
    return head


class LoginPage(Screen):
    """الدخول بخطوات: البريد ← (كلمة المرور | إنشاء حساب)."""

    def __init__(self, app, adding=False, **kw):
        super().__init__(**kw)
        self.app, self.adding = app, adding
        self.mode, self.email = "auto", ""
        self.has_second, self.viewer, self.busy = False, False, False
        root = BoxLayout(orientation="vertical", spacing=0)
        with root.canvas.before:
            Color(*_LOGIN_BLUE)
            _rbg = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=lambda *a: setattr(_rbg, "pos", root.pos),
                  size=lambda *a: setattr(_rbg, "size", root.size))
        root.add_widget(_login_head(app, dp(210), adding))
        sheet = BoxLayout(orientation="vertical", size_hint=(1, 1))
        with sheet.canvas.before:
            Color(*T.BG)
            _sr = RoundedRectangle(pos=sheet.pos, size=sheet.size, radius=[dp(30), dp(30), 0, 0])
        sheet.bind(pos=lambda *a: setattr(_sr, "pos", sheet.pos),
                   size=lambda *a: setattr(_sr, "size", sheet.size))
        sv = ScrollView(size_hint=(1, 1))
        self.body = BoxLayout(orientation="vertical", spacing=dp(12), size_hint_y=None,
                              padding=[dp(24), dp(28), dp(24), dp(20)])
        self.body.bind(minimum_height=self.body.setter("height"))
        sv.add_widget(self.body)
        sheet.add_widget(sv)
        root.add_widget(sheet)
        self.add_widget(root)
        self.show_email()

    # ---------- مساعدات ----------
    def _msg(self, text="", color=None):
        if getattr(self, "msg_dt", None) is not None:
            set_text(self.msg_dt, text, color or T.RED)

    def _new_msg(self):
        self.msg_dt = mk_text("", dp(13), T.RED, halign="center", height=dp(30))
        return self.msg_dt

    @staticmethod
    def _pair(first, second, height=dp(50)):
        """صفّان متجاوران: الأول على اليمين."""
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=height, spacing=dp(10))
        row.add_widget(second)
        row.add_widget(first)
        return row

    # ---------- الخطوة 1: البريد ----------
    def show_email(self):
        self.step = "email"
        self.body.clear_widgets()
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(52), spacing=dp(6))
        self.arrow_slot = BoxLayout(size_hint=(None, 1), width=0)
        self.arrow = CircleBtn("arrow", T.BLUE, side=dp(44), on_tap=self.next_email,
                               pos_hint={"center_x": 0.5, "center_y": 0.5})
        self.arrow.set_visible(False)
        slot_fl = FloatLayout()
        slot_fl.add_widget(self.arrow)
        self.arrow_slot.add_widget(slot_fl)
        self.email_in = GmailInput("سجل البريد الإلكتروني")
        row.add_widget(self.arrow_slot)
        row.add_widget(self.email_in)
        self.body.add_widget(row)
        self.email_in.inp.bind(text=lambda *a: self._email_changed())
        self.body.add_widget(LinkText("اختيار بريد من قوقل", self.pick_google_email, T.BLUE, dp(15)))
        self.body.add_widget(self._new_msg())
        reg_txt = "تسجيل حساب جديد" if self.mode != "register" else "سيتم إنشاء حساب جديد لهذا البريد"
        self.body.add_widget(LinkText(reg_txt, self.set_register_mode, T.MUTED, dp(13)))
        self.body.add_widget(LinkText("فحص الاتصال بالانترنت", diagnose_popup, T.MUTED, dp(12)))
        schedule_refresh(0.1)

    def _email_changed(self):
        has = bool(self.email_in.inp.text.strip())
        self.arrow_slot.width = dp(50) if has else 0
        self.arrow.set_visible(has)

    def set_register_mode(self):
        self.mode = "register"
        self._msg("اكتب بريدك ثم اضغط السهم لإنشاء حسابك", T.MUTED)

    def pick_google_email(self):
        if self.busy:
            return

        def ok(email):
            self.email_in.text = email
            self._email_changed()
            self.next_email()                   # الاختيار من قوقل ينقلك مباشرة للخطوة التالية

        def fail():
            self._msg("تعذر فتح حسابات قوقل. اكتب بريدك يدوياً.")
        pick_google(ok, fail)

    def next_email(self):
        if self.busy:
            return
        email = self.email_in.text.strip().lower()
        if not re.fullmatch(r"[a-z0-9._+\-]+@gmail\.com", email):
            return self._msg("اكتب بريد Gmail صحيحاً")
        self.busy = True
        self._msg("جاري التحقق...", T.MUTED)

        def worker():
            try:
                res = (True, worker_auth("/v1/auth/check", {"email": email}))
            except Exception as e:
                res = (False, str(e)[:100])
            Clock.schedule_once(lambda dt: self._checked(email, res), 0)
        threading.Thread(target=worker, daemon=True).start()

    def _checked(self, email, res):
        self.busy = False
        ok, info = res
        if not ok:
            return self._msg(info or "تعذر الاتصال بالخادم")
        info = info if isinstance(info, dict) else {}
        self.email = email
        if info.get("banned"):
            return self._msg("تم حظر هذا الحساب من قبل الإدارة")
        if info.get("exists"):
            self.has_second = bool(info.get("has_second"))
            self.show_login(note="هذا البريد مسجل بالفعل، سجّل الدخول" if self.mode == "register" else "")
        else:
            self.show_create()

    # ---------- الخطوة 2أ: دخول حساب موجود ----------
    def _email_chip(self):
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(34), spacing=dp(8))
        row.add_widget(LinkText("تغيير", self.show_email, T.BLUE, dp(13), size_hint_x=None, width=dp(60)))
        row.add_widget(mk_text(self.email, dp(14), T.TEXT, halign="center", height=dp(34)))
        return row

    def show_login(self, note=""):
        self.step = "login"
        self.viewer = False
        self.body.clear_widgets()
        self.body.add_widget(self._email_chip())
        caps = self._pair(mk_text("كلمة المرور", dp(12), T.MUTED, height=dp(20)),
                          mk_text("كلمة المرور الثانية", dp(12), T.MUTED, height=dp(20)), height=dp(22))
        self.body.add_widget(caps)
        self.pw_in = mk_input(hint_text="********", password=True, size_hint_y=None, height=dp(50))
        self.code_in = mk_input(hint_text="********" if self.has_second else "غير مفعّلة",
                                password=True, size_hint_y=None, height=dp(50))
        second = FloatLayout(size_hint_y=None, height=dp(50))
        self.code_in.size_hint = (1, 1)
        self.code_in.pos_hint = {"x": 0, "y": 0}
        second.add_widget(self.code_in)
        self.eye = CircleBtn("eye", T.GRAY_BTN, side=dp(34), on_tap=self.toggle_view,
                             pos_hint={"x": 0.03, "center_y": 0.5})
        self.eye.set_state(on=False)
        second.add_widget(self.eye)
        if not self.has_second:
            self.code_in.set_frozen(True)
            self.eye.set_visible(False)
        self.body.add_widget(self._pair(self.pw_in, second))
        self.hint_lbl = mk_text("المشاهدة فقط: متوقفة" if self.has_second else
                                "تُفعَّل كلمة المرور الثانية من الإعدادات", dp(12), T.MUTED,
                                halign="center", height=dp(22))
        self.body.add_widget(self.hint_lbl)
        self.body.add_widget(self._new_msg())
        if note:
            self._msg(note, T.ORANGE)
        self.go_btn = mk_btn("تسجيل الدخول", font_size=dp(16), height=dp(52), background_color=T.BLUE)
        self.go_btn.bind(on_release=lambda *a: self.do_login())
        self.body.add_widget(self.go_btn)
        schedule_refresh(0.1)

    def toggle_view(self):
        self.viewer = not self.viewer
        self.code_in.set_frozen(self.viewer)
        self.eye.set_state(on=self.viewer, color=T.ORANGE if self.viewer else T.GRAY_BTN)
        set_text(self.hint_lbl, "المشاهدة فقط: مفعّلة" if self.viewer else "المشاهدة فقط: متوقفة",
                 T.ORANGE if self.viewer else T.MUTED)

    def do_login(self):
        if self.busy:
            return
        pw, code = self.pw_in.text.strip(), self.code_in.text.strip()
        if not pw:
            return self._msg("أدخل كلمة المرور")
        if self.has_second and not self.viewer and not code:
            return self._msg("أدخل كلمة المرور الثانية أو فعّل المشاهدة فقط")
        self.busy = True
        self._msg("جاري التحقق...", T.MUTED)
        email, viewer = self.email, bool(self.viewer and self.has_second)
        code = code if self.has_second else ""

        def worker():
            ok, res = self.app.verify_login(email, pw, code, viewer)
            Clock.schedule_once(lambda dt: self._login_done(ok, res, viewer), 0)
        threading.Thread(target=worker, daemon=True).start()

    def _login_done(self, ok, res, viewer):
        self.busy = False
        if ok:
            self.app.on_login_success(res, viewer, self.adding)
        else:
            self._msg(res or "فشل الدخول")

    # ---------- الخطوة 2ب: إنشاء حساب جديد ----------
    def show_create(self):
        self.step = "create"
        self.body.clear_widgets()
        self.body.add_widget(self._email_chip())
        self.body.add_widget(mk_text("حساب جديد: اختر كلمة مرور", dp(15), T.PRIMARY, halign="center", height=dp(28)))
        caps = self._pair(mk_text("كلمة المرور", dp(12), T.MUTED, height=dp(20)),
                          mk_text("تأكيد كلمة المرور", dp(12), T.MUTED, height=dp(20)), height=dp(22))
        self.body.add_widget(caps)
        self.pw_in = mk_input(hint_text="********", password=True, size_hint_y=None, height=dp(50))
        self.conf_in = mk_input(hint_text="********", password=True, size_hint_y=None, height=dp(50))
        self.body.add_widget(self._pair(self.pw_in, self.conf_in))
        self.body.add_widget(LinkText("بالمتابعة توافق على شروط الاستخدام (اضغط للقراءة)", terms_popup,
                                      T.MUTED, dp(12)))
        self.body.add_widget(self._new_msg())
        cb = mk_btn("إنشاء الحساب", font_size=dp(16), height=dp(52), background_color=T.BLUE)
        cb.bind(on_release=lambda *a: self.do_create())
        self.body.add_widget(cb)
        schedule_refresh(0.1)

    def do_create(self):
        if self.busy:
            return
        pw, conf = self.pw_in.text.strip(), self.conf_in.text.strip()
        if len(pw) < 4:
            return self._msg("كلمة المرور قصيرة جداً (4 أحرف على الأقل)")
        if pw != conf:
            return self._msg("كلمتا المرور غير متطابقتين")
        self._run_register(pw, {})

    def _run_register(self, pw, extra):
        self.busy = True
        self._msg("جاري إنشاء الحساب...", T.MUTED)
        email = self.email

        def worker():
            try:
                global SESSION_TOKEN
                dev = {"device_id": self.app.data.get("device_id"), "device_name": device_name()}
                try:
                    auth = worker_auth("/v1/auth/register", {"email": email, "password": pw, **extra, **dev})
                except ApiError as e:
                    if "مسجل" not in str(e):
                        raise
                    try:       # رد ضاع بسبب الشبكة: نفس البيانات تدخل الحساب
                        auth = worker_auth("/v1/auth/login", {"email": email, "password": pw, "viewer": False, **dev})
                    except ApiError:
                        raise ApiError("هذا البريد مسجل بالفعل. ارجع وسجّل الدخول.")
                SESSION_TOKEN = str(auth.get("session") or auth.get("session_token") or "")
                if not auth.get("ok", True) or not SESSION_TOKEN:
                    raise ApiError(str(auth.get("error") or "تعذر إنشاء جلسة"))
                self.app.data["session_token"] = SESSION_TOKEN
                self.app.save()
                res = (True, None)
            except Exception as e:
                res = (False, str(e)[:100])
            Clock.schedule_once(lambda dt: self._create_done(res, email), 0)
        threading.Thread(target=worker, daemon=True).start()

    def _create_done(self, res, email):
        self.busy = False
        if res[0]:
            self.app.on_register_success(email, email.split("@")[0], "", self.adding)
        else:
            self._msg(res[1] or "فشل إنشاء الحساب")


class ProfileSetupPage(Screen):
    """بعد أول دخول: الاسم ورقم الهاتف."""

    def __init__(self, app, on_done, **kw):
        super().__init__(**kw)
        self.app, self.on_done, self.busy = app, on_done, False
        root = BoxLayout(orientation="vertical", spacing=0)
        root.add_widget(_auth_head(app, dp(190), title=False))
        sv = ScrollView(size_hint=(1, 1))
        body = BoxLayout(orientation="vertical", spacing=dp(10), size_hint_y=None,
                         padding=[dp(24), dp(18), dp(24), dp(20)])
        body.bind(minimum_height=body.setter("height"))
        body.add_widget(mk_text("أكمل بياناتك", dp(20), T.PRIMARY, halign="center", height=dp(36)))
        body.add_widget(mk_text("الاسم يظهر في الفواتير والرقم للتواصل", dp(12), T.MUTED,
                                halign="center", height=dp(22)))
        box, self.name_in = lab_in("الاسم", "الاسم الكامل", None, "")
        body.add_widget(box)
        box, self.phone_in = lab_in("رقم الهاتف", "مثال: 777123456", "int", "")
        body.add_widget(box)
        self.msg_dt = mk_text("", dp(13), T.RED, halign="center", height=dp(30))
        body.add_widget(self.msg_dt)
        self.btn = mk_btn("متابعة", height=dp(52), font_size=dp(18), background_color=T.GREEN)
        self.btn.bind(on_release=lambda *a: self.save())
        body.add_widget(self.btn)
        sv.add_widget(body)
        root.add_widget(sv)
        self.add_widget(root)

    def save(self):
        if self.busy:
            return
        name = " ".join(self.name_in.text.split())
        phone = re.sub(r"\D", "", self.phone_in.text)
        if len(name.split()) < 2:
            return set_text(self.msg_dt, "اكتب اسمك الكامل (كلمتان على الأقل)")
        if len(phone) < 7:
            return set_text(self.msg_dt, "اكتب رقم هاتف صحيح")
        self.busy = True
        set_text(self.msg_dt, "جاري الحفظ...", T.MUTED)

        def worker():
            try:
                worker_request("PATCH", "/v1/app/profile", {"name": name, "phone": phone})
                res = (True, "")
            except Exception as e:
                res = (False, str(e)[:100])
            Clock.schedule_once(lambda dt: self._saved(res, name, phone), 0)
        threading.Thread(target=worker, daemon=True).start()

    def _saved(self, res, name, phone):
        self.busy = False
        if not res[0]:
            return set_text(self.msg_dt, res[1] or "تعذر الحفظ، تحقق من الإنترنت")
        u = self.app.data.get("user") or {}
        u["name"], u["phone"] = name, phone
        self.app.data["user"] = u
        self.app.save(auto=False)
        self.on_done()


class FreeNoticePage(Screen):
    """إشعار النسخة المجانية مع رقم المطور."""

    def __init__(self, app, on_done, **kw):
        super().__init__(**kw)
        self.app, self.on_done = app, on_done
        root = BoxLayout(orientation="vertical", spacing=0)
        root.add_widget(_auth_head(app, dp(200), title=False))
        sv = ScrollView(size_hint=(1, 1))
        body = BoxLayout(orientation="vertical", spacing=dp(8), size_hint_y=None,
                         padding=[dp(24), dp(22), dp(24), dp(20)])
        body.bind(minimum_height=body.setter("height"))
        body.add_widget(mk_text("أنت الآن على النسخة المجانية", dp(20), T.PRIMARY, halign="center", height=dp(38)))
        body.add_widget(mk_text("لديك %d فاتورة مجانية لتجربة التطبيق" % FREE_START_INVOICES, dp(14), T.TEXT,
                                halign="center", height=dp(28)))
        body.add_widget(mk_text("لتفعيل النسخة الكاملة تواصل مع المطور", dp(14), T.MUTED,
                                halign="center", height=dp(28)))
        body.add_widget(mk_text(ADMIN_PHONE_SHOW, dp(26), T.GREEN, halign="center", height=dp(52)))
        go = mk_btn("تواصل", height=dp(54), font_size=dp(18), background_color=T.GREEN)
        go.bind(on_release=lambda *a: open_whatsapp(ADMIN_PHONE_WA, "السلام عليكم، أرغب بتفعيل النسخة الكاملة من دفتر المحاسب"))
        body.add_widget(go)
        body.add_widget(LinkText("تخطي", lambda: self.on_done(), T.MUTED, dp(15)))
        sv.add_widget(body)
        root.add_widget(sv)
        self.add_widget(root)



class RegisterChoosePage(Page):
    def __init__(self, app, adding=False, **kw):
        super().__init__(app, "حساب جديد", **kw)
        self.adding = adding
        self.label("اختر طريقة التسجيل", 16, T.PRIMARY, dp(40), "center")
        g = mk_btn("التسجيل عن طريق قوقل", height=dp(60), font_size=dp(17), background_color=T.RED)
        g.bind(on_release=self.google)
        self.add(g)
        e = mk_btn("التسجيل بالبريد الإلكتروني", height=dp(60), font_size=dp(17),
                   background_color=T.BLUE)
        e.bind(on_release=lambda *a: app.push(lambda: RegisterFormPage(app, "", self.adding)))
        self.add(e)

    def google(self, *a):
        def ok(email):
            self.app.push(lambda: RegisterFormPage(self.app, email, self.adding))

        def fail():
            msg_popup("قوقل", "تعذر فتح حسابات قوقل. اكتب بريدك يدوياً.", T.ORANGE)
            self.app.push(lambda: RegisterFormPage(self.app, "", self.adding))
        pick_google(ok, fail)


class RegisterFormPage(Page):
    def __init__(self, app, email, adding=False, **kw):
        super().__init__(app, "إكمال التسجيل", **kw)
        self.adding = adding
        self.agreed = False
        self.fixed = bool(email)
        self.label("البريد الإلكتروني (Gmail)", 13)
        self.email_in = self.add(GmailInput("example"))
        if email:
            self.email_in.text = email
            self.email_in.lock()
        self.name_in = self.field("الاسم الرباعي", "الاسم الكامل (أربعة أسماء)")
        self.phone_in = self.field("رقم هاتفك (يظهر في الفواتير)", "مثال: 777123456", flt="int")
        self.pass_in = self.field("كلمة مرور المشاهدة", "********", password=True)
        self.conf_in = self.field("تأكيد كلمة مرور المشاهدة", "********", password=True)
        self.code_in = self.field("كلمة المرور الثانية (من الإدارة)", "********", password=True)
        tb = mk_btn("شروط الاستخدام (اضغط للقراءة والموافقة)", height=dp(48), background_color=T.BLUE)
        tb.bind(on_release=self.show_terms)
        self.add(tb)
        self.terms_state = self.add(mk_text("لم تتم الموافقة بعد", dp(12), T.RED,
                                            halign="center", height=dp(26)))
        self.msg("")
        self.reg_btn = mk_btn("تسجيل", height=dp(52), font_size=dp(18), background_color=T.GRAY_BTN)
        self.reg_btn.bind(on_release=self.do_register)
        self.add_footer(self.reg_btn)

    def show_terms(self, *a):
        box = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(6))
        sc, lay = make_scroll_list(spacing=dp(2))
        for para in TERMS_TEXT.strip().split("\n"):
            para = para.strip()
            if not para:
                lay.add_widget(Widget(size_hint_y=None, height=dp(10)))
                continue
            for ln in textwrap.wrap(para, width=34) or [""]:
                lay.add_widget(mk_text(ln, dp(15), T.TEXT, height=dp(28)))
        box.add_widget(sc)
        bb = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        ok = mk_btn("أوافق", background_color=T.GREEN)
        no = mk_btn("إغلاق", background_color=T.GRAY_BTN)
        bb.add_widget(ok)
        bb.add_widget(no)
        box.add_widget(bb)
        pop = make_arabic_popup("شروط الاستخدام", box, size_hint=(0.95, 0.9))

        def agree(*x):
            self.agreed = True
            set_text(self.terms_state, "تمت الموافقة على الشروط", T.GREEN)
            self.reg_btn.set_bg(T.GREEN)
            pop.dismiss()
        ok.bind(on_release=agree)
        no.bind(on_release=lambda *x: pop.dismiss())
        pop.open()

    def do_register(self, *a):
        email = self.email_in.text.strip().lower()
        name = self.name_in.text.strip()
        pw, conf, code = self.pass_in.text.strip(), self.conf_in.text.strip(), self.code_in.text.strip()
        if not re.fullmatch(r"[a-z0-9._+\-]+@gmail\.com", email):
            return self.msg("يجب أن يكون البريد من نوع @gmail.com")
        if len(name.split()) < 4:
            return self.msg("أدخل الاسم الرباعي كاملاً")
        if len(pw) < 4:
            return self.msg("كلمة المرور قصيرة جداً")
        if pw != conf:
            return self.msg("كلمتا المرور غير متطابقتين")
        if not code:
            return self.msg("أدخل كلمة المرور الثانية من الإدارة")
        if not self.agreed:
            return self.msg("يجب الموافقة على شروط الاستخدام")
        phone = re.sub(r"\D", "", self.phone_in.text)
        self.msg("جاري التسجيل...", T.MUTED)

        def worker():
            try:
                global SESSION_TOKEN
                dev = {"device_id":self.app.data.get("device_id"),"device_name":device_name()}
                try:
                    auth = worker_auth("/v1/auth/register", {"email":email,"name":name,"password":pw,"code":code,"phone":phone, **dev})
                except ApiError as e:
                    # قد يكون الطلب السابق نجح على الخادم وضاع الرد (شبكة بطيئة). ببيانات مطابقة يدخل الحساب بدل الخطأ.
                    if "مسجل" not in str(e): raise
                    try:
                        auth = worker_auth("/v1/auth/login", {"email":email,"password":pw,"code":code,"viewer":False, **dev})
                    except ApiError:
                        raise ApiError("البريد الإلكتروني مسجل بالفعل ببيانات مختلفة. سجّل الدخول أو استخدم بريداً آخر.")
                SESSION_TOKEN = str(auth.get("session") or auth.get("session_token") or "")
                if not auth.get("ok", True) or not SESSION_TOKEN:
                    raise ApiError(str(auth.get("error") or auth.get("description") or "تعذر إنشاء جلسة"))
                self.app.data["session_token"] = SESSION_TOKEN
                self.app.save()
                ok, res = True, None
            except ApiError as e:
                ok, res = False, str(e)
            except Exception as e:
                ok, res = False, str(e)[:60]
            Clock.schedule_once(lambda dt: self._done(ok, res, email, name, phone), 0)
        threading.Thread(target=worker, daemon=True).start()

    def _done(self, ok, res, email, name, phone=""):
        if ok:
            self.app.on_register_success(email, name, phone, self.adding)
        else:
            self.msg(res or "فشل التسجيل")


# ============================================================
# 5) مستندات الواتساب
# ============================================================
def shop_lines(app):
    u = app.data.get("user") or {}
    shop = (app.db.get("meta") or {}).get("shop") or ""
    out = [shop] if shop else [u.get("name", "")]
    if u.get("phone"):
        out.append("للتواصل: " + u["phone"])
    return out


def party_info(p):
    """سطر رقم العميل/المورد في المستندات."""
    return [f"الهاتف: {p['phone']}"] if p.get("phone") else []


def sale_doc(app, v):
    db = app.db
    c = find(db["customers"], v.get("cust")) or {}
    bal = cust_balance(db, v.get("cust"))
    rows = [[it["name"], it["unit"], fmt(it["qty"]), money(it["price"]), money(line_total(it))]
            for it in v["items"]]
    totals = [("إجمالي المرتجع" if is_return(v) else "إجمالي الفاتورة", money(abs(sale_total(v))), True),
              ("الدفع", PAY_LABEL.get(v["pay"], "آجل"), False),
              ("حساب العميل", money_signed(bal), False)]
    png, size = render_doc("مرتجع" if is_return(v) else "فاتورة", T.INV_RED, [f"العميل: {c.get('name', '')}"] + party_info(c) + [f"التاريخ: {v['date']}"],
                           ["الصنف", "الوحدة", "العدد", "السعر", "المبلغ"], [.31, .15, .14, .18, .22],
                           rows, totals, shop_lines(app))
    lines = [f"أخي {(c.get('name') or '').split()[0] if c.get('name') else ''}",
             f"{'مرتجع' if is_return(v) else 'فاتورة'} بمبلغ {money(abs(sale_total(v)))} - {v['date'][:10]}", "~" * 15]
    for i, it in enumerate(v["items"], 1):
        s = f"{i} {it['name']} - {fmt(it['qty'])} {it['unit']} - {money(line_total(it))}"
        if it.get("disc"):
            s += f" (خصم {money(it['disc'])})"
        lines.append(s)
    lines += ["~" * 15, "الدفع: " + PAY_LABEL.get(v["pay"], "آجل"),
              f"حسابكم الآن {money_signed(bal)}"] + shop_lines(app)
    return png, size, "\n".join(lines), c.get("phone", "")


def voucher_doc(app, x):
    db = app.db
    isc = x["pt"] == "c"
    p = find(db["customers" if isc else "suppliers"], x["pid"]) or {}
    bal = cust_balance(db, x["pid"]) if isc else sup_balance(db, x["pid"])
    title = "سند قبض" if isc else "سند صرف"
    totals = [("المبلغ", money(x["amount"]), True), ("الرصيد المتبقي", money_signed(bal), False)]
    info = ([f"{'العميل' if isc else 'المورد'}: {p.get('name', '')}"] + party_info(p) +
            [f"التاريخ: {x['date']}"])
    if x.get("note"):
        info.append("ملاحظة: " + x["note"])
    png, size = render_doc(title, T.GREEN, info, None, None, [], totals, shop_lines(app))
    text = "\n".join([f"{title} بمبلغ {money(x['amount'])}", f"التاريخ: {x['date'][:10]}",
                      f"الرصيد المتبقي: {money_signed(bal)}", "شكرا على تعاملك"] + shop_lines(app))
    return png, size, text, p.get("phone", "")


def statement_doc(app, kind, pid):
    db = app.db
    p = find(db["customers" if kind == "c" else "suppliers"], pid) or {}
    ops = party_ops(db, kind, pid)
    run, rows = 0, []
    for o in ops:
        run += o["eff"]
        rows.append([o["date"][:10], o["label"], money(o["amount"]), money_signed(run)])
    rows = rows[-20:]
    bal = run
    png, size = render_doc("كشف حساب", T.PRIMARY,
                           [f"{'العميل' if kind == 'c' else 'المورد'}: {p.get('name', '')}"] +
                           party_info(p) + [f"التاريخ: {now_str()[:10]}"],
                           ["التاريخ", "البيان", "المبلغ", "الرصيد"], [.24, .32, .22, .22], rows,
                           [("الرصيد الحالي", money_signed(bal), True)], shop_lines(app))
    text = "\n".join([f"كشف حساب {p.get('name', '')}",
                      f"الرصيد الحالي {money_signed(bal)}", "يرجى السداد في اقرب وقت"] + shop_lines(app))
    return png, size, text, p.get("phone", "")


def safe_doc(fn, app, *a):
    try:
        return fn(app, *a)
    except Exception as e:
        print(f"[DOC] {e}")
        _write_crash("DOC: " + traceback.format_exc())
        return None, (1, 1), "", ""


# ============================================================
# 6) الصفحة الرئيسية
# ============================================================
def voice_bar(app):
    """زر «إضافة فاتورة» بالصوت بعرض الأزرار وبأيقونة الميكروفون."""
    h = dp(52)
    box = FloatLayout(size_hint_y=None, height=h)
    b = mk_btn("إضافة فاتورة", font_size=dp(16), background_color=T.TEAL, size_hint=(1, 1),
               pos_hint={"x": 0, "y": 0})
    box.add_widget(b)
    dark = tuple(c * 0.6 for c in T.TEAL[:3]) + (1,)
    mic = IconButton("mic", dark, side=dp(40), pos_hint={"x": 0.015, "center_y": 0.5})
    box.add_widget(mic)

    def go(*a):
        start_voice(app)
    b.bind(on_release=go)
    mic.bind(on_release=go)
    return box


# ============================================================
# شريط التنقل السفلي + الزر العائم (تصميم Floating button micro-interaction)
# ============================================================
NAV_H = dp(70)          # ارتفاع الشريط السفلي


def _ease_out_back(t):
    t = max(0.0, min(1.0, t))
    c1 = 1.70158
    c3 = c1 + 1.0
    return 1.0 + c3 * (t - 1.0) ** 3 + c1 * (t - 1.0) ** 2


def _arc_pts(cx, cy, rx, ry, a0, a1, n=14):
    pts = []
    for k in range(n + 1):
        a = math.radians(a0 + (a1 - a0) * k / float(n))
        pts += [cx + rx * math.cos(a), cy + ry * math.sin(a)]
    return pts


def _tri(x0, y0, x1, y1, x2, y2):
    Mesh(vertices=[x0, y0, 0, 0, x1, y1, 0, 0, x2, y2, 0, 0], indices=[0, 1, 2], mode="triangles")


def _tab_icon(kind, cx, cy, r, active):
    """أيقونات الشريط السفلي: ممتلئة للتبويب النشط ومخططة للبقية."""
    lw = dp(2.2) if active else dp(1.7)
    if kind == "home":
        if active:
            _tri(cx - r, cy + r * 0.12, cx, cy + r, cx + r, cy + r * 0.12)
            RoundedRectangle(pos=(cx - r * 0.72, cy - r * 0.86), size=(r * 1.44, r * 1.06), radius=[r * 0.14])
            Color(*T.NAV)
            RoundedRectangle(pos=(cx - r * 0.2, cy - r * 0.86), size=(r * 0.4, r * 0.62), radius=[r * 0.08, r * 0.08, 0, 0])
        else:
            Line(points=[cx - r, cy + r * 0.1, cx, cy + r * 0.95, cx + r, cy + r * 0.1], width=lw,
                 joint="round", cap="round")
            Line(points=[cx - r * 0.72, cy + r * 0.38, cx - r * 0.72, cy - r * 0.85, cx + r * 0.72,
                         cy - r * 0.85, cx + r * 0.72, cy + r * 0.38], width=lw, joint="round", cap="round")
    elif kind == "search":
        Line(circle=(cx - r * 0.12, cy + r * 0.12, r * 0.62), width=lw + (dp(0.5) if active else 0))
        Line(points=[cx + r * 0.34, cy - r * 0.34, cx + r * 0.92, cy - r * 0.92],
             width=lw + dp(0.8), cap="round")
    elif kind == "chart":
        bars = ((-0.62, 0.55), (0.0, 1.0), (0.62, 0.78))
        for dx, h in bars:
            bw = r * 0.42
            x0, y0, hh = cx + dx * r - bw / 2.0, cy - r * 0.85, r * 1.7 * h * 0.62 + r * 0.2
            if active:
                RoundedRectangle(pos=(x0, y0), size=(bw, hh), radius=[bw * 0.4])
            else:
                Line(rounded_rectangle=(x0, y0, bw, hh, bw * 0.4), width=lw)
    elif kind == "user":
        Line(circle=(cx, cy, r), width=lw)
        if active:
            Ellipse(pos=(cx - r * 0.26, cy + r * 0.1), size=(r * 0.52, r * 0.52))
            Line(points=_arc_pts(cx, cy - r * 0.62, r * 0.55, r * 0.55, 20, 160), width=lw + dp(0.6), cap="round")
        else:
            Line(circle=(cx, cy + r * 0.3, r * 0.24), width=lw)
            Line(points=_arc_pts(cx, cy - r * 0.62, r * 0.55, r * 0.55, 20, 160), width=lw, cap="round")


class NavTab(Widget):
    pop = NumericProperty(0.0)

    def __init__(self, text, icon, on_tap, active=False, **kw):
        super().__init__(**kw)
        self.icon, self.active, self._on_tap, self._down = icon, active, on_tap, False
        self.lbl = mk_text(text, dp(11), WHITE if active else T.NAV_OFF, halign="center",
                           height=dp(16), pad=0)
        self.lbl.size_hint = (None, None)
        self.add_widget(self.lbl)
        self.bind(pos=self._upd, size=self._upd, pop=self._upd)
        self._upd()

    def _upd(self, *a):
        self.lbl.size = (self.width, dp(16))
        self.lbl.pos = (self.x, self.y + dp(9))
        c = self.canvas.before
        c.clear()
        r = dp(10.5) * (1.0 + 0.22 * self.pop)
        cx, cy = self.center_x, self.y + dp(41) + dp(3) * self.pop
        with c:
            Color(*(WHITE if self.active else T.NAV_OFF))
            _tab_icon(self.icon, cx, cy, r, self.active)

    def on_touch_down(self, touch):
        if self.disabled or not self.collide_point(*touch.pos):
            return False
        touch.grab(self)
        self._down = True
        Animation.cancel_all(self, "pop")
        Animation(pop=1.0, d=0.08, t="out_quad").start(self)
        return True

    def on_touch_up(self, touch):
        if touch.grab_current is not self:
            return False
        touch.ungrab(self)
        was, self._down = self._down, False
        Animation.cancel_all(self, "pop")
        Animation(pop=0.0, d=0.30, t="out_back").start(self)
        if was and self.collide_point(*touch.pos) and not self.active:
            cb = self._on_tap
            Clock.schedule_once(lambda dt: cb(), 0.12)
        return True


class BottomNav(BoxLayout):
    """شريط سفلي داكن: [الإعدادات] [التقارير] (+) [بحث] [الرئيسية] — الرئيسية على اليمين (RTL)."""

    def __init__(self, app, active, has_fab=True, **kw):
        super().__init__(orientation="horizontal", size_hint_y=None, height=NAV_H, **kw)
        with self.canvas.before:
            Color(*T.NAV_LINE)
            self._edge = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(24), dp(24), 0, 0])
            Color(*T.NAV)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(24), dp(24), 0, 0])
        self.bind(pos=self._upd, size=self._upd)
        tabs = [("الإعدادات", "user", "settings", lambda: app.nav_to(lambda: SettingsPage(app))),
                ("التقارير", "chart", "reports", lambda: app.nav_to(lambda: ReportsPage(app))),
                None,
                ("بحث", "search", "search", lambda: app.nav_to(lambda: SearchPage(app))),
                ("الرئيسية", "home", "home", lambda: app.nav_home())]
        for t in tabs:
            if t is None:
                if has_fab:
                    self.add_widget(Widget(size_hint_x=0.95))
                continue
            text, icon, key, fn = t
            self.add_widget(NavTab(text, icon, fn, active=(key == active)))

    def _upd(self, *a):
        self._edge.pos, self._edge.size = self.pos, self.size
        self._bg.pos, self._bg.size = (self.x, self.y), (self.width, self.height - dp(1))


def fab_actions(app):
    """أزرار الزر العائم: (النص، اللون، الأيقونة، الدالة) — الأول يظهر على اليمين."""
    acts = [("فاتورة بيع", T.ORANGE, "doc", lambda: app.open_add_sale()),
            ("سند قبض / صرف", T.INV_RED, "cash", lambda: app.open_receipt("c", mixed=True)),
            ("فاتورة شراء", T.PURPLE, "bag", lambda: app.open_purchase()),
            ("إضافة عميل", T.TEAL, "person",
             lambda: app.need_edit() and edit_party_popup(app, "customers", None, app.reload))]
    if app.voice_on():
        acts.append(("إضافة بالصوت", T.BLUE, "mic", lambda: start_voice(app)))
    return acts


class FabLayer(FloatLayout):
    """زر أبيض دائري في منتصف الشريط السفلي: علامة + تتحول إلى X رمادية وتنبثق منها
    أزرار ملونة بشكل «بقع» عضوية على قوس فوق الزر، مع غطاء معتم للصفحة.
    actions = [(النص، اللون، الأيقونة، الدالة)] والأول على اليمين."""
    MR = 25          # نصف قطر البقعة (dp)
    ARC = 112        # نصف قطر القوس (dp)

    def __init__(self, actions, color=None, **kw):
        super().__init__(size_hint=(1, 1), **kw)
        self.actions, self.is_open, self._p, self._target = actions, False, 0.0, 0.0
        self._ev, self._tap, self._t, self._press = None, None, 0.0, False
        self.labels = []
        for (label, col, icon, fn) in actions:
            lb = mk_text(label, dp(13), WHITE, halign="center", height=dp(34),
                         width=min(dp(128), dp(26) + len(label) * dp(7.4)), pad=0)
            lb.size_hint = (None, None)
            lb.opacity = 0
            self.labels.append(lb)
            self.add_widget(lb)
        # ترتيب الكبسولات رأسياً بحيث لا تتداخل مع بعضها مهما كان عدد الأزرار
        base = [self.ARC * math.sin(self._angle(i)) + self.MR + 19 for i in range(len(actions))]
        ys = list(base)
        for i in range(1, len(ys)):
            if abs(ys[i] - ys[i - 1]) < 36:
                ys[i] = ys[i - 1] + 38
        self._lab_extra = [dp(ys[i] - base[i]) for i in range(len(ys))]
        self.bind(pos=lambda *a: self.redraw(), size=lambda *a: self.redraw())
        self.redraw()

    # ---- الهندسة ----
    def _geom(self):
        R = dp(27)
        return self.center_x, self.y + NAV_H - dp(15), R

    def _ip(self, i):
        return max(0.0, min(1.0, self._p * 1.55 - i * 0.11))

    def _angle(self, i):
        n = len(self.actions)
        deg = 90.0 if n == 1 else 30.0 + i * (120.0 / (n - 1))
        return math.radians(deg)

    def _item(self, i):
        cx, cy, R = self._geom()
        e = _ease_out_back(self._ip(i))
        a = self._angle(i)
        d = dp(self.ARC) * e
        k = self._ip(i) * min(1.0, self._p)          # الطفو يبدأ بعد اكتمال الفتح
        bx = math.cos(self._t * 1.9 + i * 1.7) * dp(2.5) * k
        by = math.sin(self._t * 2.4 + i * 1.3) * dp(3.5) * k
        return cx + d * math.cos(a) + bx, cy + d * math.sin(a) + by, e

    def _hit(self, x, y):
        cx, cy, R = self._geom()
        if (x - cx) ** 2 + (y - cy) ** 2 <= (R + dp(8)) ** 2:
            return "main"
        if self._p > 0.5:
            r = dp(self.MR) + dp(8)
            for i in range(len(self.actions)):
                ix, iy, _e = self._item(i)
                lb = self.labels[i]
                if (x - ix) ** 2 + (y - iy) ** 2 <= r * r or (lb.x <= x <= lb.right and lb.y <= y <= lb.top):
                    return i
        return None

    # ---- الرسم ----
    def _icon(self, kind, cx, cy, r):
        lw = max(dp(1.5), r * 0.09)
        if kind == "doc":
            Line(rounded_rectangle=(cx - r * 0.38, cy - r * 0.48, r * 0.76, r * 0.96, r * 0.12), width=lw)
            Line(points=[cx - r * 0.2, cy + r * 0.12, cx + r * 0.2, cy + r * 0.12], width=lw, cap="round")
            Line(points=[cx - r * 0.2, cy - r * 0.12, cx + r * 0.1, cy - r * 0.12], width=lw, cap="round")
        elif kind == "cash":
            Line(circle=(cx, cy, r * 0.5), width=lw)
            Ellipse(pos=(cx - r * 0.16, cy - r * 0.16), size=(r * 0.32, r * 0.32))
        elif kind == "bag":
            Line(rectangle=(cx - r * 0.42, cy - r * 0.45, r * 0.84, r * 0.66), width=lw)
            Line(points=[cx - r * 0.2, cy + r * 0.21, cx - r * 0.2, cy + r * 0.45, cx + r * 0.2, cy + r * 0.45,
                         cx + r * 0.2, cy + r * 0.21], width=lw, joint="round")
        elif kind == "person":
            Ellipse(pos=(cx - r * 0.2, cy + r * 0.04), size=(r * 0.4, r * 0.4))
            Ellipse(pos=(cx - r * 0.42, cy - r * 0.5), size=(r * 0.84, r * 0.5))
        elif kind == "mic":
            RoundedRectangle(pos=(cx - r * 0.17, cy - r * 0.08), size=(r * 0.34, r * 0.62), radius=[r * 0.17])
            Line(points=[cx - r * 0.34, cy + r * 0.02, cx - r * 0.34, cy - r * 0.16, cx + r * 0.34, cy - r * 0.16,
                         cx + r * 0.34, cy + r * 0.02], width=lw, joint="round")
            Line(points=[cx, cy - r * 0.16, cx, cy - r * 0.42], width=lw, cap="round")

    def _blob(self, cx, cy, r, seed):
        """بقعة عضوية تتنفس ببطء (مضلع ممتلئ بنصف قطر متموج)."""
        n = 44
        t = self._t
        v = [cx, cy, 0, 0]
        for k in range(n + 1):
            a = 2.0 * math.pi * k / n
            rr = r * (1.0 + 0.085 * math.sin(2 * a + seed + t * 2.2) + 0.055 * math.sin(3 * a + seed * 1.7 - t * 1.7))
            v += [cx + rr * math.cos(a), cy + rr * math.sin(a), 0, 0]
        Mesh(vertices=v, indices=list(range(n + 2)), mode="triangle_fan")

    def redraw(self):
        c = self.canvas
        c.clear()
        cx, cy, R = self._geom()
        p, mr = self._p, dp(self.MR)
        R = R * (1.0 - 0.16 * p) * (0.92 if self._press else 1.0)
        with c:
            if p > 0.01:
                Color(0.0, 0.03, 0.05, 0.62 * p)
                Rectangle(pos=(self.x, self.y + NAV_H), size=(self.width, max(0, self.height - NAV_H)))
            for i, (label, col, icon, fn) in enumerate(self.actions):
                ip = self._ip(i)
                if ip <= 0.01:
                    continue
                ix, iy, e = self._item(i)
                br = mr * e
                Color(0, 0, 0, 0.28 * ip)
                self._blob(ix, iy - dp(2.5), br * 1.02, i * 1.3)
                Color(col[0], col[1], col[2], min(1.0, ip * 1.4))
                self._blob(ix, iy, br, i * 1.3)
                Color(1, 1, 1, min(1.0, ip * 1.4))
                self._icon(icon, ix, iy, br)
                lb = self.labels[i]
                lb.center_x = ix + math.cos(self._angle(i)) * dp(26)
                lb.y = iy + br + dp(6) + self._lab_extra[i] * e
                op = max(0.0, (ip - 0.4) / 0.6)
                Color(0.03, 0.07, 0.09, 0.94 * op)
                Color(col[0], col[1], col[2], 0.96 * op)
                RoundedRectangle(pos=lb.pos, size=lb.size, radius=[dp(6)])
                Color(1, 1, 1, op)
                Line(rounded_rectangle=(lb.x, lb.y, lb.width, lb.height, dp(6)), width=dp(1.2))
                tx = _ctex(label, dp(13), (1, 1, 1, 1))
                if tx is not None:
                    tw, th = float(tx.width), float(tx.height)
                    k = min(1.0, (lb.width - dp(10)) / tw) if tw > 0 else 1.0
                    Color(1, 1, 1, op)
                    Rectangle(texture=tx, pos=(lb.center_x - tw * k / 2.0, lb.y + (lb.height - th * k) / 2.0),
                              size=(tw * k, th * k))
            # الزر الرئيسي: أبيض + → رمادي X
            Color(0, 0, 0, 0.30)
            Ellipse(pos=(cx - R, cy - R - dp(3)), size=(2 * R, 2 * R))
            g = (0.47, 0.55, 0.58)
            Color(1 - (1 - g[0]) * p, 1 - (1 - g[1]) * p, 1 - (1 - g[2]) * p, 1)
            Ellipse(pos=(cx - R, cy - R), size=(2 * R, 2 * R))
            ink = (T.NAV[0] + (1 - T.NAV[0]) * p, T.NAV[1] + (1 - T.NAV[1]) * p, T.NAV[2] + (1 - T.NAV[2]) * p)
            Color(ink[0], ink[1], ink[2], 1)
            ang, L = math.radians(45 * p), R * 0.40
            for a0 in (0.0, math.pi / 2):
                dx, dy = math.cos(a0 + ang) * L, math.sin(a0 + ang) * L
                Line(points=[cx - dx, cy - dy, cx + dx, cy + dy], width=dp(2.4), cap="round")
        for i, lb in enumerate(self.labels):
            lb.opacity = 0

    # ---- الحركة ----
    def _go(self, target):
        self._target = target
        if self._ev is None:
            self._ev = Clock.schedule_interval(self._step, 1 / 60.)

    def _step(self, dt):
        self._t += dt
        d = self._target - self._p
        stepv = dt / 0.34
        if abs(d) <= stepv:
            self._p = self._target
        else:
            self._p += stepv if d > 0 else -stepv
        self.redraw()
        if self._p == self._target and self._target == 0.0:
            self._ev = None
            return False          # تبقى الحركة (تنفّس البقع) شغالة طالما الزر مفتوح

    def open(self):
        self.is_open = True
        self._go(1.0)

    def close(self):
        self.is_open = False
        self._go(0.0)

    def toggle(self):
        self.close() if self.is_open else self.open()

    # ---- اللمس: خارج الأزرار يمر للصفحة؛ والمفتوح يستهلك كل شيء ----
    def on_touch_down(self, touch):
        if self.disabled:
            return False
        hit = self._hit(*touch.pos)
        if not self.is_open:
            if hit != "main":
                return False
        touch.grab(self)
        self._tap = hit
        if hit == "main":
            self._press = True
            self.redraw()
        return True

    def on_touch_up(self, touch):
        if touch.grab_current is not self:
            return False
        touch.ungrab(self)
        hit, was, self._tap = self._hit(*touch.pos), self._tap, None
        self._press = False
        if hit == "main" and was == "main":
            self.toggle()
        elif isinstance(hit, int) and hit == was and self.is_open:
            fn = self.actions[hit][3]
            self.close()
            Clock.schedule_once(lambda dt: fn(), 0.12)
        elif self.is_open:
            self.close()
        else:
            self.redraw()
        return True


class HomePage(Page):
    NAV = "home"
    OPS_STEP = 25

    def __init__(self, app, **kw):
        user = app.data.get("user") or {}
        head = FloatLayout(size_hint_y=None, height=dp(60))
        with head.canvas.before:
            Color(*T.HEADER)
            hr = RoundedRectangle(pos=head.pos, size=head.size, radius=[0, 0, dp(20), dp(20)])

        def _u(*a):
            hr.pos, hr.size = head.pos, head.size
        head.bind(pos=_u, size=_u)
        first = (user.get("name") or "").split()
        t = DrawnText(text="مرحباً " + (first[0][:12] if first else ""), font_size=dp(17),
                      color=WHITE, halign="center", size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
        t.disabled = True
        head.add_widget(t)
        sync = mk_btn("مزامنة", size_hint=(None, None), size=(dp(76), dp(38)), font_size=dp(14),
                      background_color=GLASS, pos_hint={"x": 0, "center_y": 0.5})
        sync.bind(on_release=lambda *a: app.do_sync())
        sync_box = FloatLayout(size_hint=(None, None), size=(dp(76), dp(42)))
        sync_box.add_widget(sync)
        bell = BellBtn(lambda: app.push(lambda: NotificationsPage(app)), app.unread_count())
        right = BoxLayout(size_hint=(None, None), size=(dp(124), dp(42)), spacing=dp(6),
                          pos_hint={"right": 0.98, "center_y": 0.5})
        right.add_widget(bell)
        right.add_widget(sync_box)
        left = BoxLayout(size_hint=(None, None), size=(dp(90), dp(42)), spacing=dp(4),
                         pos_hint={"x": 0.01, "center_y": 0.5})
        left.add_widget(HamburgerBtn(lambda: self.open_drawer()))
        left.add_widget(ThemeToggle(app, T.HEADER))
        head.add_widget(right)
        head.add_widget(left)
        super().__init__(app, "", header=head, **kw)
        self.bell = bell
        app.bell = bell
        app.auto_scan_notes()

        db = app.db
        r = summary(db)
        if app.data.get("role") == "viewer":
            self.add_pinned(mk_text("وضع المشاهدة فقط — لا يمكن التعديل", dp(13), T.ORANGE,
                                    halign="center", height=dp(26)))

        # ---- الأزرار (الأول في كل صف يظهر على اليمين) ----
        def tile_row(*specs):
            row = BoxLayout(size_hint_y=None, height=dp(54), spacing=dp(6))
            for text, col, fn in reversed(specs):
                b = mk_btn(text, font_size=dp(14), background_color=col, height=dp(54))
                b.bind(on_release=lambda w, fn=fn: fn())
                row.add_widget(b)
            self.add_pinned(row)

        # فاتورة البيع والسند والشراء وإضافة العميل والصوت صارت في الزر العائم (+) أسفل الشاشة
        tile_row(("العملاء", T.PURPLE, lambda: app.open_parties("c")),
                 ("الموردين", T.TEAL, lambda: app.open_parties("s")),
                 ("العمال", T.GRAY_BTN, lambda: app.open_workers()))
        tile_row(("المخازن والصندوق", T.INV_RED, lambda: app.push(lambda: WalletPage(app))))

        # ---- سطران للملاحظات (بدل المربعات الأربعة) ----
        rem = app.remaining()
        cash = r["cash"]
        pairs = [
            [("ديون العملاء", money(r["cust_debts"]) + " ريال", T.RED),
             ("الصندوق", money_signed(cash) + " ريال", T.GREEN if cash >= 0 else T.RED)],
            [("الفواتير المتبقية", "غير محدودة" if rem is None else str(rem), T.PRIMARY),
             ("حق الموردين", money(r["sup_owed"]) + " ريال", T.ORANGE)],
        ]
        notes = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(66),
                          padding=[0, dp(2)])
        for pair in pairs:
            line = BoxLayout(size_hint_y=None, height=dp(31))
            for lab, val, col in reversed(pair):
                line.add_widget(mk_text(f"{lab}: {val}", dp(14), col, halign="center"))
            notes.add_widget(line)
        self.add_pinned(notes)

        # ---- كل الفواتير والعمليات بالترتيب ----
        self.ops = all_ops(db)
        self.limit = self.OPS_STEP
        self.label(f"الفواتير والسندات ({len(self.ops)})", 15, T.PRIMARY)
        self.ops_box = GridLayout(cols=1, spacing=dp(8), size_hint_y=None)
        self.ops_box.bind(minimum_height=self.ops_box.setter("height"))
        self.add(self.ops_box)
        self.fill_ops()
        self.add(Widget(size_hint_y=None, height=dp(24)))
        self._build_overlay()

    # ---- زر العودة للأعلى (الزر العائم والشريط السفلي يضيفهما Page.attach_nav) ----
    def _build_overlay(self):
        over = FloatLayout(size_hint=(1, 1))
        self.top_btn = CircleBtn("up", T.PRIMARY if not is_dark() else T.BLUE, side=dp(44),
                                 on_tap=self.scroll_top, pos_hint={"right": 0.96, "y": 0.13})
        self.top_btn.opacity, self.top_btn.disabled, self._top_shown = 0, True, False
        over.add_widget(self.top_btn)
        self.fab = None
        self.add_widget(over)
        self.scroll.bind(scroll_y=lambda *a: self._scroll_changed())
        self.body.bind(height=lambda *a: self._scroll_changed())

    def _scroll_changed(self):
        show = self.scroll.scroll_y < 0.9 and self.body.height > self.scroll.height * 1.25
        if show == self._top_shown:
            return
        self._top_shown = show
        self.top_btn.disabled = not show
        Animation(opacity=1 if show else 0, d=0.18).start(self.top_btn)

    def scroll_top(self):
        Animation(scroll_y=1, d=0.35, t="out_quad").start(self.scroll)

    # ---- القائمة الجانبية ----
    def open_drawer(self):
        app = self.app
        old = getattr(app, "drawer", None)
        if old is not None and old.parent is not None:
            return
        items = [("تحميل الخطوط", lambda: app.push(lambda: FontPage(app))),
                 ("التحديثات", lambda: app.push(lambda: UpdatesPage(app))),
                 ("تواصل معنا واتساب",
                  lambda: open_whatsapp(ADMIN_PHONE_WA, "السلام عليكم، أريد التواصل بخصوص تطبيق دفتر المحاسب"))]
        if has_demo(app.db):
            items.append(("حذف البيانات التجريبية", lambda: confirm_popup(
                "حذف التجريبي", "حذف كل البيانات التجريبية؟ بياناتك الحقيقية لن تتأثر.",
                lambda: (remove_demo_data(app), app.reload()))))
        items.append(("إشعارات الخلفية: " + ("تعمل" if app.data.get("push", True) is not False else "متوقفة"),
                      lambda: app.toggle_push()))
        items.append(("عرض الترحيب", lambda: app.push(lambda: OnboardingPage(app))))
        items += list(getattr(app, "extra_menu", []))
        bottom = ("الإعدادات", lambda: app.push(lambda: SettingsPage(app)))
        d = SideDrawer("القائمة", items, bottom, on_close=lambda: setattr(app, "drawer", None))
        app.drawer = d
        self.add_widget(d)
        d.open()

    # ---- قائمة العمليات ----
    def fill_ops(self):
        self.ops_box.clear_widgets()
        for o in self.ops[:self.limit]:
            self.ops_box.add_widget(self.op_row(o))
        if len(self.ops) > self.limit:
            more = mk_btn("عرض المزيد", height=dp(46), background_color=T.GRAY_BTN)
            more.bind(on_release=lambda *a: self.more_ops())
            self.ops_box.add_widget(more)
        elif not self.ops:
            if not self.app.db["customers"] and self.app.data.get("role") != "viewer":
                self.ops_box.add_widget(mk_text("ابدأ بإضافة أول عميل لك", dp(16), T.PRIMARY,
                                                halign="center", height=dp(40)))
                self.ops_box.add_widget(mk_text("اضغط زر + في أسفل الشاشة", dp(13), T.MUTED,
                                                halign="center", height=dp(30)))
            else:
                self.ops_box.add_widget(mk_text("لا توجد فواتير بعد", dp(15), T.MUTED,
                                                halign="center", height=dp(60)))
        schedule_refresh(0.1)

    def more_ops(self):
        self.limit += self.OPS_STEP
        self.fill_ops()

    def op_row(self, o):
        row = CardBox(orientation="horizontal", size_hint_y=None, height=dp(64),
                      padding=[dp(12), dp(6)], on_tap=lambda o=o: self.app.open_op(o))
        row.add_widget(mk_text(money(o["amount"]), dp(16), o["color"], halign="left", width=dp(104)))
        c = BoxLayout(orientation="vertical")
        title = o["label"] + (" - " + o["party"] if o["party"] else "")
        c.add_widget(mk_text(title, dp(15), T.TEXT, hint_y=0.55))
        c.add_widget(mk_text(o["date"], dp(12), T.MUTED, hint_y=0.45))
        row.add_widget(c)
        return row


# ============================================================
# 7) الفاتورة
# ============================================================
class AddSalePage(Page):
    def __init__(self, app, cust_id=None, rec=None, **kw):
        super().__init__(app, "تعديل فاتورة" if rec else "إضافة فاتورة", T.ORANGE,
                         **kw)
        db = app.db
        self.rec = rec
        if rec:
            cust_id = rec.get("cust")
        self.cust = cust_id
        self.wh = (rec.get("wh") if rec and rec.get("wh") else
                   (db["warehouses"][0]["id"] if db["warehouses"] else None))
        self.pay = rec.get("pay", "cash") if rec else "cash"
        self.prod = None
        self.unit = None
        self._disc = 0            # خصم بند قديم أثناء تعديله (حقل الخصم استُبدل بالحاسبة)
        self.lines = copy.deepcopy(rec.get("items", [])) if rec else []
        c = find(db["customers"], cust_id) if cust_id else None
        self.cust_btn = picker_btn(c["name"] if c else "اختر العميل", h=dp(40), fs=dp(14))
        self.cust_btn.bind(on_release=self.pick_cust)
        self.wh_btn = picker_btn(self.wh_name(), h=dp(40), fs=dp(12))
        self.wh_btn.bind(on_release=self.pick_wh)
        self.prod_btn = picker_btn("اكتب الصنف", h=dp(40), fs=dp(13))
        self.prod_btn.bind(on_release=self.pick_prod)
        self.radio = PayRadio(self.set_pay, self.pay, returns=True)
        # مربع واحد مثبّت أعلى الصفحة: العميل + نقد/آجل/مرتجع، ثم الباركود/الصنف/المخزن، ثم الفئة والسعر والعدد
        card = CardBox(orientation="vertical", size_hint_y=None, height=dp(282),
                       padding=[dp(10), dp(8), dp(10), dp(8)], spacing=dp(4))
        scb = IconButton("scan", T.BLUE, side=dp(40))
        scb.bind(on_release=lambda *a: self.scan_barcode())
        trade_header(self, self.cust_btn, self.radio, self.prod_btn, self.wh_btn, box=card, lead=scb)
        self.unit_btn = picker_btn("الفئة (كرتون / حبة ...)", h=dp(38), fs=dp(13))
        self.unit_btn.bind(on_release=self.pick_unit)
        card.add_widget(self.unit_btn)
        info = BoxLayout(size_hint_y=None, height=dp(22))
        self.cost_lbl = mk_text("سعر الشراء: -", dp(12), T.MUTED, height=dp(22))
        self.stock_lbl = mk_text("المتوفر: -", dp(12), T.MUTED, height=dp(22))
        info.add_widget(self.cost_lbl)
        info.add_widget(self.stock_lbl)
        card.add_widget(info)
        b1, self.qty_in = small_in("العدد", "1", "float")
        b2, self.price_in = small_in("سعر البيع", "0", "float")
        calc_col = BoxLayout(orientation="vertical", size_hint=(None, 1), width=dp(46))
        calc_col.add_widget(Widget(size_hint_y=None, height=dp(18)))
        calc_col.add_widget(CalcIconBtn(lambda: calc_to_popup(self.price_in),
                                        size_hint=(1, None), height=dp(38)))
        card.add_widget(row_of(calc_col, b2, b1))
        nr = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
        ab = mk_btn("+ إضافة", size_hint=(None, 1), width=dp(96), font_size=dp(14),
                    background_color=T.BLUE)
        ab.bind(on_release=self.add_line)
        self.note_in = mk_input(hint_text="ملاحظة (اختياري)", size_hint_y=None, height=dp(40),
                                font_size=dp(14))
        nr.add_widget(ab)
        nr.add_widget(self.note_in)
        card.add_widget(nr)
        self.add_pinned(card)
        self.label("أصناف الفاتورة", 14)
        self.lines_box = GridLayout(cols=1, spacing=dp(6), size_hint_y=None)
        self.lines_box.bind(minimum_height=self.lines_box.setter("height"))
        self.add(self.lines_box)
        self.total_lbl = mk_text("الإجمالي: 0 ريال", dp(18), T.PRIMARY, halign="center", height=dp(40))
        self.add(self.total_lbl)
        if rec:
            sv = mk_btn("حفظ", height=dp(52), font_size=dp(16), background_color=T.GREEN)
            snd = mk_btn("حفظ وإرسال", height=dp(52), font_size=dp(15), background_color=T.BLUE)
            dl = mk_btn("حذف", height=dp(52), font_size=dp(16), background_color=T.RED)
            sv.bind(on_release=self.save_edit)
            snd.bind(on_release=self.send_edit)
            dl.bind(on_release=self.delete_edit)
            self.add_footer(dl, snd, sv)
        else:
            save = mk_btn("حفظ الفاتورة", height=dp(52), font_size=dp(17), background_color=T.GREEN)
            save.bind(on_release=self.save)
            self.add_footer(save)
        self.set_pay(self.pay)
        self.refresh_lines()

    # ---- باركود وصوت ----
    def scan_barcode(self, *a):
        scan_with_fallback(self.on_barcode)

    def _use_product(self, p):
        if not p.get("units"):
            return msg_popup("تنبيه", "هذا الصنف بلا فئات", T.ORANGE)
        u = p["units"][0]
        for ln in self.lines:
            if ln["pid"] == p["id"] and ln.get("unit") == u["name"] and not ln.get("disc"):
                ln["qty"] += 1
                self.prod, self.unit = p, u
                self.refresh_lines()
                return self.refresh_item()
        self.prod, self.unit = p, u
        self.refresh_item(reset_price=True)
        self.qty_in.text = "1"
        self.add_line()

    def on_barcode(self, code):
        db = self.app.db
        p = product_by_barcode(db, code)
        if p:
            return self._use_product(p)
        if not db["products"]:
            return msg_popup("باركود غير مسجل", "لا توجد أصناف بعد، أضف الصنف من فاتورة شراء", T.ORANGE)
        opts = [(x["name"], "", x["id"], None) for x in db["products"]]

        def link(pid):
            x = find(db["products"], pid)
            x["barcode"] = code
            touch(x)
            self.app.save()
            self._use_product(x)
        pick_popup("باركود غير مسجل - اربطه بصنف", opts, link)

    def on_voice(self, texts):
        r = best_parse(texts, self.app.db, "sale")
        if not r or not r["items"]:
            return msg_popup("الإدخال الصوتي", "لم أفهم: " + (texts[0] if texts else "") +
                             "\nمثال: بعت 3 كراتين بيبسي لفلان", T.ORANGE)
        self.voice_fill(r)

    def voice_fill(self, r):
        db = self.app.db
        if r.get("pay"):
            self.set_pay(r["pay"])
        c = r.get("party")
        if c:
            self.cust = c["id"]
            self.cust_btn.text = c["name"]
        missing = []
        for it in r["items"]:
            p = it.get("product")
            if not p or not p.get("units"):
                missing.append(it.get("name") or "؟")
                continue
            u = None
            if it.get("unit"):
                u = next((x for x in p["units"] if unit_canon(x["name"]) == unit_canon(it["unit"])), None)
            u = u or p["units"][0]
            self.prod, self.unit = p, u
            self.refresh_item(reset_price=True)
            self.qty_in.text = fmt(it.get("qty") or 1)
            self.add_line()
        self.app.show_banner("سمعت: " + str(r.get("text", ""))[:60])
        if missing:
            msg_popup("أصناف غير موجودة", "لم أجد في المخزن: " + "، ".join(missing), T.ORANGE)
        name = (r.get("party_name") or "").strip()
        if name and not self.cust:
            def go():
                def after():
                    self.cust = db["customers"][-1]["id"]
                    self.cust_btn.text = db["customers"][-1]["name"]
                edit_party_popup(self.app, "customers", None, after, name)
            confirm_popup("عميل جديد", f"العميل «{name}» غير موجود. هل تضيفه؟", go)

    # ---- مساعدات ----
    def wh_name(self):
        w = find(self.app.db["warehouses"], self.wh)
        return ("المخزن: " + w["name"]) if w else "اختر المخزن"

    def avail(self, pid, wh=None):
        wh = wh or self.wh
        # عند تعديل فاتورة نستبعدها من الحساب ثم نخصم بنودها الحالية
        sm = stock_map(self.app.db, exclude=(self.rec or {}).get("id"))
        a = sm.get((pid, wh), 0)
        for ln in self.lines:
            if ln["pid"] == pid:
                a -= ln["qty"] * ln["pack"]
        return max(0, a)

    def set_pay(self, p):
        self.pay = p
        self.radio.set(p)
        if hasattr(self, "total_lbl"):
            self.refresh_lines()

    def pick_cust(self, *a):
        opts = [(c["name"], f"الرصيد: {money_signed(cust_balance(self.app.db, c['id']))}", c["id"], None)
                for c in self.app.db["customers"]]

        def chosen(cid):
            self.cust = cid
            self.cust_btn.text = find(self.app.db["customers"], cid)["name"]

        def new(name):
            def after():
                chosen(self.app.db["customers"][-1]["id"])
            Clock.schedule_once(lambda dt: edit_party_popup(self.app, "customers", None, after, name), 0.25)
        pick_popup("اختر العميل", opts, chosen, "إضافة عميل جديد", new)

    def pick_wh(self, *a):
        sm = stock_map(self.app.db)
        opts = []
        for w in self.app.db["warehouses"]:
            if self.prod:
                q = sm.get((self.prod["id"], w["id"]), 0)
                opts.append((w["name"], f"المتوفر: {fmt(q)} حبة", w["id"], T.RED if q <= 0 else T.GREEN))
            else:
                opts.append((w["name"], "", w["id"], None))

        def chosen(wid):
            self.wh = wid
            self.refresh_item()
        pick_popup("اختر المخزن", opts, chosen)

    def pick_prod(self, *a):
        sm = stock_map(self.app.db)
        opts = []
        for p in self.app.db["products"]:
            q = sm.get((p["id"], self.wh), 0)
            opts.append((p["name"], f"المتوفر: {fmt(q)} حبة", p["id"], T.RED if q <= 0 else T.GREEN))

        def chosen(pid):
            self.prod = find(self.app.db["products"], pid)
            self.unit = self.prod["units"][0] if self.prod.get("units") else None
            self.refresh_item(reset_price=True)
        pick_popup("اختر الصنف", opts, chosen)

    def pick_unit(self, *a):
        if not self.prod:
            return
        opts = [(u["name"], f"{fmt(u['pack'])} حبة - بيع {money(u['price'])}", i, None)
                for i, u in enumerate(self.prod["units"])]

        def chosen(i):
            self.unit = self.prod["units"][i]
            self.refresh_item(reset_price=True)
        pick_popup("اختر الفئة", opts, chosen)

    def refresh_item(self, reset_price=False):
        self.wh_btn.text = self.wh_name()
        if not self.prod:
            return
        self.prod_btn.text = self.prod["name"]
        u = self.unit or {}
        self.unit_btn.text = f"الفئة: {u.get('name', '')} ({fmt(u.get('pack', 1))} حبة)"
        if reset_price:
            self.price_in.text = fmt(u.get("price", 0))
        set_text(self.cost_lbl, f"سعر الشراء: {money(u.get('cost', 0))}", T.MUTED)
        a = self.avail(self.prod["id"])
        bad = a <= 0
        set_text(self.stock_lbl, f"المتوفر: {fmt(a)} حبة" + ("  (نفد)" if bad else ""),
                 T.RED if bad else T.GREEN)
        self.wh_btn.set_bg(T.RED if bad else T.SOFT_BLUE)
        self.wh_btn._dt._color = tuple(WHITE if bad else T.PRIMARY)
        self.wh_btn.text = self.wh_name()

    def add_line(self, *a):
        if not self.prod or not self.unit:
            return msg_popup("تنبيه", "اختر الصنف والفئة أولاً", T.ORANGE)
        qty = num(self.qty_in.text, 1) if self.qty_in.text.strip() else 1
        price = num(self.price_in.text, -1)
        disc = self._disc
        if qty <= 0 or price < 0:
            return msg_popup("تنبيه", "تحقق من العدد والسعر", T.ORANGE)
        if disc > qty * price:
            return msg_popup("تنبيه", "الخصم أكبر من مجموع الصنف", T.RED)
        ln = {"pid": self.prod["id"], "name": self.prod["name"], "unit": self.unit["name"],
              "pack": self.unit["pack"], "qty": qty, "price": price, "cost": self.unit.get("cost", 0)}
        if disc:
            ln["disc"] = disc
        if self.note_in.text.strip():
            ln["note"] = self.note_in.text.strip()
        self.lines.append(ln)
        for w in (self.qty_in, self.note_in):
            w.text = ""
        self._disc = 0
        self.refresh_lines()
        self.refresh_item()

    def refresh_lines(self):
        self.lines_box.clear_widgets()
        for i, ln in enumerate(self.lines):
            row = CardBox(orientation="horizontal", size_hint_y=None, height=dp(62),
                          padding=[dp(10), dp(6)], spacing=dp(8), radius=dp(12))
            d = mk_btn("X", size_hint=(None, None), size=(dp(38), dp(38)), background_color=T.RED,
                       pos_hint={"center_y": 0.5})
            d.bind(on_release=lambda w, i=i: (self.lines.pop(i), self.refresh_lines(), self.refresh_item()))
            row.add_widget(d)
            ed = mk_btn("تعديل", size_hint=(None, None), size=(dp(58), dp(38)), font_size=dp(13),
                        background_color=T.BLUE, pos_hint={"center_y": 0.5})
            ed.bind(on_release=lambda w, i=i: self.edit_line(i))
            row.add_widget(ed)
            row.add_widget(mk_text(money(line_total(ln)), dp(16), T.PRIMARY, halign="left",
                                   width=dp(90)))
            col = BoxLayout(orientation="vertical")
            col.add_widget(mk_text(ln["name"], dp(15), T.TEXT, hint_y=0.55))
            sub = f"{fmt(ln['qty'])} {ln['unit']} × {money(ln['price'])}"
            if ln.get("disc"):
                sub += f" - خصم {money(ln['disc'])}"
            col.add_widget(mk_text(sub, dp(12), T.MUTED, hint_y=0.45))
            row.add_widget(col)
            self.lines_box.add_widget(row)
        total = sum(line_total(l) for l in self.lines)
        self.total_lbl.text = (f"إجمالي المرتجع: {money(total)} ريال" if self.pay == "return"
                               else f"الإجمالي: {money(total)} ريال")
        schedule_refresh(0.1)

    def edit_line(self, i):
        """يعيد الصنف إلى النموذج للتعديل (ثم اضغط + إضافة للفاتورة)."""
        ln = self.lines.pop(i)
        prod = find(self.app.db["products"], ln["pid"])
        unit = None
        if prod:
            unit = next((u for u in prod.get("units", []) if u.get("name") == ln.get("unit")), None)
        if unit is None:
            unit = {"name": ln.get("unit", ""), "pack": ln.get("pack", 1),
                    "price": ln.get("price", 0), "cost": ln.get("cost", 0)}
        if prod is None:
            prod = {"id": ln["pid"], "name": ln.get("name", ""), "units": [unit]}
        self.prod, self.unit = prod, unit
        self.refresh_lines()
        self.refresh_item()
        self.qty_in.text = fmt(ln.get("qty", 1))
        self.price_in.text = fmt(ln.get("price", 0))
        self._disc = ln.get("disc", 0) or 0
        self.note_in.text = ln.get("note", "")

    # ---- وضع التعديل ----
    def _apply_edit(self):
        app = self.app
        if not app.need_edit():
            return False
        if not self.cust:
            msg_popup("تنبيه", "اختر العميل أولاً", T.ORANGE)
            return False
        if not self.lines:
            msg_popup("تنبيه", "أضف صنفاً واحداً على الأقل", T.ORANGE)
            return False
        r = self.rec
        r["cust"], r["wh"], r["pay"], r["items"] = self.cust, self.wh, self.pay, list(self.lines)
        touch(r)
        app.save()
        return True

    def save_edit(self, *a):
        if self._apply_edit():
            self.app.back()

    def send_edit(self, *a):
        if self._apply_edit():
            self.app.show_sale(self.rec["id"])

    def delete_edit(self, *a):
        if not self.app.need_edit():
            return

        def go():
            delete_rec(self.app.db, "sales", self.rec["id"])
            self.app.save()
            self.app.back()
        confirm_popup("تأكيد الحذف", "حذف هذه الفاتورة نهائياً؟", go)

    def save(self, *a):
        app = self.app
        if not app.need_edit():
            return
        if not self.cust:
            return msg_popup("تنبيه", "اختر العميل أولاً", T.ORANGE)
        if not self.lines:
            return msg_popup("تنبيه", "أضف صنفاً واحداً على الأقل", T.ORANGE)
        if not app.quota_ok():
            return
        v = touch({"id": gen_id(), "cust": self.cust, "wh": self.wh, "date": now_str(),
                   "pay": self.pay, "items": list(self.lines)})
        app.db["sales"].append(v)
        app.data["pending"] = app.data.get("pending", 0) + 1
        app.save()
        app.back()
        Clock.schedule_once(lambda dt: app.show_sale(v["id"], allow_delete=False), 0.3)


# ============================================================
# 8) سند القبض / الدفع
# ============================================================
class AddVoucherPage(Page):
    def __init__(self, app, kind="c", pid=None, rec=None, mixed=False, **kw):
        if rec:
            kind, pid = rec.get("pt", "c"), rec.get("pid")
        # الوضع المختلط: الدخول من الرئيسية بدون طرف محدد (عميل أو مورد)
        mixed = bool(mixed and not rec and not pid)
        name = "سند قبض" if kind == "c" else "سند صرف"
        if mixed:
            name = "سند قبض / صرف"
        super().__init__(app, ("تعديل " + name) if rec else name,
                         T.GREEN if kind == "c" else T.TEAL, **kw)
        self.rec = rec
        self.kind = kind
        self.pid = pid
        self.mixed = mixed
        coll = "customers" if kind == "c" else "suppliers"
        p = find(app.db[coll], pid) if pid else None
        self.btn = picker_btn(p["name"] if p else
                              ("اختر عميل / مورد" if mixed else
                               ("اختر العميل" if kind == "c" else "اختر المورد")))
        self.btn.bind(on_release=self.pick)
        self.add(self.btn)
        self.bal = self.label("", 13, T.MUTED)
        self.show_bal()
        self.amount_in = self.field("المبلغ", "0", text=fmt(rec["amount"]) if rec else "", flt="float")
        self.note_in = self.field("ملاحظة (اختياري)", "", text=rec.get("note", "") if rec else "")
        if rec:
            sv = mk_btn("حفظ", height=dp(52), font_size=dp(16), background_color=T.GREEN)
            snd = mk_btn("حفظ وإرسال", height=dp(52), font_size=dp(15), background_color=T.BLUE)
            dl = mk_btn("حذف", height=dp(52), font_size=dp(16), background_color=T.RED)
            sv.bind(on_release=self.save_edit)
            snd.bind(on_release=self.send_edit)
            dl.bind(on_release=self.delete_edit)
            self.add_footer(dl, snd, sv)
        else:
            save = mk_btn("حفظ السند", height=dp(52), font_size=dp(17), background_color=T.GREEN)
            save.bind(on_release=self.save)
            self.add_footer(save)

    def show_bal(self):
        if not self.pid:
            return
        db = self.app.db
        b = cust_balance(db, self.pid) if self.kind == "c" else sup_balance(db, self.pid)
        self.bal.text = f"الرصيد الحالي: {money_signed(b)} ريال"

    def _style_btn(self):
        """لون الزر حسب النوع: المورد بلون مختلف (تركوازي) والعميل بالأزرق الفاتح."""
        if self.kind == "s":
            self.btn.set_bg(T.TEAL)
            self.btn._dt.color = WHITE
        else:
            self.btn.set_bg(T.SOFT_BLUE)
            self.btn._dt.color = T.PRIMARY

    def pick_mixed(self):
        db = self.app.db
        opts = []
        for p in db["customers"]:
            opts.append((p["name"], "عميل  •  الرصيد: " + money_signed(cust_balance(db, p["id"])),
                         ("c", p["id"]), None, T.TEXT))
        for p in db["suppliers"]:
            opts.append((p["name"], "مورد  •  الرصيد: " + money_signed(sup_balance(db, p["id"])),
                         ("s", p["id"]), T.TEAL, T.TEAL))

        def chosen(val):
            kind, pid = val
            if kind == "s" and not self.app.need_pro():
                return
            self.kind, self.pid = kind, pid
            coll = "customers" if kind == "c" else "suppliers"
            self.btn.text = find(db[coll], pid)["name"]
            self._style_btn()
            self.show_bal()
        pick_popup("اختر عميل / مورد", opts, chosen)

    def pick(self, *a):
        if self.mixed:
            return self.pick_mixed()
        db = self.app.db
        coll = "customers" if self.kind == "c" else "suppliers"
        fn = cust_balance if self.kind == "c" else sup_balance
        opts = [(p["name"], f"الرصيد: {money_signed(fn(db, p['id']))}", p["id"], None) for p in db[coll]]

        def chosen(pid):
            self.pid = pid
            self.btn.text = find(db[coll], pid)["name"]
            self.show_bal()
        pick_popup("اختر", opts, chosen)

    # ---- وضع التعديل ----
    def _apply_edit(self):
        if not self.app.need_edit():
            return False
        amount = num(self.amount_in.text)
        if not self.pid or amount <= 0:
            msg_popup("تنبيه", "اختر الطرف وأدخل مبلغاً صحيحاً", T.ORANGE)
            return False
        r = self.rec
        r["pid"], r["amount"], r["note"] = self.pid, amount, self.note_in.text.strip()
        touch(r)
        self.app.save()
        return True

    def save_edit(self, *a):
        if self._apply_edit():
            self.app.back()

    def send_edit(self, *a):
        if self._apply_edit():
            self.show_bal()
            self.app.show_voucher(self.rec["id"])

    def delete_edit(self, *a):
        if not self.app.need_edit():
            return

        def go():
            delete_rec(self.app.db, "vouchers", self.rec["id"])
            self.app.save()
            self.app.back()
        confirm_popup("تأكيد الحذف", "حذف هذا السند نهائياً؟", go)

    def save(self, *a):
        if not self.app.need_edit():
            return
        amount = num(self.amount_in.text)
        if not self.pid or amount <= 0:
            return msg_popup("تنبيه", "اختر الطرف وأدخل مبلغاً صحيحاً", T.ORANGE)
        x = touch({"id": gen_id(), "pt": self.kind, "pid": self.pid, "amount": amount,
                   "note": self.note_in.text.strip(), "date": now_str()})
        self.app.db["vouchers"].append(x)
        self.app.save()
        self.app.back()
        self.app.show_voucher(x["id"])


# ============================================================
# 9) إضافة صنف (شراء)
# ============================================================
class AddPurchasePage(Page):
    """فاتورة شراء بنفس تصميم فاتورة البيع: رأس ثابت ثم بطاقة إدخال صنف واحد، وكل صنف
    يُحفظ في قائمة الفاتورة بزر «حفظ الصنف»، وفي الأسفل «حفظ الفاتورة».
    لو أُعطي rec تُفتح نفس الصفحة والبيانات موجودة للتعديل."""

    NEW_UNIT_HINT = "الفئة (اختر أو أضف)"

    def __init__(self, app, sup_id=None, rec=None, **kw):
        super().__init__(app, "تعديل فاتورة شراء" if rec else "إضافة فاتورة شراء", T.BLUE, **kw)
        db = app.db
        self.rec = rec
        self.pro = app.plan() != "free"
        self.sup = rec.get("sup") if rec else sup_id
        self.pay = rec.get("pay", "cash") if rec else "cash"
        self.wh = (rec.get("wh") if rec and rec.get("wh") else
                   (db["warehouses"][0]["id"] if db["warehouses"] else None))
        self.prod = None          # صنف موجود في المخزن
        self.pname = ""           # اسم صنف جديد
        self.unit = None          # فئة موجودة للصنف
        self.uname = ""           # اسم الفئة (موجودة أو جديدة)
        self._pieces = 0          # المتوفر بالحبات للصنف في المخزن المختار
        self.lines = []
        self.sup_btn = self.radio = None
        if rec:
            for it in rec.get("items", []):
                price = it.get("price")
                if price is None:
                    p = find(db["products"], it.get("pid")) or {}
                    pu = next((x for x in p.get("units", []) if x.get("name") == it.get("unit")), {})
                    price = pu.get("price", 0)
                self.lines.append({"pid": it.get("pid"), "name": it.get("name", ""),
                                   "unit": it.get("unit", ""), "pack": it.get("pack", 1),
                                   "qty": it.get("qty", 0), "cost": it.get("cost", 0), "price": price})
        if self.pro:
            s = find(db["suppliers"], self.sup) if self.sup else None
            self.sup_btn = picker_btn(s["name"] if s else "اكتب أو اختر المورد", h=dp(40), fs=dp(14))
            self.sup_btn.bind(on_release=self.pick_sup)
            self.radio = PayRadio(self.set_pay, self.pay)
        self.wh_btn = picker_btn(self.wh_name(), h=dp(40), fs=dp(12))
        self.wh_btn.bind(on_release=self.pick_wh)
        self.prod_btn = picker_btn("اكتب الصنف", h=dp(40), fs=dp(13))
        self.prod_btn.bind(on_release=self.pick_prod)

        # ---- مربع واحد مثبّت أعلى الصفحة (مثل فاتورة البيع) ----
        card = CardBox(orientation="vertical", size_hint_y=None, height=dp(338),
                       padding=[dp(10), dp(8), dp(10), dp(8)], spacing=dp(4))
        # (المورد + نقد/آجل) ثم (الصنف + المخزن)؛ الحاسبة صارت بجانب سعر البيع
        trade_header(self, self.sup_btn, self.radio, self.prod_btn, self.wh_btn, box=card, calc=False)

        # الباركود: حقل كتابة + زر مسح بالكاميرا (الرقم يظهر في الحقل بعد المسح)
        self.bc_in = mk_input(hint_text="الباركود (اكتبه أو امسحه)", size_hint_y=None, height=dp(40),
                              font_size=dp(14))
        self.bc_in.bind(on_text_validate=self.lookup_barcode)
        scb = IconButton("scan", T.BLUE, side=dp(40))
        scb.bind(on_release=lambda *a: self.scan_barcode())
        br = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
        br.add_widget(self.bc_in)
        br.add_widget(scb)
        card.add_widget(br)

        # الفئة: اختر أو أضف فئة + الصوت بجانبها
        self.unit_btn = picker_btn(self.NEW_UNIT_HINT, h=dp(40), fs=dp(13))
        self.unit_btn.bind(on_release=self.pick_unit)
        mic = IconButton("mic", T.TEAL, side=dp(40))
        self.vh = VoiceHold(app, self.on_voice, mic)
        mic.bind(on_press=self.vh.press, on_release=self.vh.release)
        ur = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
        if app.voice_on():
            ur.add_widget(mic)
        ur.add_widget(self.unit_btn)
        card.add_widget(ur)

        # تحت الفئة: العدد المتوفر (+ عدد الحبات في الفئة)
        info = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(6))
        self.pack_in = mk_input(hint_text="حبات/فئة", input_filter="float", size_hint=(None, None),
                                size=(dp(90), dp(36)), font_size=dp(13))
        self.pack_in.bind(text=lambda *a: self._show_stock())
        self.stock_lbl = mk_text("المتوفر: -", dp(13), T.MUTED, height=dp(38))
        info.add_widget(self.pack_in)
        info.add_widget(self.stock_lbl)
        card.add_widget(info)

        # الأسعار والعدد: سعر البيع | (سعر الشراء + الحاسبة) | العدد المشترى
        b_qty, self.qty_in = small_in("العدد المشترى", "0", "float")
        b_cost, self.cost_in = small_in("سعر الشراء", "0", "float")
        b_price, self.price_in = small_in("سعر البيع", "0", "float")
        calc_col = BoxLayout(orientation="vertical", size_hint=(None, 1), width=dp(42))
        calc_col.add_widget(Widget(size_hint_y=None, height=dp(18)))
        calc_col.add_widget(CalcIconBtn(lambda: calc_to_popup(self.cost_in),
                                        size_hint=(1, None), height=dp(38)))
        cost_box = BoxLayout(size_hint_y=None, height=dp(58), spacing=dp(4))
        cost_box.add_widget(calc_col)
        cost_box.add_widget(b_cost)
        card.add_widget(row_of(b_qty, cost_box, b_price))

        addb = mk_btn("حفظ الصنف", height=dp(40), font_size=dp(15), background_color=T.PURPLE)
        addb.bind(on_release=self.add_line)
        card.add_widget(addb)
        self.add_pinned(card)

        self.label("أصناف الفاتورة", 14)
        self.lines_box = GridLayout(cols=1, spacing=dp(6), size_hint_y=None)
        self.lines_box.bind(minimum_height=self.lines_box.setter("height"))
        self.add(self.lines_box)
        self.total_lbl = mk_text("الإجمالي: 0 ريال", dp(18), T.PRIMARY, halign="center", height=dp(40))
        self.add(self.total_lbl)
        if rec:
            sv = mk_btn("حفظ", height=dp(52), font_size=dp(16), background_color=T.GREEN)
            dl = mk_btn("حذف الفاتورة", height=dp(52), font_size=dp(15), background_color=T.RED)
            sv.bind(on_release=self.save)
            dl.bind(on_release=self.delete)
            self.add_footer(dl, sv)
        else:
            save = mk_btn("حفظ الفاتورة", height=dp(52), font_size=dp(17), background_color=T.GREEN)
            save.bind(on_release=self.save)
            self.add_footer(save)
        self.refresh_lines()

    # ---- مساعدات ----
    def wh_name(self):
        w = find(self.app.db["warehouses"], self.wh)
        return ("المخزن: " + w["name"]) if w else "اختر المخزن"

    def set_pay(self, p):
        self.pay = p
        if self.radio is not None:
            self.radio.set(p)

    def _cur_name(self):
        return self.prod["name"] if self.prod else self.pname

    def _unit_display(self, u):
        key = ar_norm(u or "")
        u = _PLURALS.get(key, u or "حبة")
        return u[:-1] + "ة" if u.endswith("ه") else u

    # ---- المورد والمخزن ----
    def pick_sup(self, *a):
        db = self.app.db
        opts = [(s["name"], f"الرصيد: {money_signed(sup_balance(db, s['id']))}", s["id"], None)
                for s in db["suppliers"]]

        def chosen(sid):
            self.sup = sid
            self.sup_btn.text = find(db["suppliers"], sid)["name"]

        def new(name):
            def after():
                chosen(db["suppliers"][-1]["id"])
            Clock.schedule_once(lambda dt: edit_party_popup(self.app, "suppliers", None, after, name), 0.25)
        pick_popup("اختر المورد", opts, chosen, "إضافة مورد جديد", new)

    def pick_wh(self, *a):
        opts = [(w["name"], "", w["id"], None) for w in self.app.db["warehouses"]]

        def chosen(wid):
            self.wh = wid
            self.wh_btn.text = self.wh_name()
            self._load_stock()
        pick_popup("اختر المخزن", opts, chosen)

    # ---- الصنف ----
    def pick_prod(self, *a):
        sm = stock_map(self.app.db)
        opts = []
        for p in self.app.db["products"]:
            q = sum(v for (pid, _w), v in sm.items() if pid == p["id"])
            opts.append((p["name"], f"المخزون الكلي: {fmt(q)} حبة", p["id"], None))

        def chosen(pid):
            self.set_product(find(self.app.db["products"], pid))

        def new(name):
            name = name.strip()
            ex = next((x for x in self.app.db["products"]
                       if x["name"].strip().lower() == name.lower()), None)
            if ex:
                return self.set_product(ex)
            self.set_new_product(name)
        pick_popup("اختر الصنف", opts, chosen, "صنف جديد", new)

    def _clear_unit(self):
        self.unit, self.uname = None, ""
        self.unit_btn.text = self.NEW_UNIT_HINT
        self.pack_in.text = ""
        self.pack_in.disabled = False
        self.cost_in.text = ""
        self.price_in.text = ""

    def set_product(self, p, barcode=None):
        self.prod, self.pname = p, ""
        self.prod_btn.text = p["name"]
        if barcode:
            self.bc_in.text = barcode
        elif p.get("barcode"):
            self.bc_in.text = p["barcode"]
        self._clear_unit()
        if p.get("units"):
            self.set_unit(p["units"][0])
        self._load_stock()

    def set_new_product(self, name):
        self.prod, self.pname = None, name
        self.prod_btn.text = name + "  (صنف جديد)"
        self._clear_unit()
        self._load_stock()

    # ---- الفئة ----
    def pick_unit(self, *a):
        if not (self.prod or self.pname):
            return msg_popup("تنبيه", "اختر الصنف أولاً (أو امسح الباركود)", T.ORANGE)
        units = list(self.prod.get("units", [])) if self.prod else []
        opts = [(u["name"], f"{fmt(u['pack'])} حبة - شراء {money(u['cost'])}", i, None)
                for i, u in enumerate(units)]

        def chosen(i):
            self.set_unit(units[i])

        def new(name):
            self.set_new_unit(name)
        pick_popup("اختر الفئة", opts, chosen, "فئة جديدة", new)

    def set_unit(self, u):
        self.unit, self.uname = u, u["name"]
        self.unit_btn.text = "الفئة: " + u["name"]
        self.pack_in.text = fmt(u.get("pack", 1))
        self.pack_in.disabled = True        # عدد الحبات في فئة موجودة ثابت
        self.cost_in.text = fmt(u.get("cost", 0))
        self.price_in.text = fmt(u["price"]) if u.get("price") is not None else ""
        self._show_stock()

    def set_new_unit(self, name):
        name = (name or "").strip()
        if not name:
            return
        if self.prod:           # قد تكون الفئة الجديدة موجودة فعلاً بصياغة أخرى
            ex = next((u for u in self.prod.get("units", [])
                       if unit_canon(u["name"]) == unit_canon(name)), None)
            if ex:
                return self.set_unit(ex)
        self.unit, self.uname = None, name
        self.unit_btn.text = "الفئة: " + name + "  (جديدة)"
        self.pack_in.disabled = False
        if not self.pack_in.text.strip():
            self.pack_in.text = "1" if unit_canon(name) in ("حبه", "حبة") else ""
        self._show_stock()

    def on_voice(self, texts):
        """الصوت بجانب الفئة: يكتب اسم الفئة (كرتون / حبة ...)."""
        if not texts:
            return
        if not (self.prod or self.pname):
            return msg_popup("تنبيه", "اختر الصنف أولاً ثم انطق الفئة", T.ORANGE)
        words = [w for w in (texts[0] or "").replace("،", " ").split() if w]
        name = self._unit_display(words[0]) if words else ""
        if not name:
            return
        if self.prod:
            ex = next((u for u in self.prod.get("units", [])
                       if unit_canon(u["name"]) == unit_canon(name)), None)
            if ex:
                return self.set_unit(ex)
        self.set_new_unit(name)
        self.app.show_banner("سمعت: " + str(texts[0])[:60])

    def voice_fill(self, r):
        """تعبئة فاتورة الشراء من أمر صوتي مثل: اشتريت 5 كراتين شاهي من سعيد آجل."""
        db = self.app.db
        if r.get("pay") and self.pro:
            self.set_pay(r["pay"])
        s_ = r.get("party")
        if s_ and self.pro:
            self.sup = s_["id"]
            self.sup_btn.text = s_["name"]
        for it in r.get("items", []):
            qty = it.get("qty") or 1
            p = it.get("product")
            if p:
                self.set_product(p)
                u = None
                if it.get("unit"):
                    u = next((x for x in p.get("units", [])
                              if unit_canon(x["name"]) == unit_canon(it["unit"])), None)
                if u:
                    self.set_unit(u)
                elif not p.get("units"):
                    self.set_new_unit(self._unit_display(it.get("unit")))
            else:
                name = (it.get("name") or "").strip()
                if not name:
                    continue
                self.set_new_product(name)
                self.set_new_unit(self._unit_display(it.get("unit")))
            if not self.pack_in.text.strip():
                self.pack_in.text = "1"
            self.qty_in.text = fmt(qty)
            self.add_line()
        name = (r.get("party_name") or "").strip()
        if name and self.pro and not self.sup:
            def go():
                def after():
                    self.sup = db["suppliers"][-1]["id"]
                    self.sup_btn.text = db["suppliers"][-1]["name"]
                edit_party_popup(self.app, "suppliers", None, after, name)
            confirm_popup("مورد جديد", f"المورد «{name}» غير موجود. هل تضيفه؟", go)

    # ---- المتوفر ----
    def _load_stock(self):
        self._pieces = 0
        if self.prod and self.wh:
            self._pieces = stock_map(self.app.db).get((self.prod["id"], self.wh), 0)
        self._show_stock()

    def _show_stock(self):
        pcs = self._pieces
        pack = num(self.pack_in.text, 0)
        if self.uname and pack > 0:
            txt = f"المتوفر: {fmt(round(pcs / pack, 2))} {self.uname}  ({fmt(pcs)} حبة)"
        else:
            txt = f"المتوفر: {fmt(pcs)} حبة" if (self.prod or self.pname) else "المتوفر: -"
        set_text(self.stock_lbl, txt, T.GREEN if pcs > 0 else T.MUTED)

    # ---- الباركود ----
    def scan_barcode(self, *a):
        scan_with_fallback(self.on_barcode)

    def on_barcode(self, code):
        self.bc_in.text = str(code)       # الرقم يظهر في الحقل
        self.lookup_barcode()

    def lookup_barcode(self, *a):
        code = self.bc_in.text.strip()
        if not code:
            return False
        p = product_by_barcode(self.app.db, code)
        if p:
            self.set_product(p, barcode=code)
            return True
        if not (self.prod or self.pname):
            self.app.show_banner("باركود جديد: اختر الصنف أو اكتب اسم صنف جديد")
        return False

    # ---- بنود الفاتورة ----
    def _form_dirty(self):
        return bool(self.prod or self.pname or self.qty_in.text.strip())

    def _clear_form(self):
        self.prod, self.pname = None, ""
        self._pieces = 0
        self.prod_btn.text = "اكتب الصنف"
        self.bc_in.text = ""
        self.qty_in.text = ""
        self._clear_unit()
        set_text(self.stock_lbl, "المتوفر: -", T.MUTED)

    def _same_line(self, ln, pid, name, unit):
        same = (pid and ln.get("pid") == pid) or \
               (ln.get("name", "").strip().lower() == name.strip().lower())
        return same and ln.get("unit") == unit

    def add_line(self, *a):
        if not (self.prod or self.pname):
            self.lookup_barcode()
        if not (self.prod or self.pname):
            return msg_popup("تنبيه", "اختر الصنف أو امسح الباركود أولاً", T.ORANGE)
        un = self.uname.strip()
        if not un:
            return msg_popup("تنبيه", "اختر الفئة أو أضف فئة جديدة", T.ORANGE)
        pack = num(self.pack_in.text, 1) if self.pack_in.text.strip() else 1
        cost, price = num(self.cost_in.text), num(self.price_in.text)
        qty = num(self.qty_in.text)
        if pack <= 0 or cost < 0 or price < 0:
            return msg_popup("تنبيه", "تحقق من عدد الحبات والأسعار", T.ORANGE)
        if qty <= 0:
            return msg_popup("تنبيه", "اكتب العدد المشترى", T.ORANGE)
        name = self._cur_name()
        pid = self.prod["id"] if self.prod else None
        if any(self._same_line(l, pid, name, un) for l in self.lines):
            return msg_popup("تنبيه", "هذا الصنف بهذه الفئة مضاف في الفاتورة، اضغط «تعديل» على سطره",
                             T.ORANGE)
        ln = {"pid": pid, "name": name, "unit": un, "pack": pack, "qty": qty,
              "cost": cost, "price": price}
        bc = self.bc_in.text.strip()
        if bc:
            ln["barcode"] = bc
        self.lines.append(ln)
        self._clear_form()
        self.refresh_lines()

    def refresh_lines(self):
        self.lines_box.clear_widgets()
        for i, ln in enumerate(self.lines):
            row = CardBox(orientation="horizontal", size_hint_y=None, height=dp(62),
                          padding=[dp(10), dp(6)], spacing=dp(8), radius=dp(12))
            d = mk_btn("X", size_hint=(None, None), size=(dp(38), dp(38)), background_color=T.RED,
                       pos_hint={"center_y": 0.5})
            d.bind(on_release=lambda w, i=i: (self.lines.pop(i), self.refresh_lines()))
            row.add_widget(d)
            ed = mk_btn("تعديل", size_hint=(None, None), size=(dp(58), dp(38)), font_size=dp(13),
                        background_color=T.BLUE, pos_hint={"center_y": 0.5})
            ed.bind(on_release=lambda w, i=i: self.edit_line(i))
            row.add_widget(ed)
            row.add_widget(mk_text(money(ln["qty"] * ln["cost"]), dp(16), T.PRIMARY, halign="left",
                                   width=dp(90)))
            col = BoxLayout(orientation="vertical")
            col.add_widget(mk_text(ln["name"], dp(15), T.TEXT, hint_y=0.55))
            col.add_widget(mk_text(f"{fmt(ln['qty'])} {ln['unit']} × {money(ln['cost'])}"
                                   f"  |  بيع {money(ln['price'])}", dp(12), T.MUTED, hint_y=0.45))
            row.add_widget(col)
            self.lines_box.add_widget(row)
        total = sum(l["qty"] * l["cost"] for l in self.lines)
        self.total_lbl.text = f"الإجمالي: {money(total)} ريال"
        schedule_refresh(0.1)

    def edit_line(self, i):
        """يعيد الصنف إلى البطاقة للتعديل (ثم اضغط حفظ الصنف)."""
        if self._form_dirty():
            return msg_popup("تنبيه", "أنهِ الصنف الحالي بزر «حفظ الصنف» أولاً", T.ORANGE)
        ln = self.lines.pop(i)
        p = find(self.app.db["products"], ln.get("pid")) if ln.get("pid") else None
        if p is None:
            p = next((x for x in self.app.db["products"]
                      if x["name"].strip().lower() == ln.get("name", "").strip().lower()), None)
        if p:
            self.set_product(p)
        else:
            self.set_new_product(ln.get("name", ""))
        u = next((x for x in (p or {}).get("units", []) if x.get("name") == ln.get("unit")), None)
        if u:
            self.set_unit(u)
        else:
            self.set_new_unit(ln.get("unit", ""))
            self.pack_in.text = fmt(ln.get("pack", 1))
        self.cost_in.text = fmt(ln.get("cost", 0))
        self.price_in.text = fmt(ln.get("price", 0))
        self.qty_in.text = fmt(ln.get("qty", 0))
        if ln.get("barcode"):
            self.bc_in.text = ln["barcode"]
        self.refresh_lines()
        self._show_stock()

    # ---- الحفظ ----
    def delete(self, *a):
        if not self.app.need_edit():
            return

        def go():
            delete_rec(self.app.db, "purchases", self.rec["id"])
            self.app.save()
            self.app.back()
        confirm_popup("تأكيد الحذف", "حذف فاتورة الشراء نهائياً؟ ستُخصم كمياتها من المخزن.", go)

    def _build_items(self):
        """يحدّث الأصناف/الفئات في المخزن ويرجع بنود الفاتورة.
        عند تعديل فاتورة قديمة لا نغيّر أسعار فئات موجودة (قد تكون حُدّثت بعدها)."""
        db = self.app.db
        editing = bool(self.rec)
        items = []
        for ln in self.lines:
            name = ln["name"].strip()
            p = find(db["products"], ln.get("pid")) if ln.get("pid") else None
            if p is None:
                p = next((x for x in db["products"] if x["name"].strip().lower() == name.lower()), None)
            fresh = p is None
            if fresh:
                p = touch({"id": gen_id(), "name": name, "units": [], "pcost": 0})
                db["products"].append(p)
            u = next((x for x in p.get("units", []) if x.get("name") == ln["unit"]), None)
            if u is None:
                u = {"name": ln["unit"], "pack": ln["pack"], "cost": ln["cost"], "price": ln["price"]}
                p.setdefault("units", []).append(u)
            elif not editing:
                u["cost"], u["price"] = ln["cost"], ln["price"]
            if fresh or not editing:
                one = next((x for x in p["units"] if x.get("pack") == 1), None)
                if one is not None:
                    p["pcost"] = one["cost"]
                else:
                    f = p["units"][0]
                    p["pcost"] = f["cost"] / f["pack"] if f.get("pack") else 0
            if ln.get("barcode") and not p.get("barcode"):
                p["barcode"] = ln["barcode"]
            touch(p)
            items.append({"unit": u["name"], "pack": u["pack"], "qty": ln["qty"], "cost": ln["cost"],
                          "price": ln["price"], "pid": p["id"], "name": p["name"]})
        return items

    def save(self, *a):
        app = self.app
        if not app.need_edit():
            return
        if self._form_dirty():
            return msg_popup("تنبيه", "هناك صنف لم يُحفظ، اضغط «حفظ الصنف» أو امسح الحقول", T.ORANGE)
        if not self.lines:
            return msg_popup("تنبيه", "أضف صنفاً واحداً على الأقل", T.ORANGE)
        rec = self.rec
        pay = self.pay if self.pro else (rec.get("pay", "cash") if rec else "cash")
        sup = self.sup if self.pro else (rec.get("sup") if rec else None)
        if pay == "credit" and not sup:
            return msg_popup("تنبيه", "اختر المورد للشراء الآجل", T.ORANGE)
        items = self._build_items()
        if rec:
            rec["items"], rec["pay"], rec["sup"], rec["wh"] = items, pay, sup, self.wh
            touch(rec)
            app.save()
            return app.back()
        app.db["purchases"].append(touch({"id": gen_id(), "sup": sup, "wh": self.wh, "date": now_str(),
                                          "pay": pay, "items": items}))
        app.save()
        app.back()
        msg_popup("تم", f"تم حفظ الفاتورة ({len(items)} صنف/فئة)", T.GREEN)


class EditPurchasePage(Page):
    """تعديل فاتورة شراء: المورد، الدفع، المخزن، وكمية وسعر كل صنف."""

    def __init__(self, app, rec, **kw):
        super().__init__(app, "تعديل فاتورة شراء", T.BLUE, **kw)
        self.rec = rec
        db = app.db
        self.pro = app.plan() != "free"
        self.sup = rec.get("sup")
        self.pay = rec.get("pay", "cash")
        self.wh = rec.get("wh")
        self.rows = []
        if self.pro:
            s = find(db["suppliers"], self.sup) if self.sup else None
            self.sup_btn = picker_btn(s["name"] if s else "اختر المورد")
            self.sup_btn.bind(on_release=self.pick_sup)
            self.sup_btn.height = dp(40)
            self.radio = PayRadio(self.set_pay, self.pay)
            pr = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
            pr.add_widget(self.radio)
            pr.add_widget(self.sup_btn)
            self.add_pinned(pr)
        self.wh_btn = picker_btn(self.wh_name())
        self.wh_btn.bind(on_release=self.pick_wh)
        self.add(self.wh_btn)
        self.label("الأصناف: عدّل العدد أو سعر الشراء، أو احذف الصنف، أو أضف صنفاً", 12)
        self.cards_box = GridLayout(cols=1, spacing=dp(8), size_hint_y=None)
        self.cards_box.bind(minimum_height=self.cards_box.setter("height"))
        self.add(self.cards_box)
        for it in rec.get("items", []):
            self.add_item_card(it)
        ad = mk_btn("+ إضافة صنف للفاتورة", height=dp(46), background_color=T.PURPLE)
        ad.bind(on_release=lambda *a: self.add_new_item())
        self.add(ad)
        sv = mk_btn("حفظ", height=dp(52), font_size=dp(16), background_color=T.GREEN)
        dl = mk_btn("حذف الفاتورة", height=dp(52), font_size=dp(15), background_color=T.RED)
        sv.bind(on_release=self.save)
        dl.bind(on_release=self.delete)
        self.add_footer(dl, sv)

    def add_new_item(self):
        """اختيار صنف موجود ثم فئته وإضافته للفاتورة."""
        db = self.app.db
        if not db["products"]:
            return msg_popup("تنبيه", "لا توجد أصناف، أضف صنفاً من صفحة إضافة فاتورة شراء", T.ORANGE)
        opts = [(p["name"], "", p["id"], None) for p in db["products"]]

        def chosen_unit(p, u):
            self.add_item_card({"pid": p["id"], "name": p["name"], "unit": u.get("name", ""),
                                "pack": u.get("pack", 1), "qty": 1, "cost": u.get("cost", 0),
                                "price": u.get("price", 0)})
            schedule_refresh(0.1)

        def chosen(pid):
            p = find(db["products"], pid)
            us = p.get("units", [])
            if len(us) <= 1:
                chosen_unit(p, us[0] if us else {"name": "حبة", "pack": 1})
                return
            uo = [(u.get("name", ""), f"{fmt(u.get('pack', 1))} حبة - شراء {money(u.get('cost', 0))}",
                   i, None) for i, u in enumerate(us)]
            Clock.schedule_once(lambda dt: pick_popup("اختر الفئة", uo,
                                                      lambda i: chosen_unit(p, us[i])), 0.25)
        pick_popup("اختر الصنف", opts, chosen)

    def wh_name(self):
        w = find(self.app.db["warehouses"], self.wh)
        return ("المخزن: " + w["name"]) if w else "اختر المخزن"

    def set_pay(self, p):
        self.pay = p
        self.radio.set(p)

    def pick_sup(self, *a):
        db = self.app.db
        opts = [(s["name"], f"الرصيد: {money_signed(sup_balance(db, s['id']))}", s["id"], None)
                for s in db["suppliers"]]

        def chosen(sid):
            self.sup = sid
            self.sup_btn.text = find(db["suppliers"], sid)["name"]
        pick_popup("اختر المورد", opts, chosen)

    def pick_wh(self, *a):
        opts = [(w["name"], "", w["id"], None) for w in self.app.db["warehouses"]]

        def chosen(wid):
            self.wh = wid
            self.wh_btn.text = self.wh_name()
        pick_popup("اختر المخزن", opts, chosen)

    def add_item_card(self, it):
        card = CardBox(orientation="vertical", size_hint_y=None, height=dp(176),
                       padding=[dp(10), dp(8)], spacing=dp(4))
        card.add_widget(mk_text(f"{it.get('name', '')} - {it.get('unit', '')}", dp(15), T.TEXT,
                                height=dp(26)))
        b1, qty_in = lab_in("العدد", "0", "float", fmt(it.get("qty", 0)))
        b2, cost_in = lab_in("سعر الشراء", "0", "float", fmt(it.get("cost", 0)))
        card.add_widget(two_cols(b2, b1))
        rm = mk_btn("حذف الصنف من الفاتورة", height=dp(38), background_color=T.RED, font_size=dp(13))
        card.add_widget(rm)
        rec = {"item": it, "qty": qty_in, "cost": cost_in, "card": card, "removed": False}
        rm.bind(on_release=lambda *a: self.remove_item(rec))
        self.rows.append(rec)
        self.cards_box.add_widget(card)

    def remove_item(self, rec):
        rec["removed"] = True
        if rec["card"].parent is not None:
            rec["card"].parent.remove_widget(rec["card"])

    def save(self, *a):
        app = self.app
        if not app.need_edit():
            return
        items = []
        for r in self.rows:
            if r["removed"]:
                continue
            qty, cost = num(r["qty"].text, -1), num(r["cost"].text, -1)
            if qty <= 0 or cost < 0:
                return msg_popup("تنبيه", "تحقق من العدد وسعر الشراء", T.ORANGE)
            it = dict(r["item"])
            it["qty"], it["cost"] = qty, cost
            items.append(it)
        if not items:
            return msg_popup("تنبيه", "الفاتورة بلا أصناف، استخدم زر حذف الفاتورة", T.ORANGE)
        rec = self.rec
        pay = self.pay if self.pro else rec.get("pay", "cash")
        sup = self.sup if self.pro else rec.get("sup")
        if pay == "credit" and not sup:
            return msg_popup("تنبيه", "اختر المورد للشراء الآجل", T.ORANGE)
        rec["items"], rec["pay"], rec["sup"], rec["wh"] = items, pay, sup, self.wh
        touch(rec)
        app.save()
        app.back()

    def delete(self, *a):
        if not self.app.need_edit():
            return

        def go():
            delete_rec(self.app.db, "purchases", self.rec["id"])
            self.app.save()
            self.app.back()
        confirm_popup("تأكيد الحذف", "حذف فاتورة الشراء نهائياً؟ ستُخصم كمياتها من المخزن.", go)


# ============================================================
# 10) العملاء والموردين
# ============================================================
class PartiesPage(Page):
    def __init__(self, app, kind, **kw):
        super().__init__(app, "العملاء" if kind == "c" else "الموردين", **kw)
        self.kind = kind
        self.coll = "customers" if kind == "c" else "suppliers"
        self.q = ""
        add = mk_btn("+ إضافة " + ("عميل" if kind == "c" else "مورد"), height=dp(50),
                     font_size=dp(16), background_color=T.GREEN)
        add.bind(on_release=lambda *a: self.add_popup())
        self.add(add)
        self.search = mk_input(hint_text="بحث بالاسم أو الرقم...", size_hint_y=None, height=dp(48))
        self.search.bind(text=lambda i, v: (setattr(self, "q", v.strip().lower()), self.fill()))
        self.add(self.search)
        self.lst = GridLayout(cols=1, spacing=dp(8), size_hint_y=None)
        self.lst.bind(minimum_height=self.lst.setter("height"))
        self.add(self.lst)
        self.fill()

    def fill(self):
        self.lst.clear_widgets()
        db = self.app.db
        fn = cust_balance if self.kind == "c" else sup_balance
        n = 0
        for p in db[self.coll]:
            if self.q and self.q not in p["name"].lower() and self.q not in (p.get("phone") or ""):
                continue
            n += 1
            self.lst.add_widget(party_row(p["name"], p.get("phone"), fn(db, p["id"]), self.kind,
                                          lambda pid=p["id"]: self.app.push(
                                              lambda: PartyDetailPage(self.app, self.kind, pid))))
        if not n:
            self.lst.add_widget(mk_text("لا يوجد بعد" if not self.q else "لا توجد نتائج", dp(15),
                                        T.MUTED, halign="center", height=dp(60)))
        schedule_refresh(0.1)

    def add_popup(self):
        if not self.app.need_edit():
            return
        edit_party_popup(self.app, self.coll, None, self.fill)


def pick_contact(cb, fail):
    """يفتح جهات اتصال الهاتف ويرجع (الاسم، الرقم) عبر cb. لا يحتاج صلاحية قراءة الجهات."""
    if platform != "android":
        return fail("اختيار جهات الاتصال متاح على الأندرويد فقط")
    try:
        from jnius import autoclass
        from android import activity, mActivity
        Intent = autoclass("android.content.Intent")
        Uri = autoclass("android.net.Uri")
        code = 7301

        def on_result(req, res, data):
            if req != code:
                return
            try:
                activity.unbind(on_activity_result=on_result)
            except Exception:
                pass
            if res != -1 or data is None:
                return
            name = num_ = ""
            try:
                cur = mActivity.getContentResolver().query(data.getData(), None, None, None, None)
                if cur is not None:
                    if cur.moveToFirst():
                        num_ = cur.getString(cur.getColumnIndex("data1")) or ""
                        name = cur.getString(cur.getColumnIndex("display_name")) or ""
                    cur.close()
            except Exception as e:
                print(f"[CONTACT] {e}")
            Clock.schedule_once(lambda dt: cb(str(name), str(num_)), 0)
        activity.bind(on_activity_result=on_result)
        it = Intent(Intent.ACTION_PICK)
        it.setData(Uri.parse("content://com.android.contacts/data/phones"))
        mActivity.startActivityForResult(it, code)
    except Exception as e:
        print(f"[CONTACT] {e}")
        fail("تعذر فتح جهات الاتصال")


def edit_party_popup(app, coll, rec, after, name=""):
    box = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(10))
    b1, name_in = lab_in("الاسم", "الاسم الكامل", None, rec["name"] if rec else name)
    b2, phone_in = lab_in("رقم الواتساب", "0771234567", "int", rec.get("phone", "") if rec else "")
    box.add_widget(b1)
    box.add_widget(b2)
    ct = mk_btn("اختيار من جهات الاتصال", height=dp(42), font_size=dp(14), background_color=T.TEAL)
    box.add_widget(ct)
    box.add_widget(Widget())
    bb = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(8))
    sv = mk_btn("حفظ", background_color=T.GREEN)
    cn = mk_btn("إلغاء", background_color=T.GRAY_BTN)
    bb.add_widget(sv)
    bb.add_widget(cn)
    box.add_widget(bb)
    pop = make_arabic_popup("بيانات", box, size_hint=(0.92, 0.66))

    def got(n, p):
        digits = re.sub(r"[^\d+]", "", p)
        if digits.startswith("+"):
            digits = digits[1:]
        if digits.startswith("00"):
            digits = digits[2:]
        phone_in.text = digits
        if n and not name_in.text.strip():
            name_in.text = n
    ct.bind(on_release=lambda *a: pick_contact(got, lambda m: msg_popup("جهات الاتصال", m, T.ORANGE)))

    def save(*a):
        n = name_in.text.strip()
        if not n:
            return
        if rec:
            rec["name"], rec["phone"] = n, phone_in.text.strip()
            touch(rec)
        else:
            app.db[coll].append(touch({"id": gen_id(), "name": n, "phone": phone_in.text.strip()}))
        app.save()
        pop.dismiss()
        after()
    sv.bind(on_release=save)
    cn.bind(on_release=lambda *a: pop.dismiss())
    pop.open()


class PartyDetailPage(Page):
    def __init__(self, app, kind, pid, **kw):
        super().__init__(app, "بيانات العميل" if kind == "c" else "بيانات المورد", **kw)
        self.kind, self.pid = kind, pid
        self.coll = "customers" if kind == "c" else "suppliers"
        db = app.db
        p = find(db[self.coll], pid)
        if not p:
            return
        ops = party_ops(db, kind, pid)
        if kind == "c":
            took = sum(o["amount"] for o in ops if o["type"] == "sale")
            paid = sum(o["amount"] for o in ops if o["type"] == "voucher")
            bal = cust_balance(db, pid)
            labs = ("إجمالي مشترياته", "إجمالي ما سدّده", "المتبقي عليه")
        else:
            took = sum(o["amount"] for o in ops if o["type"] == "purchase")
            paid = sum(o["amount"] for o in ops if o["type"] == "voucher")
            bal = sup_balance(db, pid)
            labs = ("إجمالي ما اشتريته منه", "إجمالي ما سددته له", "المتبقي عليّ له")

        # ---- الاسم وبجانبه حذف وتعديل ----
        top = CardBox(orientation="horizontal", size_hint_y=None, height=dp(94),
                      padding=[dp(12), dp(8)], spacing=dp(8), bg=T.HEADER)
        side = BoxLayout(orientation="vertical", size_hint_x=None, width=dp(96), spacing=dp(6))
        b_edit = mk_btn("تعديل", height=dp(36), font_size=dp(14), background_color=GLASS)
        b_del = mk_btn("حذف", height=dp(36), font_size=dp(14), background_color=T.RED)
        b_edit.bind(on_release=lambda *a: self.edit())
        b_del.bind(on_release=lambda *a: self.delete())
        side.add_widget(b_edit)
        side.add_widget(b_del)
        top.add_widget(side)
        info = BoxLayout(orientation="vertical")
        info.add_widget(mk_text(p["name"], dp(21), WHITE, hint_y=0.55))
        info.add_widget(mk_text("الرقم: " + (p.get("phone") or "غير محدد"), dp(13), SOFT_WHITE, hint_y=0.45))
        top.add_widget(info)
        top.height = dp(80)
        self.add_pinned(top)

        def mini(lab, val, col):
            c = CardBox(orientation="vertical", size_hint_y=None, height=dp(56),
                        padding=[dp(6), dp(4)], radius=dp(10))
            c.add_widget(mk_text(lab, dp(11), T.MUTED, halign="center", hint_y=0.42))
            c.add_widget(mk_text(val, dp(15), col, halign="center", hint_y=0.58))
            return c
        stats = BoxLayout(size_hint_y=None, height=dp(56), spacing=dp(6))
        for lab, val, col in reversed([(labs[0], money(took), T.PRIMARY),
                                       (labs[1], money(paid), T.GREEN),
                                       (labs[2], money_signed(bal), T.RED if bal > 0 else T.GREEN)]):
            stats.add_widget(mini(lab, val, col))
        self.add_pinned(stats)

        # ---- تحت: كشف الحساب + فاتورة + سند (الأول على اليمين) ----
        if kind == "c":
            specs = [("إرسال\nكشف الحساب", T.BLUE, self.statement),
                     ("إضافة\nفاتورة", T.ORANGE, lambda: app.open_add_sale(pid)),
                     ("إضافة\nسند قبض", T.GREEN, lambda: app.open_receipt("c", pid))]
        else:
            specs = [("إرسال\nكشف الحساب", T.BLUE, self.statement),
                     ("إضافة\nفاتورة شراء", T.ORANGE, lambda: app.open_purchase(pid)),
                     ("إضافة\nسند صرف", T.TEAL, lambda: app.open_receipt("s", pid))]
        acts = BoxLayout(size_hint_y=None, height=dp(58), spacing=dp(6))
        for t, col, fn in reversed(specs):
            b = mk_btn(t, font_size=dp(14), background_color=col, height=dp(58))
            b.bind(on_release=lambda w, fn=fn: fn())
            acts.add_widget(b)
        self.add_pinned(acts)

        # ---- فواتيره وسنداته: اضغط لفتحها وتعديلها وإرسالها ----
        self.label(f"الفواتير والسندات ({len(ops)})", 15, T.PRIMARY)
        for o in reversed(ops):
            col = T.RED if (o["eff"] > 0) else (T.GREEN if o["eff"] < 0 else T.MUTED)
            row = CardBox(orientation="horizontal", size_hint_y=None, height=dp(64),
                          padding=[dp(12), dp(6)], on_tap=lambda o=o: self.open_op(o))
            row.add_widget(mk_text(money(o["amount"]), dp(17), col, halign="left", width=dp(110)))
            c = BoxLayout(orientation="vertical")
            c.add_widget(mk_text(o["label"], dp(15), T.TEXT, hint_y=0.55))
            c.add_widget(mk_text(o["date"], dp(12), T.MUTED, hint_y=0.45))
            row.add_widget(c)
            self.add(row)

    def open_op(self, o):
        self.app.open_op({"type": o["type"], "id": o["rec"]["id"]})

    def statement(self):
        png, size, text, phone = safe_doc(statement_doc, self.app, self.kind, self.pid)
        show_doc_popup(png, size, text, phone, "كشف الحساب")

    def edit(self):
        if not self.app.need_edit():
            return
        edit_party_popup(self.app, self.coll, find(self.app.db[self.coll], self.pid), self.app.reload)

    def delete(self):
        if not self.app.need_edit():
            return

        def go():
            db = self.app.db
            delete_rec(db, self.coll, self.pid)
            for c, key in (("sales", "cust"), ("purchases", "sup")):
                for r in list(db[c]):
                    if r.get(key) == self.pid:
                        delete_rec(db, c, r["id"])
            for v in list(db["vouchers"]):
                if v.get("pid") == self.pid:
                    delete_rec(db, "vouchers", v["id"])
            self.app.save()
            self.app.back()
        confirm_popup("تأكيد الحذف", "سيُحذف مع كل عملياته. متأكد؟", go)


# ============================================================
# 11) العمال
# ============================================================
class WorkersPage(Page):
    def __init__(self, app, **kw):
        super().__init__(app, "العمال", **kw)
        add = mk_btn("+ إضافة عامل", height=dp(50), font_size=dp(16), background_color=T.GREEN)
        add.bind(on_release=lambda *a: app.need_edit() and edit_party_popup(app, "workers", None, app.reload))
        self.add(add)
        db = app.db
        for w in db["workers"]:
            paid = sum(e["amount"] for e in db["cash"] if e.get("wid") == w["id"])
            self.add(party_row(w["name"], w.get("phone"), paid, "w",
                               lambda wid=w["id"]: app.push(lambda: WorkerDetailPage(app, wid))))
        if not db["workers"]:
            self.label("لا يوجد عمال بعد", 15, T.MUTED, dp(60), "center")


class WorkerDetailPage(Page):
    def __init__(self, app, wid, **kw):
        super().__init__(app, "بيانات العامل", **kw)
        self.wid = wid
        db = app.db
        w = find(db["workers"], wid)
        if not w:
            return
        pays = sorted([e for e in db["cash"] if e.get("wid") == wid], key=lambda e: e["date"], reverse=True)
        self.add(kv_row("الاسم", w["name"], T.TEXT))
        self.add(kv_row("إجمالي ما صُرف له", money(sum(e["amount"] for e in pays)), T.ORANGE))
        bb = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(8))
        pay = mk_btn("صرف راتب / سلفة", background_color=T.GREEN)
        pay.bind(on_release=lambda *a: self.pay())
        dl = mk_btn("حذف العامل", background_color=T.RED)
        dl.bind(on_release=lambda *a: self.delete())
        bb.add_widget(dl)
        bb.add_widget(pay)
        self.add(bb)
        self.label("المصروفات", 15, T.PRIMARY)
        for e in pays:
            row = CardBox(orientation="horizontal", size_hint_y=None, height=dp(62),
                          padding=[dp(12), dp(6)],
                          on_tap=lambda e=e: confirm_popup("حذف", "حذف هذه الدفعة؟",
                                                          lambda: self.del_pay(e["id"])))
            row.add_widget(mk_text(money(e["amount"]), dp(17), T.ORANGE, halign="left", width=dp(110)))
            c = BoxLayout(orientation="vertical")
            c.add_widget(mk_text(e.get("note") or "دفعة", dp(15), T.TEXT, hint_y=0.55))
            c.add_widget(mk_text(e["date"], dp(12), T.MUTED, hint_y=0.45))
            row.add_widget(c)
            self.add(row)

    def del_pay(self, eid):
        if self.app.need_edit():
            delete_rec(self.app.db, "cash", eid)
            self.app.save()
            self.app.reload()

    def pay(self):
        if not self.app.need_edit():
            return
        cash_popup(self.app, "out", self.app.reload, wid=self.wid, title="صرف للعامل")

    def delete(self):
        if not self.app.need_edit():
            return

        def go():
            delete_rec(self.app.db, "workers", self.wid)
            self.app.save()
            self.app.back()
        confirm_popup("تأكيد", "حذف العامل؟ (تبقى دفعاته في الصندوق)", go)


def cash_popup(app, kind, after, wid=None, title=None):
    box = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(10))
    b1, amt = lab_in("المبلغ", "0", "float")
    b2, note = lab_in("ملاحظة", "")
    box.add_widget(b1)
    box.add_widget(b2)
    box.add_widget(Widget())
    bb = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(8))
    sv = mk_btn("حفظ", background_color=T.GREEN)
    cn = mk_btn("إلغاء", background_color=T.GRAY_BTN)
    bb.add_widget(sv)
    bb.add_widget(cn)
    box.add_widget(bb)
    pop = make_arabic_popup(title or ("إيداع في الصندوق" if kind == "in" else "سحب من الصندوق"), box,
                            size_hint=(0.92, 0.55))

    def save(*a):
        a_ = num(amt.text)
        if a_ <= 0:
            return
        e = touch({"id": gen_id(), "kind": kind, "amount": a_, "note": note.text.strip(), "date": now_str()})
        if wid:
            e["wid"] = wid
        app.db["cash"].append(e)
        app.save()
        pop.dismiss()
        after()
    sv.bind(on_release=save)
    cn.bind(on_release=lambda *a: pop.dismiss())
    pop.open()


# ============================================================
# 12) المخازن والصندوق
# ============================================================
class WalletPage(Page):
    """الصندوق والمخازن في تبويبين منفصلين بدل صفحة واحدة مخلوطة."""

    def __init__(self, app, **kw):
        super().__init__(app, "المخازن والصندوق", **kw)
        self.tab = getattr(app, "wallet_tab", "cash")
        self.cash_limit = 30
        bar = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        self.tab_btns = {}
        for key, text in (("wh", "المخازن"), ("cash", "الصندوق")):      # الصندوق يظهر على اليمين
            b = mk_btn(text, font_size=dp(15), height=dp(44), background_color=T.GRAY_BTN)
            b.bind(on_release=lambda w, k=key: self.show(k))
            bar.add_widget(b)
            self.tab_btns[key] = b
        self.add_pinned(bar)
        self.show(self.tab)

    def show(self, tab):
        self.tab = tab
        self.app.wallet_tab = tab
        for k, b in self.tab_btns.items():
            b.set_bg(T.PRIMARY if (k == tab and not is_dark()) else (T.BLUE if k == tab else T.GRAY_BTN))
        self.body.clear_widgets()
        if tab == "cash":
            self.fill_cash()
        else:
            self.fill_wh()
        schedule_refresh(0.1)

    # ---------- الصندوق ----------
    def fill_cash(self):
        app, db = self.app, self.app.db
        bal = cash_balance(db)
        top = CardBox(orientation="vertical", size_hint_y=None, height=dp(96),
                      padding=[dp(14), dp(8)], bg=T.HEADER)
        top.add_widget(mk_text("رصيد الصندوق", dp(13), SOFT_WHITE, hint_y=0.4))
        top.add_widget(mk_text(f"{money_signed(bal)} ريال", dp(26),
                               (1, 0.93, 0.65, 1) if bal >= 0 else (1, 0.6, 0.6, 1), hint_y=0.6))
        self.add(top)
        bb = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(8))
        wd = mk_btn("سحب", background_color=T.RED)
        dp_ = mk_btn("إيداع", background_color=T.GREEN)
        wd.bind(on_release=lambda *a: app.need_edit() and cash_popup(app, "out", app.reload))
        dp_.bind(on_release=lambda *a: app.need_edit() and cash_popup(app, "in", app.reload))
        bb.add_widget(wd)
        bb.add_widget(dp_)
        self.add(bb)
        led = cash_ledger(db)
        self.label(f"حركة الصندوق ({len(led)})", 15, T.PRIMARY)
        if not led:
            self.add(mk_text("لا توجد حركات بعد", dp(14), T.MUTED, halign="center", height=dp(50)))
        for d, lab, amt in led[:self.cash_limit]:
            row = CardBox(orientation="horizontal", size_hint_y=None, height=dp(58),
                          padding=[dp(12), dp(4)], radius=dp(10))
            row.add_widget(mk_text(("+" if amt >= 0 else "-") + money(abs(amt)), dp(15),
                                   T.GREEN if amt >= 0 else T.RED, halign="left", width=dp(100)))
            c = BoxLayout(orientation="vertical")
            c.add_widget(mk_text(lab, dp(14), T.TEXT, hint_y=0.55))
            c.add_widget(mk_text(d, dp(11), T.MUTED, hint_y=0.45))
            row.add_widget(c)
            self.add(row)
        if len(led) > self.cash_limit:
            more = mk_btn("عرض المزيد", height=dp(46), background_color=T.GRAY_BTN)
            more.bind(on_release=lambda *a: (setattr(self, "cash_limit", self.cash_limit + 30),
                                             self.show("cash")))
            self.add(more)

    # ---------- المخازن ----------
    def fill_wh(self):
        app, db = self.app, self.app.db
        sm = stock_map(db)
        try:
            tot_val = stock_value(db, sm)
        except Exception:
            tot_val = 0
        top = CardBox(orientation="vertical", size_hint_y=None, height=dp(96),
                      padding=[dp(14), dp(8)], bg=T.HEADER)
        top.add_widget(mk_text(f"قيمة المخزون في {len(db['warehouses'])} مخزن", dp(13), SOFT_WHITE, hint_y=0.4))
        top.add_widget(mk_text(f"{money(tot_val)} ريال", dp(26), (1, 0.93, 0.65, 1), hint_y=0.6))
        self.add(top)
        add = mk_btn("+ إضافة مخزن", height=dp(48), background_color=T.BLUE)
        add.bind(on_release=lambda *a: self.add_wh())
        self.add(add)
        self.label("المخازن", 15, T.PRIMARY)
        for w in db["warehouses"]:
            items = [(pid, q) for (pid, wid), q in sm.items() if wid == w["id"] and q != 0]
            total = sum(q for _p, q in items)
            val = sum(q * (find(db["products"], p) or {}).get("pcost", 0) for p, q in items if q > 0)
            empty = not any(q > 0 for _p, q in items)
            row = CardBox(orientation="horizontal", size_hint_y=None, height=dp(70),
                          padding=[dp(12), dp(6)],
                          on_tap=lambda wid=w["id"]: app.push(lambda: WarehousePage(app, wid)))
            c1 = BoxLayout(orientation="vertical", size_hint_x=None, width=dp(130))
            c1.add_widget(mk_text(f"{fmt(total)} حبة", dp(15), T.RED if empty else T.GREEN,
                                  halign="left", hint_y=0.5))
            c1.add_widget(mk_text(f"قيمة: {money(val)}", dp(12), T.MUTED, halign="left", hint_y=0.5))
            row.add_widget(c1)
            c2 = BoxLayout(orientation="vertical")
            c2.add_widget(mk_text(w["name"], dp(16), T.TEXT, hint_y=0.55))
            c2.add_widget(mk_text("فارغ" if empty else f"{len(items)} صنف", dp(12),
                                  T.RED if empty else T.MUTED, hint_y=0.45))
            row.add_widget(c2)
            self.add(row)

    def add_wh(self):
        if not self.app.need_edit():
            return
        box = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(10))
        b1, nm = lab_in("اسم المخزن", "مثال: مخزن الدكان")
        box.add_widget(b1)
        box.add_widget(Widget())
        bb = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(8))
        sv = mk_btn("حفظ", background_color=T.GREEN)
        cn = mk_btn("إلغاء", background_color=T.GRAY_BTN)
        bb.add_widget(sv)
        bb.add_widget(cn)
        box.add_widget(bb)
        pop = make_arabic_popup("إضافة مخزن", box, size_hint=(0.92, 0.42))

        def save(*a):
            if nm.text.strip():
                self.app.db["warehouses"].append(touch({"id": gen_id(), "name": nm.text.strip()}))
                self.app.save()
                pop.dismiss()
                self.app.reload()
        sv.bind(on_release=save)
        cn.bind(on_release=lambda *a: pop.dismiss())
        pop.open()


class WarehousePage(Page):
    def __init__(self, app, wid, **kw):
        w = find(app.db["warehouses"], wid) or {"name": ""}
        super().__init__(app, w["name"], **kw)
        db = app.db
        sm = stock_map(db)
        rows = []
        for p in db["products"]:
            q = sm.get((p["id"], wid), 0)
            if q != 0:
                rows.append((p, q))
        if not rows:
            self.label("المخزن فارغ", 16, T.RED, dp(60), "center")
        for p, q in rows:
            big = max(p["units"], key=lambda u: u["pack"]) if p.get("units") else None
            sub = ""
            if big and big["pack"] > 1:
                sub = f"≈ {fmt(round(q / big['pack'], 2))} {big['name']}"
            row = CardBox(orientation="horizontal", size_hint_y=None, height=dp(66), padding=[dp(12), dp(6)],
                          on_tap=lambda pid=p["id"]: app.push(lambda: ProductDetailPage(app, pid, wid)))
            row.add_widget(mk_text(f"{fmt(q)} حبة", dp(16), T.GREEN if q > 0 else T.RED,
                                   halign="left", width=dp(120)))
            c = BoxLayout(orientation="vertical")
            c.add_widget(mk_text(p["name"], dp(16), T.TEXT, hint_y=0.55))
            c.add_widget(mk_text(sub, dp(12), T.MUTED, hint_y=0.45))
            row.add_widget(c)
            self.add(row)


# ============================================================
# 12-ب) تفاصيل الصنف
# ============================================================
class ProductDetailPage(Page):
    """تفاصيل صنف: المخزون في كل مخزن، الفئات والأسعار، وإجمالي المشتريات والمبيعات."""

    def __init__(self, app, pid, wid=None, **kw):
        db = app.db
        p = find(db["products"], pid)
        super().__init__(app, p["name"] if p else "الصنف", **kw)
        if not p:
            return
        sm = stock_map(db)
        total = sum(q for (x, _w), q in sm.items() if x == pid)
        bought = sold = b_cost = s_rev = s_cost = 0.0
        ops = []
        for pu in db["purchases"]:
            for it in pu.get("items", []):
                if it.get("pid") == pid:
                    bought += it["qty"] * it.get("pack", 1)
                    amt = it["qty"] * it.get("cost", 0)
                    b_cost += amt
                    ops.append((pu.get("date", ""), "شراء", f"{fmt(it['qty'])} {it.get('unit', '')}",
                                amt, T.BLUE))
        for v in db["sales"]:
            sg = -1 if is_return(v) else 1
            for it in v.get("items", []):
                if it.get("pid") == pid:
                    sold += sg * it["qty"] * it.get("pack", 1)
                    rev = sg * line_total(it)
                    s_rev += rev
                    s_cost += sg * it["qty"] * it.get("cost", 0)
                    ops.append((v.get("date", ""), "مرتجع" if sg < 0 else "بيع",
                                f"{fmt(it['qty'])} {it.get('unit', '')}", rev,
                                T.RED if sg < 0 else T.ORANGE))

        top = CardBox(orientation="vertical", size_hint_y=None, height=dp(96),
                      padding=[dp(14), dp(8)], bg=T.HEADER)
        top.add_widget(mk_text("المخزون الحالي (كل المخازن)", dp(13), SOFT_WHITE, hint_y=0.4))
        top.add_widget(mk_text(f"{fmt(total)} حبة", dp(26),
                               (1, 0.93, 0.65, 1) if total > 0 else (1, 0.6, 0.6, 1), hint_y=0.6))
        self.add(top)

        self.label("المخزون في كل مخزن", 14, T.PRIMARY)
        for w in db["warehouses"]:
            q = sm.get((pid, w["id"]), 0)
            self.add(kv_row(w["name"] + ("  (الحالي)" if w["id"] == wid else ""),
                            f"{fmt(q)} حبة", T.GREEN if q > 0 else T.RED, dp(44)))

        self.label("الفئات والأسعار", 14, T.PRIMARY)
        for u in p.get("units", []):
            card = CardBox(orientation="vertical", size_hint_y=None, height=dp(72),
                           padding=[dp(12), dp(6)], radius=dp(12))
            card.add_widget(mk_text(f"{u.get('name', '')}  ({fmt(u.get('pack', 1))} حبة)", dp(15),
                                    T.TEXT, hint_y=0.5))
            profit = u.get("price", 0) - u.get("cost", 0)
            card.add_widget(mk_text(f"شراء {money(u.get('cost', 0))}  -  بيع {money(u.get('price', 0))}"
                                    f"  -  الربح {money_signed(profit)}", dp(12), T.MUTED, hint_y=0.5))
            self.add(card)

        self.label("الإجماليات", 14, T.PRIMARY)
        self.add(kv_row("إجمالي المشترى (حبة)", fmt(bought), T.BLUE, dp(44)))
        self.add(kv_row("تكلفة المشتريات", money(b_cost), T.BLUE, dp(44)))
        self.add(kv_row("إجمالي المباع (حبة)", fmt(sold), T.ORANGE, dp(44)))
        self.add(kv_row("قيمة المبيعات", money(s_rev), T.ORANGE, dp(44)))
        prof = s_rev - s_cost
        self.add(kv_row("ربح المبيعات", money_signed(prof), T.GREEN if prof >= 0 else T.RED, dp(44)))
        self.add(kv_row("قيمة المتبقي (بسعر الشراء)", money(total * p.get("pcost", 0)), T.PRIMARY, dp(44)))
        self.add(kv_row("قيمة المتبقي (بسعر البيع)", money(total * piece_price(p)), T.PRIMARY, dp(44)))

        ops.sort(key=lambda o: o[0], reverse=True)
        self.label(f"آخر العمليات ({len(ops)})", 14, T.PRIMARY)
        for d, kind, qty, amt, col in ops[:20]:
            row = CardBox(orientation="horizontal", size_hint_y=None, height=dp(58),
                          padding=[dp(12), dp(4)], radius=dp(10))
            row.add_widget(mk_text(money(amt), dp(15), col, halign="left", width=dp(100)))
            c = BoxLayout(orientation="vertical")
            c.add_widget(mk_text(f"{kind}: {qty}", dp(14), T.TEXT, hint_y=0.55))
            c.add_widget(mk_text(d, dp(11), T.MUTED, hint_y=0.45))
            row.add_widget(c)
            self.add(row)


# ============================================================
# 12-ج) الآلة الحاسبة
# ============================================================
class CalcPad(BoxLayout):
    """لوحة الآلة الحاسبة (تُعرض داخل نافذة صغيرة)."""
    KEYS = [["مسح", "(", ")", "/"], ["7", "8", "9", "*"], ["4", "5", "6", "-"],
            ["1", "2", "3", "+"], ["0", ".", "حذف", "="]]

    def __init__(self, **kw):
        super().__init__(orientation="vertical", spacing=dp(6), padding=dp(8), **kw)
        self.expr = ""
        card = CardBox(orientation="vertical", size_hint_y=None, height=dp(78),
                       padding=[dp(12), dp(6)], bg=T.HEADER)
        self.e_lbl = mk_text("", dp(15), SOFT_WHITE, halign="right", hint_y=0.4)
        self.r_lbl = mk_text("0", dp(26), WHITE, halign="right", hint_y=0.6)
        card.add_widget(self.e_lbl)
        card.add_widget(self.r_lbl)
        self.add_widget(card)
        grid = GridLayout(cols=4, spacing=dp(6), size_hint_y=None, row_force_default=True,
                          row_default_height=dp(50), height=dp(50) * 5 + dp(6) * 4)
        for row in self.KEYS:
            for k in row:
                if k == "=":
                    col = T.GREEN
                elif k in ("/", "*", "-", "+"):
                    col = T.ORANGE
                elif k in ("مسح", "حذف"):
                    col = T.RED
                elif k in ("(", ")"):
                    col = T.GRAY_BTN
                else:
                    col = T.BLUE
                b = mk_btn(k, height=dp(50), font_size=dp(19), background_color=col)
                b.bind(on_release=lambda w, k=k: self.press(k))
                grid.add_widget(b)
        self.add_widget(grid)

    @staticmethod
    def calc(expr):
        s = (expr or "").strip()
        if not s or not re.fullmatch(r"[0-9+\-*/(). ]+", s) or "**" in s or "//" in s:
            return None
        try:
            v = eval(s, {"__builtins__": {}}, {})
            return float(v) if isinstance(v, (int, float)) else None
        except Exception:
            return None

    @staticmethod
    def show(v):
        if abs(v - round(v)) < 1e-9 and abs(v) < 1e15:
            return f"{round(v):,}"
        return f"{v:,.6f}".rstrip("0").rstrip(".")

    def press(self, k):
        err = False
        if k == "مسح":
            self.expr = ""
        elif k == "حذف":
            self.expr = self.expr[:-1]
        elif k == "=":
            v = self.calc(self.expr)
            if v is None:
                err = bool(self.expr)
            else:
                self.expr = (str(round(v)) if abs(v - round(v)) < 1e-9 and abs(v) < 1e15
                             else f"{v:.6f}".rstrip("0").rstrip("."))
        else:
            self.expr += k
        self.e_lbl.text = self.expr
        v = self.calc(self.expr)
        self.r_lbl.text = "خطأ" if err else (self.show(v) if v is not None else "0")


def calc_popup(*a):
    """نافذة حاسبة صغيرة في وسط الشاشة، تختفي بالضغط خارجها."""
    pop = make_arabic_popup("الآلة الحاسبة", CalcPad(), size_hint=(None, None), auto_dismiss=True)
    pop.size = (min(dp(300), Window.width * 0.88), dp(440))
    pop.open()


def calc_to_popup(target):
    """حاسبة بزر «حفظ»: ناتج العملية يُكتب في الحقل target (سعر الشراء في الشراء، سعر البيع في البيع)."""
    box = BoxLayout(orientation="vertical", spacing=dp(6))
    pad = CalcPad()
    pad.size_hint_y = None
    pad.height = dp(376)
    box.add_widget(pad)
    sv = mk_btn("حفظ", height=dp(48), font_size=dp(16), background_color=T.GREEN)
    box.add_widget(sv)
    pop = make_arabic_popup("الآلة الحاسبة", box, size_hint=(None, None), auto_dismiss=True)
    pop.size = (min(dp(300), Window.width * 0.88), min(dp(500), Window.height * 0.95))

    def save(*a):
        if not pad.expr.strip():
            return pop.dismiss()
        v = CalcPad.calc(pad.expr)
        if v is None:
            return msg_popup("تنبيه", "العملية الحسابية غير صحيحة", T.ORANGE)
        target.text = fmt(round(v, 2))
        pop.dismiss()
    sv.bind(on_release=save)
    pop.open()


class CalcIconBtn(Widget):
    """زر الحاسبة: يُرسم على شكل آلة حاسبة (شاشة + أزرار) بدل كتابة كلمة."""

    def __init__(self, on_press, **kw):
        kw.setdefault("size_hint", (None, 1))
        kw.setdefault("width", dp(48))
        super().__init__(**kw)
        self._cb = on_press
        self.bind(pos=self._draw, size=self._draw)
        self._draw()

    def _draw(self, *a):
        self.canvas.clear()
        bw, bh = dp(22), min(dp(30), self.height - dp(6))
        x0, y0 = self.center_x - bw / 2, self.center_y - bh / 2
        pad = dp(3)
        ks = (bw - 2 * pad - 2 * dp(2)) / 3.0
        scr_h = bh * 0.3
        top = y0 + bh - pad - scr_h
        kh = (top - dp(2) - (y0 + pad) - 2 * dp(2)) / 3.0
        with self.canvas:
            Color(*T.TEAL)
            RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(10)])
            Color(1, 1, 1, 1)
            RoundedRectangle(pos=(x0, y0), size=(bw, bh), radius=[dp(4)])
            Color(*T.TEAL)
            RoundedRectangle(pos=(x0 + pad, top), size=(bw - 2 * pad, scr_h), radius=[dp(1.5)])
            for r in range(3):
                for c in range(3):
                    if c == 2 and r < 2:
                        Color(*T.ORANGE)
                    else:
                        Color(*T.TEAL)
                    RoundedRectangle(pos=(x0 + pad + c * (ks + dp(2)), y0 + pad + r * (kh + dp(2))),
                                     size=(ks, kh), radius=[dp(1)])

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            touch.grab(self)
            return True
        return False

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            if self.collide_point(*touch.pos):
                cb = self._cb
                Clock.schedule_once(lambda dt: cb(), 0)
            return True
        return False


class _RadioOpt(BoxLayout):
    """خيار: النص + دائرة (مفتوحة = غير مختار، مغلقة = مختار)."""

    def __init__(self, text, cb, fs=dp(20), **kw):
        super().__init__(orientation="horizontal", spacing=dp(4), **kw)
        self._cb = cb
        self.sel = False
        lbl = mk_text(text, fs, T.TEXT, halign="right")
        lbl.disabled = True
        self.add_widget(lbl)
        self.dot = Widget(size_hint=(None, None), size=(dp(22), dp(22)), pos_hint={"center_y": 0.5})
        self.dot.bind(pos=self._draw, size=self._draw)
        self.add_widget(self.dot)
        self._draw()

    def set(self, on):
        self.sel = bool(on)
        self._draw()

    def _draw(self, *a):
        d = self.dot
        d.canvas.clear()
        cx, cy, r = d.center_x, d.center_y, dp(9)
        with d.canvas:
            Color(*T.PRIMARY)
            Line(circle=(cx, cy, r), width=dp(1.5))
            if self.sel:
                Ellipse(pos=(cx - r * 0.58, cy - r * 0.58), size=(r * 1.16, r * 1.16))

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            touch.grab(self)
            return True
        return False

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            if self.collide_point(*touch.pos):
                self._cb()
            return True
        return False


class PayRadio(BoxLayout):
    """نقد / آجل كدائرتين صغيرتين."""

    def __init__(self, on_change, value="cash", returns=False, **kw):
        """returns=True يضيف خيار «مرتجع» (فاتورة البيع)."""
        kw.setdefault("size_hint", (None, 1))
        kw.setdefault("width", dp(208) if returns else dp(175))
        super().__init__(orientation="horizontal", spacing=dp(6), **kw)
        fs = dp(15) if returns else dp(20)
        self.o_ret = _RadioOpt("مرتجع", lambda: on_change("return"), fs=fs) if returns else None
        self.o_credit = _RadioOpt("آجل", lambda: on_change("credit"), fs=fs)
        self.o_cash = _RadioOpt("نقد", lambda: on_change("cash"), fs=fs)
        if self.o_ret is not None:
            self.add_widget(self.o_ret)
        self.add_widget(self.o_credit)
        self.add_widget(self.o_cash)
        self.set(value)

    def set(self, p):
        self.o_cash.set(p == "cash")
        self.o_credit.set(p == "credit")
        if self.o_ret is not None:
            self.o_ret.set(p == "return")


def small_in(label, hint="", flt=None, text=""):
    box = BoxLayout(orientation="vertical", spacing=dp(2), size_hint_y=None, height=dp(58))
    box.add_widget(mk_text(label, dp(11), T.MUTED, height=dp(16)))
    inp = mk_input(hint_text=hint, input_filter=flt, size_hint_y=None, height=dp(38), font_size=dp(14))
    if text != "":
        inp.text = str(text)
    box.add_widget(inp)
    return box, inp


def row_of(*boxes, h=dp(58)):
    row = BoxLayout(size_hint_y=None, height=h, spacing=dp(6))
    for b in boxes:
        row.add_widget(b)
    return row


def trade_header(page, party_btn, radio, prod_btn, wh_btn, box=None, calc=True, lead=None):
    """رأس ثابت صغير لفواتير البيع والشراء: (العميل + نقد/آجل) ثم (حاسبة + الصنف + المخزن).
    box: لو أُعطي توضع الصفوف داخله (مربع واحد) بدل تثبيتها في الصفحة مباشرة."""
    put = box.add_widget if box is not None else page.add_pinned
    if party_btn is not None or radio is not None:
        r1 = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
        if radio is not None:
            r1.add_widget(radio)
        if party_btn is not None:
            r1.add_widget(party_btn)
        put(r1)
    r2 = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
    if lead is not None:
        r2.add_widget(lead)
    elif calc:
        r2.add_widget(CalcIconBtn(calc_popup))
    r2.add_widget(prod_btn)
    r2.add_widget(wh_btn)
    put(r2)


# ============================================================
# 13-أ) الرسوم البيانية
# ============================================================
def short_num(v):
    a, sg = abs(v), ("-" if v < 0 else "")
    if a >= 1e6:
        return f"{sg}{a / 1e6:.1f}M"
    if a >= 1e3:
        return f"{sg}{a / 1e3:.1f}K"
    return sg + f"{a:,.0f}"


_CTEX = {}


def _ctex(text, size, color):
    """نسيج نص عربي مشكَّل للرسم المباشر على الكانفاس (بدون ودجات)."""
    key = (text, int(size), tuple(color), _font_name())
    t = _CTEX.get(key)
    if t is None:
        try:
            cl = CoreLabel(text=ar(text), font_size=int(size), font_name=_font_name(),
                           color=tuple(color), padding=(0, 0, 0, 0))
            cl.refresh()
            t = cl.texture
        except Exception as e:
            print(f"[CTEX] {e}")
            t = None
        if len(_CTEX) > 600:
            _CTEX.clear()
        _CTEX[key] = t
    return t


class BarChart(Widget):
    """أعمدة ملوّنة: data = [(الاسم، القيمة، اللون)] — الأول يظهر على اليمين.
    كل شيء (الأعمدة والنصوص) يُرسم في كانفاس واحد بإحداثيات مطلقة فلا تتحرك التسميات."""

    def __init__(self, data, vsize=dp(11), lsize=dp(11), **kw):
        super().__init__(**kw)
        self.data, self.vsize, self.lsize = list(data), vsize, lsize
        self.bind(size=self._draw, pos=self._draw)
        Clock.schedule_once(lambda dt: self._draw(), 0)
        Clock.schedule_once(lambda dt: self._draw(), 0.3)

    def _put(self, text, cx, y, maxw, size, color):
        t = _ctex(text, size, color)
        k = 0
        while t is not None and t.width > maxw and size - k > dp(7):
            k += 1
            t = _ctex(text, size - k, color)
        if t is None:
            return
        Color(1, 1, 1, 1)
        Rectangle(texture=t, pos=(cx - t.width / 2.0, y), size=t.size)

    def _draw(self, *a):
        self.canvas.clear()
        W, H = float(self.width), float(self.height)
        if W < dp(30) or H < dp(60) or not self.data:
            return
        n = len(self.data)
        pb, pt = dp(26), dp(20)
        ch = H - pb - pt
        mx = max([abs(v) for _l, v, _c in self.data] + [1])
        slot = W / n
        bw = min(dp(46), slot * 0.62)
        x0, y0 = self.x, self.y
        with self.canvas:
            Color(*T.BORDER)
            Line(points=[x0, y0 + pb, x0 + W, y0 + pb], width=dp(1))
            for i, (lab, val, col) in enumerate(self.data):
                k = n - 1 - i
                h = max(dp(2), ch * abs(val) / mx) if val else dp(2)
                cx = x0 + slot * k + slot / 2.0
                Color(*col)
                RoundedRectangle(pos=(cx - bw / 2.0, y0 + pb), size=(bw, h),
                                 radius=[dp(6), dp(6), 0, 0])
                if val != 0 or n <= 7:
                    self._put(short_num(val), cx, y0 + pb + h + dp(2), slot, self.vsize, T.TEXT)
                self._put(lab, cx, y0 + dp(5), slot - dp(2), self.lsize, T.MUTED)


class DonutChart(Widget):
    """دائرة بنسب: data = [(الاسم، القيمة، اللون)]."""

    def __init__(self, data, **kw):
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("size", (dp(140), dp(140)))
        super().__init__(**kw)
        self.data = [d for d in data if d[1] > 0]
        self.bind(pos=self._draw, size=self._draw)
        self._draw()

    def _draw(self, *a):
        self.canvas.clear()
        d = float(min(self.width, self.height))
        if d < 4:
            return
        tot = sum(v for _l, v, _c in self.data)
        with self.canvas:
            PushMatrix()
            Translate(self.x + (self.width - d) / 2, self.y + (self.height - d) / 2)
            if tot <= 0:
                Color(*T.BORDER)
                Ellipse(pos=(0, 0), size=(d, d))
            else:
                ang = 0.0
                for _l, v, col in self.data:
                    sw = 360.0 * v / tot
                    Color(*col)
                    Ellipse(pos=(0, 0), size=(d, d), angle_start=ang, angle_end=ang + sw)
                    ang += sw
            Color(*T.CARD)
            hd = d * 0.56
            Ellipse(pos=((d - hd) / 2, (d - hd) / 2), size=(hd, hd))
            PopMatrix()


def chart_card(title, widget, height):
    card = CardBox(orientation="vertical", size_hint_y=None, height=height,
                   padding=[dp(10), dp(8), dp(10), dp(8)], spacing=dp(4))
    card.add_widget(mk_text(title, dp(15), T.PRIMARY, height=dp(28)))
    card.add_widget(widget)
    return card


def _dot(color):
    w = Widget(size_hint=(None, None), size=(dp(14), dp(14)))
    with w.canvas:
        Color(*color)
        r = RoundedRectangle(pos=w.pos, size=w.size, radius=[dp(4)])

    def _u(*a):
        r.pos, r.size = w.pos, w.size
    w.bind(pos=_u, size=_u)
    return w


def donut_card(title, data):
    tot = sum(v for _l, v, _c in data if v > 0)
    n = len(data)
    body_h = max(dp(150), n * dp(30))
    card = CardBox(orientation="vertical", size_hint_y=None, height=dp(28) + body_h + dp(24),
                   padding=[dp(10), dp(8), dp(10), dp(8)], spacing=dp(4))
    card.add_widget(mk_text(title, dp(15), T.PRIMARY, height=dp(28)))
    if tot <= 0:
        card.add_widget(mk_text("لا توجد بيانات", dp(14), T.MUTED, halign="center"))
        card.height = dp(28) + dp(70) + dp(24)
        return card
    row = BoxLayout(orientation="horizontal", spacing=dp(8))
    row.add_widget(DonutChart(data, pos_hint={"center_y": 0.5}))
    legend = BoxLayout(orientation="vertical", spacing=dp(2))
    for lab, v, col in data:
        pct = (100.0 * v / tot) if v > 0 else 0
        line = BoxLayout(size_hint_y=None, height=dp(28), spacing=dp(6))
        line.add_widget(mk_text(f"{lab}: {money(v)} ({pct:.0f}%)", dp(13), T.TEXT, pad=0))
        line.add_widget(_dot(col))
        legend.add_widget(line)
    row.add_widget(legend)
    card.add_widget(row)
    return card


def daily_sales(db, d1, d2, days=14):
    """[(التاريخ، مجموع المبيعات)] لآخر days يوماً ضمن الفترة."""
    try:
        end = datetime.strptime(d2, "%Y-%m-%d") if d2 else datetime.now()
        start = datetime.strptime(d1, "%Y-%m-%d") if d1 else end - timedelta(days=days - 1)
    except Exception:
        return []
    if end < start:
        return []
    if (end - start).days + 1 > days:
        start = end - timedelta(days=days - 1)
    tot = {}
    for v in db["sales"]:
        tot[v["date"][:10]] = tot.get(v["date"][:10], 0) + sale_total(v)
    out, d = [], start
    while d <= end:
        k = d.strftime("%Y-%m-%d")
        out.append((k, tot.get(k, 0)))
        d += timedelta(days=1)
    return out if any(v for _k, v in out) else []


# ============================================================
# 13-ب) البحث
# ============================================================
_OP_COLL = {"sale": "sales", "purchase": "purchases", "voucher": "vouchers"}
def date_presets(with_all=True):
    """اختصارات الفترات: [(المفتاح، النص، من، إلى)] — الأول يظهر على اليمين."""
    t = datetime.now()
    d = lambda n: (t - timedelta(days=n)).strftime("%Y-%m-%d")
    first = t.replace(day=1)
    last_prev = first - timedelta(days=1)
    out = []
    if with_all:
        out.append(("all", "كل الأوقات", "", ""))
    out += [("today", "اليوم", d(0), d(0)), ("yest", "أمس", d(1), d(1)),
            ("7", "آخر 7 أيام", d(6), d(0)), ("10", "آخر 10 أيام", d(9), d(0)),
            ("30", "آخر 30 يوم", d(29), d(0)),
            ("month", "هذا الشهر", first.strftime("%Y-%m-%d"), d(0)),
            ("pmonth", "الشهر السابق", last_prev.replace(day=1).strftime("%Y-%m-%d"),
             last_prev.strftime("%Y-%m-%d"))]
    return out


class ChipStrip(CardBox):
    """بطاقة فيها أزرار تتحرك (تُسحب) يميناً ويساراً؛ كلها بلون واحد والمحدد أقوى."""
    def __init__(self, options, on_pick, color=None, **kw):
        super().__init__(orientation="horizontal", size_hint_y=None, height=dp(58),
                         padding=[dp(6), dp(7)], radius=dp(16), shadow=False, **kw)
        self._col = tuple(color or T.BLUE)
        self._btns = {}
        self._on_pick = on_pick
        sv = ScrollView(size_hint=(1, 1), do_scroll_x=True, do_scroll_y=False, bar_width=0)
        row = BoxLayout(orientation="horizontal", size_hint=(None, 1), spacing=dp(6))
        row.bind(minimum_width=row.setter("width"))
        for key, text in reversed(list(options)):        # العربية: الأول على اليمين
            b = mk_btn(text, font_size=dp(13), background_color=self._faded(), size_hint_x=None,
                       width=max(dp(70), dp(26) + len(text) * dp(8.2)))
            b.bind(on_release=lambda w, k=key: self._on_pick(k))
            self._btns[key] = b
            row.add_widget(b)
        sv.add_widget(row)
        self.add_widget(sv)
        Clock.schedule_once(lambda dt: setattr(sv, "scroll_x", 1), 0.05)

    def _faded(self):
        return (self._col[0], self._col[1], self._col[2], 0.5)

    def paint(self, active):
        for k, b in self._btns.items():
            b.set_bg(self._col if k == active else self._faded())


SEARCH_SCOPES = [("all", "الكل"), ("sales", "فواتير البيع"), ("purchases", "فواتير الشراء"),
                 ("vouchers", "السندات (قبض/صرف)"), ("notes", "الملاحظات")]
_SCOPE_TYPE = {"sales": "sale", "purchases": "purchase", "vouchers": "voucher"}


def search_ops(db, scope, q, d1, d2):
    q = (q or "").strip().lower().translate(_AR_DIGITS)
    want = _SCOPE_TYPE.get(scope)
    out = []
    for o in all_ops(db):
        if want and o["type"] != want:
            continue
        d = o["date"][:10]
        if (d1 and d < d1) or (d2 and d > d2):
            continue
        if q:
            rec = find(db[_OP_COLL[o["type"]]], o["id"]) or {}
            hay = [o["party"], o["label"], money(o["amount"]), fmt(o["amount"]), o["date"],
                   rec.get("note", "")]
            for it in rec.get("items", []):
                hay += [it.get("name", ""), it.get("unit", ""), it.get("note", "")]
            if q not in " ".join(str(x) for x in hay).lower().translate(_AR_DIGITS):
                continue
        out.append(o)
    return out


def search_notes(app, q, d1, d2):
    q = (q or "").strip().lower()
    out = []
    for k, n in visible_notes(app).items():
        d = str(n.get("d", ""))[:10]
        if d and ((d1 and d < d1) or (d2 and d > d2)):
            continue
        if q and q not in str(n.get("t", "")).lower():
            continue
        out.append((int(k), n))
    out.sort(key=lambda kv: kv[0], reverse=True)
    return out


class SearchPage(Page):
    NAV = "search"
    STEP = 30

    def __init__(self, app, **kw):
        super().__init__(app, "بحث", **kw)
        st = app.search_state or {}
        self.scope = st.get("scope", "all")
        self.limit = self.STEP
        self._ev = None
        box = FloatLayout(size_hint_y=None, height=dp(52))
        self.q_in = mk_input(hint_text="اكتب اسماً أو صنفاً أو مبلغاً...", size_hint=(1, None),
                             height=dp(48), pos_hint={"x": 0, "center_y": 0.5})
        self.q_in.text = st.get("q", "")
        box.add_widget(self.q_in)
        sq = mk_btn("بحث", font_size=dp(12), background_color=T.GREEN, size_hint=(None, None),
                    size=(dp(52), dp(38)), pos_hint={"x": 0.015, "center_y": 0.5})
        sq.bind(on_release=lambda *a: self.run(True))
        box.add_widget(sq)
        self.add(box)
        self.label("نوع البحث", 13)
        self.scope_strip = ChipStrip(SEARCH_SCOPES, self.set_scope)
        self.add(self.scope_strip)
        self.label("الأيام", 13)
        self._presets = date_presets()
        self.rng_key = None
        self.day_strip = ChipStrip([(k, t) for k, t, _a, _b in self._presets], self.pick_day)
        self.add(self.day_strip)
        self.label("من فترة إلى فترة", 13)
        r = BoxLayout(size_hint_y=None, height=dp(72), spacing=dp(10))
        b1, self.to_in = lab_in("إلى تاريخ", "2026-12-31", text=st.get("to", ""))
        b2, self.from_in = lab_in("من تاريخ", "2026-01-01", text=st.get("from", ""))
        r.add_widget(b1)
        r.add_widget(b2)
        self.add(r)
        self.from_in.bind(text=lambda *a: self._later())
        self.to_in.bind(text=lambda *a: self._later())
        self.info = self.add(mk_text("", dp(13), T.MUTED, halign="center", height=dp(26)))
        self.out = GridLayout(cols=1, spacing=dp(8), size_hint_y=None)
        self.out.bind(minimum_height=self.out.setter("height"))
        self.add(self.out)
        self.q_in.bind(text=lambda i, v: self._later())
        self._paint_scopes()
        self.run(True)

    def _later(self):
        if self._ev is not None:
            self._ev.cancel()
        self._ev = Clock.schedule_once(lambda dt: self.run(True), 0.35)

    def _paint_scopes(self):
        self.scope_strip.paint(self.scope)
        self.day_strip.paint(self.rng_key)

    def pick_day(self, key):
        for k, _t, a, b in self._presets:
            if k == key:
                self.rng_key = key
                self.from_in.text, self.to_in.text = a, b
                self._paint_scopes()
                self.run(True)
                return

    def set_scope(self, k):
        self.scope = k
        self._paint_scopes()
        self.run(True)

    def set_range(self, a, b):
        self.from_in.text, self.to_in.text = a, b
        self.run(True)

    def more(self):
        self.limit += self.STEP
        self.run(False)

    def run(self, reset=True):
        if reset:
            self.limit = self.STEP
        d1 = self.from_in.text.strip().translate(_AR_DIGITS) or None
        d2 = self.to_in.text.strip().translate(_AR_DIGITS) or None
        for x in (d1, d2):
            if x and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", x):
                self.out.clear_widgets()
                set_text(self.info, "اكتب التاريخ بالشكل 2026-10-28", T.RED)
                return
        q = self.q_in.text.strip()
        self.app.search_state = {"scope": self.scope, "q": q, "from": d1 or "", "to": d2 or ""}
        db = self.app.db
        rows = []
        if self.scope != "notes":
            rows += [("op", o) for o in search_ops(db, self.scope, q, d1, d2)]
        if self.scope in ("all", "notes"):
            rows += [("note", n) for n in search_notes(self.app, q, d1, d2)]
        total = None
        if self.scope in _SCOPE_TYPE:
            total = sum(o["amount"] for k, o in rows)
        msg = f"عدد النتائج: {len(rows)}" + (f"  -  الإجمالي: {money(total)}" if total is not None else "")
        set_text(self.info, msg, T.MUTED)
        self.out.clear_widgets()
        for kind, x in rows[:self.limit]:
            self.out.add_widget(self._op_row(x) if kind == "op" else self._note_row(*x))
        if len(rows) > self.limit:
            more = mk_btn("عرض المزيد", height=dp(46), background_color=T.GRAY_BTN)
            more.bind(on_release=lambda *a: self.more())
            self.out.add_widget(more)
        elif not rows:
            self.out.add_widget(mk_text("لا توجد نتائج", dp(15), T.MUTED, halign="center",
                                        height=dp(60)))
        schedule_refresh(0.1)

    def _op_row(self, o):
        row = CardBox(orientation="horizontal", size_hint_y=None, height=dp(64),
                      padding=[dp(12), dp(6)], on_tap=lambda o=o: self.app.open_op(o))
        row.add_widget(mk_text(money(o["amount"]), dp(16), o["color"], halign="left", width=dp(104)))
        c = BoxLayout(orientation="vertical")
        c.add_widget(mk_text(o["label"] + (" - " + o["party"] if o["party"] else ""), dp(15),
                             T.TEXT, hint_y=0.55))
        c.add_widget(mk_text(o["date"], dp(12), T.MUTED, hint_y=0.45))
        row.add_widget(c)
        return row

    def _note_row(self, mid, n):
        row = CardBox(orientation="vertical", size_hint_y=None, height=dp(64),
                      padding=[dp(12), dp(6)],
                      on_tap=lambda: note_popup(self.app, mid, n))
        row.add_widget(mk_text("إشعار: " + note_title(n.get("t", "")), dp(15), T.TEXT, hint_y=0.55))
        row.add_widget(mk_text(n.get("d", ""), dp(12), T.MUTED, hint_y=0.45))
        return row


def report_doc(app, r, d1, d2, prods):
    """صورة PNG للتقرير (تُحوَّل لـ PDF عند الحاجة)."""
    period = "التقرير الشامل" if not d1 and not d2 else f"الفترة: {d1 or '...'} → {d2 or '...'}"
    rows = [[p["name"], fmt(p["bought"]), fmt(p["sold"]), fmt(p["rem"]), money(p["val_sale"])]
            for p in prods[:60]]
    totals = [("إجمالي المبيعات", money(r["sales_total"]), True),
              ("الربح", money_signed(r["profit"]), False),
              ("إجمالي المشتريات", money(r["purch_total"]), False),
              ("البضاعة بسعر الشراء", money(r["stock_value"]), False),
              ("البضاعة بسعر البيع", money(r["stock_sale"]), False),
              ("الربح المتوقع من البضاعة", money_signed(r["stock_sale"] - r["stock_value"]), False),
              ("المبلغ في الصندوق", money_signed(r["cash"]), False),
              ("ديون العملاء", money(r["cust_debts"]), False),
              ("حق الموردين", money(r["sup_owed"]), False)]
    png, size = render_doc("تقرير", T.PRIMARY, [period, f"التاريخ: {now_str()[:10]}"],
                           ["الصنف", "مشترى", "مباع", "متبقي", "قيمة البيع"],
                           [.34, .15, .15, .15, .21], rows, totals, shop_lines(app))
    return png, size


def save_to_device(path, mime):
    """يحفظ الملف في مجلد التنزيلات (Download/DebtBook). يرجع (نجاح، رسالة)."""
    if platform != "android":
        return True, path
    try:
        _publish_media(path, mime)
        return True, "تم الحفظ في مجلد Download/DebtBook"
    except Exception as e:
        _write_crash("SAVE: " + traceback.format_exc())
        return False, str(e)[:120]


class ReportsPage(Page):
    NAV = "reports"
    def __init__(self, app, **kw):
        super().__init__(app, "التقارير", **kw)
        self.label("اختر الفترة", 14, T.PRIMARY)
        pres = [("all", "شامل", None, None)] + [(k, t, a, b) for k, t, a, b in date_presets(False)]
        self._pres = pres
        self.rep_strip = ChipStrip([(k, t) for k, t, _a, _b in pres], self.pick_preset)
        self.add(self.rep_strip)
        self.rep_strip.paint("all")
        r = BoxLayout(size_hint_y=None, height=dp(72), spacing=dp(10))
        b1, self.to_in = lab_in("إلى تاريخ", "2026-12-31")
        b2, self.from_in = lab_in("من تاريخ", "2026-01-01")
        r.add_widget(b1)
        r.add_widget(b2)
        self.add(r)
        go = mk_btn("عرض التقرير للفترة", height=dp(48), background_color=T.GREEN)
        go.bind(on_release=lambda *a: self.show(self.from_in.text.strip() or None,
                                                 self.to_in.text.strip() or None))
        self.add(go)
        ex = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        b_save = mk_btn("تحميل PDF للجهاز", font_size=dp(14), background_color=T.TEAL)
        b_send = mk_btn("إرسال PDF", font_size=dp(14), background_color=T.INV_RED)
        b_save.bind(on_release=lambda *a: self.export(False))
        b_send.bind(on_release=lambda *a: self.export(True))
        ex.add_widget(b_save)
        ex.add_widget(b_send)
        self.add(ex)
        self.cur = (None, None)
        self.out = GridLayout(cols=1, spacing=dp(6), size_hint_y=None)
        self.out.bind(minimum_height=self.out.setter("height"))
        self.add(self.out)
        self._gen = 0
        self.out.add_widget(mk_text("جاري تحضير التقرير...", dp(15), T.MUTED, halign="center",
                                    height=dp(60)))
        # نؤجل الحساب الثقيل حتى تنتهي حركة فتح الصفحة فلا «تعلق» الشاشة
        Clock.schedule_once(lambda dt: self.show(None, None), 0.25)

    def pick_preset(self, key):
        for k, _t, a, b in self._pres:
            if k == key:
                self.rep_strip.paint(k)
                self.from_in.text, self.to_in.text = a or "", b or ""
                self.show(a, b)
                return

    def add_charts(self, o, r, d1, d2):
        o.add_widget(chart_card("المبيعات والمشتريات والربح", BarChart([
            ("المبيعات", r["sales_total"], T.BLUE),
            ("المشتريات", r["purch_total"], T.ORANGE),
            ("الربح", r["profit"], T.GREEN if r["profit"] >= 0 else T.RED),
            ("قبض", r["cust_receipts"], T.PURPLE),
            ("صرف", r["sup_payments"], T.TEAL)]), dp(250)))
        o.add_widget(donut_card("المبيعات: نقد / آجل", [
            ("نقد", r["sales_cash"], T.GREEN), ("آجل", r["sales_credit"], T.RED)]))
        o.add_widget(donut_card("المشتريات: نقد / آجل", [
            ("نقد", r["purch_cash"], T.TEAL), ("آجل", r["purch_credit"], T.ORANGE)]))
        if not d1 and not d2:
            o.add_widget(donut_card("توزيع الأموال والالتزامات", [
                ("بضاعة", r["stock_value"], T.BLUE),
                ("الصندوق", max(r["cash"], 0), T.GREEN),
                ("ديون العملاء", r["cust_debts"], T.PURPLE),
                ("حق الموردين", r["sup_owed"], T.RED)]))
        days = daily_sales(self.app.db, d1, d2)
        if days:
            o.add_widget(chart_card("المبيعات اليومية", BarChart(
                [(d[8:10], v, T.BLUE) for d, v in days], vsize=dp(9), lsize=dp(10)), dp(230)))

    def show(self, d1, d2):
        for x in (d1, d2):
            if x and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", x):
                return msg_popup("تنبيه", "اكتب التاريخ بالشكل 2026-10-28", T.ORANGE)
        self.cur = (d1, d2)
        self._gen += 1
        gen = self._gen
        sm = stock_map(self.app.db)
        r = summary(self.app.db, d1, d2, sm)
        o = self.out
        o.clear_widgets()
        title = "التقرير الشامل" if not d1 and not d2 else f"الفترة: {d1 or '...'}  →  {d2 or '...'}"
        o.add_widget(mk_text(title, dp(16), T.PRIMARY, height=dp(34)))
        try:
            self.add_charts(o, r, d1, d2)
        except Exception:
            _write_crash("CHARTS: " + traceback.format_exc())
        K = lambda l, v, c=None: o.add_widget(kv_row(l, v, c))
        if not d1 and not d2:
            o.add_widget(mk_text("الوضع الكلي", dp(14), T.MUTED, height=dp(24)))
            K("بضاعة الدكان (بسعر الشراء)", money(r["stock_value"]), T.PRIMARY)
            K("بضاعة الدكان (بسعر البيع)", money(r["stock_sale"]), T.BLUE)
            ep = r["stock_sale"] - r["stock_value"]
            K("الربح المتوقع من البضاعة", money_signed(ep), T.GREEN if ep >= 0 else T.RED)
            K("المبلغ في الصندوق", money_signed(r["cash"]), T.GREEN if r["cash"] >= 0 else T.RED)
            K("الديون عند العملاء", money(r["cust_debts"]), T.RED)
            K("حق الموردين علينا", money(r["sup_owed"]), T.ORANGE)
            c = CardBox(orientation="horizontal", size_hint_y=None, height=dp(64), bg=T.SOFT_BLUE,
                        padding=[dp(14), dp(4)], shadow=False)
            c.add_widget(mk_text(money_signed(r["net"]), dp(22), T.GREEN if r["net"] >= 0 else T.RED,
                                 halign="left", hint_x=0.5))
            c.add_widget(mk_text("الصافي (بضاعة + صندوق + ديون - الموردين)", dp(13), T.TEXT, hint_x=0.5))
            o.add_widget(c)
            o.add_widget(mk_text("الحركة الكلية", dp(14), T.MUTED, height=dp(24)))
        K("عدد الفواتير", str(r["sales_n"]))
        K("إجمالي المبيعات", money(r["sales_total"]), T.PRIMARY)
        K("مبيعات نقد", money(r["sales_cash"]), T.GREEN)
        K("مبيعات آجل (ديون)", money(r["sales_credit"]), T.RED)
        K("الربح", money_signed(r["profit"]), T.GREEN if r["profit"] >= 0 else T.RED)
        K("إجمالي المشتريات", money(r["purch_total"]), T.PRIMARY)
        K("مشتريات آجل من الموردين", money(r["purch_credit"]), T.ORANGE)
        K("سُدِّد للموردين", money(r["sup_payments"]), T.TEAL)
        K("سندات قبض من العملاء", money(r["cust_receipts"]), T.GREEN)
        K("إيداع يدوي بالصندوق", money(r["cash_in"]), T.GREEN)
        K("سحب من الصندوق", money(r["cash_out"]), T.RED)
        prods = product_report(self.app.db, d1, d2, sm)
        if prods:
            o.add_widget(mk_text("حركة الأصناف (بالحبة) والمتبقي في المخزن", dp(14), T.MUTED,
                                 height=dp(28)))
            it = iter(prods)

            def add_chunk(dt):
                """يضيف بطاقات الأصناف على دفعات صغيرة بين الإطارات فتبقى الشاشة سلسة."""
                if gen != self._gen:
                    return False
                for _ in range(5):
                    p = next(it, None)
                    if p is None:
                        schedule_refresh(0.1)
                        return False
                    c = CardBox(orientation="vertical", size_hint_y=None, height=dp(92),
                                padding=[dp(12), dp(6)], radius=dp(12))
                    c.add_widget(mk_text(p["name"], dp(15), T.TEXT, hint_y=0.34))
                    c.add_widget(mk_text(f"مشترى {fmt(p['bought'])}  -  مباع {fmt(p['sold'])}  -  "
                                         f"متبقي {fmt(p['rem'])}", dp(12), T.PRIMARY, hint_y=0.33))
                    c.add_widget(mk_text(f"قيمة المتبقي: شراء {money(p['val_cost'])}  -  "
                                         f"بيع {money(p['val_sale'])}", dp(12), T.MUTED, hint_y=0.33))
                    o.add_widget(c)
                return True
            Clock.schedule_interval(add_chunk, 0)
        schedule_refresh(0.1)

    def export(self, send):
        d1, d2 = self.cur
        try:
            r = summary(self.app.db, d1, d2)
            prods = product_report(self.app.db, d1, d2)
            png, _size = report_doc(self.app, r, d1, d2, prods)
            pdf = png_to_pdf(png)
        except Exception as e:
            _write_crash("REPORT PDF: " + traceback.format_exc())
            return msg_popup("تعذر إنشاء التقرير", str(e)[:160], T.RED)
        if send:
            err = share_file(pdf, "application/pdf")
            if err:
                msg_popup("خطأ في الإرسال", err, T.RED)
        else:
            ok, m = save_to_device(pdf, "application/pdf")
            msg_popup("تم" if ok else "تعذر الحفظ", m, T.GREEN if ok else T.RED)


# ============================================================
# 14) الإعدادات
# ============================================================
class SettingsPage(Page):
    NAV = "settings"
    def __init__(self, app, **kw):
        super().__init__(app, "الإعدادات", theme_toggle=False, **kw)
        u = app.data.get("user") or {}
        # ---- الحساب ----
        section_title(self, "الحساب")
        card = CardBox(orientation="vertical", size_hint_y=None, height=dp(236),
                       padding=[dp(14), dp(10)], spacing=dp(2))
        rem = app.remaining()
        for lab, val in (("الاسم", u.get("name", "")), ("البريد", u.get("email", "")),
                         ("الهاتف", u.get("phone", "")),
                         ("الباقة", app.plan().upper() + ("  | الفواتير المتبقية: " +
                                                          ("غير محدودة" if rem is None else str(rem))))):
            card.add_widget(mk_text(lab, dp(11), T.MUTED, height=dp(18)))
            card.add_widget(mk_text(val or "-", dp(14), T.TEXT, height=dp(26)))
        self.add(card)
        self.accounts_card()

        # ---- المزامنة والتحديثات ----
        section_title(self, "المزامنة والتحديثات")
        paid = app.plan() != "free"
        row, _sw = switch_row(
            "المزامنة التلقائية",
            "ترفع بياناتك بعد كل عملية عند توفر الانترنت" if paid else "متاحة لباقة Pro و Max فقط",
            app.data.get("auto_sync", True) is not False and paid, app.set_auto_sync,
            enabled=paid, locked=lambda: app.subscribe_popup())
        self.add(row)
        row, _sw = switch_row("التحديث التلقائي", "تنزيل وتفعيل كل التحديثات تلقائياً (لكل الباقات)",
                              app.auto_update_on(), app.set_auto_update_all)
        self.add(row)
        self.add(menu_row("التحديثات", "عرض التحديثات وإيقاف التلقائي لتحديث معيّن",
                          lambda: app.push(lambda: UpdatesPage(app))))
        self.add(menu_row("مزامنة الآن", "رفع وتحديث البيانات يدوياً", lambda: app.do_sync()))

        # ---- المظهر ----
        section_title(self, "المظهر")
        self.theme_row()

        # ---- بياناتي وكلمات المرور ----
        section_title(self, "بياناتي وكلمات المرور")
        self.add(menu_row("تغيير اسم الدكان", (app.db.get("meta") or {}).get("shop", "") or "يظهر في الفواتير",
                          lambda: shop_name_popup(app)))
        self.add(menu_row("الإدخال الصوتي", "مفعّل (اضغط للإيقاف)" if app.voice_on() else "متوقف (اضغط للتفعيل)",
                          lambda: app.toggle_voice()))
        self.add(menu_row("تغيير اسم المالك", "", lambda: app.push(lambda: ChangeOwnerPage(app))))
        self.add(menu_row("رقم هاتفي", "يظهر في الفواتير", lambda: app.push(lambda: ChangePhonePage(app))))
        self.add(menu_row("كلمة مرور المشاهدة", "", lambda: app.push(lambda: ChangePasswordPage(app))))
        _hs = bool((app.data.get("user") or {}).get("has_second", True))
        self.add(menu_row("كلمة المرور الثانية", "مفعّلة (اضغط للتغيير)" if _hs else "غير مفعّلة، اضغط لإضافتها وتفعيل الميزة",
                          lambda: app.push(lambda: ChangeCodePage(app))))

        # ---- الأمان والطباعة ----
        section_title(self, "الأمان والطباعة")
        _lk = lock_on(app)
        self.add(menu_row("قفل التطبيق بالرمز", "مفعّل (اضغط للتغيير أو الإيقاف)" if _lk
                          else "غير مفعّل — فعّله لحماية بياناتك الحساسة",
                          lambda: app.push(lambda: LockSetupPage(app))))
        if _lk:
            row, _sw = switch_row("الفتح بالبصمة", "تفتح التطبيق بالبصمة بدل كتابة الرمز",
                                  bool(lock_cfg(app).get("bio")), lambda on: set_bio(app, on))
            self.add(row)
        _pc = printer_cfg(app)
        self.add(menu_row("الطابعة الحرارية", (_pc.get("name", "") + " — ورق %s مم" % _pc.get("paper", 58))
                          if _pc.get("mac") else "غير محددة — اضغط لاختيار الطابعة",
                          lambda: app.push(lambda: PrinterPage(app))))

        # ---- الأجهزة ----
        section_title(self, "الأجهزة المتصلة")
        self.devices_card()

        # ---- أخرى ----
        section_title(self, "أخرى")
        if app.plan() == "free":
            self.add(menu_row("ترقية الباقة", "تواصل واتساب", app.subscribe_popup, T.GREEN))
        self.add(menu_row("فحص الاتصال بالانترنت", "", diagnose_popup))
        self.add(menu_row("فحص الصوت والمسح", "", voice_scan_popup))
        self.add(menu_row("تسجيل الخروج", "اضغط مزامنة قبل الخروج لرفع آخر التغييرات",
                          lambda: confirm_popup("خروج", "هل تريد تسجيل الخروج؟ اضغط مزامنة قبل الخروج "
                                                        "لرفع آخر التغييرات.", app.do_logout)))
        self.add(menu_row("حذف الحساب", "", self.delete_account, T.RED))

    # ---- المظهر (ليلي / نهاري) ----
    def theme_row(self):
        row = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(8))
        dark = is_dark()
        b_dark = mk_btn("ليلي", font_size=dp(16), background_color=T.BLUE if dark else T.GRAY_BTN)
        b_light = mk_btn("نهاري", font_size=dp(16), background_color=T.ORANGE if not dark else T.GRAY_BTN)

        def want(dark_wanted):
            if is_dark() != dark_wanted:
                Clock.schedule_once(lambda dt: self.app.toggle_theme(), 0)
        b_dark.bind(on_release=lambda *a: want(True))
        b_light.bind(on_release=lambda *a: want(False))
        row.add_widget(b_dark)
        row.add_widget(b_light)
        self.add(row)

    # ---- الحسابات المحفوظة على الجهاز (تنقّل بدون انترنت) ----
    def accounts_card(self):
        app = self.app
        accts = app.account_list()
        n = len(accts)
        card = CardBox(orientation="vertical", size_hint_y=None,
                       height=dp(30) + n * (dp(54) + dp(4)) + dp(46) + dp(30),
                       padding=[dp(12), dp(8)], spacing=dp(4))
        card.add_widget(mk_text("الحسابات على هذا الجهاز", dp(16), T.PRIMARY, height=dp(30)))
        for email, name, cur in accts:
            row = BoxLayout(size_hint_y=None, height=dp(54), spacing=dp(6))
            if not cur:
                rm = mk_btn("إزالة", size_hint=(None, None), size=(dp(60), dp(34)), font_size=dp(12),
                            background_color=T.RED, pos_hint={"center_y": 0.5})
                rm.bind(on_release=lambda w, e=email: self.remove_account(e))
                row.add_widget(rm)
            info = CardBox(orientation="horizontal", size_hint_y=None, height=dp(54), shadow=False,
                           padding=[dp(8), dp(4)], spacing=dp(8), radius=dp(10),
                           bg=(T.SOFT_BLUE if cur else None),
                           on_tap=(None if cur else (lambda e=email: app.switch_account(e))))
            col = BoxLayout(orientation="vertical")
            col.add_widget(mk_text((name or email) + ("  -  الحالي" if cur else ""), dp(14),
                                   T.PRIMARY if cur else T.TEXT, hint_y=0.55))
            col.add_widget(mk_text(email, dp(11), T.MUTED, hint_y=0.45))
            info.add_widget(col)
            av = Avatar(name or email, diameter=dp(38))
            av.pos_hint = {"center_y": 0.5}
            info.add_widget(av)
            row.add_widget(info)
            card.add_widget(row)
        add = mk_btn("+ إضافة حساب", height=dp(46), background_color=T.GREEN)
        add.bind(on_release=lambda *a: app.add_account_flow())
        card.add_widget(add)
        card.add_widget(mk_text("بعد إضافة الحساب تتنقل بين الحسابات بدون انترنت", dp(11), T.MUTED,
                                halign="center", height=dp(22)))
        self.add(card)

    def remove_account(self, email):
        confirm_popup("إزالة الحساب",
                      "سيُحذف هذا الحساب وبياناته غير المُزامَنة من هذا الجهاز فقط. هل تريد المتابعة؟",
                      lambda: (self.app.remove_saved_account(email), self.app.reload()),
                      yes_text="إزالة")

    # ---- الأجهزة المتصلة بالحساب ----
    def devices_card(self):
        self.dev_card = CardBox(orientation="vertical", size_hint_y=None, height=dp(100),
                                padding=[dp(12), dp(8)], spacing=dp(4))
        self.add(self.dev_card)
        self._draw_devices(None, "جاري التحميل...")
        self._load_devices()

    def _load_devices(self):
        app = self.app
        me = {"id": app.data.get("device_id"), "name": device_name(), "last": now_str()}

        def w():
            devs, note = None, ""
            try:
                email = (app.data.get("user") or {}).get("email", "")
                rec, mid, _e = DB.get_user(email)
                if rec:
                    if touch_device(rec, me["id"], me["name"]):
                        DB.save_user(rec, mid)
                    devs = rec.get("devices") or []
                else:
                    note = "الحساب غير موجود على الانترنت"
            except Exception:
                note = "تعذر الاتصال بالانترنت"
            if not devs:
                devs = [me]
            Clock.schedule_once(lambda dt: self._draw_devices(devs, note), 0)
        threading.Thread(target=w, daemon=True).start()

    def _draw_devices(self, devs, note=""):
        card = getattr(self, "dev_card", None)
        if card is None:
            return
        card.clear_widgets()
        card.add_widget(mk_text("الأجهزة المتصلة بالحساب", dp(16), T.PRIMARY, height=dp(30)))
        did = self.app.data.get("device_id")
        h = dp(30) + dp(24)
        if not devs:
            card.add_widget(mk_text(note or "-", dp(13), T.MUTED, halign="center", height=dp(40)))
            h += dp(40)
        else:
            can_remove = self.app.data.get("role") != "viewer" and not note
            for d in sorted(devs, key=lambda x: str(x.get("last", "")), reverse=True):
                me = d.get("id") == did
                row = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(6))
                if not me and can_remove:
                    b = mk_btn("إزالة", size_hint=(None, None), size=(dp(60), dp(34)), font_size=dp(12),
                               background_color=T.RED, pos_hint={"center_y": 0.5})
                    b.bind(on_release=lambda w, dd=d: self.remove_device(dd))
                    row.add_widget(b)
                col = BoxLayout(orientation="vertical")
                col.add_widget(mk_text((d.get("name") or "جهاز") + ("  -  هذا الجهاز" if me else ""),
                                       dp(14), T.PRIMARY if me else T.TEXT, hint_y=0.55))
                col.add_widget(mk_text("آخر نشاط: " + str(d.get("last", "")), dp(11), T.MUTED, hint_y=0.45))
                row.add_widget(col)
                card.add_widget(row)
                h += dp(52) + dp(4)
            if note:
                card.add_widget(mk_text(note, dp(11), T.MUTED, halign="center", height=dp(22)))
                h += dp(26)
        card.height = h
        schedule_refresh(0.05)

    def remove_device(self, d):
        if not self.app.need_edit():
            return

        def go():
            self._draw_devices(None, "جاري الإزالة...")

            def w():
                try:
                    email = (self.app.data.get("user") or {}).get("email", "")
                    rec, mid, _e = DB.get_user(email)
                    if rec:
                        rec["devices"] = [x for x in (rec.get("devices") or []) if x.get("id") != d.get("id")]
                        rv = [x for x in (rec.get("revoked") or []) if x != d.get("id")]
                        rv.append(d.get("id"))
                        rec["revoked"] = rv[-20:]
                        DB.save_user(rec, mid)
                except Exception as e:
                    print(f"[DEVICE RM] {e}")
                Clock.schedule_once(lambda dt: self._load_devices(), 0)
            threading.Thread(target=w, daemon=True).start()
        confirm_popup("إزالة الجهاز",
                      "سيُسجَّل خروج هذا الجهاز من الحساب عند أول مزامنة له. هل تريد المتابعة؟", go,
                      yes_text="إزالة")

    # ---- حذف الحساب (يطلب كلمة المرور الثانية) ----
    def delete_account(self):
        box = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(10))
        box.add_widget(mk_text("سيُحذف حسابك وكل بياناتك من الجهاز ومن الانترنت نهائياً", dp(14),
                               T.RED, halign="center", height=dp(60)))
        b1, pw = lab_in("اكتب كلمة المرور الثانية للتأكيد", "********", password=True)
        box.add_widget(b1)
        msg = mk_text("", dp(13), T.RED, halign="center", height=dp(28))
        box.add_widget(msg)
        box.add_widget(Widget())
        bb = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        yes = mk_btn("حذف نهائي", background_color=T.RED)
        no = mk_btn("إلغاء", background_color=T.GRAY_BTN)
        bb.add_widget(yes)
        bb.add_widget(no)
        box.add_widget(bb)
        pop = make_arabic_popup("حذف الحساب", box, size_hint=(0.92, 0.6), title_color=T.RED,
                                separator_color=T.RED, auto_dismiss=False)
        busy = {"v": False}

        def do(*a):
            if busy["v"]:
                return
            if not self.app.need_edit():
                return
            code = pw.text.strip()
            if not code:
                return set_text(msg, "أدخل كلمة المرور الثانية")
            busy["v"] = True
            set_text(msg, "جاري الحذف...", T.MUTED)

            def w():
                ok, err = self.app.delete_account_remote(code)

                def done(dt):
                    busy["v"] = False
                    if ok:
                        pop.dismiss()
                        self.app.wipe_local()
                    else:
                        set_text(msg, err or "فشل الحذف", T.RED)
                Clock.schedule_once(done, 0)
            threading.Thread(target=w, daemon=True).start()
        yes.bind(on_release=do)
        no.bind(on_release=lambda *a: None if busy["v"] else pop.dismiss())
        pop.open()


class ChangePasswordPage(Page):
    def __init__(self, app, **kw):
        super().__init__(app, "تغيير كلمة مرور المشاهدة", theme_toggle=False, **kw)
        self.old_in = self.field("كلمة مرور المشاهدة الحالية", "********", password=True)
        self.new_in = self.field("كلمة مرور المشاهدة الجديدة", "********", password=True)
        self.conf_in = self.field("تأكيد كلمة مرور المشاهدة الجديدة", "********", password=True)
        self.msg("")
        b = mk_btn("حفظ", height=dp(52), font_size=dp(18), background_color=T.GREEN)
        b.bind(on_release=self.save)
        self.add_footer(b)

    def save(self, *a):
        if not self.app.need_edit():
            return
        old, new = self.old_in.text.strip(), self.new_in.text.strip()
        if not old:
            return self.msg("أدخل كلمة مرور المشاهدة الحالية")
        if len(new) < 4:
            return self.msg("كلمة مرور المشاهدة الجديدة قصيرة")
        if new != self.conf_in.text.strip():
            return self.msg("كلمتا المرور غير متطابقتين")
        self.msg("جاري الحفظ...", T.MUTED)

        def w():
            ok, err = self.app.change_password_remote(old, new)
            Clock.schedule_once(lambda dt: self.app.back() if ok else self.msg(err), 0)
        threading.Thread(target=w, daemon=True).start()


class ChangeOwnerPage(Page):
    def __init__(self, app, **kw):
        super().__init__(app, "تغيير اسم المالك", theme_toggle=False, **kw)
        cur = (app.data.get("user") or {}).get("name", "")
        self.name_in = self.field("اسم المالك", "الاسم الكامل", text=cur)
        self.msg("")
        b = mk_btn("حفظ", height=dp(52), font_size=dp(18), background_color=T.GREEN)
        b.bind(on_release=self.save)
        self.add_footer(b)

    def save(self, *a):
        if not self.app.need_edit():
            return
        new = self.name_in.text.strip()
        if len(new) < 2:
            return self.msg("أدخل اسم المالك")
        self.msg("جاري الحفظ...", T.MUTED)

        def w():
            ok, err = self.app.change_owner_remote(new)

            def done(dt):
                if ok:
                    u = self.app.data.get("user")
                    if u is not None:
                        u["name"] = new
                    self.app.save()
                    self.app.back()
                else:
                    self.msg(err or "فشل الحفظ")
            Clock.schedule_once(done, 0)
        threading.Thread(target=w, daemon=True).start()


class ChangePhonePage(Page):
    def __init__(self, app, **kw):
        super().__init__(app, "رقم هاتفي", theme_toggle=False, **kw)
        cur = (app.data.get("user") or {}).get("phone", "")
        self.label("يظهر هذا الرقم في الفواتير والسندات وكشوف الحساب", 12)
        self.phone_in = self.field("رقم الهاتف", "مثال: 777123456", text=cur, flt="int")
        self.msg("")
        b = mk_btn("حفظ", height=dp(52), font_size=dp(18), background_color=T.GREEN)
        b.bind(on_release=self.save)
        self.add_footer(b)

    def save(self, *a):
        if not self.app.need_edit():
            return
        new = re.sub(r"\D", "", self.phone_in.text)
        self.msg("جاري الحفظ...", T.MUTED)

        def w():
            ok, err = self.app.change_phone_remote(new)

            def done(dt):
                if ok:
                    u = self.app.data.get("user")
                    if u is not None:
                        u["phone"] = new
                    self.app.save()
                    self.app.back()
                else:
                    self.msg(err or "فشل الحفظ")
            Clock.schedule_once(done, 0)
        threading.Thread(target=w, daemon=True).start()


class ChangeCodePage(Page):
    def __init__(self, app, **kw):
        has_old = bool((app.data.get("user") or {}).get("has_second", True))
        super().__init__(app, "تغيير كلمة المرور الثانية" if has_old else "إضافة كلمة المرور الثانية",
                         theme_toggle=False, **kw)
        self.has_old = has_old
        if has_old:
            self.old_in = self.field("كلمة المرور الثانية الحالية", "********", password=True)
        else:
            for ln in textwrap.wrap("كلمة المرور الثانية تحمي التعديل والبيع: من يدخل بدونها يكون للمشاهدة فقط. "
                                    "بعد إضافتها تُطلب عند الدخول من أي جهاز.", width=36):
                self.label(ln, 13, T.MUTED, dp(24), "center")
        self.new_in = self.field("كلمة المرور الثانية الجديدة", "********", password=True)
        self.conf_in = self.field("تأكيد كلمة المرور الثانية الجديدة", "********", password=True)
        self.msg("")
        b = mk_btn("حفظ" if has_old else "تفعيل", height=dp(52), font_size=dp(18), background_color=T.GREEN)
        b.bind(on_release=self.save)
        self.add_footer(b)

    def save(self, *a):
        if not self.app.need_edit():
            return
        old = self.old_in.text.strip() if self.has_old else ""
        new = self.new_in.text.strip()
        if self.has_old and not old:
            return self.msg("أدخل كلمة المرور الثانية الحالية")
        if not new:
            return self.msg("أدخل كلمة المرور الثانية الجديدة")
        if len(new) < 3:
            return self.msg("كلمة المرور الثانية قصيرة جداً")
        if new != self.conf_in.text.strip():
            return self.msg("كلمتا المرور غير متطابقتين")
        self.msg("جاري الحفظ...", T.MUTED)

        def w():
            ok, err = self.app.change_code_remote(old, new)

            def done(dt):
                if ok:
                    u = self.app.data.get("user") or {}
                    u["has_second"] = True
                    self.app.data["user"] = u
                    self.app.save(auto=False)
                    self.app.back()
                else:
                    self.msg(err)
            Clock.schedule_once(done, 0)
        threading.Thread(target=w, daemon=True).start()


# ============================================================
# الطباعة الحرارية (بلوتوث) + قفل التطبيق بالرمز + البصمة
# ============================================================
SPP_UUID = "00001101-0000-1000-8000-00805F9B34FB"
LOCK_AFTER_AWAY = 30          # ثانية: يُقفل التطبيق إن غاب عن الشاشة أكثر من هذا


def _ask_bt_perms():
    if platform != "android":
        return
    try:
        from android.permissions import request_permissions
        request_permissions(["android.permission.BLUETOOTH_CONNECT", "android.permission.BLUETOOTH_SCAN"])
    except Exception:
        pass


def bt_paired_devices():
    """الأجهزة المقترنة بالجوال: [(الاسم، العنوان)]."""
    if platform != "android":
        return [("طابعة تجريبية", "00:11:22:33:44:55")]
    _ask_bt_perms()
    from jnius import autoclass
    ad = autoclass("android.bluetooth.BluetoothAdapter").getDefaultAdapter()
    if ad is None:
        raise RuntimeError("البلوتوث غير متوفر في هذا الجهاز")
    if not ad.isEnabled():
        raise RuntimeError("شغّل البلوتوث أولاً")
    out = []
    for d in ad.getBondedDevices().toArray():
        out.append((str(d.getName() or "جهاز"), str(d.getAddress())))
    return out


def bt_send(mac, data):
    """يرسل بايتات ESC/POS إلى الطابعة عبر RFCOMM."""
    if platform != "android":
        print("[PRINT] %d bytes -> %s" % (len(data), mac))
        return
    _ask_bt_perms()
    from jnius import autoclass
    ad = autoclass("android.bluetooth.BluetoothAdapter").getDefaultAdapter()
    UUID = autoclass("java.util.UUID")
    dev = ad.getRemoteDevice(mac)
    try:
        ad.cancelDiscovery()
    except Exception:
        pass
    sock = dev.createRfcommSocketToServiceRecord(UUID.fromString(SPP_UUID))
    sock.connect()
    out = sock.getOutputStream()
    try:
        for i in range(0, len(data), 1024):
            chunk = [b - 256 if b > 127 else b for b in data[i:i + 1024]]
            out.write(chunk)
            out.flush()
            time.sleep(0.04)
        time.sleep(0.8)
    finally:
        try:
            out.close()
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass


def receipt_image(args, paper=58):
    """يرسم إيصالاً أبيض وأسود بعرض الورقة (58مم=384 نقطة، 80مم=576)."""
    from PIL import Image as PImage, ImageDraw
    title, info, headers, widths, rows, totals, footer = args
    W = 384 if int(paper) == 58 else 576
    k = W / 384.0
    img = PImage.new("L", (W, 6000), 255)
    d = ImageDraw.Draw(img)
    f_t, f_m, f_s = _pil_font(int(34 * k)), _pil_font(int(26 * k)), _pil_font(int(22 * k))
    L, R = 6, W - 6

    def tw(s, f):
        try:
            return d.textlength(ar(str(s)), font=f)
        except Exception:
            return len(str(s)) * 12

    def fit(s, f, maxw):
        s = str(s)
        if tw(s, f) <= maxw:
            return s
        while s and tw(s + "…", f) > maxw:
            s = s[:-1]
        return s + "…"

    def right(y, s, f, maxw=None):
        s = fit(s, f, maxw or (R - L))
        d.text((R - tw(s, f), y), ar(s), font=f, fill=0)

    def left(y, s, f):
        d.text((L, y), ar(str(s)), font=f, fill=0)

    def center(y, s, f):
        s = fit(s, f, R - L)
        d.text(((W - tw(s, f)) / 2.0, y), ar(s), font=f, fill=0)

    def dash(y):
        x = L
        while x < R:
            d.line([x, y, min(x + 8, R), y], fill=0, width=2)
            x += 14

    y = 6
    foot = list(footer or [])
    if foot:
        center(y, foot[0], f_t)
        y += int(46 * k)
    center(y, title, f_m)
    y += int(40 * k)
    dash(y + 4)
    y += int(14 * k)
    for ln in info:
        right(y, ln, f_s)
        y += int(32 * k)
    dash(y + 4)
    y += int(14 * k)
    lh = int(32 * k)
    if headers and rows:
        sale = len(headers) == 5 and headers[0] == "الصنف"
        for r in rows:
            r = [str(c) for c in r]
            if sale:
                right(y, r[0], f_s)
                y += lh
                left(y, r[4], f_s)
                right(y, "%s %s × %s" % (r[2], r[1], r[3]), f_s, int((R - L) * 0.62))
                y += lh
            else:
                right(y, " - ".join(r[:2]), f_s, int((R - L) * 0.62))
                if len(r) > 2:
                    left(y, r[2], f_s)
                y += lh
                if len(r) > 3:
                    left(y, "  ".join(r[3:]), f_s)
                    y += lh
            dash(y + 2)
            y += int(8 * k)
    for lab, val, hi in totals:
        fnt = f_m if hi else f_s
        right(y, lab, fnt, int((R - L) * 0.55))
        left(y, val, fnt)
        y += int(36 * k) if hi else lh
    dash(y + 4)
    y += int(14 * k)
    for ln in foot[1:]:
        center(y, ln, f_s)
        y += lh
    y += int(16 * k)
    return img.crop((0, 0, W, y))


def escpos_bytes(img):
    """يحوّل الصورة إلى أوامر ESC/POS (طباعة نقطية) مع تغذية وقصّ الورق."""
    g = img.convert("L")
    bw = g.point(lambda p: 255 if p < 160 else 0).convert("1")
    W, H = bw.size
    wb = W // 8
    raw = bw.tobytes()
    out = bytearray(b"\x1b@")
    band = 128
    for y0 in range(0, H, band):
        h = min(band, H - y0)
        out += b"\x1d\x76\x30\x00" + bytes([wb & 255, wb >> 8, h & 255, h >> 8])
        out += raw[y0 * wb:(y0 + h) * wb]
    out += b"\n\n\n\x1d\x56\x42\x00"
    return bytes(out)


def printer_cfg(app):
    return app.data.get("printer") or {}


def print_doc(app, args):
    """يطبع المستند على الطابعة المختارة (في خيط خلفي)."""
    cfg = printer_cfg(app)
    if not cfg.get("mac"):
        return msg_popup("اختر الطابعة", "اذهب إلى الإعدادات ← الطابعة الحرارية واختر الطابعة أولاً.", T.ORANGE)
    app.show_banner("جاري الطباعة...")

    def work():
        try:
            data = escpos_bytes(receipt_image(args, cfg.get("paper", 58)))
            bt_send(cfg["mac"], data)
            res = None
        except Exception as e:
            _write_crash("PRINT: " + traceback.format_exc())
            res = str(e)[:140] or "تعذر الاتصال بالطابعة"

        def done(dt):
            if res:
                msg_popup("تعذرت الطباعة", res + "\nتأكد أن الطابعة تعمل ومقترنة بالجوال.", T.RED)
            else:
                app.show_banner("تمت الطباعة")
        Clock.schedule_once(done, 0)
    threading.Thread(target=work, daemon=True).start()


def save_pdf_oneclick(app, png):
    """حفظ المستند PDF في مجلد التنزيلات بضغطة واحدة."""
    try:
        pdf = png_to_pdf(png)
        ok, m = save_to_device(pdf, "application/pdf")
        app.show_banner("تم حفظ PDF — " + m if ok else "تعذر الحفظ: " + str(m))
    except Exception as e:
        _write_crash("SAVEPDF: " + traceback.format_exc())
        msg_popup("تعذر الحفظ", str(e)[:140], T.RED)


class PrinterPage(Page):
    def __init__(self, app, **kw):
        super().__init__(app, "الطابعة الحرارية", theme_toggle=False, **kw)
        cfg = printer_cfg(app)
        self.label("1) شغّل الطابعة واقرنها (Pair) من إعدادات بلوتوث الجوال", 13, T.MUTED, dp(24))
        self.label("2) اختر حجم الورق ثم اختر الطابعة من القائمة", 13, T.MUTED, dp(24))
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        cur = int(cfg.get("paper", 58))
        for mm in (80, 58):
            b = mk_btn("ورق %d مم" % mm, font_size=dp(15),
                       background_color=T.BLUE if cur == mm else T.GRAY_BTN)
            b.bind(on_release=lambda w, m=mm: self.set_paper(m))
            row.add_widget(b)
        self.add(row)
        section_title(self, "الأجهزة المقترنة")
        try:
            devs = bt_paired_devices()
        except Exception as e:
            devs = []
            self.label(str(e), 13, T.RED, dp(30), "center")
        if not devs and platform == "android":
            self.label("لا توجد أجهزة مقترنة. اقرن الطابعة من إعدادات البلوتوث ثم ارجع.", 13, T.MUTED, dp(30), "center")
        for name, mac in devs:
            sel = cfg.get("mac") == mac
            self.add(menu_row(("✓ " if sel else "") + name, mac, lambda n=name, m=mac: self.pick(n, m),
                              T.GREEN if sel else None))
        t = mk_btn("طباعة تجريبية", height=dp(52), font_size=dp(17), background_color=T.GREEN)
        t.bind(on_release=self.test)
        self.add_footer(t)

    def set_paper(self, mm):
        cfg = dict(printer_cfg(self.app))
        cfg["paper"] = mm
        self.app.data["printer"] = cfg
        self.app.save(auto=False)
        self.app.reload()

    def pick(self, name, mac):
        cfg = dict(printer_cfg(self.app))
        cfg.update(name=name, mac=mac)
        self.app.data["printer"] = cfg
        self.app.save(auto=False)
        self.app.reload()

    def test(self, *a):
        args = ("اختبار الطابعة", ["التاريخ: " + now_str()[:16]], None, None, [],
                [("الحالة", "تعمل بنجاح", True)], shop_lines(self.app))
        print_doc(self.app, args)


# ----------------------------- قفل التطبيق -----------------------------
def _digits(s):
    """يحوّل الأرقام العربية إلى لاتينية ويحذف المسافات."""
    tbl = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
    return str(s).strip().translate(tbl)


def lock_cfg(app):
    return app.data.get("lock") or {}


def lock_on(app):
    c = lock_cfg(app)
    return bool(c.get("on") and c.get("hash"))


def _pin_hash(pin, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt_hex), 60000).hex()


def lock_set_pin(app, pin):
    salt = os.urandom(16).hex()
    cfg = dict(lock_cfg(app))
    cfg.update(on=True, salt=salt, hash=_pin_hash(pin, salt), len=len(pin))
    app.data["lock"] = cfg
    app.save(auto=False)


def lock_check(app, pin):
    c = lock_cfg(app)
    try:
        return bool(c.get("hash")) and _pin_hash(pin, c["salt"]) == c["hash"]
    except Exception:
        return False


def lock_disable(app):
    app.data["lock"] = {"on": False}
    app.save(auto=False)


# ----- البصمة: تعتمد على الكلاس org.daftar.BioHelper (ملف Java داخل الـ APK) -----
_BIO_REF = []


def bio_available():
    if platform != "android":
        return False
    try:
        from jnius import autoclass
        from android import mActivity
        return bool(autoclass("org.daftar.BioHelper").isSupported(mActivity))
    except Exception:
        return False


def bio_auth(title, sub, cb):
    """cb(ok, msg) تُستدعى في خيط الواجهة."""
    try:
        from jnius import autoclass, PythonJavaClass, java_method
        from android import mActivity

        class _L(PythonJavaClass):
            __javainterfaces__ = ["org/daftar/BioHelper$Listener"]
            __javacontext__ = "app"

            @java_method("(ZLjava/lang/String;)V")
            def onResult(self, ok, msg):
                ok, msg = bool(ok), str(msg or "")
                Clock.schedule_once(lambda dt: cb(ok, msg), 0)
        lis = _L()
        _BIO_REF[:] = [lis]
        autoclass("org.daftar.BioHelper").authenticate(mActivity, title, sub, "إلغاء", lis)
    except Exception as e:
        _write_crash("BIO: " + traceback.format_exc())
        Clock.schedule_once(lambda dt: cb(False, str(e)[:100]), 0)


def set_bio(app, on):
    cfg = dict(lock_cfg(app))
    if not on:
        cfg["bio"] = False
        app.data["lock"] = cfg
        app.save(auto=False)
        return
    if not bio_available():
        msg_popup("البصمة غير متاحة",
                  "تأكد أن بصمة مسجّلة في إعدادات الجوال، وأنك ثبّت آخر نسخة من التطبيق (APK).", T.ORANGE)
        return app.reload()

    def done(ok, msg):
        if ok:
            cfg["bio"] = True
            app.data["lock"] = cfg
            app.save(auto=False)
            app.show_banner("تم تفعيل الفتح بالبصمة")
        else:
            app.show_banner("لم يتم تفعيل البصمة")
        app.reload()
    bio_auth("تفعيل البصمة", "المس مستشعر البصمة", done)


class LockSetupPage(Page):
    def __init__(self, app, **kw):
        on = lock_on(app)
        super().__init__(app, "تغيير رمز القفل" if on else "تفعيل قفل التطبيق", theme_toggle=False, **kw)
        self.on = on
        if not on:
            for ln in textwrap.wrap("يحمي القفل بياناتك الحساسة: يُطلب الرمز عند فتح التطبيق وعند العودة إليه "
                                    "بعد غياب. اختر رمزاً من 4 إلى 6 أرقام.", width=36):
                self.label(ln, 13, T.MUTED, dp(24), "center")
        else:
            self.old_in = self.field("الرمز الحالي", "****", flt="int", password=True)
        self.new_in = self.field("الرمز الجديد (4 - 6 أرقام)", "****", flt="int", password=True)
        self.conf_in = self.field("تأكيد الرمز الجديد", "****", flt="int", password=True)
        self.msg("")
        b = mk_btn("تغيير الرمز" if on else "تفعيل القفل", height=dp(52), font_size=dp(18),
                   background_color=T.GREEN)
        b.bind(on_release=self.save)
        btns = [b]
        if on:
            off = mk_btn("إيقاف القفل", height=dp(52), font_size=dp(16), background_color=T.RED)
            off.bind(on_release=self.turn_off)
            btns.append(off)
        self.add_footer(*btns)

    def save(self, *a):
        if self.on and not lock_check(self.app, _digits(self.old_in.text)):
            return self.msg("الرمز الحالي غير صحيح")
        new = _digits(self.new_in.text)
        if not (new.isdigit() and 4 <= len(new) <= 6):
            return self.msg("الرمز يجب أن يكون من 4 إلى 6 أرقام")
        if new != _digits(self.conf_in.text):
            return self.msg("الرمزان غير متطابقين")
        lock_set_pin(self.app, new)
        self.app.show_banner("تم حفظ رمز القفل")
        self.app.back()

    def turn_off(self, *a):
        if not lock_check(self.app, _digits(self.old_in.text)):
            return self.msg("اكتب الرمز الحالي لإيقاف القفل")
        lock_disable(self.app)
        self.app.show_banner("تم إيقاف القفل")
        self.app.back()


class LockOverlay(FloatLayout):
    """شاشة القفل: لوحة أرقام + بصمة، تغطي التطبيق كله."""

    def __init__(self, app, **kw):
        super().__init__(**kw)
        self.app = app
        self.pin = ""
        self.n = int(lock_cfg(app).get("len", 4))
        self.fails, self.block_until = 0, 0.0
        with self.canvas.before:
            Color(*_LOGIN_BLUE)
            self._bg = Rectangle()
            Color(1, 1, 1, 0.06)
            self._e1 = Ellipse()
        self.bind(pos=self._lay, size=self._lay)
        self.add_widget(self._title())
        self.dots = mk_text("", dp(26), (1, 1, 1, 1), halign="center", height=dp(40))
        self.dots.size_hint = (1, None)
        self.dots.pos_hint = {"x": 0, "top": 0.77}
        self.add_widget(self.dots)
        self.note = mk_text("", dp(13), (1, 0.85, 0.85, 1), halign="center", height=dp(28))
        self.note.size_hint = (1, None)
        self.note.pos_hint = {"x": 0, "top": 0.71}
        self.add_widget(self.note)
        grid = GridLayout(cols=3, spacing=dp(12), size_hint=(None, None), size=(dp(264), dp(264)),
                          pos_hint={"center_x": 0.5, "y": 0.05})
        use_bio = bool(lock_cfg(app).get("bio")) and bio_available()
        for key in ("1", "2", "3", "4", "5", "6", "7", "8", "9", "bio" if use_bio else "", "0", "del"):
            if not key:
                grid.add_widget(Widget())
                continue
            txt = {"bio": "بصمة", "del": "حذف"}.get(key, key)
            b = mk_btn(txt, font_size=dp(22 if key.isdigit() else 14),
                       background_color=(1, 1, 1, 0.18) if key.isdigit() else (1, 1, 1, 0.10))
            b.size_hint = (1, 1)
            b.bind(on_release=lambda w, k=key: self.press(k))
            grid.add_widget(b)
        self.add_widget(grid)
        self._draw_dots()
        if use_bio:
            Clock.schedule_once(lambda dt: self.try_bio(), 0.4)

    def _title(self):
        t = mk_text("أدخل رمز القفل", dp(22), (1, 1, 1, 1), halign="center", height=dp(40))
        t.size_hint = (1, None)
        t.pos_hint = {"x": 0, "top": 0.88}
        return t

    def _lay(self, *a):
        self._bg.pos, self._bg.size = self.pos, self.size
        self._e1.pos, self._e1.size = (self.x - dp(120), self.y + self.height * 0.6), (dp(340), dp(340))

    def _draw_dots(self):
        set_text(self.dots, " ".join("●" if i < len(self.pin) else "○" for i in range(self.n)))

    def on_touch_down(self, touch):
        super().on_touch_down(touch)
        return True

    def press(self, key):
        if key == "bio":
            return self.try_bio()
        if time.time() < self.block_until:
            return set_text(self.note, "حاول بعد %d ثانية" % int(self.block_until - time.time() + 1))
        if key == "del":
            self.pin = self.pin[:-1]
        elif len(self.pin) < self.n:
            self.pin += key
        set_text(self.note, "")
        self._draw_dots()
        if len(self.pin) == self.n:
            if lock_check(self.app, self.pin):
                return self.unlock()
            self.fails += 1
            self.pin = ""
            self._draw_dots()
            if self.fails >= 5:
                self.block_until = time.time() + 30
                self.fails = 0
                set_text(self.note, "محاولات كثيرة، انتظر 30 ثانية")
            else:
                set_text(self.note, "الرمز غير صحيح")

    def try_bio(self):
        bio_auth("فتح التطبيق", "المس مستشعر البصمة",
                 lambda ok, msg: self.unlock() if ok else set_text(self.note, "استخدم الرمز للدخول"))

    def unlock(self):
        try:
            Window.remove_widget(self)
        except Exception:
            pass
        self.app._lock_ui = None
        self.app._unlocked_at = time.time()


def show_lock(app):
    if not lock_on(app) or getattr(app, "_lock_ui", None) is not None:
        return
    ov = LockOverlay(app)
    app._lock_ui = ov
    Window.add_widget(ov)


DOC_HOOKS["print"] = lambda args: print_doc(App.get_running_app(), args)
DOC_HOOKS["save"] = lambda png: save_pdf_oneclick(App.get_running_app(), png)


class FontPage(Page):
    def __init__(self, app, **kw):
        super().__init__(app, "تغيير الخط", **kw)
        self.status = ""
        self.scanning = False
        self.downloading = None
        self.draw()
        self.refresh_catalog()

    def names(self):
        items = (self.app.data.get("dl") or {}).get("items", {})
        return sorted(set(USER_FONTS) | {n for n in items if FONT_NAME_RE.fullmatch(n)}, key=font_sort_key)

    def draw(self):
        self.body.clear_widgets()
        self.add(mk_text(self.status, dp(13), T.MUTED, halign="center", height=dp(28)))
        cur = self.app.data.get("font") or "font.otf"
        items = (self.app.data.get("dl") or {}).get("items", {})
        for fn in self.names():
            info = USER_FONTS.get(fn)
            if info:
                sel = fn == cur
                row = CardBox(orientation="vertical", size_hint_y=None, height=dp(84),
                              padding=[dp(12), dp(6)], bg=(T.SOFT_BLUE if sel else None),
                              on_tap=lambda f=fn: self.pick(f))
                s = DrawnText(text="دفتر المحاسب", font_size=dp(22), color=T.TEXT, halign="center",
                              size_hint_y=0.62, font_override=info[0])
                s.disabled = True
                row.add_widget(s)
                row.add_widget(mk_text(font_label(fn) + ("  -  المستخدم حالياً" if sel else ""), dp(12),
                                       T.PRIMARY if sel else T.MUTED, halign="center", hint_y=0.38))
            else:
                row = CardBox(orientation="horizontal", size_hint_y=None, height=dp(74),
                              padding=[dp(12), dp(8)], spacing=dp(8))
                if self.downloading != fn:
                    b = mk_btn("تحميل", size_hint=(None, None), size=(dp(80), dp(38)),
                               background_color=T.GREEN, pos_hint={"center_y": 0.5})
                    b.bind(on_release=lambda w, f=fn: self.download(f))
                    row.add_widget(b)
                kb = max(1, int((items.get(fn) or {}).get("size", 0) / 1024))
                c = BoxLayout(orientation="vertical")
                c.add_widget(mk_text(font_label(fn), dp(16), T.TEXT, hint_y=0.55))
                c.add_widget(mk_text("جاري التحميل..." if self.downloading == fn else f"الحجم: {kb} KB",
                                     dp(12), T.MUTED, hint_y=0.45))
                row.add_widget(c)
            self.add(row)
        self.add(mk_text("اضغط على الخط لاستخدامه، أو حمّل الخطوط الجديدة التي تضيفها الإدارة." if len(self.names()) > 1 else "لا توجد خطوط إضافية حالياً. الخط الأساسي هو المستخدم.", dp(12), T.MUTED, halign="center", height=dp(44)))
        schedule_refresh(0.05)

    def refresh_catalog(self):
        if self.scanning:
            return
        self.scanning = True
        self.status = ""
        self.draw()

        def w():
            err = None
            try:
                scan_downloads(self.app.data.setdefault("dl", {}))
                self.app.save()
            except ApiError as e:
                err = str(e)
            except Exception:
                err = "تعذر الاتصال، تحقق من الانترنت"

            def done(dt):
                self.scanning = False
                self.app.apply_scan()
                new = [n for n in self.names() if n not in USER_FONTS]
                self.status = ""
                self.draw()
            Clock.schedule_once(done, 0)
        threading.Thread(target=w, daemon=True).start()

    def download(self, fn):
        item = ((self.app.data.get("dl") or {}).get("items", {})).get(fn)
        if self.downloading or not item:
            return
        self.downloading = fn
        self.draw()

        def w():
            err = None
            try:
                download_font(fn, item)
            except ApiError as e:
                err = str(e)
            except Exception:
                err = "فشل التحميل، تحقق من الانترنت"

            def done(dt):
                self.downloading = None
                self.status = err or f"تم تحميل {font_label(fn)}"
                self.draw()
            Clock.schedule_once(done, 0)
        threading.Thread(target=w, daemon=True).start()

    def pick(self, fn):
        self.app.set_font(fn)
        self.draw()


def note_popup(app, mid, n, after=None):
    """نافذة قراءة إشعار (تُستعمل في الإشعارات والبحث)."""
    app.mark_note_read(mid)
    box = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(6))
    sc, lay = make_scroll_list(spacing=dp(2))
    for ln in wrap_lines(n.get("t", ""), 30):
        if ln:
            lay.add_widget(mk_text(ln, dp(16), T.TEXT, height=dp(30)))
        else:
            lay.add_widget(Widget(size_hint_y=None, height=dp(12)))
    box.add_widget(sc)
    if n.get("d"):
        box.add_widget(mk_text(n["d"], dp(12), T.MUTED, halign="center", height=dp(24)))
    ok = mk_btn("إغلاق", height=dp(46), background_color=T.GRAY_BTN)
    box.add_widget(ok)
    pop = make_arabic_popup("إشعار", box, size_hint=(0.92, 0.7))
    ok.bind(on_release=lambda *a: pop.dismiss())
    if after:
        pop.bind(on_dismiss=lambda *a: after())
    pop.open()


class NotificationsPage(Page):
    """الإشعارات: كل رسالة نصية تكتبها في قناة التحميلات تظهر هنا."""

    def __init__(self, app, **kw):
        super().__init__(app, "الإشعارات", **kw)
        self.status = ""
        self.scanning = False
        self.draw()
        self.refresh()

    def notes(self):
        notes = visible_notes(self.app)
        return sorted(((int(k), v) for k, v in notes.items()), key=lambda kv: kv[0], reverse=True)

    def draw(self):
        self.body.clear_widgets()
        if self.status:
            self.add(mk_text(self.status, dp(13), T.MUTED, halign="center", height=dp(28)))
        read = set(self.app.data.get("notes_read") or [])
        items = self.notes()
        if not items:
            self.add(mk_text("لا توجد إشعارات", dp(15), T.MUTED, halign="center", height=dp(60)))
        for mid, n in items:
            unread = str(mid) not in read
            row = CardBox(orientation="vertical", size_hint_y=None, height=dp(66),
                          padding=[dp(12), dp(6)], bg=(T.SOFT_BLUE if unread else None),
                          on_tap=lambda m=mid, n=n: self.open_note(m, n))
            row.add_widget(mk_text(note_title(n.get("t", "")), dp(16), T.TEXT, hint_y=0.55))
            row.add_widget(mk_text(n.get("d", ""), dp(12), T.MUTED, hint_y=0.45))
            self.add(row)
        u = mk_btn("تحديث", height=dp(46), background_color=T.BLUE)
        u.bind(on_release=lambda *a: self.refresh())
        self.add(u)
        schedule_refresh(0.05)

    def refresh(self):
        if self.scanning:
            return
        self.scanning = True
        self.status = "جاري التحقق من الإشعارات الجديدة..."
        self.draw()

        def w():
            err = None
            try:
                scan_downloads(self.app.data.setdefault("dl", {}))
                self.app.save()
            except ApiError as e:
                err = str(e)
            except Exception:
                err = "تعذر الاتصال، تحقق من الانترنت"

            def done(dt):
                self.scanning = False
                self.app.apply_scan()
                self.status = ""
                self.draw()
                self.app.refresh_bell()
            Clock.schedule_once(done, 0)
        threading.Thread(target=w, daemon=True).start()

    def open_note(self, mid, n):
        note_popup(self.app, mid, n, self.draw)


class UpdatesPage(Page):
    """التحديثات: ملفات py التي ترسلها في قناة التحميلات تظهر هنا للتنزيل والتفعيل."""

    def __init__(self, app, **kw):
        super().__init__(app, "التحديثات", **kw)
        self.status = ""
        self.scanning = False
        self.busy = None
        self.draw()
        self.refresh()

    def entries(self):
        return sorted(((self.app.data.get("dl") or {}).get("updates") or {}).items())

    def draw(self):
        self.body.clear_widgets()
        if self.status:
            self.add(mk_text(self.status, dp(13), T.MUTED, halign="center", height=dp(28)))
        gsw, _sw = switch_row("التحديث التلقائي للكل", "تنزيل كل التحديثات تلقائياً عند توفر الانترنت",
                              self.app.auto_update_on(), self.app.set_auto_update_all)
        self.add(gsw)
        inst = self.app.data.get("updates") or {}
        ents = self.entries()
        if not ents:
            self.add(mk_text("لا توجد تحديثات متاحة", dp(15), T.MUTED, halign="center", height=dp(60)))
        for name, item in ents:
            cur = inst.get(name)
            has_new = cur is not None and int(item.get("mid", 0)) > int(cur.get("mid", 0))
            err = self.app.update_errors.get(name)
            if cur is None:
                state = "غير منزّل"
            elif has_new:
                state = "يتوفر إصدار أحدث"
            elif err:
                state = "فشل التشغيل: " + err[:40]
            else:
                state = "مفعّل"
            row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(62), spacing=dp(8))
            if self.busy == name:
                state = "جاري التحميل..."
            else:
                stopped = self.app.auto_stopped(name)
                if cur is not None:
                    rm = mk_btn("حذف", size_hint=(None, None), size=(dp(54), dp(38)), font_size=dp(13),
                                background_color=T.RED, pos_hint={"center_y": 0.5})
                    rm.bind(on_release=lambda w, n=name: self.remove(n))
                    row.add_widget(rm)
                au = mk_btn("تشغيل\nتلقائي" if stopped else "إيقاف\nتلقائي", size_hint=(None, None),
                            size=(dp(62), dp(38)), font_size=dp(11),
                            background_color=T.GRAY_BTN if not stopped else T.TEAL,
                            pos_hint={"center_y": 0.5})
                au.bind(on_release=lambda w, n=name, st=stopped: self.toggle_auto(n, st))
                row.add_widget(au)
                if cur is None or has_new:
                    b = mk_btn("تحميل" if cur is None else "تحديث", size_hint=(None, None),
                               size=(dp(64), dp(38)), font_size=dp(13), background_color=T.GREEN,
                               pos_hint={"center_y": 0.5})
                    b.bind(on_release=lambda w, n=name: self.install(n))
                    row.add_widget(b)
            kb = max(1, int(item.get("size", 0) / 1024))
            if self.busy != name and self.app.auto_stopped(name):
                state += "  -  التحميل التلقائي متوقف"
            c = BoxLayout(orientation="vertical")
            c.add_widget(mk_text(name, dp(15), T.TEXT, hint_y=0.5))
            c.add_widget(mk_text(f"{state}  -  {kb} KB", dp(12),
                                 T.RED if (err and cur is not None and not has_new) else T.MUTED, hint_y=0.5))
            row.add_widget(c)
            cap_lines = wrap_lines(str(item.get("cap") or "").strip(), 34)[:8] if item.get("cap") else []
            card = CardBox(orientation="vertical", size_hint_y=None,
                           height=dp(16) + dp(62) + ((dp(6) + len(cap_lines) * dp(21)) if cap_lines else 0),
                           padding=[dp(12), dp(8)], spacing=dp(2))
            card.add_widget(row)
            if cap_lines:
                card.add_widget(Widget(size_hint_y=None, height=dp(6)))
                for ln in cap_lines:
                    card.add_widget(mk_text(ln, dp(12), T.MUTED, height=dp(21)))
            self.add(card)
        u = mk_btn("تحديث القائمة", height=dp(46), background_color=T.BLUE)
        u.bind(on_release=lambda *a: self.refresh())
        self.add(u)
        schedule_refresh(0.05)

    def refresh(self):
        if self.scanning:
            return
        self.scanning = True
        self.status = ""
        self.draw()

        def w():
            err = None
            try:
                scan_downloads(self.app.data.setdefault("dl", {}))
                self.app.save()
            except ApiError as e:
                err = str(e)
            except Exception:
                err = "تعذر الاتصال، تحقق من الانترنت"

            def done(dt):
                self.scanning = False
                self.app.apply_scan()
                self.status = ""
                self.draw()
                self.app.refresh_bell()
            Clock.schedule_once(done, 0)
        threading.Thread(target=w, daemon=True).start()

    def install(self, name):
        item = ((self.app.data.get("dl") or {}).get("updates") or {}).get(name)
        if self.busy or not item:
            return
        if not UPDATE_NAME_RE.fullmatch(name):
            self.status = "اسم الملف غير صالح"
            return self.draw()
        was_new = name in (self.app.data.get("updates") or {})
        self.busy = name
        self.status = ""
        self.draw()

        def w():
            err = None
            try:
                download_channel_file(name, item, UPDATES_DIR, min_size=5)
            except ApiError as e:
                err = str(e)
            except Exception:
                err = "فشل التحميل، تحقق من الانترنت"

            def done(dt):
                if err:
                    self.status = err
                else:
                    ok, e2 = self.app.activate_update(name, item)
                    if ok:
                        self.status = ("تم التحديث - أعد تشغيل التطبيق لتطبيقه كاملاً" if was_new
                                       else "تم تفعيل التحديث")
                    else:
                        self.status = "فشل تشغيل التحديث: " + e2[:50]
                self.busy = None
                self.draw()
            Clock.schedule_once(done, 0)
        threading.Thread(target=w, daemon=True).start()

    def toggle_auto(self, name, stopped):
        self.app.set_auto_update(name, stopped)      # كان متوقفاً -> نشغّله
        self.draw()

    def remove(self, name):
        def go():
            self.app.remove_update(name)
            self.app.set_auto_update(name, False)
            self.status = "تم الحذف - أعد تشغيل التطبيق لإيقافه بالكامل"
            self.draw()
        confirm_popup("حذف التحديث", "هل تريد حذف هذا التحديث؟", go, yes_text="حذف")


class SyncPage(Page):
    def __init__(self, app, **kw):
        super().__init__(app, "مزامنة", **kw)
        self.msg_dt = self.add(mk_text("جاري المزامنة...", dp(20), T.PRIMARY, halign="center", height=dp(60)))
        self.sub_dt = self.add(mk_text("", dp(14), T.MUTED, halign="center", height=dp(40)))
        c = mk_btn("إغلاق", height=dp(52), background_color=T.GRAY_BTN)
        c.bind(on_release=lambda *a: app.go_home())
        self.add_footer(c)

    def status(self, main, sub=""):
        set_text(self.msg_dt, main)
        set_text(self.sub_dt, sub)


# ============================================================
# 15) التطبيق
# ============================================================
# ============================================================
# 10) ميزات جديدة: الترحيب، الباركود، الإدخال الصوتي، إشعارات الخلفية
# ============================================================
# <<PARSER>>
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_TASHKEEL = re.compile("[\u064B-\u0652\u0640]")


def ar_norm(s):
    s = str(s or "").translate(_AR_DIGITS)
    s = _TASHKEEL.sub("", s)
    s = re.sub("[أإآٱ]", "ا", s)
    s = s.replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    return re.sub(r"\s+", " ", s).strip().lower()


_NUM_WORDS = {"واحد": 1, "واحده": 1, "اثنين": 2, "اثنان": 2, "اثنتين": 2, "ثنتين": 2,
              "ثلاثه": 3, "ثلاث": 3, "اربعه": 4, "اربع": 4, "خمسه": 5, "خمس": 5,
              "سته": 6, "ست": 6, "سبعه": 7, "سبع": 7, "ثمانيه": 8, "ثماني": 8, "ثمان": 8,
              "تسعه": 9, "تسع": 9, "عشره": 10, "عشر": 10, "احدعشر": 11, "اثنعشر": 12,
              "عشرين": 20, "ثلاثين": 30, "اربعين": 40, "خمسين": 50, "ستين": 60,
              "سبعين": 70, "ثمانين": 80, "تسعين": 90, "مايه": 100, "ميه": 100, "مئه": 100,
              "مئتين": 200, "نصف": 0.5, "ربع": 0.25}
_TENS = {k: v for k, v in _NUM_WORDS.items() if v in (20, 30, 40, 50, 60, 70, 80, 90)}
_PLURALS = {"كراتين": "كرتون", "كراتن": "كرتون", "حبات": "حبه", "حبوب": "حبه", "علب": "علبه",
            "اكياس": "كيس", "قطع": "قطعه", "دزينات": "دزينه", "دزن": "دزينه",
            "صناديق": "صندوق", "شوالات": "شوال", "بكسات": "بكس", "رزم": "رزمه",
            "باكتات": "باكت", "باكيت": "باكت", "دبات": "دبه", "زجاجات": "زجاجه",
            "قوارير": "قاروره", "لترات": "لتر", "اطنان": "طن", "عبوات": "عبوه"}
_SALE_W = {"بعت", "بعنا", "بيع", "بيعت", "بعته"}
_BUY_W = {"اشتريت", "اشترينا", "شراء", "اشتري", "شريت", "اشتريناه"}
_CREDIT_W = {"اجل", "بالاجل", "الاجل", "اجلا", "دين", "بالدين", "ذمه", "بالذمه", "اجله"}
_CASH_W = {"نقد", "نقدا", "نقدي", "بالنقد", "كاش", "كاشي", "cash"}
_MARK_W = {"الي", "الى", "عند", "من", "لدي", "لدى", "علي", "على"}
_STOP_W = (_SALE_W | _BUY_W | _CREDIT_W | _CASH_W | _MARK_W |
           {"و", "في", "ب", "فاتوره", "فاتورة", "بسعر", "ريال", "ريالات", "ثم", "ل", "هذا", "هذه"})


def _strip_al(w):
    return w[2:] if w.startswith("ال") and len(w) > 3 else w


def unit_canon(w):
    w = _strip_al(ar_norm(w))
    w = _PLURALS.get(w, w)
    if len(w) > 3 and w.endswith("ه"):
        w = w[:-1]
    return w


def _forms(tok):
    out = {tok, _strip_al(tok)}
    if tok.startswith("لل") and len(tok) > 3:
        out |= {tok[2:], "ال" + tok[2:]}
    elif tok.startswith("ل") and len(tok) > 3:
        out |= {tok[1:], _strip_al(tok[1:])}
    return out


def _unit_cands(tok):
    c = [(tok, False)]
    if tok.endswith("تين") and len(tok) > 4:
        c.append((tok[:-3] + "ه", True))
    if (tok.endswith("ين") or tok.endswith("ان")) and len(tok) > 4:
        c.append((tok[:-2], True))
    return c


def parse_voice_command(text, db, force_kind=None):
    """يحوّل جملة مثل «بعت 3 كراتين بيبسي لفلان» إلى بنية: النوع، الأصناف، الطرف، الدفع."""
    raw = str(text or "").translate(_AR_DIGITS)
    raw = re.sub(r"[،,؟?!:;]", " ", raw)
    raw = re.sub(r"(?<![\d])\.(?![\d])", " ", raw)
    raw = re.sub(r"(?<!\S)و(?=\d)", "و ", raw)
    orig = raw.split()
    toks = [ar_norm(t) for t in orig]
    n = len(toks)
    kind = force_kind
    if kind is None:
        if any(t in _SALE_W for t in toks):
            kind = "sale"
        elif any(t in _BUY_W for t in toks):
            kind = "purchase"
    pay = None
    if any(t in _CREDIT_W for t in toks):
        pay = "credit"
    elif any(t in _CASH_W for t in toks):
        pay = "cash"
    if toks and toks[-1] in _CREDIT_W:          # آخر كلمة تحسم طريقة الدفع
        pay = "credit"
    elif toks and toks[-1] in _CASH_W:
        pay = "cash"
    used = set(i for i, t in enumerate(toks) if t in _STOP_W)
    # ---- الأرقام ----
    nums = {}
    i = 0
    while i < n:
        t = toks[i]
        if re.fullmatch(r"\d+(\.\d+)?", t):
            nums[i] = (float(t), 1)
        elif t in _NUM_WORDS:
            v, ln = _NUM_WORDS[t], 1
            if v < 10 and i + 1 < n and toks[i + 1].startswith("و") and toks[i + 1][1:] in _TENS:
                v, ln = v + _TENS[toks[i + 1][1:]], 2
            nums[i] = (v, ln)
            i += ln - 1
        i += 1
    starts = sorted(nums)
    cuts = [0]
    for k in starts[1:]:
        if k > 0 and toks[k - 1] == "و":
            cuts.append(k - 1)
    cuts.append(n)
    ranges = [(cuts[j], cuts[j + 1]) for j in range(len(cuts) - 1)]
    products = db.get("products", [])
    all_units = set(_PLURALS.values())
    for p in products:
        for u in p.get("units", []):
            all_units.add(u.get("name", ""))
    all_units_c = {unit_canon(u) for u in all_units if u}
    segs = []
    for (s, e) in ranges:
        idx = [j for j in range(s, e) if j not in used]
        qty = None
        for j in idx:
            if j in nums:
                qty = nums[j][0]
                for q in range(j, j + nums[j][1]):
                    used.add(q)
                break
        idx = [j for j in range(s, e) if j not in used]
        best, best_hits = None, None
        for p in products:
            ptoks = [_strip_al(t) for t in ar_norm(p.get("name", "")).split()]
            if not ptoks:
                continue
            hits = []
            for pt in ptoks:
                h = next((j for j in idx if j not in hits and pt in _forms(toks[j])), None)
                if h is None:
                    break
                hits.append(h)
            else:
                if best is None or (len(ptoks), len(p["name"])) > (len(best[1]), len(best[0]["name"])):
                    best, best_hits = (p, ptoks), hits
        prod = best[0] if best else None
        if prod:
            used.update(best_hits)
        idx = [j for j in range(s, e) if j not in used]
        unit_set = ({unit_canon(u["name"]): u["name"] for u in prod.get("units", [])}
                    if prod else {})
        unit, dual = None, False
        for j in idx:
            for cand, is_dual in _unit_cands(toks[j]):
                c = unit_canon(cand)
                if c in unit_set:
                    unit, dual = unit_set[c], is_dual
                elif not prod and c in all_units_c:
                    unit, dual = orig[j], is_dual
                if unit:
                    break
            if unit:
                used.add(j)
                break
        if qty is None and dual:
            qty = 2
        segs.append({"s": s, "e": e, "qty": qty, "product": prod, "unit": unit})
    # ---- الطرف (عميل / مورد) ----
    coll = "suppliers" if kind == "purchase" else "customers"
    party, best_sc = None, 0
    for c in db.get(coll, []):
        ctoks = [_strip_al(t) for t in ar_norm(c.get("name", "")).split()]
        if not ctoks:
            continue
        hits = []
        for ct in ctoks:
            h = next((j for j in range(n) if j not in used and j not in hits and ct in _forms(toks[j])), None)
            if h is not None:
                hits.append(h)
        if hits and ctoks[0] and any(ctoks[0] in _forms(toks[j]) for j in hits) and len(hits) > best_sc:
            party, best_sc, best_hits = c, len(hits), hits
    party_name = ""
    if party:
        used.update(best_hits)
    else:
        for j in range(n):
            if j in used:
                continue
            t = toks[j]
            nm = None
            if j > 0 and toks[j - 1] in _MARK_W and t not in _STOP_W:
                nm = [j]
            elif t.startswith("ل") and len(t) > 2 and not t.startswith("لا"):
                nm = [j]
            if nm:
                k = j + 1
                while k < n and k not in used and toks[k] not in _STOP_W and len(nm) < 3 and k not in nums:
                    nm.append(k)
                    k += 1
                words = [orig[q] for q in nm]
                if not toks[j - 1:j] or toks[j - 1] not in _MARK_W:
                    words[0] = words[0][1:] if len(words[0]) > 2 else words[0]
                party_name = " ".join(words)
                used.update(nm)
                break
    # ---- الأصناف ----
    items = []
    for sg in segs:
        name = ""
        if not sg["product"]:
            left = [orig[j] for j in range(sg["s"], sg["e"]) if j not in used and j not in nums]
            name = " ".join(left).strip()
        if sg["product"] or name:
            items.append({"qty": sg["qty"] or 1, "unit": sg["unit"] or "",
                          "product": sg["product"], "name": name})
    return {"kind": kind, "items": items, "party": party, "party_name": party_name,
            "pay": pay, "text": text}
# <<END_PARSER>>

from kivy.graphics import Color, RoundedRectangle, Rectangle, Line
from kivy.uix.widget import Widget


_KEEP = []          # مراجع تمنع حذف الكائنات الحية من الذاكرة


def _err_text(e):
    return str(e).replace("\n", " ")[:140]


BUNDLE_VERSION = "v6.1-smooth"
VOICE_ENABLED = True                          # الصوت معطّل مؤقتاً؛ غيّرها إلى True لتفعيله        # يظهر في «فحص الصوت والمسح» لمعرفة أي نسخة تعمل عندك


def vs_log(msg):
    """سجل أخطاء الصوت والمسح (يظهر في شاشة الفحص)."""
    try:
        with open(os.path.join(DATA_DIR, "voice_scan.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%m-%d %H:%M ") + str(msg).replace("\n", " | ")[:300] + "\n")
    except Exception:
        pass


def vs_log_tail(n=5):
    try:
        with open(os.path.join(DATA_DIR, "voice_scan.log"), encoding="utf-8") as f:
            return f.read().strip().split("\n")[-n:]
    except Exception:
        return []


def voice_scan_diagnose():
    L = ["إصدار الحزمة: " + BUNDLE_VERSION]
    if platform != "android":
        return L + ["✗ هذا الفحص يعمل على أندرويد فقط"]
    try:
        from jnius import autoclass
        PA = autoclass("org.kivy.android.PythonActivity")
        act = PA.mActivity
        pm, pkg = act.getPackageManager(), act.getPackageName()
        PMc = autoclass("android.content.pm.PackageManager")
    except Exception as e:
        return L + ["✗ pyjnius: " + _err_text(e)]
    declared = []
    try:
        pi = pm.getPackageInfo(pkg, PMc.GET_PERMISSIONS)
        declared = [str(p) for p in (pi.requestedPermissions or [])]
    except Exception as e:
        L.append("✗ قراءة صلاحيات الـ APK: " + _err_text(e))
    try:
        from android.permissions import check_permission
    except Exception:
        check_permission = lambda p: False
    perm = "android.permission.RECORD_AUDIO"
    if perm not in declared:
        L.append("✗ صلاحية الميكروفون غير موجودة داخل الـ APK ← أعد البناء بملف buildozer.spec الجديد")
    elif not check_permission(perm):
        L.append("✗ صلاحية الميكروفون موجودة لكنها غير ممنوحة ← فعّلها من إعدادات الهاتف")
    else:
        L.append("✓ صلاحية الميكروفون ممنوحة")
    try:
        autoclass("com.google.mlkit.vision.codescanner.GmsBarcodeScanning")
        L.append("✓ مكتبة المسح (ML Kit) داخل الـ APK")
    except Exception as e:
        L.append("ℹ مكتبة ML Kit غير موجودة في الـ APK، فيُستخدم ماسح التطبيق الداخلي (يعمل بدونها)")
    try:
        from android.permissions import check_permission as _cp
        L.append("✓ صلاحية الكاميرا ممنوحة" if _cp("android.permission.CAMERA")
                 else "✗ صلاحية الكاميرا غير ممنوحة ← فعّلها من إعدادات التطبيق")
    except Exception as e:
        L.append("✗ فحص صلاحية الكاميرا: " + _err_text(e))
    for p, nm in (("com.google.android.gms", "خدمات Google Play"),
                  ("com.google.android.googlequicksearchbox", "تطبيق Google (للتعرف الصوتي)")):
        try:
            pm.getPackageInfo(p, 0)
            L.append("✓ " + nm + " مثبّت")
        except Exception:
            L.append("✗ " + nm + " غير ظاهر/غير مثبّت")
    try:
        SR = autoclass("android.speech.SpeechRecognizer")
        if SR.isRecognitionAvailable(act):
            L.append("✓ خدمة التعرف الصوتي متاحة")
        else:
            L.append("✗ خدمة التعرف الصوتي غير متاحة ← ثبّت/حدّث تطبيق Google، وتأكد من ملف extra_manifest.xml")
        lst = pm.queryIntentServices(autoclass("android.content.Intent")("android.speech.RecognitionService"), 0)
        names = [str(lst.get(i).serviceInfo.packageName) for i in range(lst.size())]
        L.append(("✓ خدمات التعرف: " + ", ".join(names)) if names else "✗ لا توجد خدمات تعرف صوتي على الجهاز")
    except Exception as e:
        L.append("✗ فحص التعرف الصوتي: " + _err_text(e))
    tail = vs_log_tail()
    if tail:
        L.append("آخر الأخطاء المسجّلة:")
        L += ["  " + t for t in tail]
    return L


def voice_scan_popup():
    box = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(8))
    sc, lay = make_scroll_list(spacing=dp(2))
    for ln in voice_scan_diagnose():
        bad = ln.startswith("✗")
        lay.add_widget(mk_text(ln, dp(13), T.RED if bad else (T.GREEN if ln.startswith("✓") else T.MUTED),
                               height=dp(44)))
    box.add_widget(sc)
    ok = mk_btn("إغلاق", height=dp(46), background_color=T.GRAY_BTN)
    box.add_widget(ok)
    pop = make_arabic_popup("فحص الصوت والمسح", box, size_hint=(0.94, 0.78))
    ok.bind(on_release=lambda *a: pop.dismiss())
    pop.open()
    schedule_refresh(0.1)



# ============================================================
# مسح الباركود: ML Kit (استطلاع بدون مستمعات) ثم ZXing ثم إدخال يدوي
# ============================================================
def _manual_barcode_popup(on_code):
    box = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(10))
    b1, inp = lab_in("الباركود (اكتبه، أو استخدم ماسح بلوتوث)", "", None, "")
    box.add_widget(b1)
    box.add_widget(Widget())
    bb = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(8))
    ok = mk_btn("اعتماد", background_color=T.GREEN)
    cn = mk_btn("إلغاء", background_color=T.GRAY_BTN)
    bb.add_widget(ok)
    bb.add_widget(cn)
    box.add_widget(bb)
    pop = make_arabic_popup("إدخال الباركود", box, size_hint=(0.92, 0.42))

    def done(*a):
        code = inp.text.strip()
        if code:
            pop.dismiss()
            on_code(code)
    ok.bind(on_release=done)
    cn.bind(on_release=lambda *a: pop.dismiss())
    pop.open()


def _scan_mlkit(on_code, on_fail):
    from jnius import autoclass, cast
    from android.runnables import run_on_ui_thread
    PA = autoclass("org.kivy.android.PythonActivity")
    # يرمي استثناء إن لم تكن مكتبة المسح داخل الـ APK
    Scanner = autoclass("com.google.mlkit.vision.codescanner.GmsBarcodeScanning")
    box = {"task": None, "err": None}

    @run_on_ui_thread
    def go():
        try:
            box["task"] = Scanner.getClient(PA.mActivity).startScan()
        except Exception as e:
            box["err"] = _err_text(e)
    go()
    t0 = time.time()

    def poll(dt):
        if box["err"]:
            on_fail("ML Kit: " + box["err"])
            return False
        task = box["task"]
        if task is None:
            if time.time() - t0 > 8:
                on_fail("ML Kit: لم يبدأ الماسح")
                return False
            return True
        if not task.isComplete():
            if time.time() - t0 > 300:
                on_fail("ML Kit: انتهت المهلة")
                return False
            return True
        try:
            if task.isSuccessful():
                bc = cast("com.google.mlkit.vision.barcode.common.Barcode", task.getResult())
                code = str(bc.getRawValue() or "").strip()
                if code:
                    on_code(code)
                else:
                    on_fail("ML Kit: الباركود فارغ")
            elif not task.isCanceled():
                on_fail("ML Kit: " + _err_text(task.getException()))
        except Exception as e:
            on_fail("ML Kit: " + _err_text(e))
        return False
    Clock.schedule_interval(poll, 0.25)


# <<BARCODE_DECODER>>
from PIL import Image as PILImage
# مفكّك باركود EAN-13 / UPC-A / EAN-8 بلغة بايثون فقط (يعمل على صورة رمادية من Pillow)
_EAN_L = ((3, 2, 1, 1), (2, 2, 2, 1), (2, 1, 2, 2), (1, 4, 1, 1), (1, 1, 3, 2),
          (1, 2, 3, 1), (1, 1, 1, 4), (1, 3, 1, 2), (1, 2, 1, 3), (3, 1, 1, 2))
_EAN_G = ((1, 1, 2, 3), (1, 2, 2, 2), (2, 2, 1, 2), (1, 1, 4, 1), (2, 3, 1, 1),
          (1, 3, 2, 1), (4, 1, 1, 1), (2, 1, 3, 1), (3, 1, 2, 1), (2, 1, 1, 3))
_EAN_PARITY = ("LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG",
               "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL")


def _match_digit(w, tables):
    """يطابق 4 عروض (بالبكسل) مع أقرب رقم. يرجع (الرقم، رمز الجدول) أو None."""
    tot = float(sum(w))
    if tot <= 0:
        return None
    m = tot / 7.0
    best, best_err = None, 1e9
    for code, table in tables:
        for d, pat in enumerate(table):
            err = 0.0
            for i in range(4):
                err += abs(w[i] / m - pat[i])
            if err < best_err:
                best, best_err = (d, code), err
    return best if best_err < 1.35 else None


def _ean_checksum_ok(digits):
    s = 0
    n = len(digits)
    for i, ch in enumerate(digits[:-1]):
        d = int(ch)
        # من اليمين (بدون رقم التحقق): الموضع الأول وزنه 3
        s += d * (3 if (n - 2 - i) % 2 == 0 else 1)
    return (10 - s % 10) % 10 == int(digits[-1])


def _decode_runs(runs, s, kind):
    """runs: عروض متتالية (بار/فراغ بالتناوب) والعنصر s بار. يفك EAN-13 أو EAN-8 من الموضع s."""
    n_digits = 6 if kind == 13 else 4
    need = 3 + n_digits * 4 + 5 + n_digits * 4 + 3
    if s + need > len(runs):
        return None
    g = runs[s:s + 3]
    m = sum(g) / 3.0
    if m < 1.0 or any(x < 0.5 * m or x > 1.7 * m for x in g):
        return None
    if s > 0 and runs[s - 1] < 4 * m:                    # منطقة هدوء قبل الباركود
        return None
    i = s + 3
    left, parity = [], ""
    for _ in range(n_digits):
        r = _match_digit(runs[i:i + 4], (("L", _EAN_L), ("G", _EAN_G)) if kind == 13 else (("L", _EAN_L),))
        if r is None:
            return None
        left.append(r[0])
        parity += r[1]
        i += 4
    c = runs[i:i + 5]
    if any(x < 0.45 * m or x > 1.8 * m for x in c):
        return None
    i += 5
    right = []
    for _ in range(n_digits):
        r = _match_digit(runs[i:i + 4], (("R", _EAN_L),))
        if r is None:
            return None
        right.append(r[0])
        i += 4
    e = runs[i:i + 3]
    if any(x < 0.45 * m or x > 1.8 * m for x in e):
        return None
    if kind == 13:
        if parity not in _EAN_PARITY:
            return None
        first = _EAN_PARITY.index(parity)
        digits = str(first) + "".join(map(str, left)) + "".join(map(str, right))
    else:
        if parity != "L" * 4:
            return None
        digits = "".join(map(str, left)) + "".join(map(str, right))
    return digits if _ean_checksum_ok(digits) else None


def _row_to_runs(row):
    """صف بكسلات رمادية → عروض متناوبة تبدأ بـ (فراغ أو بار) مع لون أول عنصر."""
    n = len(row)
    if n < 60:
        return None
    sm = [(row[max(0, i - 1)] + row[i] + row[min(n - 1, i + 1)]) / 3.0 for i in range(n)]
    srt = sorted(sm)
    lo, hi = srt[int(n * 0.05)], srt[int(n * 0.95)]
    if hi - lo < 35:
        return None
    thr = (lo + hi) / 2.0
    runs, first_dark = [], sm[0] < thr
    cur, cnt = first_dark, 0
    for v in sm:
        dark = v < thr
        if dark == cur:
            cnt += 1
        else:
            runs.append(cnt)
            cur, cnt = dark, 1
    runs.append(cnt)
    return runs, first_dark


def decode_row(row):
    """يحاول فك صف واحد بالاتجاهين. يرجع نص الباركود أو None."""
    rr = _row_to_runs(row)
    if rr is None:
        return None
    runs, first_dark = rr
    for direction in (0, 1):
        r = runs if direction == 0 else runs[::-1]
        fd = first_dark if direction == 0 else (first_dark == (len(runs) % 2 == 1))
        # أول بار: الفهرس 0 إن بدأ بإظلام، وإلا 1
        start = 0 if fd else 1
        for s in range(start, len(r) - 40, 2):
            for kind in (13, 8):
                d = _decode_runs(r, s, kind)
                if d:
                    return d
    return None


def decode_gray_image(img, rows=18):
    """img: صورة Pillow رمادية. يفحص خطوطاً أفقية ورأسية ويرجع قائمة بالأكواد المقروءة."""
    out = []
    try:
        trans = getattr(PILImage, "Transpose", PILImage)
        views = (img, img.transpose(trans.ROTATE_90))
    except Exception:
        views = (img,)
    for v in views:
        w, h = v.size
        for k in range(rows):
            y = int(h * (0.12 + 0.76 * k / max(1, rows - 1)))
            data = list(v.crop((0, y, w, y + 1)).tobytes())
            d = decode_row(data)
            if d:
                out.append(d)
    return out
# <<END_BARCODE_DECODER>>


from kivy.uix.modalview import ModalView
from kivy.graphics import PushMatrix, PopMatrix, Rotate


def _bc_norm(c):
    c = str(c or "").strip()
    return c.lstrip("0") if c.isdigit() else c          # UPC-A (12) = EAN-13 بصفر في البداية


class BarcodeScanner(ModalView):
    """ماسح باركود داخل التطبيق: كاميرا مباشرة + إطار توجيه، ويعمل بدون مكتبات إضافية."""

    def __init__(self, on_code, on_manual, **kw):
        super().__init__(size_hint=(1, 1), auto_dismiss=False, background="",
                         background_color=(0, 0, 0, 1), **kw)
        self.on_code, self.on_manual = on_code, on_manual
        self.angle = 270                        # معظم الهواتف: المعاينة تحتاج تدويراً 90° (زر تدوير للتصحيح)
        self.busy = self.done = False
        self.seen = {}
        self.t0 = time.time()
        self.stale_sig, self.live = None, False     # لتجاهل آخر صورة قديمة تبقى في ذاكرة الشاشة
        self.cam, self.rot = None, None
        self._tsz = None                        # حجم صورة الكاميرا الفعلي (لتغطية الشاشة بدون تشويه)
        self._frame = (0, 0, 0, 0)
        self._line = self._glow = None
        root = FloatLayout()
        self.root_l = root
        try:
            from kivy.uix.camera import Camera
            self.cam = Camera(resolution=(640, 480), play=False, index=0, size_hint=(None, None))
            with self.cam.canvas.before:
                PushMatrix()
                self.rot = Rotate(angle=self.angle, origin=(0, 0))
            with self.cam.canvas.after:
                PopMatrix()
            root.add_widget(self.cam)
        except Exception as e:
            vs_log("SCANCAM: " + _err_text(e))
        self.ov = Widget()
        root.add_widget(self.ov)
        top = BoxLayout(orientation="vertical", size_hint=(1, None), height=dp(70),
                        pos_hint={"top": 0.97}, padding=[dp(12), 0])
        self.msg = mk_text("وجّه الباركود داخل الإطار", dp(17), WHITE, halign="center", height=dp(40))
        top.add_widget(self.msg)
        root.add_widget(top)
        bar = BoxLayout(size_hint=(1, None), height=dp(58), pos_hint={"y": 0.03},
                        padding=[dp(12), 0], spacing=dp(10))
        for txt, col, fn in (("إلغاء", T.GRAY_BTN, self.close), ("تدوير", T.BLUE, self.rotate),
                             ("إدخال يدوي", T.GREEN, self.manual)):
            b = mk_btn(txt, font_size=dp(15), background_color=col)
            b.bind(on_release=lambda *a, f=fn: f())
            bar.add_widget(b)
        root.add_widget(bar)
        self.add_widget(root)
        root.bind(size=self._layout, pos=self._layout)
        self._layout()

    def _layout(self, *a):
        W, H = self.root_l.size
        if self.cam is not None:
            # الكاميرا تغطي الشاشة كلها (cover) مع الحفاظ على نسبة الصورة
            tw, th = self._tsz or (640, 480)
            swap = self.angle % 180 == 90
            bw, bh = (th, tw) if swap else (tw, th)
            sc = max(W / float(bw), H / float(bh))
            self.cam.size = (tw * sc, th * sc)
            self.cam.center = self.root_l.center
            self.rot.origin = self.cam.center
            self.rot.angle = self.angle
        fw, fh = W * 0.84, max(H * 0.26, dp(160))
        x, y = (W - fw) / 2, (H - fh) / 2 + dp(20)
        self._frame = (x, y, fw, fh)
        self.ov.canvas.clear()
        with self.ov.canvas:
            Color(0, 0, 0, 0.35)                                 # تعتيم خفيف حول الإطار
            Rectangle(pos=(0, 0), size=(W, y))
            Rectangle(pos=(0, y + fh), size=(W, H - y - fh))
            Rectangle(pos=(0, y), size=(x, fh))
            Rectangle(pos=(x + fw, y), size=(x, fh))
            Color(1, 1, 1, 0.95)
            Line(rectangle=(x, y, fw, fh), width=dp(2))
            Color(0.95, 0.2, 0.2, 0.25)
            self._glow = Line(points=[x + dp(8), y + fh / 2, x + fw - dp(8), y + fh / 2], width=dp(6))
            Color(0.95, 0.2, 0.2, 0.95)
            self._line = Line(points=[x + dp(8), y + fh / 2, x + fw - dp(8), y + fh / 2], width=dp(1.8))

    def _anim(self, dt):
        """الخط الأحمر يتحرك صعوداً ونزولاً داخل الإطار."""
        if self.done or self._line is None:
            return False
        x, y, fw, fh = self._frame
        ph = ((time.time() - self.t0) * 0.7) % 2.0
        k = ph if ph <= 1 else 2 - ph
        k = 0.5 - 0.5 * math.cos(k * math.pi)              # حركة ناعمة عند الأطراف
        yy = y + dp(6) + (fh - dp(12)) * k
        pts = [x + dp(8), yy, x + fw - dp(8), yy]
        self._line.points = pts
        self._glow.points = pts

    def rotate(self):
        self.angle = (self.angle + 90) % 360
        self._layout()

    def manual(self):
        self.close()
        self.on_manual()

    def begin(self):
        self.open()
        Clock.schedule_interval(self._anim, 1 / 30.0)
        self._ensure_permission(self._play)

    def _ensure_permission(self, ok):
        if platform != "android":
            return ok()
        try:
            from android.permissions import check_permission, request_permissions
            if check_permission("android.permission.CAMERA"):
                return ok()
            request_permissions(
                ["android.permission.CAMERA"],
                lambda perms, grants: Clock.schedule_once(
                    lambda dt: ok() if all(grants) else self._fail("صلاحية الكاميرا غير ممنوحة"), 0))
        except Exception:
            ok()

    def _play(self):
        if self.cam is None:
            return self._fail("تعذّر إنشاء الكاميرا داخل التطبيق")
        try:
            self.cam.play = True
        except Exception as e:
            return self._fail("تشغيل الكاميرا: " + _err_text(e))
        Clock.schedule_interval(self._tick, 0.3)

    def _fail(self, text):
        vs_log("SCAN: " + text)
        self.msg.text = text + "\nيمكنك استخدام «إدخال يدوي»"

    def _tick(self, dt):
        if self.done:
            return False
        tex = self.cam.texture if self.cam is not None else None
        if tex is not None and tuple(tex.size) != self._tsz:
            self._tsz = tuple(tex.size)
            self._layout()
        if tex is None:
            if time.time() - self.t0 > 7:
                self._fail("تعذّر تشغيل الكاميرا. افتح تطبيق الكاميرا للتأكد أنها تعمل")
                return False
            return
        if self.busy:
            return
        try:
            pix, size = tex.pixels, tex.size
            sig = zlib.crc32(pix[::61])
        except Exception as e:
            self._fail("قراءة صورة الكاميرا: " + _err_text(e))
            return False
        if not self.live:
            # أول صورة قد تكون بقايا المرة السابقة؛ لا نقرأ إلا بعد أن تتغير الصورة فعلاً
            if self.stale_sig is None:
                self.stale_sig = sig
                return
            if sig == self.stale_sig:
                if time.time() - self.t0 > 8:
                    self._fail("الكاميرا لا تحدّث الصورة. أغلق الماسح وافتحه من جديد")
                    return False
                return
            self.live = True
        self.busy = True
        threading.Thread(target=self._work, args=(pix, size), daemon=True).start()

    def _work(self, pix, size):
        codes = []
        try:
            img = PILImage.frombytes("RGBA", size, pix).convert("L")
            codes = decode_gray_image(img)
        except Exception as e:
            vs_log("SCANDECODE: " + _err_text(e))
        Clock.schedule_once(lambda dt: self._found(codes), 0)

    def _found(self, codes):
        self.busy = False
        if self.done:
            return
        for c in codes:
            self.seen[c] = self.seen.get(c, 0) + 1
        for c, n in self.seen.items():
            if n >= 2:                          # قراءتان متطابقتان تكفيان (مع فحص المجموع)
                self.close()
                Clock.schedule_once(lambda dt, code=c: self.on_code(code), 0.05)
                return

    def close(self, *a):
        self.done = True
        try:
            Clock.unschedule(self._tick)
            Clock.unschedule(self._anim)
        except Exception:
            pass
        try:
            if self.cam is not None:
                self.cam.play = False
                core = getattr(self.cam, "_camera", None)       # تحرير الكاميرا فعلياً قبل المرة القادمة
                if core is not None:
                    for name in ("stop", "_release_camera"):
                        fn = getattr(core, name, None)
                        if callable(fn):
                            try:
                                fn()
                            except Exception:
                                pass
                self.root_l.remove_widget(self.cam)
        except Exception:
            pass
        self.cam = None
        self.dismiss()


def scan_barcode(on_code, on_err):
    """ماسح Google (إن وُجد في الـ APK) ثم ماسح التطبيق الداخلي بالكاميرا المباشرة."""
    if platform != "android":
        return on_err("المسح متاح على أندرويد فقط")

    try:
        # ماسح التطبيق: كاميرا بملء الشاشة وخط أحمر متحرك (نافذة ML Kit الجاهزة لا تسمح بذلك)
        BarcodeScanner(on_code, lambda: _manual_barcode_popup(on_code)).begin()
    except Exception as e:
        on_err("الماسح الداخلي: " + _err_text(e))


def scan_with_fallback(on_code):
    """مسح الباركود، وإن تعذّر يعرض السبب ويعرض الإدخال اليدوي."""
    def failed(m):
        vs_log("SCAN: " + m)
        confirm_popup("تعذّر المسح", m + "\n\nجرّب: الإعدادات > فحص الصوت والمسح"
                      "\nهل تريد إدخال الباركود يدوياً؟",
                      lambda: _manual_barcode_popup(on_code), yes_text="إدخال يدوي")
    scan_barcode(on_code, failed)


# ============================================================
# الإدخال الصوتي بالضغط المستمر (SpeechRecognizer)
# ============================================================
_VOICE_ERR = {
    1: "انتهت مهلة الشبكة", 2: "يلزم انترنت للتعرف الصوتي بالعربية",
    3: "مشكلة في الميكروفون", 4: "خدمة التعرف الصوتي لا تستجيب",
    9: "لا توجد صلاحية للميكروفون. فعّلها من إعدادات الهاتف > التطبيقات",
    10: "طلبات كثيرة، انتظر قليلاً", 11: "انقطع اتصال خدمة التعرف",
    12: "اللغة العربية غير مدعومة في خدمة التعرف", 13: "حمّل حزمة اللغة العربية للتعرف الصوتي",
}
_VL = {}


def _bundle_texts(b):
    try:
        lst = b.getStringArrayList("results_recognition")
        return [str(lst.get(i)) for i in range(lst.size())] if lst is not None else []
    except Exception:
        return []


def _listener_class():
    if "cls" in _VL:
        return _VL["cls"]
    from jnius import PythonJavaClass, java_method

    class _L(PythonJavaClass):
        __javainterfaces__ = ["android/speech/RecognitionListener"]
        __javacontext__ = "app"

        def __init__(self, sess):
            super().__init__()
            self.s = sess

        @java_method("(Landroid/os/Bundle;)V")
        def onReadyForSpeech(self, params):
            pass

        @java_method("()V")
        def onBeginningOfSpeech(self):
            pass

        @java_method("(F)V")
        def onRmsChanged(self, rms):
            pass

        @java_method("([B)V")
        def onBufferReceived(self, buf):
            pass

        @java_method("()V")
        def onEndOfSpeech(self):
            pass

        @java_method("(I)V")
        def onError(self, code):
            c = int(code)
            Clock.schedule_once(lambda dt: self.s._error(c), 0)

        @java_method("(Landroid/os/Bundle;)V")
        def onResults(self, bundle):
            t = _bundle_texts(bundle)
            Clock.schedule_once(lambda dt: self.s._results(t, True), 0)

        @java_method("(Landroid/os/Bundle;)V")
        def onPartialResults(self, bundle):
            t = _bundle_texts(bundle)
            Clock.schedule_once(lambda dt: self.s._results(t, False), 0)

        @java_method("(ILandroid/os/Bundle;)V")
        def onEvent(self, t, b):
            pass
    _VL["cls"] = _L
    return _L


class VoiceSession:
    """تسجيل مستمر طوال الضغط: إن توقف المتعرّف لحظة أثناء الكلام يعيد تشغيله ويجمع المقاطع."""

    def __init__(self, on_live, on_done, on_err):
        self.on_live, self.on_done, self.on_err = on_live, on_done, on_err
        self.rec = self.listener = None
        self.holding, self.finished = True, False
        self.chunks, self.partial = [], ""
        self.fast_errors, self.last_start = 0, 0
        self._safety = None

    def _ui(self, fn, delay=0):
        from android.runnables import run_on_ui_thread

        def go(dt=None):
            @run_on_ui_thread
            def w():
                try:
                    fn()
                except Exception as e:
                    m = _err_text(e)
                    Clock.schedule_once(lambda d: self._fail("التعرف الصوتي: " + m), 0)
            w()
        if delay:
            Clock.schedule_once(go, delay)
        else:
            go()

    def begin(self):
        from android.permissions import check_permission, request_permissions
        if not check_permission("android.permission.RECORD_AUDIO"):
            request_permissions(["android.permission.RECORD_AUDIO"])
            return self._fail("اسمح للتطبيق باستخدام الميكروفون، ثم اضغط مرة أخرى")
        _KEEP.append(self)
        self._ui(self._create)

    # ---- تعمل على خيط الواجهة في أندرويد ----
    def _create(self):
        from jnius import autoclass
        PA = autoclass("org.kivy.android.PythonActivity")
        SR = autoclass("android.speech.SpeechRecognizer")
        self.rec = SR.createSpeechRecognizer(PA.mActivity)
        self.listener = _listener_class()(self)
        self.rec.setRecognitionListener(self.listener)
        self._listen()

    def _listen(self):
        if self.rec is None or self.finished:
            return
        from jnius import autoclass
        PA = autoclass("org.kivy.android.PythonActivity")
        Intent = autoclass("android.content.Intent")
        RI = autoclass("android.speech.RecognizerIntent")
        try:
            self.rec.cancel()
        except Exception:
            pass
        it = Intent(RI.ACTION_RECOGNIZE_SPEECH)
        it.putExtra(RI.EXTRA_LANGUAGE_MODEL, RI.LANGUAGE_MODEL_FREE_FORM)
        it.putExtra(RI.EXTRA_LANGUAGE, "ar-SA")
        it.putExtra(RI.EXTRA_PARTIAL_RESULTS, True)
        it.putExtra(RI.EXTRA_MAX_RESULTS, 3)
        it.putExtra(RI.EXTRA_CALLING_PACKAGE, PA.mActivity.getPackageName())
        try:                                    # تطويل مهلة الصمت قدر الإمكان (تحترمها بعض الخدمات)
            Long = autoclass("java.lang.Long")
            for k in ("EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS",
                      "EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS"):
                it.putExtra(getattr(RI, k), Long(30000))
        except Exception:
            pass
        self.last_start = time.time()
        self.rec.startListening(it)

    def _destroy(self):
        try:
            if self.rec is not None:
                self.rec.cancel()
                self.rec.destroy()
        except Exception:
            pass
        self.rec = None

    # ---- على خيط كيفي ----
    def _live(self):
        parts = [c[0] for c in self.chunks] + ([self.partial] if self.partial else [])
        self.on_live(" ".join(parts))

    def _results(self, texts, final):
        if self.finished:
            return
        if not final:
            self.partial = texts[0] if texts else ""
            return self._live()
        self.partial = ""
        if texts:
            self.chunks.append(texts)
        self.fast_errors = 0
        self._live()
        if self.holding:
            self._ui(self._listen, 0.12)
        else:
            self._finish()

    def _error(self, code):
        if self.finished:
            return
        if code in (5, 6, 7, 8):                # صمت / لا تطابق / عميل / مشغول
            if not self.holding:
                return self._finish()
            self.fast_errors = self.fast_errors + 1 if time.time() - self.last_start < 0.6 else 0
            if self.fast_errors > 8:
                return self._fail("التعرف الصوتي لا يستجيب. تأكد من تثبيت تطبيق Google ومن الانترنت")
            return self._ui(self._listen, 0.25)
        self._fail(_VOICE_ERR.get(code, "خطأ في التعرف الصوتي (رمز %d)" % code))

    def end(self):
        if self.finished:
            return
        self.holding = False
        self._ui(lambda: self.rec.stopListening() if self.rec is not None else None)
        self._safety = Clock.schedule_once(lambda dt: self._finish(), 2.5)

    def _stop_timers(self):
        if self._safety is not None:
            self._safety.cancel()
        if self in _KEEP:
            _KEEP.remove(self)

    def _finish(self):
        if self.finished:
            return
        self.finished = True
        self._stop_timers()
        self._ui(self._destroy)
        chunks = list(self.chunks)
        if self.partial:
            chunks.append([self.partial])
        if not chunks:
            return self.on_err("لم يُلتقط كلام. اضغط باستمرار على الميكروفون وتكلم ثم اتركه")
        prefix = " ".join(c[0] for c in chunks[:-1])
        cands = []
        for alt in chunks[-1]:
            t = (prefix + " " + alt).strip()
            if t not in cands:
                cands.append(t)
        self.on_done(cands)

    def _fail(self, msg):
        if self.finished:
            return
        self.finished = True
        self._stop_timers()
        try:
            self._ui(self._destroy)
        except Exception:
            pass
        self.on_err(msg)


# ============================================================
# أزرار الأيقونات (ميكروفون / مسح) ولوحة الاستماع
# ============================================================
class IconButton(Widget):
    """زر مرسوم بأيقونة (بدون نص). kind: mic أو scan. active=True يلوّنه بالأحمر."""
    __events__ = ("on_press", "on_release")

    def __init__(self, kind, color, side=None, **kw):
        side = side or dp(52)
        super().__init__(size_hint=(None, None), size=(side, side), **kw)
        self.kind, self.color = kind, color
        self.active = False
        self._down = False
        self.bind(pos=self.redraw, size=self.redraw)
        self.redraw()

    def set_active(self, v):
        self.active = v
        self.redraw()

    def redraw(self, *a):
        self.canvas.clear()
        x, y = self.pos
        w, h = self.size
        lw = max(dp(2), w * 0.045)
        with self.canvas:
            c = (0.86, 0.17, 0.17) if self.active else tuple(self.color[:3])
            Color(c[0], c[1], c[2], 0.8 if self._down else 1)
            RoundedRectangle(pos=self.pos, size=self.size, radius=[w * 0.28])
            Color(1, 1, 1, 1)
            if self.kind == "mic":
                cx = x + w / 2
                RoundedRectangle(pos=(cx - w * 0.12, y + h * 0.44), size=(w * 0.24, h * 0.36),
                                 radius=[w * 0.12])
                pts, r, cy = [], w * 0.22, y + h * 0.52
                for d in range(180, 361, 12):
                    pts += [cx + r * math.cos(math.radians(d)), cy + r * math.sin(math.radians(d))]
                Line(points=pts, width=lw)
                Line(points=[cx, y + h * 0.30, cx, y + h * 0.21], width=lw)
                Line(points=[cx - w * 0.13, y + h * 0.21, cx + w * 0.13, y + h * 0.21], width=lw)
            else:
                L, ins = w * 0.17, w * 0.2
                for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
                    px = x + (w - ins if sx > 0 else ins)
                    py = y + (h - ins if sy > 0 else ins)
                    Line(points=[px - sx * L, py, px, py, px, py - sy * L], width=lw)
                bx, unit = x + w * 0.31, w * 0.38 / 13.0
                for ofs, wd in ((0, 2), (3, 1), (5, 3), (9, 1), (11, 2)):
                    Rectangle(pos=(bx + ofs * unit, y + h * 0.32), size=(wd * unit, h * 0.36))

    def on_press(self):
        pass

    def on_release(self):
        pass

    def on_touch_down(self, t):
        if self.collide_point(*t.pos) and not self._down:
            t.grab(self)
            self._down = True
            self.redraw()
            self.dispatch("on_press")
            return True
        return super().on_touch_down(t)

    def on_touch_up(self, t):
        if t.grab_current is self:
            t.ungrab(self)
            self._down = False
            self.redraw()
            if self.kind == "mic" or self.collide_point(*t.pos):
                self.dispatch("on_release")
            return True
        return super().on_touch_up(t)


class VoiceHold:
    """يربط زر الميكروفون بالتسجيل: اضغط مطولاً وتكلم، واترك الزر عند الانتهاء."""

    def __init__(self, app, on_texts, btn):
        self.app, self.on_texts, self.btn = app, on_texts, btn
        self.sess = None
        self.box = None

    def _show(self):
        self._hide()
        b = CardBox(orientation="vertical", size_hint=(None, None),
                    size=(Window.width - dp(24), dp(132)), bg=T.HEADER,
                    padding=[dp(12), dp(6)])
        b.pos = (dp(12), dp(110))
        b.add_widget(mk_text("جاري الاستماع... اترك الزر عند انتهاء كلامك", dp(14), WHITE,
                             halign="center", height=dp(26)))
        self.lbl = mk_text("", dp(17), WHITE, halign="center", height=dp(48))
        b.add_widget(self.lbl)
        b.add_widget(mk_text("اختم كلامك بكلمة  آجل  أو  نقد", dp(13), WHITE,
                             halign="center", height=dp(24)))
        Window.add_widget(b)
        self.box = b

    def _hide(self):
        if self.box is not None and self.box.parent is not None:
            Window.remove_widget(self.box)
        self.box = None

    def _close(self):
        self._hide()
        self.btn.set_active(False)

    def press(self, *a):
        """ميكروفون الفئة: يفتح نافذة التعرف الصوتي من جوجل."""
        if not VOICE_ENABLED:
            return msg_popup("الإدخال الصوتي", "معطّل في الوقت الحالي", T.ORANGE)
        recognize_speech(self.on_texts, "انطق اسم الفئة")

    def release(self, *a):
        pass

    def _done(self, texts):
        self._close()
        self.on_texts(texts)

    def _err(self, msg):
        vs_log("VOICE: " + msg)
        self._close()
        msg_popup("الإدخال الصوتي", msg + "\n\nجرّب: الإعدادات > فحص الصوت والمسح", T.ORANGE)


_SPEECH_REQ = 7742


def recognize_speech(on_texts, prompt="تكلّم الآن"):
    """يفتح نافذة الاستماع الجاهزة من جوجل ويرجع قائمة النصوص المحتملة إلى on_texts."""
    if platform != "android":
        return msg_popup("الإدخال الصوتي", "متاح على أندرويد فقط", T.ORANGE)
    app_ = App.get_running_app()
    try:
        from jnius import autoclass
        from android import activity
        PA = autoclass("org.kivy.android.PythonActivity")
        Intent = autoclass("android.content.Intent")
        RI = autoclass("android.speech.RecognizerIntent")
        it = Intent(RI.ACTION_RECOGNIZE_SPEECH)
        it.putExtra(RI.EXTRA_LANGUAGE_MODEL, RI.LANGUAGE_MODEL_FREE_FORM)
        it.putExtra(RI.EXTRA_LANGUAGE, "ar-SA")
        it.putExtra(RI.EXTRA_MAX_RESULTS, 3)
        it.putExtra(RI.EXTRA_PROMPT, prompt)

        def on_result(req, res, data):
            if req != _SPEECH_REQ:
                return
            try:
                activity.unbind(on_activity_result=on_result)
            except Exception:
                pass
            texts, err = [], ""
            try:
                if data is not None:
                    lst = data.getStringArrayListExtra(RI.EXTRA_RESULTS)
                    texts = [str(lst.get(i)) for i in range(lst.size())] if lst is not None else []
            except Exception as e:
                err = _err_text(e)
                vs_log("VOICE RESULT: " + err)

            def deliver(dt):
                try:
                    if texts:
                        app_.show_banner("سمعت: " + texts[0][:50])
                        on_texts(texts)
                    elif res != 0:      # 0 = ألغى المستخدم
                        msg_popup("الإدخال الصوتي", "لم يصل نص من جوجل (رمز النتيجة %s)%s\nتأكد أن اللغة العربية "
                                  "مفعّلة في إعدادات الصوت لتطبيق Google" % (res, ("\n" + err) if err else ""),
                                  T.ORANGE)
                except Exception as e:
                    vs_log("VOICE DELIVER: " + _err_text(e))
                    msg_popup("الإدخال الصوتي", "خطأ بعد الاستماع:\n" + _err_text(e), T.RED)
            Clock.schedule_once(deliver, 0)

        activity.bind(on_activity_result=on_result)
        PA.mActivity.startActivityForResult(it, _SPEECH_REQ)
    except Exception as e:
        vs_log("VOICE START: " + _err_text(e))
        msg_popup("الإدخال الصوتي", "تعذّر فتح التعرف الصوتي:\n" + _err_text(e) +
                  "\nتأكد من تثبيت تطبيق Google وتحديثه", T.ORANGE)


def start_voice(app):
    """زر «إضافة فاتورة» في الرئيسية."""
    fn = getattr(app, "voice_handler", None)         # يمكن لملف تحديث أن يستبدله
    if callable(fn):
        return fn()
    if app.need_edit():
        recognize_speech(app._voice_done, "مثال: بعت 3 كراتين بيبسي لأحمد نقد")


def icon_row(app, on_texts, on_scan, h=None):
    """صف أيقونات (ميكروفون + مسح) يظهر على اليمين. يرجع (الصف، متحكم الصوت)."""
    h = h or dp(52)
    row = BoxLayout(size_hint_y=None, height=h, spacing=dp(12))
    mic = IconButton("mic", T.TEAL, side=h)
    scb = IconButton("scan", T.BLUE, side=h)
    vh = VoiceHold(app, on_texts, mic)
    mic.bind(on_press=vh.press, on_release=vh.release)
    scb.bind(on_release=lambda *a: on_scan())
    row.add_widget(Widget())
    row.add_widget(mic)
    row.add_widget(scb)
    return row, vh


def best_parse(texts, db, force_kind=None):
    """يختار أفضل صياغة من بدائل التعرف (الأكثر أصنافاً معروفة)."""
    best, best_sc = None, -1
    for t in texts:
        r = parse_voice_command(t, db, force_kind)
        sc = sum(2 for i in r["items"] if i["product"]) + len(r["items"]) + (1 if r["party"] else 0)
        if sc > best_sc:
            best, best_sc = r, sc
    return best


def product_by_barcode(db, code):
    n = _bc_norm(code)
    if not n:
        return None
    return next((p for p in db["products"] if p.get("barcode") and _bc_norm(p["barcode"]) == n), None)


# ---- بيانات تجريبية ----
def add_demo_data(app):
    db = app.db
    if any(r.get("demo") for c in COLLS for r in db.get(c, [])):
        return False
    wh = db["warehouses"][0]["id"] if db["warehouses"] else None
    cu = [touch({"id": gen_id(), "name": "عميل تجريبي - أحمد", "phone": "", "demo": True}),
          touch({"id": gen_id(), "name": "عميل تجريبي - سعيد", "phone": "", "demo": True})]
    su = touch({"id": gen_id(), "name": "مورد تجريبي", "phone": "", "demo": True})
    specs = [("بيبسي (تجريبي)", [("كرتون", 24, 2400, 3000), ("حبة", 1, 100, 150)], "حبة", 100),
             ("شاهي (تجريبي)", [("كرتون", 12, 3000, 3600), ("حبة", 1, 250, 320)], "حبة", 250),
             ("سكر (تجريبي)", [("كيس", 1, 9000, 10000)], "كيس", 9000)]
    prods, buy_items = [], []
    for name, units, base, pcost in specs:
        us = [{"name": u, "pack": pk, "cost": c, "price": pr} for u, pk, c, pr in units]
        p = touch({"id": gen_id(), "name": name, "units": us, "pcost": pcost, "demo": True})
        prods.append(p)
        u0 = us[0]
        buy_items.append({"unit": u0["name"], "pack": u0["pack"], "qty": 10, "cost": u0["cost"],
                          "price": u0["price"], "pid": p["id"], "name": name})
    db["customers"].extend(cu)
    db["suppliers"].append(su)
    db["products"].extend(prods)
    db["purchases"].append(touch({"id": gen_id(), "sup": su["id"], "wh": wh, "date": now_str(),
                                  "pay": "cash", "items": buy_items, "demo": True}))
    p0, u0 = prods[0], prods[0]["units"][0]
    db["sales"].append(touch({"id": gen_id(), "cust": cu[0]["id"], "wh": wh, "date": now_str(),
                              "pay": "credit", "demo": True,
                              "items": [{"pid": p0["id"], "name": p0["name"], "unit": u0["name"],
                                         "pack": u0["pack"], "qty": 2, "price": u0["price"],
                                         "cost": u0["cost"]}]}))
    app.save()
    return True


def has_demo(db):
    return any(r.get("demo") for c in COLLS for r in db.get(c, []))


def remove_demo_data(app):
    db = app.db
    for c in COLLS:
        for r in [x for x in db.get(c, []) if x.get("demo")]:
            delete_rec(db, c, r["id"])
    app.save()


class OnboardingPage(Page):
    """شرح التطبيق: شرائح كل واحدة هاتف مرسوم (بمكتبة Pillow) عليه شاشة التطبيق والشرح فوقه.
    في آخر شريحة زر «بدء استخدام التطبيق»."""
    _cache = {}

    def __init__(self, app, **kw):
        super().__init__(app, "مرحباً بك", _LOGIN_BLUE, **kw)
        self.step = 0
        app.data["onboarded"] = True
        app.save(auto=False)
        self.render()

    def _slide(self, idx):
        tex = self._cache.get(idx)
        if tex is None:
            import io
            from kivy.core.image import Image as CoreImage
            img = onboarding_slide(idx)
            bio = io.BytesIO()
            img.save(bio, "PNG")
            bio.seek(0)
            tex = (CoreImage(bio, ext="png").texture, img.size)
            self._cache[idx] = tex
        return tex

    def render(self):
        from kivy.uix.image import Image as KImage
        self.body.clear_widgets()
        self.footer.clear_widgets()
        last = len(ONB_SLIDES) - 1
        try:
            tex, (iw, ih) = self._slide(self.step)
            wpx = Window.width - dp(24)
            self.add(KImage(texture=tex, size_hint=(1, None), height=wpx * ih / float(iw)))
        except Exception as e:
            _write_crash("ONBOARDING: " + traceback.format_exc())
            title, intro, legend, _m = ONB_SLIDES[self.step]
            self.add(mk_text(title, dp(22), T.PRIMARY, halign="center", height=dp(48)))
            for ln in wrap_lines(intro, 30):
                self.add(mk_text(ln, dp(15), T.TEXT, halign="center", height=dp(28)))
        dots = "  ".join("●" if i == self.step else "○" for i in range(len(ONB_SLIDES)))
        self.add(mk_text(dots, dp(16), T.BLUE, halign="center", height=dp(30)))
        prev = mk_btn("السابق", height=dp(52), font_size=dp(15), background_color=T.GRAY_BTN)
        prev.bind(on_release=lambda *a: self.go(-1))
        if self.step == last:
            go = mk_btn("بدء استخدام التطبيق", height=dp(52), font_size=dp(17), background_color=T.BLUE)
            go.bind(on_release=lambda *a: self.finish())
            self.add_footer(prev, go)
        else:
            nxt = mk_btn("التالي", height=dp(52), font_size=dp(16), background_color=T.BLUE)
            nxt.bind(on_release=lambda *a: self.go(1))
            skip = mk_btn("تخطي", height=dp(52), font_size=dp(15), background_color=T.GRAY_BTN)
            skip.bind(on_release=lambda *a: self.finish())
            self.add_footer(*((skip, nxt) if self.step == 0 else (prev, nxt)))
        try:
            self.scroll.scroll_y = 1
        except Exception:
            pass
        schedule_refresh(0.1)

    def go(self, d):
        self.step = max(0, min(len(ONB_SLIDES) - 1, self.step + d))
        self.render()

    def finish(self):
        self.app.back()


# ============================================================
# شرائح الشرح: هاتف مرسوم بمكتبة Pillow وعليه شاشات التطبيق الحقيقية، والشرح فوقه
# ============================================================
_OC = dict(PRIMARY=(23, 74, 125), TEXT=(31, 41, 61), MUTED=(117, 130, 150), BG=(242, 245, 250),
           CARD=(255, 255, 255), SOFT_BLUE=(230, 240, 252), INPUT_BG=(251, 252, 254),
           INPUT_BORDER=(204, 212, 224), BORDER=(230, 235, 242), RED=(204, 56, 56),
           INV_RED=(219, 84, 77), GREEN=(33, 153, 92), ORANGE=(235, 140, 31), BLUE=(46, 120, 204),
           GRAY=(115, 120, 133), PURPLE=(128, 84, 184), TEAL=(26, 148, 163), WHITE=(255, 255, 255),
           BADGE=(232, 62, 140))
_OFONTS = {}


def _ofont(px):
    px = max(8, int(round(px)))
    f = _OFONTS.get(px)
    if f is None:
        f = _OFONTS[px] = _pil_font(px)
    return f


class _Mock:
    """يرسم شاشة التطبيق بوحدة dp داخل إطار هاتف (K بكسل لكل dp)."""

    def __init__(self, d, ox, oy, k):
        self.d, self.ox, self.oy, self.k = d, ox, oy, k

    def px(self, x, y):
        return self.ox + x * self.k, self.oy + y * self.k

    def tw(self, s, size):
        try:
            return self.d.textlength(ar(s), font=_ofont(size * self.k)) / self.k     # بوحدة dp
        except Exception:
            return len(str(s)) * size * 0.5

    def rr(self, x, y, w, h, fill=None, r=8, outline=None, ow=1):
        x0, y0 = self.px(x, y)
        self.d.rounded_rectangle([x0, y0, x0 + w * self.k, y0 + h * self.k], radius=r * self.k,
                                 fill=fill, outline=outline, width=max(1, int(ow * self.k * 0.6)))

    def rr_part(self, x, y, w, h, fill, r, corners):
        x0, y0 = self.px(x, y)
        self.d.rounded_rectangle([x0, y0, x0 + w * self.k, y0 + h * self.k], radius=r * self.k,
                                 fill=fill, corners=corners)

    def t(self, x, cy, s, size, fill, anchor="r"):
        """نص عند x (حسب المحاذاة: r يمين، l يسار، c وسط) ومركزه العمودي cy."""
        px, py = self.px(x, cy)
        f = _ofont(size * self.k)
        txt = ar(s)
        try:
            self.d.text((px, py), txt, font=f, fill=fill, anchor={"r": "rm", "l": "lm", "c": "mm"}[anchor])
        except Exception:
            w = self.d.textlength(txt, font=f)
            off = {"r": w, "l": 0, "c": w / 2}[anchor]
            self.d.text((px - off, py - size * self.k * 0.5), txt, font=f, fill=fill)

    def fit(self, s, size, maxw):
        s = str(s)
        while len(s) > 1 and self.tw(s, size) > maxw:
            s = s[:-1]
        return s

    def btn(self, x, y, w, h, text, color, size=14, r=10, tcol=None):
        self.rr(x, y, w, h, color, r)
        for i, ln in enumerate(str(text).split("\n")):
            n = len(str(text).split("\n"))
            self.t(x + w / 2, y + h / 2 + (i - (n - 1) / 2.0) * (size * 1.25), ln, size,
                   tcol or _OC["WHITE"], "c")

    def picker(self, x, y, w, h, text, size=13):
        self.rr(x, y, w, h, _OC["SOFT_BLUE"], 10)
        self.t(x + w / 2, y + h / 2, self.fit(text, size, w - 8), size, _OC["PRIMARY"], "c")

    def inp(self, x, y, w, h, text="", hint="", size=14):
        self.rr(x, y, w, h, _OC["INPUT_BG"], 8, _OC["INPUT_BORDER"])
        if text:
            self.t(x + w - 8, y + h / 2, self.fit(text, size, w - 14), size, _OC["TEXT"], "r")
        elif hint:
            self.t(x + w - 8, y + h / 2, self.fit(hint, size - 1, w - 14), size - 1, (158, 166, 179), "r")

    def labin(self, x, y, w, label, text="", hint=""):
        self.t(x + w - 2, y + 8, label, 11, _OC["MUTED"], "r")
        self.inp(x, y + 18, w, 38, text, hint)

    def card(self, x, y, w, h, fill=None, r=12):
        self.rr(x, y, w, h, fill or _OC["CARD"], r, _OC["BORDER"])

    def radio(self, xr, cy, text, sel, size=15):
        dx, dy = self.px(xr - 11, cy)
        rad = 9 * self.k
        self.d.ellipse([dx - rad, dy - rad, dx + rad, dy + rad], outline=_OC["PRIMARY"],
                       width=max(2, int(1.6 * self.k)))
        if sel:
            q = rad * 0.58
            self.d.ellipse([dx - q, dy - q, dx + q, dy + q], fill=_OC["PRIMARY"])
        self.t(xr - 26, cy, text, size, _OC["TEXT"], "r")

    def header(self, title, color, back=True):
        self.rr_part(0, 0, 360, 58, color, 20, (False, False, True, True))
        self.t(180, 29, title, 19, _OC["WHITE"], "c")
        if back:
            self.rr(7, 10, 76, 38, tuple(min(255, int(c * 0.78 + 255 * 0.22)) for c in color), 10)
            self.t(45, 29, "رجوع", 14, _OC["WHITE"], "c")

    # ---- الأيقونات ----
    def icon_box(self, x, y, s, color):
        self.rr(x, y, s, s, color, s * 0.28)

    def icon_scan(self, x, y, s=40, color=None):
        self.icon_box(x, y, s, color or _OC["BLUE"])
        L, ins = s * 0.17, s * 0.2
        W = _OC["WHITE"]
        for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
            cx = x + (s - ins if sx > 0 else ins)
            cy = y + (s - ins if sy > 0 else ins)
            a, b, c = self.px(cx - sx * L, cy), self.px(cx, cy), self.px(cx, cy - sy * L)
            self.d.line([a, b, c], fill=W, width=max(2, int(1.8 * self.k)))
        bx, unit = x + s * 0.31, s * 0.38 / 13.0
        for ofs, wd in ((0, 2), (3, 1), (5, 3), (9, 1), (11, 2)):
            p0 = self.px(bx + ofs * unit, y + s * 0.32)
            p1 = self.px(bx + (ofs + wd) * unit, y + s * 0.68)
            self.d.rectangle([p0[0], p0[1], p1[0], p1[1]], fill=W)

    def icon_mic(self, x, y, s=40, color=None):
        self.icon_box(x, y, s, color or _OC["TEAL"])
        W, cx = _OC["WHITE"], x + s / 2
        self.rr(cx - s * 0.12, y + s * 0.2, s * 0.24, s * 0.36, W, s * 0.12)
        p0, p1 = self.px(cx - s * 0.22, y + s * 0.5), self.px(cx + s * 0.22, y + s * 0.78)
        self.d.arc([p0[0], p0[1] - s * 0.2 * self.k, p1[0], p1[1] - s * 0.1 * self.k], 0, 180, fill=W,
                   width=max(2, int(1.8 * self.k)))
        a, b = self.px(cx, y + s * 0.72), self.px(cx, y + s * 0.8)
        self.d.line([a, b], fill=W, width=max(2, int(1.8 * self.k)))

    def icon_calc(self, x, y, w=46, h=38):
        self.rr(x, y, w, h, _OC["TEAL"], 10)
        bw, bh = 22, min(30, h - 6)
        x0, y0 = x + w / 2 - bw / 2, y + h / 2 - bh / 2
        self.rr(x0, y0, bw, bh, _OC["WHITE"], 4)
        pad = 3
        ks = (bw - 2 * pad - 4) / 3.0
        sh = bh * 0.3
        self.rr(x0 + pad, y0 + pad, bw - 2 * pad, sh, _OC["TEAL"], 1.5)
        top = y0 + pad + sh + 2
        kh = (y0 + bh - pad - top - 4) / 3.0
        for r_ in range(3):
            for c_ in range(3):
                col = _OC["ORANGE"] if (c_ == 2 and r_ < 2) else _OC["TEAL"]
                self.rr(x0 + pad + c_ * (ks + 2), top + r_ * (kh + 2), ks, kh, col, 1)

    def moon(self, x, y):
        cx, cy = self.px(x, y)
        r = 8 * self.k
        self.d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_OC["WHITE"])
        self.d.ellipse([cx - r * 0.3, cy - r * 1.25, cx + r * 1.5, cy + r * 0.75], fill=(79, 109, 146))

    def badge(self, x, y, n):
        """رقم مرجعي يطابق الشرح في الأعلى."""
        cx, cy = self.px(x, y)
        r = 12 * self.k
        self.d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_OC["BADGE"], outline=_OC["WHITE"],
                       width=max(2, int(1.5 * self.k)))
        self.d.text((cx, cy), str(n), font=_ofont(14 * self.k), fill=_OC["WHITE"], anchor="mm")

    def op_row(self, y, amount, title, date, col):
        self.card(12, y, 336, 64)
        self.t(22, y + 32, amount, 16, col, "l")
        self.t(338, y + 22, title, 15, _OC["TEXT"], "r")
        self.t(338, y + 44, date, 12, _OC["MUTED"], "r")

    def line_row(self, y, total, name, sub):
        self.card(12, y, 336, 62)
        self.rr(22, y + 12, 38, 38, _OC["RED"], 10)
        self.t(41, y + 31, "X", 15, _OC["WHITE"], "c")
        self.btn(66, y + 12, 58, 38, "تعديل", _OC["BLUE"], 13)
        self.t(132, y + 31, total, 16, _OC["PRIMARY"], "l")
        self.t(338, y + 22, name, 15, _OC["TEXT"], "r")
        self.t(338, y + 44, sub, 12, _OC["MUTED"], "r")


# ---------- شاشات التطبيق ----------
def _mock_home(m):
    O = _OC
    m.rr_part(0, 0, 360, 60, O["PRIMARY"], 20, (False, False, True, True))
    m.t(180, 30, "مرحباً أحمد", 17, O["WHITE"], "c")
    m.btn(262, 11, 76, 38, "مزامنة", (79, 109, 146), 14)                  # مزامنة
    m.rr(222, 12, 34, 34, (79, 109, 146), 17)                              # الجرس
    m.t(239, 29, "!", 16, O["WHITE"], "c")
    for i in range(3):                                                     # القائمة ☰
        m.rr(10, 17 + i * 8, 24, 3.5, O["WHITE"], 1.7)
    m.rr(44, 12, 34, 34, (79, 109, 146), 17)                               # الوضع الليلي
    m.moon(61, 29)
    rows = [[("فاتورة بيع", O["ORANGE"]), ("سند قبض / صرف", O["GREEN"]), ("فاتورة شراء", O["BLUE"])],
            [("العملاء", O["PURPLE"]), ("الموردين", O["TEAL"]), ("العمال", O["GRAY"])]]
    y = 68
    for row in rows:                                                       # الأول يظهر على اليمين
        w = (336 - 12) / 3.0
        for i, (txt, col) in enumerate(row):
            m.btn(12 + (2 - i) * (w + 6), y, w, 54, txt, col, 14)
        y += 60
    w2 = (336 - 6) / 2.0
    m.btn(12 + w2 + 6, y, w2, 54, "التقارير", O["PRIMARY"], 14)
    m.btn(12, y, w2, 54, "المخازن والصندوق", O["INV_RED"], 14)
    y += 60
    m.rr(12, y, 336, 52, O["TEAL"], 10)                                    # زر الصوت
    m.icon_mic(18, y + 6, 40, (15, 89, 98))
    m.t(210, y + 26, "إضافة فاتورة", 16, O["WHITE"], "c")
    y += 62
    for i, (a, b) in enumerate((("ديون العملاء: 4,500 ريال", "الصندوق: 125,000 ريال"),
                                ("الفواتير المتبقية: غير محدودة", "حق الموردين: 2,400 ريال"))):
        m.t(348, y + 14 + i * 29, a, 13, (O["RED"], O["PRIMARY"])[i], "r")
        m.t(12, y + 14 + i * 29, b, 13, (O["GREEN"], O["ORANGE"])[i], "l")
    y += 66
    m.t(348, y + 8, "الفواتير والسندات (3)", 15, O["PRIMARY"], "r")
    m.op_row(y + 24, "3,000", "فاتورة (نقد) - أحمد علي", "2026-11-02 10:15:00", O["ORANGE"])
    m.op_row(y + 94, "2,400", "فاتورة شراء (نقد) - مورد", "2026-11-02 09:40:00", O["BLUE"])
    return {1: (338, 75), 2: (338, 135), 3: (22, 254), 4: (22, 372)}


def _mock_sale(m):
    O = _OC
    m.header("إضافة فاتورة", O["ORANGE"])
    m.card(12, 64, 336, 282)
    x0, w = 22, 316
    for i, (txt, sel) in enumerate((("نقد", True), ("آجل", False), ("مرتجع", False))):
        m.radio(x0 + 208 - i * 69, 86, txt, sel, 15)
    m.picker(x0 + 214, 70, 102, 40, "اختر العميل", 14)
    m.icon_scan(x0, 114, 40)
    m.picker(68, 114, 128, 40, "بيبسي", 13)
    m.picker(202, 114, 136, 40, "المخزن: الرئيسي", 12)
    m.picker(x0, 158, w, 38, "الفئة: كرتون (24 حبة)", 13)
    m.t(338, 211, "المتوفر: 120 حبة", 12, O["GREEN"], "r")
    m.t(180, 211, "سعر الشراء: 2,400", 12, O["MUTED"], "r")
    m.icon_calc(x0, 240, 46, 38)
    m.labin(74, 222, 129, "سعر البيع", "3,000")
    m.labin(209, 222, 129, "العدد", "1")
    m.btn(x0, 286, 96, 40, "+ إضافة", O["BLUE"], 14)
    m.inp(124, 286, 214, 40, "", "ملاحظة (اختياري)")
    m.t(348, 360, "أصناف الفاتورة", 14, O["MUTED"], "r")
    m.line_row(376, "3,000", "بيبسي", "1 كرتون x 3,000")
    m.t(180, 462, "الإجمالي: 3,000 ريال", 18, O["PRIMARY"], "c")
    m.btn(12, 534, 336, 52, "حفظ الفاتورة", O["GREEN"], 17)
    return {1: (258, 72), 2: (28, 118), 3: (28, 244), 4: (348, 540)}


def _mock_purchase(m):
    O = _OC
    m.header("إضافة فاتورة شراء", O["BLUE"])
    m.card(12, 64, 336, 340)
    x0 = 22
    for i, (txt, sel) in enumerate((("نقد", True), ("آجل", False))):
        m.radio(x0 + 175 - i * 80, 86, txt, sel, 20)
    m.picker(x0 + 181, 70, 135, 40, "اختر المورد", 14)
    m.picker(x0, 114, 172, 40, "بيبسي", 13)
    m.picker(x0 + 178, 114, 138, 40, "المخزن: الرئيسي", 12)
    m.inp(x0, 158, 270, 40, "6281001234567", "الباركود", 14)
    m.icon_scan(x0 + 276, 158, 40)
    m.icon_mic(x0, 202, 40)
    m.picker(x0 + 46, 202, 270, 40, "الفئة: كرتون", 13)
    m.inp(x0, 246, 90, 36, "24", "حبات/فئة", 13)
    m.t(338, 264, "المتوفر: 5 كرتون (120 حبة)", 13, O["GREEN"], "r")
    cw = (316 - 12) / 3.0
    m.labin(x0, 288, cw, "العدد المشترى", "10")
    m.icon_calc(x0 + cw + 6, 306, 42, 38)
    m.labin(x0 + cw + 6 + 46, 288, cw - 46, "سعر الشراء", "2400")
    m.labin(x0 + 2 * (cw + 6), 288, cw, "سعر البيع", "3000")
    m.btn(x0, 354, 316, 40, "حفظ الصنف", O["PURPLE"], 15)
    m.t(348, 420, "أصناف الفاتورة", 14, O["MUTED"], "r")
    m.line_row(436, "24,000", "بيبسي", "10 كرتون x 2,400  -  بيع 3,000")
    m.t(180, 520, "الإجمالي: 24,000 ريال", 18, O["PRIMARY"], "c")
    m.btn(12, 548, 336, 46, "حفظ الفاتورة", O["GREEN"], 17)
    return {1: (x0 + 316, 162), 2: (x0 + 316, 206), 3: (x0 + cw + 12, 296), 4: (338, 358)}


def _mock_party(m):
    O = _OC
    m.header("بيانات العميل", O["PRIMARY"])
    m.rr(12, 66, 336, 80, O["PRIMARY"], 14)
    m.t(338, 92, "أحمد علي", 21, O["WHITE"], "r")
    m.t(338, 124, "الرقم: 777123456", 13, (225, 232, 241), "r")
    m.btn(22, 74, 96, 30, "تعديل", (79, 109, 146), 14, 8)
    m.btn(22, 110, 96, 30, "حذف", O["RED"], 14, 8)
    w = (336 - 12) / 3.0
    for i, (lab, val, col) in enumerate((("إجمالي مشترياته", "7,500", O["PRIMARY"]),
                                         ("إجمالي ما سدّده", "3,000", O["GREEN"]),
                                         ("المتبقي عليه", "4,500", O["RED"]))):
        x = 12 + (2 - i) * (w + 6)
        m.card(x, 154, w, 56, None, 10)
        m.t(x + w / 2, 170, lab, 11, O["MUTED"], "c")
        m.t(x + w / 2, 194, val, 15, col, "c")
    for i, (txt, col) in enumerate((("إرسال\nكشف الحساب", O["BLUE"]), ("إضافة\nفاتورة", O["ORANGE"]),
                                    ("إضافة\nسند قبض", O["GREEN"]))):
        m.btn(12 + (2 - i) * (w + 6), 218, w, 58, txt, col, 14)
    m.t(348, 296, "الفواتير والسندات (3)", 15, O["PRIMARY"], "r")
    m.op_row(312, "-500", "مرتجع", "2026-11-02 12:00:00", O["GREEN"])
    m.op_row(382, "3,000", "سند قبض", "2026-11-01 16:20:00", O["GREEN"])
    m.op_row(452, "8,000", "فاتورة (آجل)", "2026-10-30 11:05:00", O["RED"])
    return {1: (338, 158), 2: (222, 222), 3: (338, 222), 4: (338, 316)}


def _mock_wallet(m):
    O = _OC
    m.header("المخازن والصندوق", O["PRIMARY"])
    m.btn(12, 64, 165, 44, "المخازن", O["GRAY"], 15)
    m.btn(183, 64, 165, 44, "الصندوق", O["PRIMARY"], 15)
    m.rr(12, 118, 336, 96, O["PRIMARY"], 14)
    m.t(334, 140, "رصيد الصندوق", 13, (225, 232, 241), "r")
    m.t(334, 180, "125,000 ريال", 26, (255, 237, 166), "r")
    m.btn(12, 224, 164, 50, "سحب", O["RED"], 15)
    m.btn(184, 224, 164, 50, "إيداع", O["GREEN"], 15)
    m.t(348, 296, "حركة الصندوق", 15, O["PRIMARY"], "r")
    for i, (a, b, c, col) in enumerate((("3,000", "فاتورة نقد - أحمد علي", "2026-11-02", O["GREEN"]),
                                        ("-2,400", "شراء نقد - مورد", "2026-11-02", O["RED"]),
                                        ("5,000", "سند قبض - سعيد", "2026-11-01", O["GREEN"]),
                                        ("100,000", "إيداع", "2026-10-30", O["GREEN"]))):
        m.op_row(312 + i * 70, a, b, c, col)
    return {1: (22, 68), 2: (338, 124), 3: (22, 228), 4: (338, 228)}


def _mock_reports(m):
    O = _OC
    m.header("التقارير", O["PRIMARY"])
    m.t(348, 80, "اختر الفترة", 14, O["PRIMARY"], "r")
    w = (336 - 12) / 3.0
    for i, txt in enumerate(("شامل", "اليوم", "أمس", "7 أيام", "30 يوم", "هذا الشهر")):
        m.btn(12 + (i % 3) * (w + 6), 92 + (i // 3) * 50, w, 44, txt, O["BLUE"], 14)
    m.labin(12 + 0, 196, 165, "إلى تاريخ", "", "2026-12-31")
    m.labin(183, 196, 165, "من تاريخ", "", "2026-01-01")
    m.btn(12, 262, 336, 48, "عرض التقرير للفترة", O["GREEN"], 16)
    m.btn(12, 318, 165, 46, "تحميل PDF للجهاز", O["TEAL"], 14)
    m.btn(183, 318, 165, 46, "إرسال PDF", O["INV_RED"], 14)
    m.card(12, 376, 336, 190)
    m.t(338, 394, "المبيعات والمشتريات والربح", 14, O["PRIMARY"], "r")
    bars = (("المبيعات", 120, O["BLUE"]), ("المشتريات", 84, O["ORANGE"]), ("الربح", 36, O["GREEN"]),
            ("قبض", 60, O["PURPLE"]), ("صرف", 24, O["TEAL"]))
    for i, (lab, hh, col) in enumerate(bars):
        x = 30 + i * 62
        m.rr(x, 540 - hh - 18, 36, hh, col, 5)
        m.t(x + 18, 548, lab, 10, O["MUTED"], "c")
    return {1: (22, 96), 2: (338, 266), 3: (338, 322), 4: (22, 380)}


def _mock_menu(m):
    O = _OC
    m.rr_part(0, 0, 360, 60, O["PRIMARY"], 20, (False, False, True, True))
    m.t(180, 30, "مرحباً أحمد", 17, O["WHITE"], "c")
    m.btn(262, 11, 76, 38, "مزامنة", (79, 109, 146), 14)
    m.rr(222, 12, 34, 34, (79, 109, 146), 17)
    m.t(239, 29, "!", 16, O["WHITE"], "c")
    for i in range(3):
        m.rr(10, 17 + i * 8, 24, 3.5, O["WHITE"], 1.7)
    m.rr(44, 12, 34, 34, (79, 109, 146), 17)
    m.moon(61, 29)
    for i in range(4):
        m.card(12, 76 + i * 70, 336, 60)
    pw = 200
    m.rr_part(0, 60, pw, 540, O["CARD"], 22, (False, True, True, False))
    m.t(pw / 2, 92, "القائمة", 18, O["PRIMARY"], "c")
    items = ["بحث", "التقارير", "تغيير اسم الدكان", "تحميل الخطوط", "التحديثات", "تواصل معنا واتساب",
             "إشعارات الخلفية: تعمل", "عرض الترحيب"]
    for i, it in enumerate(items):
        y = 118 + i * 46
        m.rr(10, y, pw - 20, 38, O["SOFT_BLUE"], 10)
        m.t(pw / 2, y + 19, m.fit(it, 13, pw - 30), 13, O["PRIMARY"], "c")
    m.t(pw / 2, 566, "الإعدادات", 15, O["PRIMARY"], "c")
    return {1: (22, 20), 2: (232, 22), 3: (338, 22), 4: (80, 20)}


# العنوان، المقدمة، (الشرح المرقّم)، لون الأيقونة، دالة الرسم
ONB_SLIDES = [
    ("الشاشة الرئيسية", "كل ما تحتاجه في شاشة واحدة وبضغطة واحدة.",
     ["أزرار فاتورة بيع وفاتورة شراء وسند قبض / صرف.",
      "العملاء والموردين والعمال، ثم التقارير والمخازن والصندوق.",
      "زر 'إضافة فاتورة' بالصوت: انطق الفاتورة فيجهزها التطبيق لك.",
      "ملخص الديون والصندوق وآخر الفواتير والسندات."], _mock_home),
    ("فاتورة البيع", "اختر العميل ثم أضف الأصناف واحداً بعد الآخر.",
     ["اختر العميل وطريقة الدفع: نقد أو آجل أو مرتجع. المرتجع يعيد البضاعة للمخزن ويخصم من حساب العميل.",
      "زر المسح يقرأ باركود الصنف بالكاميرا ويضيفه فوراً.",
      "الآلة الحاسبة: احسب ثم اضغط 'حفظ' ليظهر الناتج في سعر البيع.",
      "اضغط '+ إضافة' لكل صنف، ثم 'حفظ الفاتورة' في الأسفل."], _mock_sale),
    ("فاتورة الشراء", "سجّل مشترياتك وأسعار الشراء والبيع لكل فئة.",
     ["الباركود: اكتبه أو امسحه بالكاميرا ويظهر رقمه في الحقل.",
      "الفئة: اختر فئة موجودة أو أضف جديدة، والميكروفون بجانبها. تحتها العدد المتوفر.",
      "سعر الشراء وبجانبه الآلة الحاسبة، وناتجها يُحفظ فيه.",
      "اضغط 'حفظ الصنف' لكل صنف، ثم 'حفظ الفاتورة'."], _mock_purchase),
    ("العملاء والموردين", "لكل عميل ومورد حساب مستقل وكشف حساب.",
     ["ملخص الحساب: ما أخذه، وما سدّده، والمتبقي عليه.",
      "إضافة فاتورة أو سند قبض / صرف مباشرة من صفحته.",
      "إرسال كشف الحساب بصورة عبر واتساب.",
      "المرتجع يظهر في حسابه ويُنقص ما عليه."], _mock_party),
    ("المخازن والصندوق", "تابع نقدك وبضاعتك لحظة بلحظة.",
     ["تبويب 'المخازن' لمعرفة المتوفر من كل صنف.",
      "رصيد الصندوق يتحدث تلقائياً مع فواتير النقد والسندات.",
      "سحب وإيداع يدوي عند الحاجة.",
      "سجل حركات الصندوق مرتباً بالتاريخ."], _mock_wallet),
    ("التقارير", "مبيعات ومشتريات وأرباح لأي فترة تختارها.",
     ["اختر فترة جاهزة (اليوم، 7 أيام، الشهر...).",
      "أو اكتب تاريخ البداية والنهاية ثم 'عرض التقرير للفترة'.",
      "حمّل التقرير PDF أو أرسله.",
      "رسوم بيانية تبيّن المبيعات والربح والديون."], _mock_reports),
    ("القائمة والإعدادات", "كل ما تبقى في القائمة الجانبية.",
     ["أيقونة القائمة (ثلاث خطوط) تفتح القائمة: بحث، تقارير، اسم الدكان، الخطوط، التحديثات.",
      "الجرس للإشعارات والتنبيهات.",
      "المزامنة تحفظ بياناتك وتشاركها بين أجهزتك.",
      "زر الوضع الليلي لتغيير مظهر التطبيق."], _mock_menu),
]


def _wrap_px(d, text, f, maxw):
    words, lines, cur = str(text).split(), [], ""
    for w_ in words:
        t = (cur + " " + w_).strip()
        if d.textlength(ar(t), font=f) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w_
    if cur:
        lines.append(cur)
    return lines


def onboarding_slide(idx):
    """يرسم الشريحة idx ويرجع صورة Pillow: العنوان والشرح فوق، وهاتف عليه شاشة التطبيق تحتها."""
    from PIL import Image as PImage, ImageDraw
    title, intro, legend, mock = ONB_SLIDES[idx]
    W, SC = 900, 4
    O = _OC
    probe = ImageDraw.Draw(PImage.new("RGB", (10, 10)))
    f_t, f_i, f_l = _ofont(60), _ofont(33), _ofont(31)
    intro_l = _wrap_px(probe, intro, f_i, W - 120)
    leg_l = [_wrap_px(probe, s, f_l, W - 230) for s in legend]
    top_h = 120 + 66 + 56 + len(intro_l) * 46 + 24 + sum(len(x) * 42 + 22 for x in leg_l) + 30
    pw, sw = 770, 734
    K = sw / 360.0
    sh = 600 * K
    ph_h = int(sh + 40)
    H = int(top_h + ph_h + 60)
    img = PImage.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    # ---- النص فوق ----
    NB = (26, 69, 204)
    band = PImage.new("RGB", (W, 214), NB)
    bd_ = ImageDraw.Draw(band)
    bd_.ellipse([-120, 60, 200, 380], fill=(40, 88, 214))
    bd_.ellipse([W - 190, -90, W + 110, 210], fill=(40, 88, 214))
    bm_ = PImage.new("L", (W, 214), 0)
    ImageDraw.Draw(bm_).rounded_rectangle([0, -60, W - 1, 213], radius=56, fill=255)
    img.paste(band, (0, 0), bm_)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([W / 2 - 70, 28, W / 2 + 70, 76], radius=24, fill=(255, 255, 255))
    d.text((W / 2, 52), "%d / %d" % (idx + 1, len(ONB_SLIDES)), font=_ofont(28), fill=NB, anchor="mm")
    y = 98
    tw_ = d.textlength(ar(title), font=f_t)
    d.text((W / 2 - tw_ / 2, y), ar(title), font=f_t, fill=(255, 255, 255))
    y += 82 + 56
    for ln in intro_l:
        w_ = d.textlength(ar(ln), font=f_i)
        d.text((W / 2 - w_ / 2, y), ar(ln), font=f_i, fill=O["MUTED"])
        y += 46
    y += 24
    for i, lines in enumerate(leg_l):
        cy = y + (len(lines) * 42) / 2
        d.ellipse([W - 96, cy - 24, W - 48, cy + 24], fill=O["BADGE"])
        d.text((W - 72, cy), str(i + 1), font=_ofont(30), fill=O["WHITE"], anchor="mm")
        for ln in lines:
            d.text((W - 112 - d.textlength(ar(ln), font=f_l), y), ar(ln), font=f_l, fill=O["TEXT"])
            y += 42
        y += 22
    # ---- الهاتف ----
    px0, py0 = (W - pw) // 2, int(top_h)
    d.rounded_rectangle([px0 + 8, py0 + 10, px0 + pw + 8, py0 + ph_h + 10], radius=84, fill=(226, 230, 237))
    d.rounded_rectangle([px0, py0, px0 + pw, py0 + ph_h], radius=84, fill=(24, 30, 44))
    sx, sy = px0 + (pw - sw) // 2, py0 + 20
    scr = PImage.new("RGB", (sw, int(sh)), O["BG"])
    # الشاشة تُرسم على صورة مستقلة ثم تُقصّ بحواف مدوّرة
    sd = ImageDraw.Draw(scr)
    m = _Mock(sd, 0, 0, K)
    badges = mock(m)
    if idx == 6:                                    # تعتيم خلف القائمة الجانبية
        ov = PImage.new("RGBA", scr.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        od.rectangle([200 * K, 60 * K, sw, sh], fill=(0, 0, 0, 90))
        scr = PImage.alpha_composite(scr.convert("RGBA"), ov).convert("RGB")
        sd = ImageDraw.Draw(scr)
        m = _Mock(sd, 0, 0, K)
    for n, (bx, by) in badges.items():
        m.badge(bx, by, n)
    mask = PImage.new("L", scr.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, scr.size[0] - 1, scr.size[1] - 1], radius=64, fill=255)
    img.paste(scr, (sx, sy), mask)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([W / 2 - 60, py0 + 7, W / 2 + 60, py0 + 15], radius=4, fill=(60, 68, 86))
    return img


# ---- إشعارات الخلفية الحقيقية (خدمة أندرويد) ----
def write_notifier_cfg(app):
    try:
        cfg = {"worker_url": WORKER_URL, "session_token": SESSION_TOKEN,
               "email": ((app.data.get("user") or {}).get("email") or "").strip().lower(),
               "last": int((app.data.get("dl") or {}).get("last", 0)),
               "enabled": app.data.get("push", True) is not False,
               "poll": 60, "alive": os.path.join(DATA_DIR, "app_alive.txt")}
        save_json(os.path.join(DATA_DIR, "notifier_cfg.json"), cfg)
        return True
    except Exception as e:
        print(f"[NOTIFIER CFG] {e}")
        return False


def start_notifier(app):
    """يطلب صلاحية الإشعارات (أندرويد 13+) ثم يشغّل خدمة الفحص الدوري."""
    if platform != "android":
        return
    if not write_notifier_cfg(app) or app.data.get("push", True) is False:
        return
    try:
        from android.permissions import request_permissions
        request_permissions(["android.permission.POST_NOTIFICATIONS"])
    except Exception as e:
        print(f"[NOTIFIER PERM] {e}")
    try:
        from jnius import autoclass
        PA = autoclass("org.kivy.android.PythonActivity")
        act = PA.mActivity
        svc = autoclass(act.getPackageName() + ".ServiceNotifier")
        svc.start(act, "")
    except Exception as e:
        print(f"[NOTIFIER START] {e}")


def stop_notifier():
    if platform != "android":
        return
    try:
        from jnius import autoclass
        PA = autoclass("org.kivy.android.PythonActivity")
        act = PA.mActivity
        autoclass(act.getPackageName() + ".ServiceNotifier").stop(act)
    except Exception as e:
        print(f"[NOTIFIER STOP] {e}")




# <<SUGGEST_PURE>>
_QN_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"})


def _qn(s):
    """تطبيع للمطابقة بدون تغيير الأطوال (حرف مقابل حرف) ليبقى تحديد المكتوب دقيقاً."""
    return str(s or "").translate(_AR_DIGITS).translate(_QN_MAP).lower()


def best_suggestion(query, labels, norm=None):
    """أقرب اسم واحد لما كتبه المستخدم. يرجع (الفهرس، (بداية، نهاية) الجزء المكتوب) أو None.
    الترتيب: مطابق تماماً ← يبدأ بما كتب (أبجدياً) ← كلمة تبدأ بما كتب ← يحتويه ← أقرب شبهاً."""
    q = _qn(query).strip()
    if not q:
        return None
    norm = norm or [_qn(x) for x in labels]
    exact = prefix = word = contains = None
    for i, n in enumerate(norm):
        if n == q:
            return i, (0, len(q))
        if n.startswith(q):
            if prefix is None or n < norm[prefix]:
                prefix = i
            continue
        p = n.find(" " + q)
        if p >= 0:
            if word is None or (p, n) < (word[1], norm[word[0]]):
                word = (i, p, p + 1)
            continue
        p = n.find(q)
        if p >= 0 and (contains is None or (p, n) < (contains[1], norm[contains[0]])):
            contains = (i, p)
    if prefix is not None:
        return prefix, (0, len(q))
    if word is not None:
        return word[0], (word[1] + 1, word[1] + 1 + len(q))
    if contains is not None:
        return contains[0], (contains[1], contains[1] + len(q))
    if len(q) >= 2 and len(norm) <= 4000:
        import difflib
        m = difflib.get_close_matches(q, norm, n=1, cutoff=0.6)
        if m:
            return norm.index(m[0]), None            # شبيه لكن لا يحوي ما كُتب: كله أخضر
    return None


def _shape_word_idx(word, base):
    """مثل shape_word لكن يحتفظ برقم الحرف الأصلي لكل شكل. يرجع [(شكل، فهرس)] بترتيب العرض."""
    pairs, idxs, i = [], [], 0
    while i < len(word):
        if i + 1 < len(word) and word[i:i + 2] in LAM_ALEF:
            pairs.append(("PAIR", word[i:i + 2]))
            idxs.append(base + i)
            i += 2
            continue
        pairs.append(("C", word[i]))
        idxs.append(base + i)
        i += 1
    res = []
    for k, (kind, value) in enumerate(pairs):
        if kind == "PAIR":
            iso, fin = LAM_ALEF[value]
            res.append((fin if _prev_element_connects(pairs, k) else iso, idxs[k]))
            continue
        ch = value
        if ch in DIACRITICS or ch not in ARABIC_FORMS:
            res.append((ch, idxs[k]))
            continue
        isolated, initial, medial, final, fwd = ARABIC_FORMS[ch]
        prev_c = _prev_element_connects(pairs, k)
        nxt = _has_element_after(pairs, k)
        if prev_c and fwd and nxt:
            res.append((medial, idxs[k]))
        elif prev_c:
            res.append((final, idxs[k]))
        elif fwd and nxt:
            res.append((initial, idxs[k]))
        else:
            res.append((isolated, idxs[k]))
    res.reverse()
    return res


def ar_pieces(text):
    """مثل ar() تماماً لكن يرجع [(حرف معروض، رقم الحرف الأصلي)] لتلوين جزء دون آخر."""
    text = str(text or "")
    segments, current, cur_ar, cur_start = [], [], None, 0
    for pos, ch in enumerate(text):
        if ch.isspace() or ch in _BRACKETS:
            if current:
                segments.append((cur_ar, "".join(current), cur_start))
                current, cur_ar = [], None
            segments.append((None, ch, pos))
        else:
            a = is_arabic_char(ch)
            if cur_ar is None:
                cur_ar, cur_start = a, pos
                current.append(ch)
            elif a == cur_ar:
                current.append(ch)
            else:
                segments.append((cur_ar, "".join(current), cur_start))
                current, cur_ar, cur_start = [ch], a, pos
    if current:
        segments.append((cur_ar, "".join(current), cur_start))
    has_ar = any(a is True for a, _s, _p in segments)
    out = []
    for a, s, st in segments:
        if a is True:
            out.append(_shape_word_idx(s, st))
        elif a is None and s in _BRACKETS and has_ar:
            out.append([(s.translate(_MIRROR), st)])
        else:
            out.append([(c, st + k) for k, c in enumerate(s)])
    if has_ar:
        out.reverse()
    return [p for seg in out for p in seg]


def suggestion_markup(label, span, normal_hex, green_hex):
    """نص Kivy markup: الجزء المكتوب بلون النص العادي والباقي أخضر."""
    from kivy.utils import escape_markup
    typed = set(range(span[0], span[1])) if span else set()
    parts, cur, buf = [], None, []

    def flush():
        if buf:
            parts.append("[color=%s]%s[/color]" % (cur, escape_markup("".join(buf))))
            buf.clear()
    for g, idx in ar_pieces(label):
        col = normal_hex if idx in typed else green_hex
        if col != cur:
            flush()
            cur = col
        buf.append(g)
    flush()
    return "".join(parts)
# <<END_SUGGEST_PURE>>


from kivy.uix.popup import Popup as _KPopup
from kivy.uix.label import Label as _KLabel
from kivy.utils import get_hex_from_color

_pick_popup_full = pick_popup                 # القائمة الكاملة القديمة (زر «عرض كل القائمة»)
_LAST_TOUCH = {"pos": None, "t": 0.0}


def _track_touch(win, touch):
    """نتذكر مكان آخر لمسة لنضع مربع الاختيار تحت الحقل الذي ضُغط عليه."""
    try:
        _LAST_TOUCH["pos"], _LAST_TOUCH["t"] = tuple(touch.pos), time.time()
    except Exception:
        pass
    return False


try:
    Window.bind(on_touch_down=_track_touch)
except Exception:
    pass


def _suggestion_row(opt, span, on_tap):
    label, sub, val, col = opt[:4]
    lcol = opt[4] if len(opt) > 4 else None
    normal = get_hex_from_color(lcol or T.TEXT)[:7]
    green = get_hex_from_color(T.GREEN)[:7]
    row = CardBox(orientation="vertical", size_hint_y=None, height=dp(66),
                  padding=[dp(12), dp(6)], on_tap=on_tap)
    lb = _KLabel(text=suggestion_markup(str(label), span, normal, green), markup=True,
                 font_name=_font_name(), font_size=dp(20), color=T.TEXT,
                 halign="right", valign="middle", size_hint_y=0.62)
    lb.bind(size=lambda w, s: setattr(w, "text_size", (s[0], s[1])))
    row.add_widget(lb)
    row.add_widget(mk_text(sub or "", dp(12), col or T.MUTED, hint_y=0.38))
    return row


def _pick_popup_small(title, options, on_pick, new_label=None, on_new=None):
    """مربع صغير تحت الحقل مع لوحة المفاتيح: يعرض اسماً واحداً هو الأقرب لما تكتبه،
    الجزء المكتوب بلون النص العادي وباقي الاسم أخضر. options: (العنوان، سطر فرعي، القيمة، لون)."""
    labels = [str(o[0]) for o in options]
    norm = [_qn(x) for x in labels]
    box = BoxLayout(orientation="vertical", spacing=dp(6), padding=[dp(6), dp(4)])
    box.add_widget(mk_text(title, dp(13), T.MUTED, halign="right", height=dp(22)))
    search = mk_input(hint_text="اكتب للبحث...", size_hint_y=None, height=dp(48))
    box.add_widget(search)
    area = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(30), spacing=dp(6))
    box.add_widget(area)
    bar = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(8))
    allb = mk_btn("عرض كل القائمة", height=dp(38), font_size=dp(13), background_color=T.GRAY_BTN)
    bar.add_widget(allb)
    box.add_widget(bar)

    BASE = dp(22 + 48 + 38 + 6 * 3 + 40)
    MAXH = BASE + dp(66 + 6 + 46)
    popup = _KPopup(title="", title_size=0, content=box, size_hint=(None, None),
                    size=(Window.width * 0.94, BASE + dp(30)), separator_height=0,
                    separator_color=(0, 0, 0, 0), background="", background_color=T.CARD,
                    overlay_color=(0, 0, 0, 0.25), auto_dismiss=True)
    H = Window.height
    tp = _LAST_TOUCH["pos"] if time.time() - _LAST_TOUCH["t"] < 2.0 else None
    kb = max(getattr(Window, "keyboard_height", 0) or 0, H * 0.40)     # فوق لوحة المفاتيح دائماً
    top = (tp[1] - dp(26)) if tp else H * 0.9                           # تحت الحقل الذي ضغطت عليه
    top = min(max(top, kb + MAXH + dp(6)), H - dp(6))
    popup.pos_hint = {"center_x": 0.5, "top": top / H}
    state = {"best": None}

    def choose(val):
        popup.dismiss()
        on_pick(val)

    def update(*a):
        q = search.text.strip()
        area.clear_widgets()
        h = 0
        res = best_suggestion(q, labels, norm) if q else None
        state["best"] = res
        exact = False
        if res:
            i, span = res
            exact = norm[i] == _qn(q)
            area.add_widget(_suggestion_row(options[i], span, lambda v=options[i][2]: choose(v)))
            h += dp(66)
        elif q:
            area.add_widget(mk_text("لا توجد نتيجة مطابقة", dp(14), T.MUTED, halign="center", height=dp(30)))
            h += dp(30)
        else:
            area.add_widget(mk_text("اكتب وسيظهر أقرب اسم", dp(13), T.MUTED, halign="center", height=dp(26)))
            h += dp(26)
        if new_label and q and not exact:
            nb = mk_btn("+ %s: %s" % (new_label, q), height=dp(46), background_color=T.GREEN)
            nb.bind(on_release=lambda *x: (popup.dismiss(), on_new(search.text.strip())))
            h += dp(6) if h else 0
            area.add_widget(nb)
            h += dp(46)
        area.height = max(h, dp(26))
        popup.height = BASE + area.height + dp(8)
        schedule_refresh(0.1)

    def enter(*a):
        q = search.text.strip()
        if state["best"]:
            choose(options[state["best"][0]][2])
        elif new_label and q:
            popup.dismiss()
            on_new(q)

    allb.bind(on_release=lambda *a: (popup.dismiss(),
                                     _pick_popup_full(title, options, on_pick, new_label, on_new)))
    search.bind(text=update)
    try:
        search._input.bind(on_text_validate=enter)
    except Exception:
        pass
    update()
    popup.open()
    Clock.schedule_once(lambda dt: setattr(search._input, "focus", True), 0.2)
    return popup


# ---- الاختيار داخل نفس الحقل: الحقل نفسه يتحوّل إلى مربع كتابة والاقتراح تحته ----
_INLINE = {"obj": None}


def _field_under_last_touch():
    """حقل الاختيار (picker_btn) الذي يقع تحت آخر لمسة، إن وُجد وكان ظاهراً على الشاشة."""
    tp = _LAST_TOUCH["pos"]
    if tp is None or time.time() - _LAST_TOUCH["t"] > 2.0:
        return None
    best = None
    for w in list(_PICKERS):
        try:
            if w.get_root_window() is None or w.opacity == 0:
                continue
            x, y = w.to_window(w.x, w.y)
            if x - 3 <= tp[0] <= x + w.width + 3 and y - 3 <= tp[1] <= y + w.height + 3:
                if best is None or w.width * w.height < best.width * best.height:
                    best = w
        except Exception:
            continue
    return best


class _DimLayer(Widget):
    """طبقة شفافة تلتقط اللمس خارج الحقل لإغلاق الاختيار."""

    def __init__(self, on_tap, **kw):
        super().__init__(**kw)
        self._tap = on_tap
        with self.canvas:
            Color(0, 0, 0, 0.18)
            self._r = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=lambda *a: setattr(self._r, "pos", self.pos),
                  size=lambda *a: setattr(self._r, "size", self.size))

    def on_touch_down(self, t):
        self._tap()
        return True


class InlinePicker:
    def __init__(self, field, title, options, on_pick, new_label, on_new):
        self.field, self.title, self.options = field, title, options
        self.on_pick, self.new_label, self.on_new = on_pick, new_label, on_new
        self.labels = [str(o[0]) for o in options]
        self.norm = [_qn(x) for x in self.labels]
        self.best, self.closed, self.kb_seen = None, False, False
        self.layer = FloatLayout()
        self.layer.add_widget(_DimLayer(self.close, size_hint=(1, 1)))
        self.drop = CardBox(orientation="vertical", size_hint=(None, None), padding=[dp(6), dp(6)],
                            spacing=dp(6))
        self.inp = mk_input(hint_text=title, size_hint=(None, None))
        self.layer.add_widget(self.drop)
        self.layer.add_widget(self.inp)
        self.inp.bind(text=self.update)
        try:
            self.inp._input.bind(on_text_validate=lambda *a: self.enter())
        except Exception:
            pass

    # ---- العرض ----
    def open(self):
        _INLINE["obj"] = self
        self.update()
        Window.add_widget(self.layer)
        try:
            Window.bind(keyboard_height=self._kb)
        except Exception:
            pass
        self._layout()
        Clock.schedule_once(lambda dt: setattr(self.inp._input, "focus", True), 0.12)
        return self

    def _kb(self, *a):
        self.kb_seen = (Window.keyboard_height or 0) > 0
        self._layout()

    def _layout(self, *a):
        if self.closed:
            return
        f = self.field
        try:
            fx, fy = f.to_window(f.x, f.y)
        except Exception:
            fx, fy = f.pos
        fw, fh = f.size
        H, Wd = Window.height, Window.width
        kb = Window.keyboard_height or 0
        kb_eff = kb if (self.kb_seen and kb > 0) else max(kb, H * 0.40)    # قبل ظهور الكيبورد: تقدير
        dw = fw if fw >= Wd * 0.6 else min(Wd - dp(16), max(fw, dp(300)))
        dx = max(dp(8), min(fx + fw - dw, Wd - dw - dp(8)))               # محاذاة الحافة اليمنى للحقل
        dh = self.drop.height
        iy = fy
        if iy - dh - dp(4) < kb_eff + dp(6):                              # لا يغطيه الكيبورد: نرفع الحقل قليلاً
            iy = kb_eff + dp(6) + dh + dp(4)
        iy = min(iy, H - fh - dp(8))
        self.inp.size = (fw, fh)
        self.inp.pos = (fx, iy)
        self.drop.size = (dw, dh)
        self.drop.pos = (dx, iy - dh - dp(4))
        schedule_refresh(0.05)

    # ---- الاقتراح ----
    def update(self, *a):
        q = self.inp.text.strip()
        d = self.drop
        d.clear_widgets()
        rows = []
        res = best_suggestion(q, self.labels, self.norm) if q else None
        self.best = res
        exact = False
        if res:
            i, span = res
            exact = self.norm[i] == _qn(q)
            val = self.options[i][2]
            rows.append((_suggestion_row(self.options[i], span, lambda v=val: self.choose(v)), dp(66)))
        elif q:
            rows.append((mk_text("لا توجد نتيجة مطابقة", dp(14), T.MUTED, halign="center", height=dp(28)), dp(28)))
        if self.new_label and q and not exact:
            nb = mk_btn("+ %s: %s" % (self.new_label, q), height=dp(44), background_color=T.GREEN)
            nb.bind(on_release=lambda *x: (self.close(), self.on_new(self.inp.text.strip())))
            rows.append((nb, dp(44)))
        allb = mk_btn("عرض كل القائمة", height=dp(32), font_size=dp(12), background_color=T.GRAY_BTN)
        allb.bind(on_release=lambda *x: (self.close(), _pick_popup_full(
            self.title, self.options, self.on_pick, self.new_label, self.on_new)))
        rows.append((allb, dp(32)))
        for w, _h in rows:
            d.add_widget(w)
        d.height = dp(12) + sum(h for _w, h in rows) + dp(6) * (len(rows) - 1)
        self._layout()

    def choose(self, val):
        self.close()
        self.on_pick(val)

    def enter(self):
        q = self.inp.text.strip()
        if self.best:
            self.choose(self.options[self.best[0]][2])
        elif self.new_label and q:
            self.close()
            self.on_new(q)

    def close(self, *a):
        if self.closed:
            return
        self.closed = True
        if _INLINE["obj"] is self:
            _INLINE["obj"] = None
        try:
            Window.unbind(keyboard_height=self._kb)
        except Exception:
            pass
        try:
            self.inp._input.focus = False
        except Exception:
            pass
        try:
            Window.remove_widget(self.layer)
        except Exception:
            pass


_pick_popup_modal = None          # يُعيَّن بعد تعريف النافذة المنبثقة أدناه


def pick_popup(title, options, on_pick, new_label=None, on_new=None):
    """إن كان الاستدعاء من ضغطة على حقل اختيار: يتحوّل الحقل نفسه إلى مربع كتابة مع اقتراح تحته،
    وإلا (استدعاء برمجي) تظهر النافذة الصغيرة."""
    f = _field_under_last_touch()
    if f is not None:
        try:
            return InlinePicker(f, title, options, on_pick, new_label, on_new).open()
        except Exception as e:
            vs_log("INLINE: " + _err_text(e))
    return _pick_popup_modal(title, options, on_pick, new_label, on_new)


_pick_popup_modal = _pick_popup_small


def load_data():
    d = load_json(DATA_FILE, None) or {}
    d.setdefault("user", None)
    d.setdefault("role", "editor")
    d.setdefault("sub", {"plan": "free", "allowed": FREE_START_INVOICES})
    d.setdefault("pending", 0)
    d.setdefault("owner", None)
    d.setdefault("accounts", {})
    d.setdefault("updates", {})
    d.setdefault("notes_read", [])
    base = new_db()
    db = d.get("db") or {}
    for k, v in base.items():
        db.setdefault(k, v)
    d["db"] = db
    return d


class AccountingApp(App):
    def build(self):
        self.title = "دفتر المحاسب"
        self.data = load_data()
        global SESSION_TOKEN
        SESSION_TOKEN = str(self.data.get("session_token") or "")   # استعادة الجلسة المحفوظة بعد إعادة فتح التطبيق
        if not self.data.get("device_id"):
            self.data["device_id"] = gen_id()
        self.data.setdefault("font", "font.otf")
        if not set_app_font(self.data["font"]):
            set_app_font("font.otf")
        apply_theme(self.data.get("theme", "dark"))
        self.save()
        try:
            Window.softinput_mode = "below_target"
        except Exception:
            pass
        self.stack = []
        self._seq = 0
        self.drawer = None
        self.bell = None
        self.extra_menu = []
        self.menu_owner = {}
        self.update_modules = {}
        self.update_cleanups = {}
        self._loading_update = None
        self.search_state = {}
        self.update_errors = {}
        self._note_scanning = False
        self._note_scanned = False
        self._ticking = False
        self._tick_n = 0
        self._auto_failed = {}
        self._banner = None
        self.sm = ScreenManager(transition=SlideTransition(duration=0.18))
        self.sm.add_widget(SplashScreen(name="boot"))
        Window.bind(on_keyboard=self._on_key)
        Window.bind(size=lambda *a: schedule_refresh(0.1))
        self.sm.bind(current=lambda *a: schedule_refresh(0.25))
        try:
            self.load_updates()
        except Exception:
            _write_crash("LOAD UPDATES: " + traceback.format_exc())
        if (self.data.get("user") or {}).get("email"):
            Clock.schedule_once(lambda dt: self.go_home(), SPLASH_SECONDS)
        else:
            Clock.schedule_once(lambda dt: self.go_login(), SPLASH_SECONDS)
        Clock.schedule_once(self._show_last_crash, 1.5)
        Clock.schedule_once(lambda dt: show_lock(self), 0.1)
        Clock.schedule_once(self._tick, 8)
        Clock.schedule_interval(self._tick, TICK_SECONDS)
        Clock.schedule_interval(self._heartbeat, 30)
        return self.sm

    # ---- بيانات ----
    @property
    def db(self):
        return self.data["db"]

    # حالة المزامنة التلقائية
    _autosyncing = False
    _sync_due = False
    _as_again = False
    _as_ev = None
    _sync_sig = None
    wallet_tab = "cash"

    def save(self, auto=True):
        save_json(DATA_FILE, self.data)
        if auto:
            self.auto_sync_soon()

    # ---- إعدادات التشغيل/الإيقاف ----
    def auto_sync_on(self):
        """المزامنة التلقائية: للباقات المدفوعة (Pro / Max) ما لم يلغِها المستخدم من الإعدادات."""
        return self.plan() != "free" and self.data.get("auto_sync", True) is not False

    def set_auto_sync(self, on):
        self.data["auto_sync"] = bool(on)
        save_json(DATA_FILE, self.data)
        if on:
            self._sync_sig = None
            self.auto_sync_soon(0.5)

    def auto_update_on(self):
        return self.data.get("auto_update", True) is not False

    def set_auto_update_all(self, on):
        self.data["auto_update"] = bool(on)
        save_json(DATA_FILE, self.data)
        if on:
            Clock.schedule_once(self._tick, 1)

    # ---- مزامنة تلقائية بعد كل عملية (إذا توفر الانترنت) ----
    def _fast_copy(self):
        """نسخة مستقلة من القاعدة؛ أسرع بكثير من copy.deepcopy (القاعدة بيانات JSON فقط)."""
        return json.loads(json.dumps(self.data["db"], ensure_ascii=False))

    def _busy(self):
        """المستخدم داخل فاتورة/سند: نؤجل المزامنة الثقيلة حتى لا يتجمد التطبيق أثناء الإدخال والحفظ."""
        try:
            return isinstance(self.sm.current_screen, (AddSalePage, AddPurchasePage, AddVoucherPage))
        except Exception:
            return False

    def _db_sig(self):
        try:
            raw = json.dumps(self.data.get("db"), sort_keys=True, ensure_ascii=False)
            return hashlib.md5(raw.encode("utf-8")).hexdigest()
        except Exception:
            return None

    def auto_sync_soon(self, delay=2.5):
        try:
            if (not self.auto_sync_on() or self.data.get("role") == "viewer"
                    or not (self.data.get("user") or {}).get("email") or not self.data.get("db")):
                return
            if self._as_ev is not None:
                self._as_ev.cancel()
            self._as_ev = Clock.schedule_once(self._auto_sync_run, delay)
        except Exception as e:
            print(f"[AUTOSYNC] {e}")

    def _auto_sync_run(self, dt=None):
        self._as_ev = None
        try:
            if not self.auto_sync_on() or self.data.get("role") == "viewer":
                return
            if self._autosyncing:
                self._as_again = True
                return
            if self._busy():
                self._as_ev = Clock.schedule_once(self._auto_sync_run, 6)
                return
            email = (self.data.get("user") or {}).get("email")
            if not email:
                return
            self._autosyncing = True

            def worker():
                # الحساب الثقيل (توقيع البيانات + النسخة) يتم هنا بعيداً عن خيط الواجهة.
                # التوقيع يُحسب قبل النسخة دائماً حتى لا نعتبر بياناً مزامَناً وهو لم يُرسل.
                res, sig = None, None
                try:
                    sig = self._db_sig()
                    if sig is None or self._sync_sig != (email, sig):
                        snap = self._fast_copy()
                        res = self.sync_server(email, snap, False)
                except Exception as e:
                    print(f"[AUTOSYNC] {e}")
                Clock.schedule_once(lambda dt: self._auto_sync_done(email, res, sig), 0)
            threading.Thread(target=worker, daemon=True).start()
        except Exception:
            self._autosyncing = False
            _write_crash("AUTOSYNC: " + traceback.format_exc())

    def _auto_sync_done(self, email, res, s0):
        if self._busy():
            Clock.schedule_once(lambda dt: self._auto_sync_done(email, res, s0), 5)
            return
        self._autosyncing = False
        try:
            if res and (self.data.get("user") or {}).get("email") == email:
                if res.get("kicked"):
                    return self._kicked_out()
                if res.get("merged") is not None:
                    changed = self.apply_merged(res["merged"], res.get("entry"), email, s0)
                    save_json(DATA_FILE, self.data)
                    if changed:
                        self.show_banner("تمت المزامنة")
                        if isinstance(self.sm.current_screen, HomePage):
                            self.reload()
        except Exception:
            _write_crash("AUTOSYNC DONE: " + traceback.format_exc())
        if self._as_again:
            self._as_again = False
            self.auto_sync_soon(1.0)

    def apply_merged(self, merged, entry=None, email=None, s0=None):
        """يدمج نتيجة المزامنة داخل القاعدة الحالية (على الخيط الرئيسي وبنفس الكائن) فلا يضيع
        أي شيء أُضيف أو عُدّل أثناء المزامنة. يرجع True إذا تغيّرت البيانات."""
        if entry:
            self._apply_entry(entry)
        pre = self._db_sig()
        live = self.data["db"]
        new = merge_db(merged, live)
        live.clear()
        live.update(new)
        post = self._db_sig()
        if email and s0 is not None and pre == s0:
            self._sync_sig = (email, post)              # لم يتغيّر شيء محلياً أثناء المزامنة
        return post != pre

    def _consume_pending(self, n):
        self.data["pending"] = max(0, int(self.data.get("pending", 0)) - int(n))
        save_json(DATA_FILE, self.data)

    def sync_core(self, rec, mid, entry, snap, viewer):
        pending = int(self.data.get("pending", 0))
        server, version = DB.read_clients(rec)
        merged = merge_db(server, snap)
        did = self.data.get("device_id")
        if touch_device(rec, did, device_name()):
            DB.save_user(rec, mid)
        if not viewer:
            DB.write_clients(rec, mid, merged, version)
            if pending > 0 and entry.get("plan") != "max":
                DB.consume(pending)
                Clock.schedule_once(lambda dt, n=pending: self._consume_pending(n), 0)
        return merged, entry
    def sync_server(self, email, snap, viewer):
        rec, mid, entry = DB.get_user(email)
        if not rec:
            return {"missing": True}
        if is_revoked(rec, self.data.get("device_id")):
            return {"kicked": True}
        merged, entry = self.sync_core(rec, mid, entry, snap, viewer)
        return {"merged": merged, "entry": entry, "rec": rec}

    def plan(self):
        return (self.data.get("sub") or {}).get("plan", "free")

    def remaining(self):
        """None = غير محدود."""
        if self.plan() == "max":
            return None
        return int((self.data.get("sub") or {}).get("allowed", 0)) - int(self.data.get("pending", 0))

    # ---- الحسابات المتعددة (تنقّل بدون انترنت) ----
    ACCT_KEYS = ("user", "role", "sub", "pending", "owner", "db")

    def account_list(self):
        """[(البريد، الاسم، هل هو الحالي؟)]"""
        out = []
        cur = self.data.get("user") or {}
        if cur.get("email"):
            out.append((cur["email"], cur.get("name", ""), True))
        for em, snap in (self.data.get("accounts") or {}).items():
            out.append((em, ((snap or {}).get("user") or {}).get("name", ""), False))
        return out

    def _stash_current(self):
        em = (self.data.get("user") or {}).get("email")
        if em:
            self.data.setdefault("accounts", {})[em] = {k: self.data.get(k) for k in self.ACCT_KEYS}

    def _load_account(self, em=None):
        snap = (self.data.get("accounts") or {}).pop(em, None) if em else None
        snap = snap or {}
        self.data["user"] = snap.get("user")
        self.data["role"] = snap.get("role") or "editor"
        self.data["sub"] = snap.get("sub") or {"plan": "free", "allowed": FREE_START_INVOICES}
        self.data["pending"] = snap.get("pending", 0)
        self.data["owner"] = snap.get("owner")
        base = new_db()
        db = snap.get("db") or base
        for k, v in base.items():
            db.setdefault(k, v)
        self.data["db"] = db

    def switch_account(self, em):
        cur = (self.data.get("user") or {}).get("email")
        if em == cur or em not in (self.data.get("accounts") or {}):
            return
        self._stash_current()
        self._load_account(em)
        self.save()
        self.go_home()

    def remove_saved_account(self, em):
        (self.data.get("accounts") or {}).pop(em, None)
        self.save()

    def add_account_flow(self):
        msg_popup("إضافة حساب", "تحتاج اتصالاً بالانترنت لإضافة الحساب مرة واحدة فقط، "
                                "وبعدها تتنقل بين الحسابات بدون انترنت.", T.GREEN,
                  extra=[("متابعة", T.GREEN, lambda: self.push(lambda: LoginPage(self, adding=True)))])

    def _begin_add(self, email):
        """يحفظ الحساب الحالي ويجهّز مساحة نظيفة (أو الحساب المحفوظ نفسه) قبل إكمال الدخول."""
        cur = (self.data.get("user") or {}).get("email")
        if cur == email:
            return
        self._stash_current()
        if email in (self.data.get("accounts") or {}):
            self._load_account(email)
        else:
            self._load_account(None)

    # ---- الإشعارات ----
    def unread_count(self):
        notes = visible_notes(self)
        read = set(self.data.get("notes_read") or [])
        return sum(1 for k in notes if k not in read)

    def refresh_bell(self):
        b = getattr(self, "bell", None)
        if b is not None:
            try:
                b.set_count(self.unread_count())
            except Exception:
                pass

    def mark_note_read(self, mid):
        read = self.data.setdefault("notes_read", [])
        if str(mid) not in read:
            read.append(str(mid))
            self.data["notes_read"] = read[-300:]
            self.save()
        self.refresh_bell()

    def auto_scan_notes(self):
        """فحص صامت للإشعارات الجديدة عند فتح الرئيسية (مرة لكل تشغيل ناجح)."""
        if self._note_scanning or self._note_scanned:
            return
        self._note_scanning = True

        def w():
            ok = False
            try:
                scan_downloads(self.data.setdefault("dl", {}))
                self.save()
                ok = True
            except Exception as e:
                print(f"[NOTES] {e}")

            def done(dt):
                self._note_scanning = False
                self._note_scanned = ok
                self.apply_scan()
                self.refresh_bell()
            Clock.schedule_once(done, 0)
        threading.Thread(target=w, daemon=True).start()

    # ---- التحديث التلقائي لكل تحديث ----
    def auto_stopped(self, name):
        return name in (self.data.get("auto_stop") or [])

    def set_auto_update(self, name, on):
        lst = [n for n in (self.data.get("auto_stop") or []) if n != name]
        if not on:
            lst.append(name)
        self.data["auto_stop"] = lst
        self.save()

    # ---- الدورة التلقائية: إشعارات + تحديثات + مزامنة max ----
    def show_banner(self, text):
        """شريط صغير أعلى الشاشة لبضع ثوانٍ (إشعار جديد / تحديث / مزامنة)."""
        try:
            old = self._banner
            if old is not None and old.parent is not None:
                Window.remove_widget(old)
            b = CardBox(orientation="horizontal", size_hint=(None, None),
                        size=(Window.width - dp(24), dp(46)), bg=T.HEADER,
                        padding=[dp(12), dp(4)], on_tap=lambda: Window.remove_widget(b))
            b.pos = (dp(12), Window.height - dp(46) - dp(34))
            b.add_widget(mk_text(text, dp(14), WHITE, halign="center"))
            Window.add_widget(b)
            self._banner = b
            Clock.schedule_once(lambda dt: b.parent is not None and Window.remove_widget(b), 4)
        except Exception as e:
            print(f"[BANNER] {e}")

    def _tick(self, dt=None):
        if self._ticking or not (self.data.get("user") or {}).get("email"):
            return
        self._ticking = True
        self._tick_n += 1
        want = False
        if self._tick_n % SYNC_EVERY == 1:
            self._sync_due = True
        if self._sync_due and not self._busy() and self.auto_sync_on() and self.data.get("db"):
            self._sync_due = False
            want = True
        threading.Thread(target=self._tick_worker, args=(self._tick_n, want), daemon=True).start()

    def _tick_worker(self, n, want=False):
        res = {"new_notes": 0, "ups": [], "merged": None, "entry": None, "rec": None, "kicked": False}
        snap = None
        if want:
            try:
                snap = self._fast_copy()                # خارج خيط الواجهة
            except Exception:
                snap = None
        try:
            dl = self.data.setdefault("dl", {})
            read = set(self.data.get("notes_read") or [])
            before = set(visible_notes(self))
            scan_downloads(dl, tolerance=6, gap=600)
            res["new_notes"] = len([k for k in visible_notes(self) if k not in before and k not in read])
            installed = self.data.get("updates") or {}
            todo = list((dl.get("updates") or {}).items()) if self.auto_update_on() else []
            for name, item in todo:
                mid = int(item.get("mid", 0))
                cur = installed.get(name)
                if (self.auto_stopped(name) or not UPDATE_NAME_RE.fullmatch(name)
                        or (cur is not None and mid <= int(cur.get("mid", 0)))
                        or self._auto_failed.get(name) == mid):
                    continue
                try:
                    download_channel_file(name, item, UPDATES_DIR, min_size=5)
                    res["ups"].append((name, item))
                except Exception as e:
                    print(f"[AUTO UPDATE {name}] {e}")
                    self._auto_failed[name] = mid
            if n % SYNC_EVERY == 1 or snap is not None:
                self._tick_account(res, snap)
        except Exception as e:
            print(f"[TICK] {e}")
        Clock.schedule_once(lambda dt: self._tick_done(res), 0)

    def _tick_account(self, res, snap=None):
        """تحديث الباقة/الملف الشخصي لكل الباقات، ومزامنة صامتة للباقات المدفوعة."""
        email = (self.data.get("user") or {}).get("email")
        rec, mid, entry = DB.get_user(email)
        if not rec:
            return
        if is_revoked(rec, self.data.get("device_id")):
            res["kicked"] = True
            return
        res["entry"], res["rec"] = entry, rec
        if snap is not None and entry["plan"] in ("pro", "max") and self.data.get("auto_sync", True) is not False:
            viewer = self.data.get("role") == "viewer"
            merged, entry2 = self.sync_core(rec, mid, entry, snap, viewer)
            res["merged"], res["entry"] = merged, entry2

    def _tick_done(self, res):
        if self._busy():                          # لا ندمج/نعيد الرسم أثناء إدخال فاتورة
            Clock.schedule_once(lambda dt: self._tick_done(res), 5)
            return
        self._ticking = False
        if not (self.data.get("user") or {}).get("email"):
            return
        try:
            if res["kicked"]:
                return self._kicked_out()
            self.apply_scan()
            self.refresh_bell()
            msgs = []
            for name, item in res["ups"]:
                ok, _e = self.activate_update(name, item)
                msgs.append(("تم تحديث التطبيق تلقائياً: " + name) if ok else ("فشل تحديث: " + name))
            if res["new_notes"]:
                msgs.append("وصلك إشعار جديد")
            plan_before = self.plan()
            if res["entry"]:
                self._apply_entry(res["entry"])
                self._apply_profile_rec(res["rec"])
            changed = plan_before != self.plan()
            if res["merged"] is not None:
                if self.apply_merged(res["merged"]):
                    changed = True
                    msgs.append("تمت المزامنة التلقائية")
            self.save(auto=False)
            if platform == "android":
                write_notifier_cfg(self)
            if changed and isinstance(self.sm.current_screen, HomePage):
                self.reload()
            if msgs:
                self.show_banner(" - ".join(msgs[:2]))
        except Exception:
            _write_crash("TICK DONE: " + traceback.format_exc())

    def _apply_profile_rec(self, rec):
        u = self.data.get("user")
        if u is not None and rec:
            u["name"] = rec.get("name", u.get("name", ""))
            u["phone"] = rec.get("phone", u.get("phone", ""))

    # ---- التحديثات (ملفات py من القناة) ----
    def register_menu(self, label, fn):
        """للاستعمال داخل ملفات التحديث: يضيف زراً في القائمة الجانبية."""
        self.extra_menu = [(l, f) for l, f in self.extra_menu if l != label] + [(label, fn)]
        self.menu_owner[label] = self._loading_update

    def register_cleanup(self, fn):
        """للاستعمال داخل ملفات التحديث: دالة تُستدعى عند حذف التحديث أو استبداله."""
        self.update_cleanups.setdefault(self._loading_update, []).append(fn)

    def unload_update(self, name):
        """يزيل كل ما أضافه التحديث: أزرار القائمة + دوال التنظيف + remove(app) إن وُجدت."""
        mod = self.update_modules.pop(name, None)
        for fn in self.update_cleanups.pop(name, []):
            try:
                fn()
            except Exception:
                _write_crash("UPDATE CLEANUP %s: %s" % (name, traceback.format_exc()))
        if mod is not None:
            for hook in ("remove", "unload"):
                fn = mod.__dict__.get(hook)
                if callable(fn):
                    try:
                        fn(self)
                    except Exception:
                        _write_crash("UPDATE %s %s: %s" % (name, hook, traceback.format_exc()))
                    break
        mine = {l for l, o in self.menu_owner.items() if o == name}
        self.extra_menu = [(l, f) for l, f in self.extra_menu if l not in mine]
        for l in mine:
            self.menu_owner.pop(l, None)

    def apply_scan(self):
        """بعد كل مسح للقناة: التحديثات المحذوفة من القناة تُحذف من الجهاز مع مميزاتها."""
        dl = self.data.get("dl") or {}
        gone = list(dl.pop("gone", []) or [])
        changed = False
        for name in gone:
            if name in (self.data.get("updates") or {}):
                self.remove_update(name)
                changed = True
        if gone:
            self.save()
        return changed

    def load_updates(self):
        for name in sorted(self.data.get("updates") or {}):
            path = os.path.join(UPDATES_DIR, name)
            if not os.path.exists(path):
                continue
            ok, err = load_update_file(self, path)
            if not ok:
                self.update_errors[name] = err
                # ملف صالح نحويًا قد لا يتوافق مع إصدار التطبيق؛ لا تعاود
                # محاولة تشغيله عند كل فتح للتطبيق.
                try:
                    os.replace(path, path + ".disabled")
                except Exception:
                    pass
                (self.data.get("updates") or {}).pop(name, None)
        self.save()

    def activate_update(self, name, item):
        self.unload_update(name)          # النسخة القديمة (وأزرارها) تُزال قبل تفعيل الجديدة
        path = os.path.join(UPDATES_DIR, name)
        ok, err = load_update_file(self, path)
        if ok:
            self.data.setdefault("updates", {})[name] = {"mid": int(item.get("mid", 0))}
            self.update_errors.pop(name, None)
        else:
            self.update_errors[name] = err
            # لا نترك ملفًا فاشلًا ليُنفّذ تلقائيًا في التشغيل القادم.
            self.data.setdefault("updates", {}).pop(name, None)
            try:
                if os.path.exists(path):
                    os.replace(path, path + ".disabled")
            except Exception:
                pass
        self.save()
        return ok, err

    def remove_update(self, name):
        self.unload_update(name)
        try:
            os.remove(os.path.join(UPDATES_DIR, name))
        except Exception:
            pass
        (self.data.get("updates") or {}).pop(name, None)
        self.update_errors.pop(name, None)
        self.save()

    # ---- صلاحيات ----
    def need_edit(self):
        if self.data.get("role") == "viewer":
            msg_popup("وضع المشاهدة", "هذا الدخول للمشاهدة فقط. سجّل الدخول بكلمة المرور الثانية "
                      "لتتمكن من التعديل والبيع.", T.ORANGE)
            return False
        return True

    def need_pro(self):
        if self.plan() == "free":
            self.subscribe_popup()
            return False
        return True

    def quota_ok(self):
        rem = self.remaining()
        if rem is not None and rem <= 0:
            self.subscribe_popup("انتهت فواتيرك المتاحة. تواصل معنا لإضافة المزيد:")
            return False
        return True

    def subscribe_popup(self, text=None):
        msg_popup("الاشتراك", (text or "عليك الاشتراك في التطبيق لتفعيل هذه الميزة.\nيمكنك التواصل مع هذا "
                               "الرقم على واتساب:") + "\n" + ADMIN_PHONE_SHOW, T.GREEN,
                  extra=[("تواصل واتساب", T.GREEN,
                          lambda: open_whatsapp(ADMIN_PHONE_WA, "السلام عليكم، أرغب بالاشتراك في التطبيق"))])

    # ---- صوت / باركود / إشعارات الخلفية ----
    _paused = False

    _resumed_at = 0.0
    _back_t = 0.0

    _lock_ui = None
    _unlocked_at = 0.0
    _paused_at = 0.0

    def on_pause(self):
        self._paused = True
        self._paused_at = time.time()
        if platform == "android":
            write_notifier_cfg(self)
        return True

    def on_resume(self):
        self._paused = False
        self._resumed_at = time.time()
        try:
            if (lock_on(self) and self._paused_at and time.time() - self._paused_at > LOCK_AFTER_AWAY
                    and time.time() - self._unlocked_at > 5):
                show_lock(self)
        except Exception:
            _write_crash("LOCK RESUME: " + traceback.format_exc())

    def _heartbeat(self, dt=None):
        """علامة أن التطبيق مفتوح أمام المستخدم، فلا تكرّر الخدمة الإشعار."""
        if platform == "android" and not self._paused:
            try:
                with open(os.path.join(DATA_DIR, "app_alive.txt"), "w") as f:
                    f.write(str(time.time()))
            except Exception:
                pass

    def voice_on(self):
        return self.data.get("voice", True) is not False

    def toggle_voice(self):
        on = not self.voice_on()
        self.data["voice"] = on
        self.save(auto=False)
        self.show_banner("تم تشغيل الإدخال الصوتي" if on else "تم إيقاف الإدخال الصوتي وإخفاؤه")
        self.reload()

    def toggle_push(self):
        on = self.data.get("push", True) is False
        self.data["push"] = on
        self.save(auto=False)
        write_notifier_cfg(self)
        if on:
            start_notifier(self)
        else:
            stop_notifier()
        self.show_banner("تم تشغيل إشعارات الخلفية" if on else "تم إيقاف إشعارات الخلفية")

    def _voice_done(self, texts):
        if not self.need_edit():
            return
        r = best_parse(texts, self.db)
        if not r or not r["items"]:
            return msg_popup("الإدخال الصوتي", "لم أفهم: " + (texts[0] if texts else "") +
                             "\nمثال: بعت 3 كراتين بيبسي لفلان", T.ORANGE)

        def go(kind):
            rr = best_parse(texts, self.db, kind)
            pid = rr["party"]["id"] if rr.get("party") else None
            if kind == "sale":
                self.push(lambda: AddSalePage(self, pid))
            else:
                self.push(lambda: AddPurchasePage(self, pid))
            self.sm.current_screen.voice_fill(rr)
        if r["kind"]:
            go(r["kind"])
        else:
            pick_popup("نوع الفاتورة", [("فاتورة بيع", "", "sale", None),
                                        ("فاتورة شراء", "", "purchase", None)], go)

    def quick_scan(self):
        if not self.need_edit():
            return
        scan_with_fallback(self._quick_scan_done)

    def _quick_scan_done(self, code):
        self.push(lambda: AddPurchasePage(self))
        self.sm.current_screen.on_barcode(code)

    # ---- تنقل ----
    def push(self, factory):
        self.stack.append(factory)
        self._display(factory(), "left")

    def nav_home(self):
        self.stack = [lambda: HomePage(self)]
        self._display(self.stack[0](), "right")

    def nav_to(self, factory):
        self.stack = [lambda: HomePage(self), factory]
        self._display(factory(), "left")

    def back(self):
        if len(self.stack) > 1:
            self.stack.pop()
            self._display(self.stack[-1](), "right")
            return True
        return False

    def reload(self):
        if self.stack:
            self._display(self.stack[-1](), "none")

    def go_home(self):
        self.stack = [lambda: HomePage(self)]
        self._display(self.stack[0](), "right")
        try:
            db = self.db
            empty = not any(db.get(c) for c in ("customers", "suppliers", "products",
                                                "sales", "purchases", "vouchers"))
            if not self.data.get("onboarded"):
                if empty and self.data.get("role") != "viewer":
                    Clock.schedule_once(lambda dt: self.push(lambda: OnboardingPage(self)), 0.45)
                else:
                    self.data["onboarded"] = True
            start_notifier(self)
        except Exception:
            _write_crash("GO HOME EXTRAS: " + traceback.format_exc())

    def go_login(self):
        self.stack = [lambda: LoginPage(self)]
        self._display(self.stack[0](), "right")

    def _display(self, screen, direction):
        self._seq += 1
        screen.name = "s%d" % self._seq
        old = self.sm.current
        self.sm.transition.direction = direction if direction != "none" else "left"
        self.sm.transition.duration = 0.18 if direction != "none" else 0.01
        self.sm.add_widget(screen)
        self.sm.current = screen.name
        Clock.schedule_once(lambda dt: self._cleanup(), 0.5)
        schedule_refresh(0.1)

    def _cleanup(self):
        if self.sm.transition.is_active:
            Clock.schedule_once(lambda dt: self._cleanup(), 0.2)
            return
        for s in list(self.sm.screens):
            if s.name != self.sm.current:
                try:
                    self.sm.remove_widget(s)
                except Exception:
                    pass

    def _on_key(self, window, key, *a):
        if key != 27:
            return False
        if self._lock_ui is not None:       # شاشة القفل: لا رجوع
            return True
        # بعد إغلاق نافذة الصوت/الكاميرا قد يصل ضغط «رجوع» زائد للتطبيق فيغلقه؛ نتجاهله
        if self._paused or time.time() - self._resumed_at < 1.5:
            return True
        if _INLINE["obj"] is not None:
            _INLINE["obj"].close()
            return True
        for w in list(Window.children):
            if isinstance(w, Popup):
                w.dismiss()
                return True
        fab = getattr(self.sm.current_screen, "fab", None)
        if fab is not None and fab.is_open:
            fab.close()
            return True
        d = getattr(self, "drawer", None)
        if d is not None and d.parent is not None:
            d.close()
            return True
        if self.back():
            return True
        # في الصفحة الرئيسية: لا نخرج من أول ضغطة، بل نطلب ضغطة ثانية للتأكيد
        now = time.time()
        if now - self._back_t < 2.0:
            return False
        self._back_t = now
        try:
            self.show_banner("اضغط رجوع مرة أخرى للخروج")
        except Exception:
            pass
        return True

    # ---- فتح الشاشات ----
    def open_add_sale(self, cust=None):
        if self.need_edit():
            self.push(lambda: AddSalePage(self, cust))

    def open_receipt(self, kind="c", pid=None, mixed=False):
        if self.need_edit():
            if kind == "s" and not self.need_pro():
                return
            self.push(lambda: AddVoucherPage(self, kind, pid, mixed=mixed))

    def open_purchase(self, sup=None):
        if self.need_edit():
            self.push(lambda: AddPurchasePage(self, sup))

    def open_parties(self, kind):
        if kind == "s" and not self.need_pro():
            return
        self.push(lambda: PartiesPage(self, kind))

    def open_workers(self):
        if self.need_pro():
            self.push(lambda: WorkersPage(self))

    # ---- فتح العمليات للتعديل ----
    def open_op(self, o):
        t, rid = o.get("type"), o.get("id")
        if t == "sale":
            self.open_sale(rid)
        elif t == "voucher":
            self.open_voucher(rid)
        elif t == "purchase":
            self.open_purchase_edit(rid)

    def _sale_page(self, sid):
        rec = find(self.db["sales"], sid)
        return AddSalePage(self, rec=rec) if rec else HomePage(self)

    def _voucher_page(self, xid):
        rec = find(self.db["vouchers"], xid)
        return AddVoucherPage(self, rec=rec) if rec else HomePage(self)

    def _purchase_page(self, pid):
        rec = find(self.db["purchases"], pid)
        return AddPurchasePage(self, rec=rec) if rec else HomePage(self)

    def open_sale(self, sid):
        if not find(self.db["sales"], sid):
            return
        if self.data.get("role") == "viewer":
            return self.show_sale(sid)
        self.push(lambda: self._sale_page(sid))

    def open_voucher(self, xid):
        if not find(self.db["vouchers"], xid):
            return
        if self.data.get("role") == "viewer":
            return self.show_voucher(xid)
        self.push(lambda: self._voucher_page(xid))

    def open_purchase_edit(self, pid):
        p = find(self.db["purchases"], pid)
        if not p:
            return
        if self.data.get("role") == "viewer":
            lines = [f"{it['name']} - {fmt(it['qty'])} {it['unit']} × {money(it['cost'])}"
                     for it in p.get("items", [])]
            return msg_popup("فاتورة شراء", "\n".join(lines) + f"\nالإجمالي: {money(purchase_total(p))}",
                             T.PRIMARY)
        self.push(lambda: self._purchase_page(pid))

    # ---- عرض العمليات ----
    def show_sale(self, sid, allow_delete=False):
        v = find(self.db["sales"], sid)
        if not v:
            return
        png, size, text, phone = safe_doc(sale_doc, self, v)
        extra = [("حذف", T.RED, lambda: self.delete_op("sales", sid))] if allow_delete else None
        show_doc_popup(png, size, text, phone, "الفاتورة", extra)

    def show_voucher(self, xid, allow_delete=False):
        x = find(self.db["vouchers"], xid)
        if not x:
            return
        png, size, text, phone = safe_doc(voucher_doc, self, x)
        extra = [("حذف", T.RED, lambda: self.delete_op("vouchers", xid))] if allow_delete else None
        show_doc_popup(png, size, text, phone, "السند", extra)

    def delete_op(self, coll, rid):
        if not self.need_edit():
            return

        def go():
            delete_rec(self.db, coll, rid)
            self.save()
            self.reload()
        confirm_popup("تأكيد", "حذف هذه العملية؟", go)

    # ---- الدخول والتسجيل ----
    def verify_login(self, email, pw, code, viewer):
            global SESSION_TOKEN
            try:
                result = worker_auth("/v1/auth/login", {"email":email,"password":pw,"code":code,"viewer":bool(viewer),"device_id":self.data.get("device_id"),"device_name":device_name()})
                if not result.get("ok", True): return False, str(result.get("error") or result.get("description") or "تعذر تسجيل الدخول")
                SESSION_TOKEN = str(result.get("session") or result.get("session_token") or "")
                if not SESSION_TOKEN: return False, "تعذر إنشاء جلسة"
                self.data["session_token"] = SESSION_TOKEN
                self.save()
                if result.get("bundled") and isinstance(result.get("rec"), dict):
                    # الـ Worker الجديد يرجع الملف والبيانات مع الدخول: طلب واحد بدل ثلاثة.
                    rec = dict(result["rec"]); rec.setdefault("email", email)
                    entry = dict(result.get("entry") or {})
                    entry.setdefault("plan", rec.get("plan", "free"))
                    entry["expr"] = str(entry.get("expr", rec.get("quota", FREE_START_INVOICES)))
                    return True, {"rec":rec,"entry":entry,"server":result.get("server") or {}}
                profile = DB._unwrap(worker_request("GET", "/v1/app/profile"), "profile")
                rec = dict(profile); rec.setdefault("email", email)
                entry = {"plan": profile.get("plan", "free"), "expr": str(profile.get("quota", FREE_START_INVOICES))}
                server, _version = DB.read_clients(rec)
                return True, {"rec":rec,"entry":entry,"server":server}
            except Exception as e: return False, str(e)[:100]

    def _apply_entry(self, entry):
        entry = dict(entry or {})
        entry.setdefault("plan", "free")
        entry.setdefault("expr", entry.get("quota", FREE_START_INVOICES))
        allowed = eval_quota(entry["expr"])
        if allowed is None:      # صيغة غير مفهومة: نُبقي القيمة السابقة بدل تصفير الحساب
            old = self.data.get("sub") or {}
            allowed = old.get("allowed", 0) if old.get("plan") == entry["plan"] else 0
        self.data["sub"] = {"plan": entry["plan"], "allowed": allowed}

    def _claim(self, email):
        if self.data.get("owner") and self.data["owner"] != email:
            self.data["db"] = new_db()
            self.data["pending"] = 0
        self.data["owner"] = email

    def after_auth(self):
        """بعد الدخول/التسجيل: الاسم والرقم (إن نقصا) ثم إشعار النسخة المجانية ثم الرئيسية."""
        u = self.data.get("user") or {}
        viewer = self.data.get("role") == "viewer"

        def to_free():
            email = (self.data.get("user") or {}).get("email", "")
            seen = self.data.setdefault("free_seen", {})
            if self.plan() == "free" and email and not seen.get(email) and not viewer:
                seen[email] = True
                self.save(auto=False)
                self.stack = [lambda: FreeNoticePage(self, self.go_home)]
                self._display(FreeNoticePage(self, self.go_home), "left")
            else:
                self.go_home()
        if not (u.get("phone") or "").strip() and not viewer:
            self.stack = [lambda: ProfileSetupPage(self, to_free)]
            self._display(ProfileSetupPage(self, to_free), "left")
        else:
            to_free()

    def on_login_success(self, info, viewer, adding=False):
        rec = info["rec"]
        if adding:
            self._begin_add(rec["email"])
        self._claim(rec["email"])
        self.data["user"] = {"email": rec["email"], "name": rec.get("name", ""),
                             "phone": rec.get("phone", ""), "has_second": bool(rec.get("has_second", True))}
        self.data["role"] = "viewer" if viewer else "editor"
        self._apply_entry(info["entry"])
        try:
            self.data["db"] = merge_db(info.get("server"), self.db)
        except Exception as e:
            print(f"[LOGIN MERGE] {e}")
        self.save()
        self.after_auth()

    def on_register_success(self, email, name, phone="", adding=False):
        if adding: self._begin_add(email)
        self._claim(email)
        self.data["user"] = {"email":email,"name":name,"phone":phone,"has_second":False}
        self.data["role"] = "editor"
        self.data["sub"] = {"plan":"free","allowed":FREE_START_INVOICES}
        self.data["pending"] = 0
        self.save(); self.after_auth()

    def do_logout(self, *a):
        global SESSION_TOKEN
        SESSION_TOKEN = ""
        self.data["session_token"] = ""
        self.data["user"] = None
        self.save()
        self.go_login()

    # ---- المزامنة ----
    def do_sync(self):
        viewer = self.data.get("role") == "viewer"
        if viewer:
            msg_popup("المزامنة", "وضع المشاهدة يقرأ أحدث بيانات من السحابة فقط.", T.ORANGE)
        page = SyncPage(self)
        self.stack.append(lambda: SyncPage(self))
        self._display(page, "left")
        email = (self.data.get("user") or {}).get("email")
        snap = copy.deepcopy(self.db)
        s0 = self._db_sig()

        def st(m, s=""):
            Clock.schedule_once(lambda dt: page.status(m, s), 0)

        def worker():
            try:
                st("جاري المزامنة...")
                res = self.sync_server(email, snap, viewer)
                if res.get("missing"):
                    return st("الحساب غير موجود على الانترنت", "ربما حُذف")
                if res.get("kicked"):
                    Clock.schedule_once(lambda dt: self._kicked_out(), 0)
                    return st("تم إخراج هذا الجهاز من الحساب")

                def apply(dt):
                    try:
                        self.apply_merged(res["merged"], res["entry"], email, s0)
                        save_json(DATA_FILE, self.data)
                        db = self.db
                        page.status("تمت المزامنة بنجاح", f"{len(db['customers'])} عميل - "
                                    f"{len(db['sales'])} فاتورة - الباقة: {res['entry']['plan'].upper()}")
                    except Exception as e:
                        _write_crash("SYNC APPLY: " + traceback.format_exc())
                        page.status("فشلت المزامنة", str(e)[:70])
                Clock.schedule_once(apply, 0)
            except Exception as e:
                print(f"[SYNC] {e}")
                st("فشلت المزامنة", str(e)[:70])
        threading.Thread(target=worker, daemon=True).start()

    def _kicked_out(self):
        self.do_logout()
        msg_popup("تسجيل الخروج", "تم إخراج هذا الجهاز من الحساب من جهاز آخر.", T.ORANGE)

    # ---- حساب ----
    def change_password_remote(self, old, new):
        try:
            email = (self.data.get("user") or {}).get("email", "")
            worker_request("PATCH", "/v1/app/profile", {"old_password": old, "password": new})
            return True, ""
        except Exception as e:
            return False, str(e)[:60]

    def change_phone_remote(self, phone):
        try:
            email = (self.data.get("user") or {}).get("email", "")
            rec, mid, _e = DB.get_user(email)
            if not rec:
                return False, "الحساب غير موجود"
            rec["phone"] = phone
            DB.save_user(rec, mid)
            return True, ""
        except Exception as e:
            return False, str(e)[:60]

    def change_owner_remote(self, new):
        try:
            email = (self.data.get("user") or {}).get("email", "")
            rec, mid, _e = DB.get_user(email)
            if not rec:
                return False, "الحساب غير موجود"
            rec["name"] = new
            DB.save_user(rec, mid)
            return True, ""
        except Exception as e:
            return False, str(e)[:60]

    def change_code_remote(self, old, new):
        try:
            email = (self.data.get("user") or {}).get("email", "")
            worker_request("PATCH", "/v1/app/profile", {"old_code": old, "code": new})
            return True, ""
        except Exception as e:
            return False, str(e)[:60]

    def delete_account_remote(self, code):
        try:
            email = (self.data.get("user") or {}).get("email", "")
            worker_request("DELETE", "/v1/app/profile", {"code": code, "password": code})
            return True, ""
        except Exception as e:
            return False, str(e)[:60]

    def wipe_local(self):
        keep = {k: self.data.get(k) for k in ("device_id", "font", "theme", "dl", "accounts", "updates", "notes_read") if self.data.get(k) is not None}
        for p in (DATA_FILE, DATA_FILE + ".tmp"):
            try:
                os.remove(p)
            except Exception:
                pass
        self.data = load_data()
        self.data.update(keep)
        self.save()
        self.go_login()

    def set_font(self, fn):
        if set_app_font(fn):
            self.data["font"] = fn
            self.save()
            refresh_all_texts()

    def toggle_theme(self):
        new = "light" if is_dark() else "dark"
        ov = None
        try:
            ov = FadeOverlay(T.BG)
            Window.add_widget(ov)
        except Exception:
            ov = None
        apply_theme(new)
        self.data["theme"] = new
        self.save()
        self.reload()
        if ov is not None:
            Animation(opacity=0, duration=0.45, t="out_quad").start(ov)
            Clock.schedule_once(lambda dt: Window.remove_widget(ov), 0.5)

    def _show_last_crash(self, dt=None):
        try:
            if not os.path.exists(CRASH_LOG):
                return
            with open(CRASH_LOG, "r", encoding="utf-8") as f:
                txt = f.read()[-1200:]
            os.remove(CRASH_LOG)
            if txt.strip():
                from kivy.uix.label import Label as KLabel
                box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
                box.add_widget(mk_text("سجل آخر خطأ (صوّره وأرسله للمطوّر):", dp(14), T.RED,
                                       halign="center", height=dp(30)))
                lbl = KLabel(text=txt, color=(0.3, 0.3, 0.3, 1), font_size=dp(10), halign="left", valign="top")
                lbl.bind(size=lambda w, v: setattr(w, "text_size", (v[0], None)))
                box.add_widget(lbl)
                ok = mk_btn("حسناً", height=dp(46), background_color=T.GRAY_BTN)
                box.add_widget(ok)
                pop = make_arabic_popup("تقرير خطأ", box, size_hint=(0.95, 0.7),
                                        title_color=T.RED, separator_color=T.RED)
                ok.bind(on_release=lambda *a: pop.dismiss())
                pop.open()
        except Exception as e:
            print(f"[CRASHLOG] {e}")


def run():
    try:
        AccountingApp().run()
    except Exception:
        _write_crash(traceback.format_exc())
        raise


if __name__ == "__main__":      # لو شُغّل هذا الملف مباشرة بدل main.py
    run()
