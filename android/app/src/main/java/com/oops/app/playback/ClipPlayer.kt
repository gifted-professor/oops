package com.oops.app.playback

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import com.oops.app.core.Pcm
import kotlin.concurrent.thread

/** Plays a PCM slice of the ring buffer (原声回听). One clip at a time. */
class ClipPlayer(private val onState: (key: String, state: String) -> Unit) {
    private class Playback(val key: String, val track: AudioTrack) {
        @Volatile var stopped = false
    }

    private var current: Playback? = null

    val isPlaying: Boolean
        @Synchronized get() = current != null

    @Synchronized
    fun play(key: String, pcm: ByteArray) {
        stopLocked()
        val minBuffer = AudioTrack.getMinBufferSize(
            Pcm.SAMPLE_RATE, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT,
        )
        val track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setSampleRate(Pcm.SAMPLE_RATE)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .build(),
            )
            .setBufferSizeInBytes(maxOf(minBuffer, Pcm.FRAME_BYTES * 4))
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
        val playback = Playback(key, track)
        current = playback
        thread(name = "oops-clip") {
            try {
                track.play()
                onState(key, "playing")
                var offset = 0
                while (offset < pcm.size && !playback.stopped) {
                    val n = track.write(pcm, offset, minOf(Pcm.FRAME_BYTES, pcm.size - offset))
                    if (n <= 0) break
                    offset += n
                }
                // Let the tail drain before reporting "ended".
                val totalFrames = pcm.size / 2
                val deadline = System.currentTimeMillis() + Pcm.samplesToMs(totalFrames.toLong()) + 1_000
                while (!playback.stopped && track.playbackHeadPosition < totalFrames &&
                    System.currentTimeMillis() < deadline
                ) {
                    Thread.sleep(20)
                }
            } catch (_: Throwable) {
                // treated as ended
            } finally {
                runCatching { track.stop() }
                track.release()
                synchronized(this) { if (current === playback) current = null }
                onState(key, if (playback.stopped) "stopped" else "ended")
            }
        }
    }

    @Synchronized
    fun stop() = stopLocked()

    private fun stopLocked() {
        current?.let {
            it.stopped = true
            runCatching {
                it.track.pause()
                it.track.flush()
            }
        }
        current = null
    }
}
