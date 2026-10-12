package com.oops.app.audio

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.Process
import android.util.Log
import androidx.annotation.RequiresApi
import com.oops.app.core.Pcm
import kotlin.concurrent.thread

/** Phone microphone. Stand-in for the 录音豆 until its SDK and license arrive. */
class MicAudioSource : AudioSource {
    override val kind = "mic"

    @Volatile private var running = false
    private var worker: Thread? = null

    @SuppressLint("MissingPermission") // CaptureService is only started after RECORD_AUDIO is granted
    private fun open(): AudioRecord {
        val minBuffer = AudioRecord.getMinBufferSize(
            Pcm.SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
        )
        val record = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            Pcm.SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            maxOf(minBuffer, Pcm.FRAME_BYTES * 8),
        )
        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            error("麦克风初始化失败（可能被其他应用占用）")
        }
        return record
    }

    override fun start(listener: AudioSource.Listener) {
        val first = open()
        running = true
        worker = thread(name = "oops-mic") {
            Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
            var current: AudioRecord? = first
            var lostAtNanos = 0L
            try {
                while (running) {
                    val record = current ?: reopen(listener)?.also { current = it } ?: return@thread
                    if (lostAtNanos != 0L) {
                        val missingMs = (System.nanoTime() - lostAtNanos) / 1_000_000
                        lostAtNanos = 0L
                        if (missingMs >= GAP_MIN_MS) listener.onGap(missingMs)
                    }
                    try {
                        val watch = if (Build.VERSION.SDK_INT >= 29) watchSilenced(record, listener) else null
                        try {
                            record.startRecording()
                            readFrames(record, listener)
                        } finally {
                            watch?.invoke()
                        }
                    } catch (e: Throwable) {
                        if (!running) return@thread
                        // A route change or a competing app can invalidate the AudioRecord; rebuild it before giving up.
                        Log.w(TAG, "mic read failed, reopening: ${e.message}")
                        lostAtNanos = System.nanoTime()
                        runCatching { record.stop() }
                        record.release()
                        current = null
                    }
                }
            } finally {
                current?.let { runCatching { it.stop() }; it.release() }
            }
        }
    }

    private fun readFrames(record: AudioRecord, listener: AudioSource.Listener) {
        while (running) {
            val frame = ByteArray(Pcm.FRAME_BYTES)
            var filled = 0
            while (filled < frame.size && running) {
                val n = record.read(frame, filled, frame.size - filled)
                if (n < 0) error("AudioRecord.read 返回 $n")
                filled += n
            }
            if (filled == frame.size) listener.onAudio(frame)
        }
    }

    /** Rebuilds the recorder after a failure; reports the error (which ends capture) only after all retries failed. */
    private fun reopen(listener: AudioSource.Listener): AudioRecord? {
        for (delayMs in RETRY_DELAYS_MS) {
            try {
                Thread.sleep(delayMs)
            } catch (_: InterruptedException) {
                return null
            }
            if (!running) return null
            runCatching { open() }.onSuccess { return it }
        }
        if (running) listener.onError(IllegalStateException("麦克风无法恢复，可能被其他应用占用"))
        return null
    }

    /** Android silences our input (all zeros, no error) while another app holds the mic; tell the user. */
    @RequiresApi(29)
    private fun watchSilenced(record: AudioRecord, listener: AudioSource.Listener): () -> Unit {
        val callback = object : AudioManager.AudioRecordingCallback() {
            override fun onRecordingConfigChanged(configs: MutableList<android.media.AudioRecordingConfiguration>) {
                val mine = configs.firstOrNull { it.clientAudioSessionId == record.audioSessionId } ?: return
                listener.onSilenced(mine.isClientSilenced)
            }
        }
        record.registerAudioRecordingCallback({ it.run() }, callback)
        return {
            runCatching { record.unregisterAudioRecordingCallback(callback) }
            listener.onSilenced(false)
        }
    }

    override fun stop() {
        running = false
        worker?.join(500)
        worker = null
    }

    private companion object {
        const val TAG = "OopsMic"
        const val GAP_MIN_MS = 300L
        val RETRY_DELAYS_MS = longArrayOf(200, 500, 1000)
    }
}
