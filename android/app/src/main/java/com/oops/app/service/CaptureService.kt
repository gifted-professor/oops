package com.oops.app.service

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.oops.app.MainActivity
import com.oops.app.R
import com.oops.app.engine.OopsEngine

/**
 * Microphone foreground service: 按一次开始，之后常开。The ongoing notification is the
 * "状态可见" promise — capture is never hidden — and carries the "Oops 一下" recall action.
 */
class CaptureService : Service() {
    private var wakeLock: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            OopsEngine.stopCapture()
            ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }
        startInForeground()
        acquireWakeLock()
        OopsEngine.startCapture(intent?.getStringExtra(EXTRA_SOURCE) ?: OopsEngine.settings.source)
        // Android 14+ forbids restarting a microphone service from the background anyway.
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        OopsEngine.stopCapture()
        wakeLock?.takeIf { it.isHeld }?.release()
        wakeLock = null
        super.onDestroy()
    }

    private fun startInForeground() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, getString(R.string.notif_channel_capture), NotificationManager.IMPORTANCE_LOW),
        )
        val immutable = PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), immutable)
        val recall = PendingIntent.getActivity(
            this, 1, Intent(this, MainActivity::class.java).setAction(MainActivity.ACTION_RECALL), immutable,
        )
        val stop = PendingIntent.getService(
            this, 2, Intent(this, CaptureService::class.java).setAction(ACTION_STOP), immutable,
        )
        val notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_oops)
            .setContentTitle(getString(R.string.notif_capture_title))
            .setContentText(getString(R.string.notif_capture_text))
            .setOngoing(true)
            .setContentIntent(open)
            .addAction(0, getString(R.string.notif_action_recall), recall)
            .addAction(0, getString(R.string.notif_action_stop), stop)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0
        ServiceCompat.startForeground(this, NOTIFICATION_ID, notification, type)
    }

    private fun acquireWakeLock() {
        if (wakeLock?.isHeld == true) return
        wakeLock = getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "oops:capture")
            .apply {
                setReferenceCounted(false)
                acquire(12 * 60 * 60 * 1000L)
            }
    }

    companion object {
        private const val CHANNEL_ID = "capture"
        private const val NOTIFICATION_ID = 1
        private const val ACTION_START = "com.oops.app.capture.START"
        private const val ACTION_STOP = "com.oops.app.capture.STOP"
        private const val EXTRA_SOURCE = "source"

        /** Must be called while the app is in the foreground (microphone FGS rule). */
        fun start(context: Context, source: String) {
            val intent = Intent(context, CaptureService::class.java).setAction(ACTION_START).putExtra(EXTRA_SOURCE, source)
            ContextCompat.startForegroundService(context, intent)
        }

        fun stop(context: Context) {
            runCatching {
                context.startService(Intent(context, CaptureService::class.java).setAction(ACTION_STOP))
            }.onFailure {
                Log.w("Oops", "stop service failed, stopping engine directly", it)
                OopsEngine.stopCapture()
            }
        }
    }
}
