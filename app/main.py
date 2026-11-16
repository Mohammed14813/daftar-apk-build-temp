# -*- coding: utf-8 -*-
"""
main.py  (الكود الأول)  —  محرك الواجهة
-----------------------------------------
يحتوي: الحروف العربية، الألوان والوضع الليلي، الخطوط، الأزرار والحقول،
النوافذ، رسم الفواتير كصورة، ومشاركة واتساب.
وفي آخره يستدعي الكود الثاني accounting_app.py (المحاسب كاملاً).
"""
import os
import sys
import json
import re
import random
import string
import threading
import weakref
import traceback
from types import SimpleNamespace
from datetime import datetime

from kivy.app import App
from kivy.core.window import Window
from kivy.core.text import LabelBase, Label as CoreLabel
from kivy.uix.widget import Widget
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.textinput import TextInput
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.gridlayout import GridLayout
from kivy.uix.screenmanager import ScreenManager, Screen, SlideTransition
from kivy.graphics import (Color, Rectangle, RoundedRectangle, Ellipse,
                           PushMatrix, PopMatrix, Translate)
from kivy.animation import Animation
from kivy.metrics import dp
from kivy.utils import platform
from kivy.clock import Clock

# ============================================================
# 1) مسارات
# ============================================================
APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _data_dir():
    try:
        if platform == "android":
            from android.storage import app_storage_path
            return app_storage_path()
    except Exception:
        pass
    return APP_DIR


DATA_DIR = _data_dir()
DOC_DIR = os.path.join(DATA_DIR, "docs")
FONT_STORE = os.path.join(DATA_DIR, "dl_fonts")
CRASH_LOG = os.path.join(DATA_DIR, "crash.log")
for _d in (FONT_STORE,):
    try:
        os.makedirs(_d, exist_ok=True)
    except Exception:
        pass


def _write_crash(text):
    try:
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n" + text + "\n\n")
    except Exception:
        pass


def _excepthook(tp, val, tb):
    _write_crash("".join(traceback.format_exception(tp, val, tb)))
    sys.__excepthook__(tp, val, tb)


sys.excepthook = _excepthook

# أي خطأ داخل أحداث Kivy يُسجَّل ويبقى التطبيق مفتوحاً (بدل أن يُغلق فجأة)
try:
    from kivy.base import ExceptionHandler, ExceptionManager

    class _KeepAlive(ExceptionHandler):
        def handle_exception(self, inst):
            try:
                _write_crash("".join(traceback.format_exception(
                    type(inst), inst, inst.__traceback__)))
            except Exception:
                pass
            return ExceptionManager.PASS

    ExceptionManager.add_handler(_KeepAlive())
except Exception:
    pass


# ============================================================
# 2) أدوات عامة
# ============================================================
def gen_id():
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    return ts + "_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=6))


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def now_full():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def money(v):
    try:
        return f"{v:,.0f}"
    except Exception:
        return "0"


def money_signed(v):
    try:
        return ("-" if v < 0 else "") + f"{abs(v):,.0f}"
    except Exception:
        return "0"


def fmt(n):
    try:
        return f"{n:g}"
    except Exception:
        return str(n)


_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٬٫", "0123456789,.")


def num(s, default=0.0):
    s = str(s or "").translate(_AR_DIGITS).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return default


def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


_SAVE_LOCK = threading.Lock()


def save_json(path, data):
    tmp = path + ".tmp"
    with _SAVE_LOCK:                      # حفظ من خيطين معاً كان يسبب خطأ
        try:
            txt = json.dumps(data, ensure_ascii=False)      # المشفّر السريع (json.dump أبطأ بكثير)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(txt)
            os.replace(tmp, path)
        except Exception as e:
            print(f"[SAVE] {e}")


def _auto_padding():
    try:
        w = Window.width
        if w <= 0:
            return dp(14)
        return max(dp(10), min(dp(20), w * 0.03))
    except Exception:
        return dp(14)


# ============================================================
# 3) الألوان والوضع الليلي  (كل الألوان في T)
# ============================================================
WHITE = (1, 1, 1, 1)
SOFT_WHITE = (1, 1, 1, 0.85)
GLASS = (1, 1, 1, 0.22)

_LIGHT = dict(
    PRIMARY=(0.43, 0.22, 0.80, 1), HEADER=(0.058, 0.105, 0.130, 1),
    TEXT=(0.07, 0.11, 0.14, 1), MUTED=(0.40, 0.47, 0.52, 1),
    BG=(0.94, 0.96, 0.97, 1), CARD=(1, 1, 1, 1),
    SOFT_BLUE=(0.90, 0.95, 0.96, 1), INPUT_BG=(0.975, 0.985, 0.99, 1),
    INPUT_BORDER=(0.78, 0.84, 0.87, 1), HINT=(0.58, 0.65, 0.69, 1),
    BORDER=(0.88, 0.92, 0.94, 1), RED=(0.90, 0.22, 0.12, 1),
    INV_RED=(0.95, 0.24, 0.09, 1), GREEN=(0.12, 0.62, 0.45, 1),
    ORANGE=(1.00, 0.60, 0.04, 1), BLUE=(0.16, 0.50, 0.78, 1),
    GRAY_BTN=(0.40, 0.47, 0.52, 1), PURPLE=(0.72, 0.12, 0.92, 1),
    TEAL=(0.08, 0.60, 0.66, 1),
    NAV=(0.045, 0.085, 0.105, 1), NAV_LINE=(0.20, 0.30, 0.35, 1),
    NAV_OFF=(0.62, 0.72, 0.76, 1), GLOW=(0.10, 0.42, 0.52, 1),
)
_DARK = dict(
    PRIMARY=(0.62, 0.85, 0.92, 1), HEADER=(0.058, 0.105, 0.130, 1),
    TEXT=(0.93, 0.96, 0.97, 1), MUTED=(0.56, 0.66, 0.71, 1),
    BG=(0.050, 0.066, 0.078, 1), CARD=(0.078, 0.105, 0.124, 1),
    SOFT_BLUE=(0.09, 0.17, 0.21, 1), INPUT_BG=(0.060, 0.085, 0.100, 1),
    INPUT_BORDER=(0.17, 0.25, 0.30, 1), HINT=(0.38, 0.48, 0.53, 1),
    BORDER=(0.12, 0.18, 0.22, 1), RED=(0.96, 0.28, 0.18, 1),
    INV_RED=(0.96, 0.25, 0.10, 1), GREEN=(0.14, 0.70, 0.50, 1),
    ORANGE=(1.00, 0.62, 0.05, 1), BLUE=(0.20, 0.55, 0.85, 1),
    GRAY_BTN=(0.27, 0.35, 0.40, 1), PURPLE=(0.74, 0.16, 0.94, 1),
    TEAL=(0.10, 0.66, 0.72, 1),
    NAV=(0.040, 0.075, 0.095, 1), NAV_LINE=(0.16, 0.25, 0.30, 1),
    NAV_OFF=(0.56, 0.66, 0.71, 1), GLOW=(0.10, 0.42, 0.52, 1),
)
THEMES = {"light": _LIGHT, "dark": _DARK}
T = SimpleNamespace()
T.name = "light"


def is_dark():
    return T.name == "dark"


def apply_theme(name):
    name = name if name in THEMES else "light"
    T.name = name
    T.__dict__.update(THEMES[name])
    try:
        Window.clearcolor = T.BG
    except Exception:
        pass


apply_theme("light")

AVATAR_COLORS = [(0.18, 0.47, 0.80, 1), (0.55, 0.36, 0.75, 1), (0.13, 0.60, 0.36, 1),
                 (0.92, 0.55, 0.12, 1), (0.80, 0.30, 0.45, 1), (0.10, 0.58, 0.64, 1)]

# ============================================================
# 4) أشكال الحروف العربية
# ============================================================
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


# ============================================================
# 5) الخطوط
# ============================================================
FONT_NAME = "Roboto"
FONT_PATH = None
FONT_REGISTERED = False
USER_FONTS = {}
FONT_NAME_RE = re.compile(r"font\d*\.otf")


def _font_name():
    return FONT_NAME if FONT_REGISTERED else "Roboto"


def font_sort_key(fn):
    m = re.fullmatch(r"font(\d*)\.otf", fn)
    return int(m.group(1) or 0) if m else 10 ** 6


def font_label(fn):
    m = re.fullmatch(r"font(\d*)\.otf", fn)
    n = int(m.group(1) or 0) if m else 0
    return "الخط الأساسي" if n == 0 else "خط %d" % n


def _find_font_file(fn):
    for d in (APP_DIR, os.path.join(APP_DIR, "fonts"), DATA_DIR):
        p = os.path.join(d, fn)
        try:
            if os.path.exists(p) and os.path.getsize(p) > 1000:
                return p
        except Exception:
            pass
    return None


def register_user_fonts():
    cands = {}
    base = _find_font_file("font.otf")
    if base:
        cands["font.otf"] = base
    try:
        for f in os.listdir(FONT_STORE):
            p = os.path.join(FONT_STORE, f)
            if FONT_NAME_RE.fullmatch(f) and f != "font.otf" and os.path.getsize(p) > 1000:
                try:
                    with open(p, "rb") as fh:
                        sig = fh.read(4)
                    if sig in (b"OTTO", b"\x00\x01\x00\x00", b"true", b"typ1"):
                        cands[f] = p
                except Exception:
                    pass
    except Exception:
        pass
    for fn, p in cands.items():
        if USER_FONTS.get(fn, (None, None))[1] == p:
            continue
        nm = "UF_" + fn.replace(".", "_")
        try:
            LabelBase.register(name=nm, fn_regular=p)
            USER_FONTS[fn] = (nm, p)
        except Exception as e:
            print(f"[FONT] {fn}: {e}")


def set_app_font(fn):
    global FONT_NAME, FONT_PATH, FONT_REGISTERED
    info = USER_FONTS.get(fn)
    if not info:
        return False
    FONT_NAME, FONT_PATH = info
    FONT_REGISTERED = True
    return True


def _font_has_arabic(name):
    try:
        a = CoreLabel(text="\uFEE1\uFEE3\uFEE4\uFEA3", font_size=28, font_name=name)
        a.refresh()
        ta = a.texture
        if ta is None or ta.width < 4:
            return False
        b = CoreLabel(text="\U0010FFFD\U0010FFFD\U0010FFFD", font_size=28, font_name=name)
        b.refresh()
        tb = b.texture
        return tb is None or ta.size != tb.size or ta.pixels != tb.pixels
    except Exception:
        return False


def init_fallback_font():
    global FONT_NAME, FONT_PATH, FONT_REGISTERED
    pool = []
    for d in ("/system/fonts",):
        try:
            for f in sorted(os.listdir(d)):
                if f.lower().endswith((".ttf", ".otf")) and any(
                        k in f.lower() for k in ("arab", "naskh", "kufi", "fallback")):
                    pool.append(os.path.join(d, f))
        except Exception:
            pass
    for n, p in enumerate(pool, 1):
        name = "AppFont%d" % n
        try:
            LabelBase.register(name=name, fn_regular=p)
            if _font_has_arabic(name):
                FONT_NAME, FONT_PATH, FONT_REGISTERED = name, p, True
                return True
        except Exception as e:
            print(f"[FONT] {p}: {e}")
    return False


try:
    register_user_fonts()
    if not set_app_font("font.otf"):
        init_fallback_font()
except Exception as _fe:
    print(f"[FONT] init failed: {_fe}")


# ============================================================
# 6) DrawnText  (نص عربي مرسوم)
# ============================================================
_ALL_TEXTS = weakref.WeakSet()


class DrawnText(Widget):
    _tex_cache = {}

    def __init__(self, text="", font_size=dp(15), color=None, halign="right",
                 valign="middle", padding_x=None, min_size=dp(9),
                 line_gap=dp(3), font_override=None, **kwargs):
        super().__init__(**kwargs)
        self._font_override = font_override
        self._raw_text = ""
        self._lines = []
        self._font_size = float(font_size)
        self._color = color if color is not None else T.TEXT
        self._halign = halign
        self._valign = valign
        self._padding_x = _auto_padding() if padding_x is None else padding_x
        self._min_size = float(min_size)
        self._line_gap = line_gap
        self._rects = []
        self._trans = None
        _ALL_TEXTS.add(self)
        self._sig = None
        self.bind(size=self._on_size, pos=self._on_pos)
        self.text = text
        Clock.schedule_once(lambda dt: self._redraw(), 0.1)

    @property
    def text(self):
        return self._raw_text

    @text.setter
    def text(self, value):
        self._raw_text = str(value) if value is not None else ""
        self._lines = [ar(line) for line in self._raw_text.split("\n")]
        self._redraw()

    def _get_tex(self, display, size_int):
        fname = self._font_override or _font_name()
        key = (display, size_int, fname, tuple(self._color))
        cached = DrawnText._tex_cache.get(key)
        if cached is not None:
            return cached
        tex = None
        for opts in (dict(text=display, font_size=size_int, font_name=fname,
                          color=self._color, halign="left", valign="middle",
                          padding=(0, 0, 0, 0)),
                     dict(text=display, font_size=size_int, font_name=fname,
                          color=self._color),
                     dict(text=display, font_size=size_int, font_name=fname)):
            try:
                cl = CoreLabel(**opts)
                cl.refresh()
                tex = cl.texture
                if tex is not None:
                    break
            except Exception as e:
                print(f"[TEX] {e}")
        if tex is not None:
            if len(DrawnText._tex_cache) > 1500:
                DrawnText._tex_cache.clear()
            DrawnText._tex_cache[key] = tex
        return tex

    def _line_h(self, size_int):
        t = self._get_tex("م", size_int)
        return int(t.height) if t is not None and t.height > 0 else int(size_int * 1.35)

    def _render_at(self, size_int):
        h = self._line_h(size_int)
        return [((self._get_tex(line, size_int) if line else None), h) for line in self._lines]

    def _compute_locked(self, avail):
        size = float(self._font_size)
        for _ in range(6):
            size_int = max(int(self._min_size), int(round(size)))
            rendered = self._render_at(size_int)
            widest = max([t.width for t, _h in rendered if t is not None] or [0])
            if widest <= avail or size_int <= int(self._min_size):
                return size_int, rendered
            new_size = max(self._min_size, size * (avail / float(widest)) * 0.98)
            if new_size >= size - 0.5:
                return size_int, rendered
            size = new_size
        size_int = max(int(self._min_size), int(round(size)))
        return size_int, self._render_at(size_int)

    def _on_size(self, *a):
        if self.width >= 2 and self.height >= 2:
            self._redraw()

    def _on_pos(self, *a):
        if self._trans is not None:
            self._trans.xy = (int(round(self.x)), int(round(self.y)))

    def force_refresh(self):
        self._sig = None
        self._redraw()

    def _redraw(self):
        if self.width < 2 or self.height < 2:
            return
        if not any(self._lines):
            self.canvas.clear()
            self._trans = None
            self._sig = None
            return
        # لا نعيد الرسم إذا لم يتغير شيء (النص/الحجم/الخط/اللون) — كان هذا سبب البطء الأكبر
        sig = (int(self.width), int(self.height), tuple(self._lines),
               self._font_override or _font_name(), tuple(self._color), self._halign,
               self._valign, self._padding_x, self._font_size, self._min_size)
        if sig == self._sig and self._trans is not None:
            return
        self._sig = sig
        try:
            W, H = self.width, self.height
            avail = max(W - 2 * self._padding_x, dp(4))
            size_int, rendered = self._compute_locked(avail)
            self.canvas.clear()
            heights = [h for _t, h in rendered]
            total_h = sum(heights) + self._line_gap * (len(heights) - 1)
            y_top = H if self._valign == "top" else (total_h if self._valign == "bottom" else (H + total_h) / 2.0)
            with self.canvas:
                PushMatrix()
                self._trans = Translate(int(round(self.x)), int(round(self.y)))
                Color(1, 1, 1, 1)
                for tex, h in rendered:
                    y_bottom = y_top - h
                    if tex is not None:
                        tw, th = tex.width, tex.height
                        if tw > avail:
                            k = avail / float(tw)
                            tw, th = tw * k, th * k
                        if self._halign == "right":
                            x = W - tw - self._padding_x
                        elif self._halign == "center":
                            x = W / 2.0 - tw / 2.0
                        else:
                            x = self._padding_x
                        Rectangle(texture=tex, pos=(int(x), int(y_bottom + (h - th) / 2.0)),
                                  size=(tw, th))
                    y_top = y_bottom - self._line_gap
                PopMatrix()
        except Exception as e:
            print(f"[DrawnText] {e}")


def refresh_all_texts(*a):
    _REFRESH_EV[0] = None
    for w in list(_ALL_TEXTS):
        try:
            if w.parent is None or w.get_root_window() is None:
                continue                      # عنصر في صفحة مغلقة: لا داعي لرسمه
            w._redraw()
        except Exception:
            pass


_REFRESH_EV = [None]


def schedule_refresh(delay=0.05):
    """يجمع طلبات إعادة الرسم المتتالية في طلب واحد بدل تنفيذها كلها."""
    ev = _REFRESH_EV[0]
    if ev is not None:
        try:
            ev.cancel()
        except Exception:
            pass
    _REFRESH_EV[0] = Clock.schedule_once(refresh_all_texts, delay)


# ============================================================
# 7) أزرار / بطاقات / حقول
# ============================================================
class DrawnButton(FloatLayout):
    def __init__(self, text="", font_size=dp(15), color=WHITE,
                 background_color=(0.3, 0.5, 0.7, 1), halign="center",
                 on_release=None, **kwargs):
        kwargs.setdefault("size_hint_y", None)
        if "size" not in kwargs:
            kwargs.setdefault("height", dp(46))
        self._cbs = []
        super().__init__(**kwargs)
        self._bg_color = tuple(background_color)
        self._pressed = None
        with self.canvas.before:
            self._bg_col = Color(*self._bg_color)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(16)])
        self.bind(pos=self._upd, size=self._upd)
        self._dt = DrawnText(text=text, font_size=font_size, color=color, halign=halign,
                             size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
        self._dt.disabled = True
        self.add_widget(self._dt)
        if on_release:
            self._cbs.append(on_release)

    def _upd(self, *a):
        self._bg.pos, self._bg.size = self.pos, self.size
        if self._pressed:
            self._pressed.pos, self._pressed.size = self.pos, self.size

    def bind(self, **kwargs):
        if "on_release" in kwargs:
            self._cbs.append(kwargs.pop("on_release"))
        if kwargs:
            return super().bind(**kwargs)

    def set_bg(self, rgba):
        self._bg_color = tuple(rgba)
        self._bg_col.rgba = tuple(rgba)

    def _show_pressed(self):
        if self._pressed:
            return
        r, g, b = self._bg_color[:3]
        with self.canvas.before:
            Color(r * 0.75, g * 0.75, b * 0.75, 1)
            self._pressed = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(16)])

    def _hide_pressed(self):
        if self._pressed:
            try:
                self.canvas.before.remove(self._pressed)
            except Exception:
                pass
            self._pressed = None

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos) and not self.disabled:
            self._show_pressed()
            return True
        return super().on_touch_down(touch)

    def on_touch_up(self, touch):
        if self.collide_point(*touch.pos) and not self.disabled:
            self._hide_pressed()
            for cb in list(self._cbs):
                try:
                    cb(self)
                except Exception as e:
                    _write_crash(traceback.format_exc())
                    print(f"[CB] {e}")
            return True
        self._hide_pressed()
        return super().on_touch_up(touch)

    @property
    def text(self):
        return self._dt.text

    @text.setter
    def text(self, v):
        self._dt.text = v


def mk_btn(text, **kw):
    font_size = kw.pop("font_size", dp(15))
    color = kw.pop("color", WHITE)
    bg = kw.pop("background_color", (0.3, 0.5, 0.7, 1))
    on_release = kw.pop("on_release", None)
    return DrawnButton(text=text, font_size=font_size, color=color,
                       background_color=bg, on_release=on_release, **kw)


def mk_text(text, size=dp(15), color=None, halign="right", height=None,
            hint_y=None, width=None, hint_x=1, pad=None):
    kw = {}
    if height is not None:
        kw["size_hint_y"], kw["height"] = None, height
    elif hint_y is not None:
        kw["size_hint_y"] = hint_y
    else:
        kw["size_hint_y"] = 1
    if width is not None:
        kw["size_hint_x"], kw["width"] = None, width
    else:
        kw["size_hint_x"] = hint_x
    w = DrawnText(text=text, font_size=size, color=color if color is not None else T.TEXT,
                  halign=halign, padding_x=pad, **kw)
    w.disabled = True
    return w


class CardBox(BoxLayout):
    def __init__(self, on_tap=None, bg=None, radius=dp(18), shadow=True, **kwargs):
        super().__init__(**kwargs)
        self._on_tap = on_tap
        self._bg = tuple(bg if bg is not None else T.CARD)
        self._border_on = bool(is_dark() and shadow)
        self._inset = dp(1) if self._border_on else 0
        self._down = False
        self._start = (0, 0)
        self._has_shadow = shadow
        with self.canvas.before:
            if shadow:
                Color(0, 0, 0, 0.035)
                self._sh1 = RoundedRectangle(pos=(self.x - dp(2), self.y - dp(4)),
                                             size=(self.width + dp(4), self.height + dp(4)),
                                             radius=[radius + dp(2)])
                Color(0, 0, 0, 0.05)
                self._sh2 = RoundedRectangle(pos=(self.x - dp(1), self.y - dp(2)),
                                             size=(self.width + dp(2), self.height + dp(2)),
                                             radius=[radius + dp(1)])
            if self._border_on:
                Color(*T.BORDER)
                self._bd = RoundedRectangle(pos=self.pos, size=self.size, radius=[radius])
            self._col = Color(*self._bg)
            i = self._inset
            self._rect = RoundedRectangle(pos=(self.x + i, self.y + i),
                                          size=(self.width - 2 * i, self.height - 2 * i),
                                          radius=[radius])
        self.bind(pos=self._upd, size=self._upd)

    def _upd(self, *a):
        if self._has_shadow:
            self._sh1.pos = (self.x - dp(2), self.y - dp(4))
            self._sh1.size = (self.width + dp(4), self.height + dp(4))
            self._sh2.pos = (self.x - dp(1), self.y - dp(2))
            self._sh2.size = (self.width + dp(2), self.height + dp(2))
        i = self._inset
        if self._border_on:
            self._bd.pos, self._bd.size = self.pos, self.size
        self._rect.pos = (self.x + i, self.y + i)
        self._rect.size = (self.width - 2 * i, self.height - 2 * i)

    def _pressed_color(self):
        r, g, b, a = self._bg
        if is_dark():
            return (min(1, r + 0.05), min(1, g + 0.05), min(1, b + 0.07), a)
        return (r * 0.94, g * 0.94, b * 0.95, a)

    def on_touch_down(self, touch):
        if self._on_tap is None:
            return super().on_touch_down(touch)
        if self.collide_point(*touch.pos):
            self._down = True
            self._start = touch.pos
            self._col.rgba = self._pressed_color()
            return True
        return False

    def on_touch_move(self, touch):
        if self._on_tap is None:
            return super().on_touch_move(touch)
        if self._down and (abs(touch.y - self._start[1]) > dp(12)
                           or abs(touch.x - self._start[0]) > dp(12)):
            self._down = False
            self._col.rgba = self._bg
        return False

    def on_touch_up(self, touch):
        if self._on_tap is None:
            return super().on_touch_up(touch)
        if self._down:
            self._down = False
            self._col.rgba = self._bg
            if self.collide_point(*touch.pos):
                cb = self._on_tap
                Clock.schedule_once(lambda dt: cb(), 0)
            return True
        self._col.rgba = self._bg
        return False


class Avatar(FloatLayout):
    def __init__(self, name="", diameter=dp(48), **kwargs):
        kwargs["size_hint"] = (None, None)
        kwargs["size"] = (diameter, diameter)
        super().__init__(**kwargs)
        name = (name or "").strip()
        idx = sum(ord(c) for c in name) % len(AVATAR_COLORS)
        with self.canvas.before:
            Color(*AVATAR_COLORS[idx])
            self._circle = Ellipse(pos=self.pos, size=self.size)
        self.bind(pos=self._upd, size=self._upd)
        t = DrawnText(text=(name[0] if name else "?").upper(), font_size=dp(21),
                      color=WHITE, halign="center", padding_x=0,
                      size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
        t.disabled = True
        self.add_widget(t)

    def _upd(self, *a):
        self._circle.pos, self._circle.size = self.pos, self.size


class ArabicTextInput(FloatLayout):
    def __init__(self, hint_text="", multiline=False, input_filter=None,
                 font_size=dp(15), password=False, **kwargs):
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(46))
        super().__init__(**kwargs)
        self._font_size = font_size
        self._password = password
        self._padding_x = _auto_padding()
        self._cursor_visible = False
        self._blink_event = None
        with self.canvas.before:
            self._border_col = Color(*T.INPUT_BORDER)
            self._border = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(10)])
            Color(*T.INPUT_BG)
            self._bg = RoundedRectangle(pos=(self.x + dp(1), self.y + dp(1)),
                                        size=(self.width - dp(2), self.height - dp(2)),
                                        radius=[dp(9)])
        self._input = TextInput(
            multiline=multiline, input_filter=input_filter, password=password,
            font_size=font_size, background_color=(0, 0, 0, 0), background_normal="",
            background_active="", foreground_color=(0, 0, 0, 0), cursor_color=(0, 0, 0, 0),
            cursor_width=0, use_bubble=False, use_handles=False, hint_text="",
            padding=[dp(8), dp(10), dp(8), dp(10)], size_hint=(1, 1),
            pos_hint={"x": 0, "y": 0})
        self._display = DrawnText(text="", font_size=font_size, color=T.TEXT, halign="right",
                                  padding_x=self._padding_x, size_hint=(1, 1),
                                  pos_hint={"x": 0, "y": 0})
        self._display.disabled = True
        self._hint = DrawnText(text=hint_text, font_size=font_size, color=T.HINT,
                               halign="right", padding_x=self._padding_x,
                               size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
        self._hint.disabled = True
        with self.canvas:
            self._cursor_color = Color(0, 0, 0, 0)
            self._cursor_rect = Rectangle(pos=(0, 0), size=(0, 0))
        self.add_widget(self._hint)
        self.add_widget(self._display)
        self.add_widget(self._input)
        self._input.bind(text=lambda i, v: self._update_display())
        self._input.bind(focus=self._on_focus)
        self._input.bind(cursor=lambda *a: Clock.schedule_once(lambda dt: self._redraw_cursor(), 0))
        self.bind(pos=self._on_resize, size=self._on_resize)
        self._update_display()

    def _on_resize(self, *a):
        self._border.pos, self._border.size = self.pos, self.size
        self._bg.pos = (self.x + dp(1), self.y + dp(1))
        self._bg.size = (self.width - dp(2), self.height - dp(2))
        Clock.schedule_once(lambda dt: self._redraw_cursor(), 0)

    def _on_focus(self, inst, value):
        self._update_display()
        if value:
            self._border_col.rgba = T.BLUE
            self._stop_blink()
            self._cursor_visible = True
            self._blink_event = Clock.schedule_interval(self._blink, 0.5)
            Clock.schedule_once(lambda dt: self._redraw_cursor(), 0)
        else:
            self._border_col.rgba = T.INPUT_BORDER
            self._stop_blink()

    def _stop_blink(self):
        if self._blink_event:
            self._blink_event.cancel()
            self._blink_event = None
        self._cursor_visible = False
        self._cursor_color.a = 0

    def _blink(self, dt):
        self._cursor_visible = not self._cursor_visible
        self._redraw_cursor()

    def _shown(self):
        raw = self._input.text
        return "*" * len(raw) if self._password else raw

    def _measure(self, text):
        if not text:
            return 0
        try:
            cl = CoreLabel(text=text, font_size=int(self._font_size),
                           font_name=_font_name(), padding=(0, 0, 0, 0))
            cl.refresh()
            return cl.texture.size[0] if cl.texture else 0
        except Exception:
            return 0

    def _redraw_cursor(self):
        if not self._input.focus or not self._cursor_visible:
            self._cursor_color.a = 0
            return
        try:
            shown = self._shown()
            w = self._measure(ar(shown.split("\n")[-1])) if shown else 0
            cx = self.right - self._padding_x - w - (dp(2) if shown else 0)
            cx = max(self.x + dp(2), min(self.right - dp(2), cx))
            self._cursor_color.rgba = T.BLUE
            self._cursor_rect.pos = (cx, self.y + dp(9))
            self._cursor_rect.size = (dp(2), self.height - dp(18))
        except Exception as e:
            print(f"[Cursor] {e}")

    def _update_display(self):
        raw = self._input.text
        self._display.text = self._shown() if raw else ""
        self._hint.opacity = 0 if raw else 1
        Clock.schedule_once(lambda dt: self._redraw_cursor(), 0)

    def set_frozen(self, frozen):
        self._input.disabled = bool(frozen)
        self.opacity = 0.35 if frozen else 1
        if frozen:
            self._input.text = ""
            self._input.focus = False

    @property
    def text(self):
        return self._input.text

    @text.setter
    def text(self, v):
        self._input.text = v

    def bind(self, **kwargs):
        if "text" in kwargs:
            self._input.bind(text=kwargs["text"])
            kwargs = {k: v for k, v in kwargs.items() if k != "text"}
        if kwargs:
            return super().bind(**kwargs)


def mk_input(**kw):
    return ArabicTextInput(hint_text=kw.pop("hint_text", ""),
                           multiline=kw.pop("multiline", False),
                           input_filter=kw.pop("input_filter", None),
                           font_size=kw.pop("font_size", dp(15)),
                           password=kw.pop("password", False), **kw)


# ============================================================
# 8) النوافذ والهيدر والقوائم
# ============================================================
def make_arabic_popup(title_text, content, size_hint=(0.9, 0.7), title_color=None,
                      separator_color=None, auto_dismiss=True):
    title_color = title_color or T.PRIMARY
    separator_color = separator_color or T.PRIMARY
    wrapper = BoxLayout(orientation="vertical", spacing=0)
    tw = DrawnText(text=title_text, font_size=dp(18), color=title_color, halign="center",
                   size_hint_y=None, height=dp(44))
    tw.disabled = True
    wrapper.add_widget(tw)
    sep = Widget(size_hint_y=None, height=dp(2))
    with sep.canvas:
        Color(*separator_color)
        sep_rect = Rectangle(pos=sep.pos, size=sep.size)

    def _u(*a):
        sep_rect.pos, sep_rect.size = sep.pos, sep.size
    sep.bind(pos=_u, size=_u)
    wrapper.add_widget(sep)
    wrapper.add_widget(Widget(size_hint_y=None, height=dp(8)))
    content.size_hint_y = 1
    wrapper.add_widget(content)
    popup = Popup(title="", content=wrapper, size_hint=size_hint,
                  separator_color=(0, 0, 0, 0), background="",
                  background_color=T.CARD, auto_dismiss=auto_dismiss)
    popup.bind(on_open=lambda *a: schedule_refresh(0.05))
    return popup


def msg_popup(title, text, color=None, btn="حسناً", extra=None):
    """extra: قائمة (نص، لون، دالة) أزرار إضافية."""
    color = color or T.PRIMARY
    box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
    box.add_widget(mk_text(text, dp(15), T.TEXT, halign="center", hint_y=1))
    for label, col, cb in (extra or []):
        b = mk_btn(label, height=dp(46), background_color=col)
        box.add_widget(b)
        b._extra_cb = cb
    ok = mk_btn(btn, height=dp(46), background_color=T.GRAY_BTN)
    box.add_widget(ok)
    pop = make_arabic_popup(title, box, size_hint=(0.9, 0.5 if not extra else 0.6),
                            title_color=color, separator_color=color)
    for child in box.children:
        cb = getattr(child, "_extra_cb", None)
        if cb:
            child.bind(on_release=lambda w, cb=cb: (pop.dismiss(), cb()))
    ok.bind(on_release=lambda *a: pop.dismiss())
    pop.open()
    return pop


def confirm_popup(title, text, on_yes, yes_text="نعم", color=None):
    color = color or T.RED
    box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
    box.add_widget(mk_text(text, dp(15), T.TEXT, halign="center", hint_y=1))
    bb = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
    yes = mk_btn(yes_text, background_color=color)
    no = mk_btn("إلغاء", background_color=T.GRAY_BTN)
    bb.add_widget(yes)
    bb.add_widget(no)
    box.add_widget(bb)
    pop = make_arabic_popup(title, box, size_hint=(0.88, 0.4),
                            title_color=color, separator_color=color)
    yes.bind(on_release=lambda *a: (pop.dismiss(), on_yes()))
    no.bind(on_release=lambda *a: pop.dismiss())
    pop.open()
    return pop


def make_header(title, color, on_back=None, right_widget=None, height=dp(58),
                back_text="رجوع"):
    bar = FloatLayout(size_hint_y=None, height=height)
    with bar.canvas.before:
        Color(*color)
        rect = RoundedRectangle(pos=bar.pos, size=bar.size, radius=[0, 0, dp(20), dp(20)])

    def _u(*a):
        rect.pos, rect.size = bar.pos, bar.size
    bar.bind(pos=_u, size=_u)
    t = DrawnText(text=title, font_size=dp(19), color=WHITE, halign="center",
                  size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
    t.disabled = True
    bar.add_widget(t)
    if on_back is not None:
        back = mk_btn(back_text, size_hint=(None, None), size=(dp(76), dp(38)),
                      font_size=dp(14), background_color=GLASS,
                      pos_hint={"x": 0.02, "center_y": 0.5})
        back.bind(on_release=on_back)
        bar.add_widget(back)
    if right_widget is not None:
        right_widget.pos_hint = {"right": 0.98, "center_y": 0.5}
        bar.add_widget(right_widget)
    return bar


def make_scroll_list(spacing=dp(10)):
    layout = GridLayout(cols=1, spacing=spacing, size_hint_y=None,
                        padding=[dp(2), dp(4), dp(2), dp(16)])
    layout.bind(minimum_height=layout.setter("height"))
    scroll = ScrollView(size_hint=(1, 1), bar_width=dp(3))
    scroll.add_widget(layout)
    return scroll, layout


class FadeOverlay(Widget):
    def __init__(self, color, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", Window.size)
        kwargs.setdefault("pos", (0, 0))
        super().__init__(**kwargs)
        with self.canvas:
            Color(*color)
            Rectangle(pos=(0, 0), size=Window.size)

    def on_touch_down(self, touch):
        return False

    def on_touch_up(self, touch):
        return False


def pick_popup(title, options, on_pick, new_label=None, on_new=None):
    """options: قائمة (العنوان، سطر فرعي، القيمة، لون السطر الفرعي)."""
    content = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(6))
    search = mk_input(hint_text="اكتب للبحث...", size_hint_y=None, height=dp(48))
    content.add_widget(search)
    scroll, lay = make_scroll_list(spacing=dp(6))
    content.add_widget(scroll)
    popup = make_arabic_popup(title, content, size_hint=(0.94, 0.88))

    def choose(val):
        popup.dismiss()
        on_pick(val)

    def fill(*a):
        q = search.text.strip().lower()
        lay.clear_widgets()
        if new_label and q:
            nb = mk_btn(f"+ {new_label}: {search.text.strip()}", height=dp(50),
                        background_color=T.GREEN)
            nb.bind(on_release=lambda *x: (popup.dismiss(), on_new(search.text.strip())))
            lay.add_widget(nb)
        n = 0
        for opt in options:
            label, sub, val, col = opt[:4]
            lcol = opt[4] if len(opt) > 4 else None   # لون اسم الصف (اختياري)
            if q and q not in str(label).lower():
                continue
            n += 1
            row = CardBox(orientation="vertical", size_hint_y=None, height=dp(62),
                          padding=[dp(12), dp(6)], on_tap=lambda v=val: choose(v))
            row.add_widget(mk_text(label, dp(16), lcol or T.TEXT, hint_y=0.55))
            row.add_widget(mk_text(sub or "", dp(12), col or T.MUTED, hint_y=0.45))
            lay.add_widget(row)
        if n == 0 and not (new_label and q):
            lay.add_widget(mk_text("لا توجد نتائج", dp(14), T.MUTED, halign="center",
                                   height=dp(40)))
        schedule_refresh(0.1)

    search.bind(text=fill)
    fill()
    popup.open()
    return popup


# ============================================================
# 9) رسم الفاتورة كصورة + مشاركة واتساب
# ============================================================
_PIL_FONTS = {}


def _pil_font(size):
    key = (FONT_PATH, size)
    f = _PIL_FONTS.get(key)
    if f is None:
        f = _PIL_FONTS[key] = _pil_font_load(size)
    return f


def _pil_font_load(size):
    from PIL import ImageFont
    try:
        if FONT_PATH:
            try:
                return ImageFont.truetype(FONT_PATH, size, layout_engine=ImageFont.Layout.BASIC)
            except Exception:
                return ImageFont.truetype(FONT_PATH, size)
    except Exception as e:
        print(f"[PIL FONT] {e}")
    return ImageFont.load_default()


def _rgb255(c):
    return tuple(int(x * 255) for x in c[:3])


LAST_DOC_ARGS = None      # آخر مستند أُنشئ (للطباعة الحرارية)
DOC_HOOKS = {}            # "print" / "save": تضبطها الواجهة الثانية


def render_doc(title, color, info, headers, widths, rows, totals, footer):
    """ينشئ صورة PNG مرتبة.
    info: أسطر نصية تحت العنوان | headers: عناوين الأعمدة (من اليمين لليسار)
    widths: نسب الأعمدة | rows: صفوف | totals: (عنوان، قيمة، ملوّن؟) | footer: أسطر ختامية.
    يرجع (المسار، (العرض، الارتفاع))."""
    from PIL import Image as PImage, ImageDraw
    global LAST_DOC_ARGS
    LAST_DOC_ARGS = (title, list(info or []), headers, widths, [list(r) for r in (rows or [])],
                     list(totals or []), list(footer or []))
    os.makedirs(DOC_DIR, exist_ok=True)
    for fn in os.listdir(DOC_DIR):
        try:
            os.remove(os.path.join(DOC_DIR, fn))
        except Exception:
            pass
    W = 900
    L, R = 50, W - 50
    H = 800 + len(info) * 60 + len(rows) * 100 + len(totals) * 80 + len(footer) * 60
    img = PImage.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    f_t, f_m, f_s, f_x = _pil_font(60), _pil_font(36), _pil_font(30), _pil_font(25)
    DARK, GRAY = (31, 41, 61), (118, 130, 150)
    LINE, SOFT = (228, 231, 236), (240, 243, 248)
    col = _rgb255(color)

    def tw(s, f):
        try:
            return d.textlength(ar(s), font=f)
        except Exception:
            return len(str(s)) * 14

    def rt(x, y, s, f, fill=DARK):
        d.text((x - tw(s, f), y), ar(s), font=f, fill=fill)

    def lt(x, y, s, f, fill=DARK):
        d.text((x, y), ar(s), font=f, fill=fill)

    def ct(cx, y, s, f, fill=DARK):
        d.text((cx - tw(s, f) / 2.0, y), ar(s), font=f, fill=fill)

    def fit(s, f, maxw):
        s = str(s)
        if tw(s, f) <= maxw:
            return s
        while s and tw(s + "…", f) > maxw:
            s = s[:-1]
        return s + "…"

    d.rectangle([0, 0, W, 170], fill=col)
    ct(W / 2, 24, title, f_t, (255, 255, 255))
    ct(W / 2, 108, "دفتر المحاسب", f_x, (235, 240, 250))
    y = 205
    for ln in info:
        rt(R, y, fit(ln, f_s, R - L), f_s, DARK if y == 205 else GRAY)
        y += 54
    y += 8
    d.line([L, y, R, y], fill=LINE, width=3)
    y += 18
    span = R - L
    if headers:
        d.rectangle([L, y, R, y + 58], fill=SOFT)
        x = R
        for h, w in zip(headers, widths):
            rt(x - 10, y + 12, h, f_x, GRAY)
            x -= w * span
        y += 58
        for r in rows:
            x = R
            for ci, (cell, w) in enumerate(zip(r, widths)):
                rt(x - 10, y + 16, fit(cell, f_s, w * span - 20), f_s, DARK)
                x -= w * span
            y += 68
            d.line([L, y, R, y], fill=LINE, width=2)
        y += 24
    if totals:
        bh = 30 + len(totals) * 66
        d.rounded_rectangle([L, y, R, y + bh], radius=18, fill=SOFT)
        yy = y + 18
        for lab, val, hi in totals:
            rt(R - 20, yy, lab, f_s, GRAY)
            lt(L + 20, yy, str(val), f_m if hi else f_s, col if hi else DARK)
            yy += 66
        y += bh + 24
    for ln in footer:
        ct(W / 2, y, fit(ln, f_s, W - 100), f_s, GRAY)
        y += 54
    img = img.crop((0, 0, W, min(y + 30, H)))
    path = os.path.join(DOC_DIR, "doc_%s.png" % datetime.now().strftime("%H%M%S"))
    img.save(path)
    return path, img.size


def png_to_pdf(png_path):
    """يحوّل صورة المستند إلى ملف PDF بصفحة واحدة بعرض A4 تقريباً (بدون مكتبات إضافية)."""
    from PIL import Image as PImage
    pdf_path = os.path.splitext(png_path)[0] + ".pdf"
    img = PImage.open(png_path).convert("RGB")
    img.save(pdf_path, "PDF", resolution=109.0)
    return pdf_path


def wa_number(phone):
    p = "".join(ch for ch in str(phone) if ch.isdigit())
    if p.startswith("0"):
        p = "967" + p[1:]
    elif len(p) == 9:
        p = "967" + p
    return p


def open_whatsapp(phone, message=""):
    from urllib.parse import quote
    url = "https://wa.me/{}?text={}".format(wa_number(phone), quote(message))
    if platform == "android":
        try:
            from jnius import autoclass
            from android import mActivity
            Intent = autoclass("android.content.Intent")
            Uri = autoclass("android.net.Uri")
            it = Intent(Intent.ACTION_VIEW)
            it.setData(Uri.parse(url))
            mActivity.startActivity(it)
            return
        except Exception as e:
            print(f"[WA] {e}")
    print("URL:", url)


def _publish_media(path, mime):
    from jnius import autoclass
    from android import mActivity
    sdk = autoclass("android.os.Build$VERSION").SDK_INT
    JFile = autoclass("java.io.File")
    is_img = str(mime).startswith("image/")
    if sdk >= 29:
        CV = autoclass("android.content.ContentValues")
        resolver = mActivity.getContentResolver()
        v = CV()
        v.put("_display_name", os.path.basename(path))
        v.put("mime_type", mime)
        if is_img:
            v.put("relative_path", "Pictures/DebtBook")
            coll = autoclass("android.provider.MediaStore$Images$Media").EXTERNAL_CONTENT_URI
        else:
            v.put("relative_path", "Download/DebtBook")
            coll = autoclass("android.provider.MediaStore$Downloads").EXTERNAL_CONTENT_URI
        uri = resolver.insert(coll, v)
        if uri is None:
            raise RuntimeError("MediaStore insert returned null")
        out = resolver.openOutputStream(uri)
        try:
            autoclass("java.nio.file.Files").copy(JFile(path).toPath(), out)
        finally:
            out.close()
        return uri
    import shutil
    ext = mActivity.getExternalFilesDir(None)
    base = ext.getAbsolutePath() if ext is not None else os.path.dirname(path)
    pub = os.path.join(base, "DebtBook")
    os.makedirs(pub, exist_ok=True)
    dst = os.path.join(pub, os.path.basename(path))
    shutil.copyfile(path, dst)
    SM = autoclass("android.os.StrictMode")
    B = autoclass("android.os.StrictMode$VmPolicy$Builder")
    SM.setVmPolicy(B().build())
    return autoclass("android.net.Uri").fromFile(JFile(dst))


def share_file(path, mime, phone=""):
    """يرجع None عند النجاح أو نص الخطأ."""
    if platform != "android":
        print("[SHARE]", path)
        return None
    try:
        from jnius import autoclass, cast
        from android import mActivity
        Intent = autoclass("android.content.Intent")
        uri = _publish_media(path, mime)

        def make():
            it = Intent(Intent.ACTION_SEND)
            it.setType(mime)
            it.putExtra(Intent.EXTRA_STREAM, cast("android.os.Parcelable", uri))
            it.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            return it

        p = wa_number(phone)
        for pkg in ("com.whatsapp", "com.whatsapp.w4b"):
            try:
                it = make()
                it.setPackage(pkg)
                if p:
                    it.putExtra("jid", p + "@s.whatsapp.net")
                mActivity.startActivity(it)
                return None
            except Exception as e:
                print(f"[SHARE {pkg}] {e}")
        ch = Intent.createChooser(make(), "إرسال")
        ch.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        mActivity.startActivity(ch)
        return None
    except Exception as e:
        print(f"[SHARE] {e}")
        return str(e)[:160]


def show_doc_popup(png, size, text, phone="", title="معاينة", extra=None):
    """extra: (نص، لون، دالة) أزرار إضافية مثل الحذف."""
    from kivy.uix.image import Image as KImage
    content = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(4))
    if png:
        sv = ScrollView(size_hint=(1, 1), bar_width=dp(3))
        try:
            img = KImage(source=png, fit_mode="contain", size_hint=(1, None))
        except Exception:
            img = KImage(source=png, allow_stretch=True, keep_ratio=True, size_hint=(1, None))
        ratio = size[1] / float(size[0])
        img.height = Window.width * 0.9 * ratio
        img.bind(width=lambda w, v: setattr(w, "height", v * ratio))
        sv.add_widget(img)
        content.add_widget(sv)
    else:
        content.add_widget(mk_text("تعذر إنشاء الصورة، يمكنك الإرسال كنص", dp(14), T.RED,
                                   halign="center"))
    r1 = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
    b_img = mk_btn("إرسال صورة", background_color=T.GREEN, font_size=dp(13))
    b_pdf = mk_btn("إرسال PDF", background_color=T.INV_RED, font_size=dp(13))
    b_txt = mk_btn("إرسال كنص", background_color=T.BLUE, font_size=dp(13))
    r1.add_widget(b_txt)
    r1.add_widget(b_pdf)
    r1.add_widget(b_img)
    content.add_widget(r1)
    doc_args = LAST_DOC_ARGS
    r1b = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
    b_save = mk_btn("حفظ PDF بضغطة", background_color=T.TEAL, font_size=dp(13))
    b_print = mk_btn("طباعة حرارية", background_color=T.PRIMARY, font_size=dp(13))
    r1b.add_widget(b_save)
    r1b.add_widget(b_print)
    content.add_widget(r1b)
    r2 = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
    pop = make_arabic_popup(title, content, size_hint=(0.96, 0.96))
    for label, col, cb in (extra or []):
        b = mk_btn(label, background_color=col, font_size=dp(14))
        b.bind(on_release=lambda w, cb=cb: (pop.dismiss(), cb()))
        r2.add_widget(b)
    bc = mk_btn("إغلاق", background_color=T.GRAY_BTN, font_size=dp(14))
    r2.add_widget(bc)
    content.add_widget(r2)

    def send_img(*a):
        err = share_file(png, "image/png", phone) if png else "لا توجد صورة"
        if err:
            msg_popup("خطأ في الإرسال", err, T.RED)

    def send_pdf(*a):
        if not png:
            return msg_popup("خطأ في الإرسال", "لا يوجد مستند", T.RED)
        try:
            pdf = png_to_pdf(png)
        except Exception as e:
            _write_crash("PDF: " + traceback.format_exc())
            return msg_popup("تعذر إنشاء PDF", str(e)[:160], T.RED)
        err = share_file(pdf, "application/pdf", phone)
        if err:
            msg_popup("خطأ في الإرسال", err, T.RED)

    def save_pdf(*a):
        h = DOC_HOOKS.get("save")
        if h is None or not png:
            return msg_popup("تعذر الحفظ", "لا يوجد مستند", T.RED)
        h(png)

    def print_it(*a):
        h = DOC_HOOKS.get("print")
        if h is None or not doc_args:
            return msg_popup("تعذرت الطباعة", "الطباعة غير متاحة لهذا المستند", T.RED)
        h(doc_args)

    b_save.bind(on_release=save_pdf)
    b_print.bind(on_release=print_it)
    b_img.bind(on_release=send_img)
    b_pdf.bind(on_release=send_pdf)
    b_txt.bind(on_release=lambda *a: open_whatsapp(phone, text))
    bc.bind(on_release=lambda *a: pop.dismiss())
    pop.open()


# ============================================================
# 10) التشغيل: يستدعي الكود الثاني
# ============================================================
UI_KIT_READY = True


def _load_second():
    """يحمّل الكود الثاني accounting_app.py (ولو غُيّر اسمه يبحث عنه بجانب main.py)."""
    try:
        import accounting_app as m
        return m
    except ModuleNotFoundError as e:
        if "accounting_app" not in str(e):
            raise
    import importlib.util
    me = os.path.basename(os.path.abspath(__file__))
    for fn in sorted(os.listdir(APP_DIR)):
        if not fn.endswith(".py") or fn == me:
            continue
        p = os.path.join(APP_DIR, fn)
        try:
            with open(p, "r", encoding="utf-8") as f:
                src = f.read()
        except Exception:
            continue
        if "class AccountingApp" in src and "def run(" in src:
            spec = importlib.util.spec_from_file_location("accounting_app", p)
            m = importlib.util.module_from_spec(spec)
            sys.modules["accounting_app"] = m
            spec.loader.exec_module(m)
            return m
    raise ModuleNotFoundError(
        "لم أجد accounting_app.py بجانب main.py في: " + APP_DIR)


def _show_fatal(tb_text):
    """لو فشل التشغيل نعرض سبب الخطأ على الشاشة بدل الخروج الصامت."""
    try:
        from kivy.app import App as _App
        from kivy.uix.label import Label as _Lbl
        from kivy.uix.scrollview import ScrollView as _SV
        from kivy.uix.boxlayout import BoxLayout as _BL

        class _ErrApp(_App):
            def build(self):
                box = _BL(orientation="vertical", padding=dp(10), spacing=dp(8))
                box.add_widget(_Lbl(text="Startup error - send this text to the developer:",
                                    size_hint_y=None, height=dp(34), color=(1, 0.45, 0.45, 1)))
                sv = _SV()
                lb = _Lbl(text=tb_text[-3500:], size_hint_y=None, halign="left",
                          valign="top", font_size=dp(11))
                lb.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
                lb.bind(texture_size=lambda w, v: setattr(w, "height", v[1]))
                sv.add_widget(lb)
                box.add_widget(sv)
                return box
        _ErrApp().run()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        _load_second().run()
    except Exception:
        _tb = traceback.format_exc()
        _write_crash(_tb)
        _show_fatal(_tb)
