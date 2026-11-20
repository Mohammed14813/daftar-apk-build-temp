# -*- coding: utf-8 -*-
"""
main.py  —  مُحمِّل «دفتر المحاسب»
----------------------------------
التدفق (تلقائي بالكامل، بدون أي زر، وبدون إغلاق التطبيق):
  1) إن وُجدت نسخة محفوظة من قبل تُشغَّل فوراً.
  2) إن لم توجد: يُنزَّل app-bundle.zip من جيت هاب تلقائياً (مهلة قصيرة)،
     وإن لم يتوفر إنترنت تُفكّ الحزمة المضمّنة داخل الـ APK.
  3) تُشغَّل الواجهة الحقيقية في نفس العملية ونفس نافذة Kivy.
  4) في الخلفية يفحص التطبيق جيت هاب كل 5 دقائق وينزّل أي تحديث بصمت
     ويُطبَّق عند الفتح القادم.

سبب الخروج المفاجئ سابقاً: كان اللودر ينشئ تطبيق Kivy أولاً ثم يستدعي stop()
ثم يشغّل تطبيق Kivy ثانياً؛ وعلى أندرويد إغلاق التطبيق الأول ينهي النشاط
(Activity) فيخرج المستخدم. الآن لا يُنشأ إلا تطبيق Kivy واحد.
"""
import os
import sys
import json
import time
import shutil
import threading
import traceback
import zipfile
import io
import ssl
import urllib.request

# ============================ الإعدادات (عدّل هذه فقط) ============================
GITHUB_USER = "Mohammed14813"
GITHUB_REPO = "Integrated-Accountant-Updates"
GITHUB_BRANCH = "main"
BUNDLE_FILE = "app-bundle.zip"           # ملف التطبيق المضغوط داخل المستودع (فيه main.py وباقي الملفات)
GITHUB_TOKEN = ""  # الرمز لا يُخزّن داخل التطبيق؛ يبقى على الخادم فقط
APP_TITLE = "دفتر المحاسب"
APP_SUBTITLE = "حساباتك بدقة وأمان"
CHECK_EVERY_LAUNCH = True                # فحص التحديث في كل تشغيل
# ================================================================================

from kivy.app import App
from kivy.clock import Clock
from kivy.core.text import LabelBase
from kivy.graphics import Color, RoundedRectangle, Ellipse, Rectangle
from kivy.metrics import dp
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.widget import Widget
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.popup import Popup
from kivy.utils import platform

BLUE = (0.10, 0.27, 0.80, 1)
BLUE_LIGHT = (0.16, 0.34, 0.86, 1)
LOADER_DIR = os.path.dirname(os.path.abspath(__file__))


def _data_dir():
    try:
        if platform == "android":
            from android.storage import app_storage_path
            return app_storage_path()
    except Exception:
        pass
    return LOADER_DIR


DATA = _data_dir()
CODE_DIR = os.path.join(DATA, "app_code")           # هنا تُحفظ ملفات التطبيق المنزّلة
STAMP = os.path.join(DATA, "app_code_version.json")  # آخر نسخة نزّلناها

# ----------------------------- تشكيل الحروف العربية -----------------------------
ARABIC_FORMS = {
    '\u0627': ('\uFE8D', '\uFE8D', '\uFE8E', '\uFE8E', False),
    '\u0622': ('\uFE81', '\uFE81', '\uFE82', '\uFE82', False),
    '\u0623': ('\uFE83', '\uFE83', '\uFE84', '\uFE84', False),
    '\u0625': ('\uFE87', '\uFE87', '\uFE88', '\uFE88', False),
    '\u0624': ('\uFE85', '\uFE85', '\uFE86', '\uFE86', False),
    '\u0629': ('\uFE93', '\uFE93', '\uFE94', '\uFE94', False),
    '\u062F': ('\uFEA9', '\uFEA9', '\uFEAA', '\uFEAA', False),
    '\u0630': ('\uFEAB', '\uFEAB', '\uFEAC', '\uFEAC', False),
    '\u0631': ('\uFEAD', '\uFEAD', '\uFEAE', '\uFEAE', False),
    '\u0632': ('\uFEAF', '\uFEAF', '\uFEB0', '\uFEB0', False),
    '\u0648': ('\uFEED', '\uFEED', '\uFEEE', '\uFEEE', False),
    '\u0649': ('\uFEEF', '\uFEEF', '\uFEF0', '\uFEF0', False),
    '\u0621': ('\uFE80', '\uFE80', '\uFE80', '\uFE80', False),
    '\u0626': ('\uFE89', '\uFE8B', '\uFE8C', '\uFE8A', True),
    '\u0628': ('\uFE8F', '\uFE91', '\uFE92', '\uFE90', True),
    '\u062A': ('\uFE95', '\uFE97', '\uFE98', '\uFE96', True),
    '\u062B': ('\uFE99', '\uFE9B', '\uFE9C', '\uFE9A', True),
    '\u062C': ('\uFE9D', '\uFE9F', '\uFEA0', '\uFE9E', True),
    '\u062D': ('\uFEA1', '\uFEA3', '\uFEA4', '\uFEA2', True),
    '\u062E': ('\uFEA5', '\uFEA7', '\uFEA8', '\uFEA6', True),
    '\u0633': ('\uFEB1', '\uFEB3', '\uFEB4', '\uFEB2', True),
    '\u0634': ('\uFEB5', '\uFEB7', '\uFEB8', '\uFEB6', True),
    '\u0635': ('\uFEB9', '\uFEBB', '\uFEBC', '\uFEBA', True),
    '\u0636': ('\uFEBD', '\uFEBF', '\uFEC0', '\uFEBE', True),
    '\u0637': ('\uFEC1', '\uFEC3', '\uFEC4', '\uFEC2', True),
    '\u0638': ('\uFEC5', '\uFEC7', '\uFEC8', '\uFEC6', True),
    '\u0639': ('\uFEC9', '\uFECB', '\uFECC', '\uFECA', True),
    '\u063A': ('\uFECD', '\uFECF', '\uFED0', '\uFECE', True),
    '\u0641': ('\uFED1', '\uFED3', '\uFED4', '\uFED2', True),
    '\u0642': ('\uFED5', '\uFED7', '\uFED8', '\uFED6', True),
    '\u0643': ('\uFED9', '\uFEDB', '\uFEDC', '\uFEDA', True),
    '\u0644': ('\uFEDD', '\uFEDF', '\uFEE0', '\uFEDE', True),
    '\u0645': ('\uFEE1', '\uFEE3', '\uFEE4', '\uFEE2', True),
    '\u0646': ('\uFEE5', '\uFEE7', '\uFEE8', '\uFEE6', True),
    '\u0647': ('\uFEE9', '\uFEEB', '\uFEEC', '\uFEEA', True),
    '\u064A': ('\uFEF1', '\uFEF3', '\uFEF4', '\uFEF2', True),
}
DIACRITICS = set('\u064B\u064C\u064D\u064E\u064F\u0650\u0651\u0652\u0653\u0654\u0655\u0670\u0640')
LAM_ALEF = {'\u0644\u0622': ('\uFEF5', '\uFEF6'), '\u0644\u0623': ('\uFEF7', '\uFEF8'),
            '\u0644\u0625': ('\uFEF9', '\uFEFA'), '\u0644\u0627': ('\uFEFB', '\uFEFC')}


def is_arabic_char(ch):
    return ('\u0600' <= ch <= '\u06FF') or ('\uFB50' <= ch <= '\uFEFF')


def _prev_element_connects(chars, idx):
    for j in range(idx - 1, -1, -1):
        kind, ch = chars[j]
        if kind == 'C':
            if ch in DIACRITICS:
                continue
            return ARABIC_FORMS[ch][4] if ch in ARABIC_FORMS else False
        if kind == 'PAIR':
            return False
    return False


def _has_element_after(chars, idx):
    for j in range(idx + 1, len(chars)):
        kind, ch = chars[j]
        if kind == 'C':
            if ch in DIACRITICS:
                continue
            return ch in ARABIC_FORMS
        if kind == 'PAIR':
            return True
    return False


def shape_word(word):
    if not word:
        return ""
    chars, i = [], 0
    while i < len(word):
        if i + 1 < len(word) and word[i:i + 2] in LAM_ALEF:
            chars.append(('PAIR', word[i:i + 2]))
            i += 2
            continue
        chars.append(('C', word[i]))
        i += 1
    result = []
    for idx, (kind, value) in enumerate(chars):
        if kind == 'PAIR':
            iso, fin = LAM_ALEF[value]
            result.append(fin if _prev_element_connects(chars, idx) else iso)
            continue
        ch = value
        if ch in DIACRITICS or ch not in ARABIC_FORMS:
            result.append(ch)
            continue
        isolated, initial, medial, final, fwd = ARABIC_FORMS[ch]
        prev_c = _prev_element_connects(chars, idx)
        nxt = _has_element_after(chars, idx)
        if prev_c and fwd and nxt:
            result.append(medial)
        elif prev_c:
            result.append(final)
        elif fwd and nxt:
            result.append(initial)
        else:
            result.append(isolated)
    result.reverse()
    return ''.join(result)


_BRACKETS = set("()[]{}")
_MIRROR = str.maketrans("()[]{}", ")(][}{")


def ar(text):
    if not text:
        return ""
    text = str(text)
    segments, current, cur_ar = [], [], None
    for ch in text:
        if ch.isspace() or ch in _BRACKETS:
            if current:
                segments.append((cur_ar, ''.join(current)))
                current, cur_ar = [], None
            segments.append((None, ch))
        else:
            a = is_arabic_char(ch)
            if cur_ar is None:
                cur_ar = a
                current.append(ch)
            elif a == cur_ar:
                current.append(ch)
            else:
                segments.append((cur_ar, ''.join(current)))
                current, cur_ar = [ch], a
    if current:
        segments.append((cur_ar, ''.join(current)))
    out = [shape_word(s) if a is True else s for a, s in segments]
    if any(a is True for a, _ in segments):
        # عند قلب السطر تُعكس الأقواس أيضاً ليظهر (اختياري) سليماً
        out = [s.translate(_MIRROR) if (a is None and s in _BRACKETS) else s
               for (a, _o), s in zip(segments, out)]
        out.reverse()
    return ''.join(out)


# ----------------------------- الخط -----------------------------
_FONT = "Roboto"
for _p in (os.path.join(LOADER_DIR, "font.otf"), os.path.join(CODE_DIR, "font.otf")):
    if os.path.exists(_p):
        try:
            LabelBase.register(name="LoaderFont", fn_regular=_p)
            _FONT = "LoaderFont"
            break
        except Exception:
            pass


# ----------------------------- الشبكة والتنزيل -----------------------------
def _ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _bundle_url():
    """رابط ملف التحديث: مع التوكن نستخدم واجهة GitHub API (تعمل مع المستودع الخاص)."""
    if GITHUB_TOKEN:
        return "https://api.github.com/repos/%s/%s/contents/%s?ref=%s" % (
            GITHUB_USER, GITHUB_REPO, BUNDLE_FILE, GITHUB_BRANCH)
    return "https://raw.githubusercontent.com/%s/%s/%s/%s" % (
        GITHUB_USER, GITHUB_REPO, GITHUB_BRANCH, BUNDLE_FILE)


def _get(url, timeout=25, binary=True, progress=None):
    h = {"User-Agent": "DaftarLoader/1.0"}
    if GITHUB_TOKEN:
        h["Authorization"] = "Bearer " + GITHUB_TOKEN
        h["Accept"] = "application/vnd.github.raw"       # يرجع محتوى الملف الخام مباشرة
        h["X-GitHub-Api-Version"] = "2022-11-28"
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
        total = int(r.headers.get("Content-Length") or 0)
        buf, got = io.BytesIO(), 0
        while True:
            chunk = r.read(32768)
            if not chunk:
                break
            buf.write(chunk)
            got += len(chunk)
            if progress and total:
                progress(got / float(total))
        data = buf.getvalue()
    return data if binary else data.decode("utf-8")


def _remote_sha():
    """رقم آخر تعديل على ملف app-bundle.zip (تغيّر الملفات الأخرى لا يعتبر تحديثاً)."""
    url = "https://api.github.com/repos/%s/%s/commits?path=%s&sha=%s&per_page=1" % (
        GITHUB_USER, GITHUB_REPO, BUNDLE_FILE, GITHUB_BRANCH)
    h = {"User-Agent": "DaftarLoader/1.0", "Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        h["Authorization"] = "Bearer " + GITHUB_TOKEN
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=8, context=_ctx()) as r:
        arr = json.loads(r.read().decode("utf-8"))
    if not arr:
        raise RuntimeError("bundle not found")
    return str(arr[0]["sha"])


def _read_stamp():
    try:
        with open(STAMP, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _have_code():
    return os.path.isdir(CODE_DIR) and os.path.exists(os.path.join(CODE_DIR, "main.py"))


def _install_bytes(data, progress=lambda p: None, stage_only=False):
    """يستخرج محتوى app-bundle.zip (بايتات) إلى مجلد مؤقت ثم يبدّل به المجلد القديم."""
    progress(0.75)
    tmp = CODE_DIR + "_new"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        mains = sorted([x for x in names if x.endswith("main.py") and not x.startswith("__MACOSX")],
                       key=lambda x: x.count("/"))
        if not mains:
            raise RuntimeError("لم أجد main.py داخل app-bundle.zip")
        prefix = mains[0][:-len("main.py")]                       # قد يكون فارغاً أو اسم مجلد
        n = 0
        for m in z.infolist():
            if m.is_dir() or not m.filename.startswith(prefix):
                continue
            rel = m.filename[len(prefix):]
            if not rel or rel.startswith(".git"):
                continue
            dest = os.path.join(tmp, rel)
            if not os.path.abspath(dest).startswith(os.path.abspath(tmp)):
                continue                                         # حماية من مسارات خبيثة
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with z.open(m) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            n += 1
    if not os.path.exists(os.path.join(tmp, "main.py")):
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError("لم أجد main.py داخل الملف")
    progress(0.9)
    if stage_only:                      # أثناء عمل التطبيق: نحفظ النسخة الجديدة وتُطبَّق عند الفتح القادم
        return n
    shutil.rmtree(CODE_DIR, ignore_errors=True)
    os.replace(tmp, CODE_DIR)
    return n


def _download(progress=lambda p: None, status=lambda t: None, stage_only=False,
              timeout=60, remote_sha=None):
    """ينزّل app-bundle.zip من جيت هاب ويثبّته (أو يحفظه للفتح القادم إن stage_only).
    يرمي استثناء عند الفشل. يسجّل رقم النسخة حتى لا يُعاد تنزيلها في كل مرة."""
    if remote_sha is None:
        try:
            remote_sha = _remote_sha()
        except Exception:
            remote_sha = ""
    status("جاري تنزيل ملفات التطبيق...")
    data = _get(_bundle_url(), timeout=timeout, progress=lambda p: progress(0.1 + 0.6 * p))
    status("جاري التجهيز...")
    n = _install_bytes(data, progress, stage_only)
    if stage_only:
        _write_stamp(pending=remote_sha or "unknown")
    else:
        _write_stamp(sha=remote_sha or "unknown", pending="", time=int(time.time()))
    return n

def _write_stamp(**kw):
    st = _read_stamp()
    st.update(kw)
    try:
        with open(STAMP, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except Exception:
        pass


def _apply_staged():
    """إن كان هناك تحديث تم تحميله سابقاً أثناء عمل التطبيق نطبّقه الآن."""
    tmp = CODE_DIR + "_new"
    if os.path.exists(os.path.join(tmp, "main.py")):
        try:
            shutil.rmtree(CODE_DIR, ignore_errors=True)
            os.replace(tmp, CODE_DIR)
            st = _read_stamp()
            _write_stamp(sha=st.get("pending") or st.get("sha") or "", pending="")
        except Exception:
            traceback.print_exc()


_update_prompt_active = False
_update_prompt_sha = ""


def _download_after_consent(remote_sha, popup, status_label):
    """ينزّل التحديث بعد موافقة المستخدم، من دون تنزيل صامت."""
    global _update_prompt_active
    popup.dismiss()
    status_label.text = ar("جاري تحميل التحديث...")

    def work():
        global _update_prompt_active
        try:
            _download(stage_only=True, remote_sha=remote_sha)
            Clock.schedule_once(lambda dt: status_label.__setattr__("text", ar("تم تحميل التحديث، سيُطبَّق عند فتح التطبيق القادم")), 0)
        except Exception:
            Clock.schedule_once(lambda dt: status_label.__setattr__("text", ar("تعذر تحميل التحديث، حاول لاحقًا")), 0)
        finally:
            _update_prompt_active = False

    threading.Thread(target=work, daemon=True).start()


def _show_update_prompt(remote_sha):
    """يعرض نافذة تأكيد على خيط واجهة Kivy قبل بدء تنزيل التحديث."""
    global _update_prompt_active, _update_prompt_sha
    if _update_prompt_active or not remote_sha:
        return
    _update_prompt_active = True
    _update_prompt_sha = remote_sha

    box = BoxLayout(orientation="vertical", spacing=dp(12), padding=dp(16))
    msg = Label(text=ar("يوجد تحديث جديد لتطبيق دفتر المحاسب.\nهل تريد تحميله الآن؟"),
                font_name=_FONT, font_size=dp(16), halign="center", valign="middle")
    msg.bind(size=lambda w, size: setattr(w, "text_size", (size[0], None)))
    buttons = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(10))
    no_btn = Button(text=ar("ليس الآن"), font_name=_FONT)
    yes_btn = Button(text=ar("نعم، تحميل"), font_name=_FONT)
    buttons.add_widget(no_btn)
    buttons.add_widget(yes_btn)
    box.add_widget(msg)
    box.add_widget(buttons)
    popup = Popup(title=ar("تحديث متوفر"), content=box, size_hint=(0.88, None), height=dp(220),
                  auto_dismiss=False)

    def later(*_):
        global _update_prompt_active
        popup.dismiss()
        _update_prompt_active = False

    no_btn.bind(on_release=later)
    yes_btn.bind(on_release=lambda *_: _download_after_consent(remote_sha, popup, msg))
    popup.open()


def _watch_updates():
    """يفحص التحديثات دوريًا، ولا يبدأ التنزيل إلا بعد موافقة المستخدم."""
    time.sleep(8)
    while True:
        try:
            st = _read_stamp()
            cur = st.get("pending") or st.get("sha") or ""
            remote = _remote_sha()
            if remote and remote != cur:
                Clock.schedule_once(lambda dt, sha=remote: _show_update_prompt(sha), 0)
        except Exception:
            pass                                   # لا إنترنت: نحاول في الدورة القادمة
        time.sleep(300)


def launch_app():
    """يشغّل التطبيق الحقيقي من الملفات المنزّلة داخل نفس العملية."""
    import runpy
    sys.path.insert(0, CODE_DIR)
    for k in ("main", "accounting_app", "accounting_app_v2"):
        sys.modules.pop(k, None)
    os.chdir(CODE_DIR)
    if CHECK_EVERY_LAUNCH:
        threading.Thread(target=_watch_updates, daemon=True).start()
    runpy.run_path(os.path.join(CODE_DIR, "main.py"), run_name="__main__")


# ----------------------------- الواجهة -----------------------------
class Splash(FloatLayout):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.p = 0.0
        self._shown = 0.0
        with self.canvas.before:
            Color(*BLUE)
            self._bg = Rectangle()
            Color(1, 1, 1, 0.06)
            self._e1 = Ellipse()
            Color(1, 1, 1, 0.05)
            self._e2 = Ellipse()
        self.art = Widget(size_hint=(1, 1))
        self.add_widget(self.art)
        self.title = Label(text=ar(APP_TITLE), font_name=_FONT, font_size=dp(30), bold=False,
                           color=(1, 1, 1, 1), size_hint=(1, None), height=dp(46))
        self.sub = Label(text=ar(APP_SUBTITLE), font_name=_FONT, font_size=dp(14),
                         color=(1, 1, 1, 0.78), size_hint=(1, None), height=dp(28))
        self.status = Label(text="", font_name=_FONT, font_size=dp(14), color=(1, 1, 1, 0.9),
                            size_hint=(1, None), height=dp(30))
        self.btn = Label(text=ar("تحميل الواجهة"), font_name=_FONT, font_size=dp(16),
                         color=BLUE, size_hint=(None, None), size=(dp(190), dp(48)), opacity=0)
        self.on_retry = None
        for w in (self.title, self.sub, self.status, self.btn):
            self.add_widget(w)
        self.bind(size=self._lay, pos=self._lay)
        Clock.schedule_interval(self._tick, 1 / 45.0)
        self._t = 0.0

    def set_status(self, text, err=False):
        self.status.text = ar(text)
        self.status.color = (1, 0.82, 0.82, 1) if err else (1, 1, 1, 0.9)

    def show_retry(self, cb):
        self.on_retry = cb
        self.btn.opacity = 1
        self._lay()

    def hide_retry(self):
        self.btn.opacity = 0
        self.on_retry = None

    def on_touch_down(self, touch):
        if self.on_retry and self.btn.opacity and self.btn.collide_point(*touch.pos):
            cb, self.on_retry = self.on_retry, None
            self.btn.opacity = 0
            cb()
            return True
        return super().on_touch_down(touch)

    def _lay(self, *a):
        W, H = self.size
        self._bg.pos, self._bg.size = self.pos, self.size
        self._e1.pos, self._e1.size = (self.x - dp(110), self.y + H * 0.55), (dp(320), dp(320))
        self._e2.pos, self._e2.size = (self.right - dp(150), self.y - dp(50)), (dp(260), dp(260))
        cx = self.x + W / 2.0
        self.title.pos = (self.x, self.y + H * 0.40)
        self.sub.pos = (self.x, self.y + H * 0.40 - dp(34))
        self.status.pos = (self.x, self.y + H * 0.17)
        self.btn.pos = (cx - dp(95), self.y + H * 0.17 - dp(64))
        self._draw()

    def _tick(self, dt):
        self._t += dt
        self._shown += (self.p - self._shown) * min(1.0, dt * 6)
        self._draw()

    def _draw(self):
        W, H = self.size
        if W < 10:
            return
        S = min(W, H) * 0.30 / 180.0
        cx, cy = self.x + W / 2.0, self.y + H * 0.64
        pulse = 1 + 0.025 * (0.5 - 0.5 * __import__("math").cos(self._t * 2.2))
        K = S * pulse
        X = lambda x: cx + (x - 190) * K
        Y = lambda y: cy + (190 - y) * K

        def rr(x, y, w, h, r):
            RoundedRectangle(pos=(X(x), Y(y + h)), size=(w * K, h * K), radius=[r * K])
        c = self.art.canvas
        c.clear()
        with c:
            Color(0.106, 0.384, 0.745, 1)
            rr(100, 100, 180, 180, 44)
            Color(0.169, 0.424, 0.769, 0.55)
            rr(100, 100, 180, 90, 44)
            Color(1, 1, 1, 1)
            rr(138, 136, 104, 104, 17)
            Color(0.81, 0.84, 0.88, 1)
            rr(150.5, 149, 54, 5, 2.5)
            rr(150.5, 159, 31, 5, 2.5)
            for (x, y, w, h, col) in ((154, 202, 18, 24, (0.37, 0.68, 0.94)), (179, 186, 18, 40, (0.11, 0.47, 0.86)),
                                      (204, 166, 18, 60, (1.0, 0.70, 0.0))):
                Color(*col, 1)
                rr(x, y, w, h, 4)
            Color(1, 1, 1, 1)
            Ellipse(pos=(X(209), Y(257)), size=(48 * K, 48 * K))
            Color(0.18, 0.73, 0.38, 1)
            Ellipse(pos=(X(213), Y(253)), size=(40 * K, 40 * K))
            # شريط التقدم
            bw, bh = W * 0.62, dp(8)
            bx, by = self.x + (W - bw) / 2.0, self.y + H * 0.26
            Color(1, 1, 1, 0.22)
            RoundedRectangle(pos=(bx, by), size=(bw, bh), radius=[bh / 2.0])
            Color(1, 1, 1, 1)
            RoundedRectangle(pos=(bx, by), size=(max(bh, bw * max(0.02, min(1.0, self._shown))), bh), radius=[bh / 2.0])



class ErrorApp(App):
    """تُعرض فقط إذا تعذّر تحضير الملفات كلياً (لا نسخة محفوظة ولا إنترنت ولا حزمة مضمّنة)."""
    title = "Daftar"

    def build(self):
        ui = Splash()
        ui.set_status("تعذّر تجهيز التطبيق. تأكد من الإنترنت ثم أعد فتح التطبيق", True)
        return ui


def _install_bundled():
    """يفكّ الحزمة المضمّنة داخل الـ APK محلياً (بدون إنترنت)."""
    local_bundle = os.path.join(LOADER_DIR, BUNDLE_FILE)
    if not os.path.exists(local_bundle):
        return False
    try:
        with open(local_bundle, "rb") as f:
            _install_bytes(f.read())
        # "bundled" يختلف عن أي sha بعيد، فينزّل الخيط الخلفي نسخة جيت هاب لاحقاً إن وُجدت
        _write_stamp(sha="bundled", pending="", time=int(time.time()))
        return True
    except Exception:
        _log_crash("install bundled")
        return False


def _log_crash(tag):
    try:
        with open(os.path.join(DATA, "loader_crash.log"), "a", encoding="utf-8") as f:
            f.write("[%s] %s\n%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), tag, traceback.format_exc()))
    except Exception:
        pass


def prepare_code():
    """يجهّز الملفات؛ يفضّل الحزمة المضمّنة، ثم يطلب التأكيد للتحديثات اللاحقة."""
    _apply_staged()
    if _have_code():
        return True
    # الحزمة المضمّنة هي النسخة الأساسية عند أول تشغيل، فلا يبدأ تنزيلًا مخفيًا.
    if _install_bundled():
        return True
    try:
        # حالة نادرة: APK بلا حزمة مضمّنة. لا يوجد كود يمكن تشغيله قبل تنزيله.
        _download(timeout=12)
        return True
    except Exception:
        _log_crash("github download")
    return False


if __name__ == "__main__":
    if prepare_code():
        launch_app()
    else:
        ErrorApp().run()
