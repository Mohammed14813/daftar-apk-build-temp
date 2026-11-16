[app]
title = دفتر المحاسب
package.name = daftar
package.domain = org.daftar
source.dir = .
source.include_exts = py,otf,png,txt,zip,json
version = 1.1.0
requirements = python3,kivy,pillow,pyjnius,certifi
orientation = portrait
fullscreen = 0
icon.filename = %(source.dir)s/icon.png
presplash.filename = %(source.dir)s/presplash.png
android.presplash_color = #0A1637
android.api = 35
android.minapi = 24
android.ndk = 27b
# هواتف ARM الفعلية، إضافة إلى x86/x86_64 عند التشغيل على المحاكيات
android.archs = arm64-v8a,armeabi-v7a,x86,x86_64
android.add_src = %(source.dir)s/src
android.add_resources = %(source.dir)s/res
android.permissions = INTERNET,CAMERA,RECORD_AUDIO,BLUETOOTH,BLUETOOTH_ADMIN,BLUETOOTH_CONNECT,BLUETOOTH_SCAN,POST_NOTIFICATIONS,USE_BIOMETRIC,VIBRATE
android.enable_androidx = True
android.private_storage = True
android.allow_backup = False
android.logcat_filters = *:S python:D
p4a.branch = develop

[buildozer]
log_level = 2
warn_on_root = 0
