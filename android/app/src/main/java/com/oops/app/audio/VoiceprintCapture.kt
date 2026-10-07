package com.oops.app.audio

import java.io.ByteArrayOutputStream

/** A bounded, temporary recording; never written into saved clips or preferences. */
class VoiceprintCapture(val limitMs: Long) {
    private val output = ByteArrayOutputStream()
    private val maxBytes = (limitMs * 32).toInt()
    val elapsedMs: Long get() = output.size() / 32L

    fun append(pcm: ByteArray): Boolean {
        val remaining = maxBytes - output.size()
        if (remaining > 0) output.write(pcm, 0, minOf(remaining, pcm.size))
        return output.size() == maxBytes
    }

    fun finish(): ByteArray = output.toByteArray().also { output.reset() }
}
