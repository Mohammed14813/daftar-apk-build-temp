package org.daftar;

import android.app.Activity;
import android.os.Build;
import java.util.concurrent.Executor;

public final class BioHelper {
    public interface Listener { void onResult(boolean ok, String message); }

    public static boolean isSupported(Activity activity) {
        if (activity == null || Build.VERSION.SDK_INT < 28) return false;
        try {
            android.hardware.biometrics.BiometricManager bm =
                activity.getSystemService(android.hardware.biometrics.BiometricManager.class);
            return bm != null && bm.canAuthenticate() ==
                android.hardware.biometrics.BiometricManager.BIOMETRIC_SUCCESS;
        } catch (Throwable t) { return false; }
    }

    public static void authenticate(final Activity activity, String title, String subtitle,
                                    String negative, final Listener listener) {
        if (activity == null || listener == null || Build.VERSION.SDK_INT < 28) {
            if (listener != null) listener.onResult(false, "البصمة غير متاحة على هذا الإصدار");
            return;
        }
        Executor executor = activity.getMainExecutor();
        android.hardware.biometrics.BiometricPrompt.AuthenticationCallback callback =
            new android.hardware.biometrics.BiometricPrompt.AuthenticationCallback() {
                @Override public void onAuthenticationSucceeded(
                    android.hardware.biometrics.BiometricPrompt.AuthenticationResult result) {
                    listener.onResult(true, "ok");
                }
                @Override public void onAuthenticationError(int code, CharSequence msg) {
                    listener.onResult(false, msg == null ? "" : msg.toString());
                }
                @Override public void onAuthenticationFailed() { }
            };
        android.hardware.biometrics.BiometricPrompt prompt =
            new android.hardware.biometrics.BiometricPrompt.Builder(activity)
                .setTitle(title == null ? "التحقق بالبصمة" : title)
                .setSubtitle(subtitle == null ? "المس مستشعر البصمة" : subtitle)
                .setNegativeButton(negative == null ? "إلغاء" : negative, executor,
                    (dialog, which) -> listener.onResult(false, "إلغاء"))
                .build();
        prompt.authenticate(new android.os.CancellationSignal(), executor, callback);
    }
}
