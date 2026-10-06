package com.oops.app.audio

import android.os.SystemClock
import com.oops.app.core.Pcm
import com.oops.app.core.Wav
import java.io.InputStream
import kotlin.concurrent.thread

/**
 * Plays a 16 kHz mono WAV in real time, then keeps emitting silence so the session clock
 * runs on like a live mic — handy for "recall 10 / 30 / 60 / 120 s later" tests.
 * Test audio only; it never counts as a device run.
 */
class WavFileAudioSource(private val open: () -> InputStream) : AudioSource {
    override val kind = "wav"

    @Volatile private var running = false
    private var worker: Thread? = null
    private var input: InputStream? = null

    override fun start(listener: AudioSource.Listener) {
        val raw = open()
        val pcm = try { Wav.openPcm(raw) } catch (e: Throwable) { raw.close(); throw e }
        input = pcm
        running = true
        worker = thread(name = "oops-wav") {
            val startedAt = SystemClock.elapsedRealtime()
            var frame = 0L
            var ended = false
            try {
                while (running) {
                    val chunk = ByteArray(Pcm.FRAME_BYTES)
                    var filled = 0
                    while (!ended && filled < chunk.size) {
                        val n = pcm.read(chunk, filled, chunk.size - filled)
                        if (n < 0) { ended = true; pcm.close() } else filled += n
                    }
                    listener.onAudio(chunk)
                    frame++
                    val wait = startedAt + frame * Pcm.FRAME_MS - SystemClock.elapsedRealtime()
                    if (wait > 0) Thread.sleep(wait)
                }
            } catch (_: InterruptedException) {
                // stopped
            } catch (e: Throwable) {
                if (running) listener.onError(e)
            } finally {
                runCatching { pcm.close() }
            }
        }
    }

    override fun stop() {
        running = false
        runCatching { input?.close() }
        input = null
        worker?.interrupt()
        worker?.join(500)
        worker = null
    }
}
