package com.oops.app.notice

import android.content.Context
import android.os.Build
import android.os.SystemClock
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager

class NoticeAlert(private val context: Context) {
    private var lastAt = -60_000L
    @Synchronized fun alert(enabled: Boolean, kind: String) {
        val now = SystemClock.elapsedRealtime()
        if (!enabled || kind == "summary" || now - lastAt < 60_000) return
        val vibrator = if (Build.VERSION.SDK_INT >= 31) context.getSystemService(VibratorManager::class.java).defaultVibrator
            else context.getSystemService(Vibrator::class.java)
        if (!vibrator.hasVibrator()) return
        vibrator.vibrate(VibrationEffect.createOneShot(80, VibrationEffect.DEFAULT_AMPLITUDE))
        lastAt = now
    }
}
