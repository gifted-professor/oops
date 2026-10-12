package com.oops.app.audio

/**
 * Where audio comes from: the phone mic or a test WAV today, the 录音豆 SDK later
 * (a `BeanAudioSource` that maps the SDK's stream / double-tap / disconnect callbacks here).
 * Every source delivers 16 kHz mono 16-bit PCM, normally in 100 ms frames.
 */
interface AudioSource {
    val kind: String

    /** Begins delivering audio on a background thread. */
    fun start(listener: Listener)

    fun stop()

    interface Listener {
        fun onAudio(pcm: ByteArray)

        /** Device-side marks, e.g. the bean's double tap. */
        fun onMark(mark: Mark) {}

        /** The source lost [missingMs] of audio (e.g. Bluetooth dropped); the timeline must still advance. */
        fun onGap(missingMs: Long) {}

        /** Connection state shown to the user, e.g. "等待 Mac 收音连接"; null clears it. */
        fun onStatus(detail: String?) {}

        /** False when the external input disconnects; received PCM marks it ready again. */
        fun onReady(ready: Boolean) {}

        /** True while the OS silences this input because another app uses the microphone. */
        fun onSilenced(silenced: Boolean) {}

        fun onError(error: Throwable)
    }

    enum class Mark { DOUBLE_TAP }
}
