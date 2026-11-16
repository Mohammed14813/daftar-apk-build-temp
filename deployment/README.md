# وسيط تحديثات التطبيق

المسار المقترح: التطبيق ← Supabase Edge Function (`/update/*`) ← Cloudflare Worker ← GitHub.

1. انشر `cloudflare-update-worker.js` كـWorker واضبط السر `GITHUB_TOKEN`، واختياريًا `GITHUB_REPO`.
2. اضبط سر Supabase `CLOUDFLARE_UPDATE_URL` على رابط Worker.
3. أضف كود `supabase-update-proxy.ts` إلى Edge Function الحالية تحت المسار `/update/*`.
4. لا تضع `GITHUB_TOKEN` في APK. النسخة الحالية لا تحتوي الرمز وتستخدم الحزمة المضمنة عند فشل الشبكة، كما أن مستودع التحديث العام يعمل بدون رمز.
