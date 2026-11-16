# مشروع دفتر المحاسب — الحزمة الكاملة

## البناء

افتح الطرفية داخل مجلد `build` ثم نفّذ:

```bash
pip install buildozer cython==0.29.37
buildozer android debug
```

الناتج يظهر داخل `build/bin/`.

## سلوك اللودر

- لا يبدأ تنزيلًا تلقائيًا عند فتح التطبيق.
- يظهر زر **تحميل الواجهة**.
- لا يبدأ تنزيل `app-bundle.zip` إلا بعد الضغط على الزر.
- بعد التحميل يشغّل الواجهة.
- إذا كانت الواجهة محفوظة، يظهر زر **تشغيل الواجهة**.
- لا يقتل عملية Android بعد التحميل.

## ملفات المشروع

- `app/`: كود الواجهة والتطبيق الرئيسي.
- `build/main.py`: لودر Android المعدل.
- `build/app-bundle.zip`: الحزمة التي يتم تنزيلها يدويًا عند الضغط.
- `build/buildozer.spec`: إعدادات البناء والصلاحيات.
- `build/src/`: كود Android المساعد للبصمة.
- `build/res/`: موارد Android.
- `deployment/`: ملفات الوسيط Cloudflare/Supabase.

## ملاحظات

- لا تضع أي token سري داخل APK.
- احذف مجلد `.buildozer` إذا أردت بناءً نظيفًا بالكامل.
- إذا عدّلت ملفات `app/`، أعد إنشاء الحزمة:

```bash
cd app
zip -q -j ../build/app-bundle.zip main.py accounting_app_v2.py notifier_service.py font.otf
```

ثم نفّذ البناء من مجلد `build`.
