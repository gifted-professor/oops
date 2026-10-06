package com.oops.app.audio

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Process
import com.oops.app.core.Pcm
import kotlin.concurrent.thread

/** Phone microphone. Stand-in for the 录音豆 until its SDK and license arrive. */
class MicAudioSource : AudioSource {
    override val kind = "mic"

    @Volatile private var running = false
    private var worker: Thread? = null

    @SuppressLint("MissingPermission") // CaptureService is only started after RECORD_AUDIO is granted
    override fun start(listener: AudioSource.Listener) {
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
        running = true
        worker = thread(name = "oops-mic") {
            Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
            try {
                record.startRecording()
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
            } catch (e: Throwable) {
                if (running) listener.onError(e)
            } finally {
                runCatching { record.stop() }
                record.release()
            }
        }
    }

    override fun stop() {
        running = false
        worker?.join(500)
        worker = null
    }
}
