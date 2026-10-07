package com.oops.app.buffer

import java.util.Arrays

/**
 * Fixed-size PCM ring addressed by absolute sample offset on the session timeline.
 * Anything older than [capacitySamples] is overwritten — 默认临时，到期自动覆盖。
 */
class AudioRingBuffer(val capacitySamples: Int) {
    init { require(capacitySamples > 0 && capacitySamples <= Int.MAX_VALUE / 2) { "Invalid ring capacity" } }
    private val data = ByteArray(capacitySamples * 2)
    private var end = 0L

    /** Exclusive end of what has been written, i.e. total samples this session. */
    val endSample: Long
        @Synchronized get() = end

    /** Oldest sample still held. */
    val startSample: Long
        @Synchronized get() = maxOf(0L, end - capacitySamples)

    @Synchronized
    fun append(pcm: ByteArray, offset: Int = 0, length: Int = pcm.size - offset) {
        require(offset >= 0 && length >= 0 && offset <= pcm.size - length && offset % 2 == 0) { "Invalid PCM range" }
        require(length % 2 == 0) { "PCM length must be even" }
        var src = offset
        var len = length
        if (len > data.size) { // only the newest capacity's worth can survive
            val skip = len - data.size
            src += skip
            len -= skip
            end += skip / 2
        }
        var pos = ((end % capacitySamples) * 2).toInt()
        var remaining = len
        while (remaining > 0) {
            val n = minOf(remaining, data.size - pos)
            System.arraycopy(pcm, src, data, pos, n)
            src += n
            remaining -= n
            pos = (pos + n) % data.size
        }
        end += len / 2
    }

    /** Advances the timeline over a span with no audio (e.g. the device disconnected). */
    @Synchronized
    fun appendSilence(samples: Long) {
        if (samples <= 0) return
        val kept = minOf(samples, capacitySamples.toLong())
        end += samples - kept
        var pos = ((end % capacitySamples) * 2).toInt()
        var bytes = kept * 2
        while (bytes > 0) {
            val n = minOf(bytes, (data.size - pos).toLong()).toInt()
            Arrays.fill(data, pos, pos + n, 0)
            bytes -= n
            pos = (pos + n) % data.size
        }
        end += kept
    }

    @Synchronized
    fun contains(fromSample: Long, toSample: Long): Boolean =
        fromSample < toSample && fromSample >= startSample && toSample <= end

    /** Copies `[fromSample, toSample)` if it is entirely still held, else null (expired or not recorded yet). */
    @Synchronized
    fun read(fromSample: Long, toSample: Long): ByteArray? {
        if (!contains(fromSample, toSample)) return null
        val out = ByteArray(((toSample - fromSample) * 2).toInt())
        var pos = ((fromSample % capacitySamples) * 2).toInt()
        var dst = 0
        while (dst < out.size) {
            val n = minOf(out.size - dst, data.size - pos)
            System.arraycopy(data, pos, out, dst, n)
            dst += n
            pos = (pos + n) % data.size
        }
        return out
    }

    /** Like [read] but trimmed to what is held; returns the actual start sample with the data. */
    @Synchronized
    fun readAvailable(fromSample: Long, toSample: Long): Pair<Long, ByteArray>? {
        val from = maxOf(fromSample, startSample)
        val to = minOf(toSample, end)
        if (from >= to) return null
        return from to read(from, to)!!
    }

    /** ASR endpoints can be a few samples past the final frame; never borrow expired audio. */
    @Synchronized
    fun readWithEndTolerance(fromSample: Long, toSample: Long, toleranceSamples: Long): ByteArray? {
        require(toleranceSamples >= 0)
        if (fromSample < startSample || toSample <= fromSample || toSample - end > toleranceSamples) return null
        return read(fromSample, minOf(toSample, end))
    }

    @Synchronized
    fun reset() {
        end = 0
    }
}
