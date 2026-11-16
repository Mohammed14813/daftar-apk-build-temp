# حالة التسليم

تم تحديث المشروع إلى الإصدار 1.1.0.

- طلب أذونات الكاميرا والميكروفون والبلوتوث والإشعارات أصبح عند استخدام الميزة، وليس عند فتح التطبيق.
- تم تحديث app-bundle.zip من أحدث ملفات app/.
- تم إضافة معماريات ARM وx86/x86_64 في buildozer.spec.
- اجتاز الكود فحص Python compile واختبار سلامة ZIP.

ملاحظة البناء: لم ينتج APK في بيئة Sandbox الحالية لأن أدوات Android SDK التي نزّلها Buildozer هي x86_64 بينما البيئة ARM64، فظهر Exec format error عند تشغيل aidl. ابنِ المشروع على Linux x86_64 أو CI يدعم Android SDK.

البناء:

    cd build
    buildozer android debug

إذا ظهرت شاشة ترخيص Android SDK، اقبلها مرة واحدة.
