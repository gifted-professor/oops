package com.oops.app.core

import java.io.File
import java.io.InputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.sqrt

/**
 * All audio inside Oops is 16 kHz, mono, 16-bit little-endian PCM — the format the
 * 录音豆 SDK delivers and Volc streaming ASR expects. Positions on the session timeline
 * are sample offsets, never network arrival times.
 */
object Pcm {
    const val SAMPLE_RATE = 16_000
    const val BYTES_PER_SAMPLE = 2
    const val FRAME_MS = 100
    const val FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS / 1000
    const val FRAME_BYTES = FRAME_SAMPLES * BYTES_PER_SAMPLE

    fun samplesToMs(samples: Long): Long = samples * 1000 / SAMPLE_RATE

    fun msToSamples(ms: Long): Long = ms * SAMPLE_RATE / 1000

    /** RMS level of a PCM chunk, normalized to 0..1. */
    fun rms(pcm: ByteArray, length: Int = pcm.size): Double {
        val n = length / 2
        if (n == 0) return 0.0
        var sum = 0.0
        for (i in 0 until n) {
            val s = ((pcm[2 * i + 1].toInt() shl 8) or (pcm[2 * i].toInt() and 0xFF)).toShort().toDouble()
            sum += s * s
        }
        return sqrt(sum / n) / Short.MAX_VALUE
    }
}

object Wav {
    fun header(dataBytes: Int): ByteArray =
        ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN).apply {
            put("RIFF".toByteArray())
            putInt(36 + dataBytes)
            put("WAVE".toByteArray())
            put("fmt ".toByteArray())
            putInt(16)
            putShort(1) // PCM
            putShort(1) // mono
            putInt(Pcm.SAMPLE_RATE)
            putInt(Pcm.SAMPLE_RATE * Pcm.BYTES_PER_SAMPLE)
            putShort(Pcm.BYTES_PER_SAMPLE.toShort())
            putShort(16)
            put("data".toByteArray())
            putInt(dataBytes)
        }.array()

    fun write(file: File, pcm: ByteArray) {
        file.outputStream().use {
            it.write(header(pcm.size))
            it.write(pcm)
        }
    }

    /** Validates the header and streams PCM without loading a long recording into memory. */
    fun openPcm(input: InputStream): InputStream {
        fun bytes(count: Int): ByteArray {
            val out = ByteArray(count)
            var pos = 0
            while (pos < count) {
                val n = input.read(out, pos, count - pos)
                require(n > 0) { "WAV 文件不完整" }
                pos += n
            }
            return out
        }
        fun uint32(b: ByteArray, offset: Int) =
            ByteBuffer.wrap(b, offset, 4).order(ByteOrder.LITTLE_ENDIAN).int.toLong() and 0xFFFFFFFFL
        fun skip(count: Long) {
            var left = count
            val scratch = ByteArray(4096)
            while (left > 0) {
                val n = input.read(scratch, 0, minOf(left, scratch.size.toLong()).toInt())
                require(n > 0) { "WAV 文件不完整" }
                left -= n
            }
        }
        val header = bytes(12)
        require(String(header, 0, 4) == "RIFF" && String(header, 8, 4) == "WAVE") { "不是 WAV 文件" }
        var formatSeen = false
        while (true) {
            val chunk = bytes(8)
            val id = String(chunk, 0, 4)
            val size = uint32(chunk, 4)
            when (id) {
                "fmt " -> {
                    require(size in 16..65536) { "WAV fmt 块无效" }
                    val fmt = ByteBuffer.wrap(bytes(size.toInt())).order(ByteOrder.LITTLE_ENDIAN)
                    val code = fmt.getShort(0).toInt()
                    val channels = fmt.getShort(2).toInt()
                    val rate = fmt.getInt(4)
                    val bits = fmt.getShort(14).toInt()
                    require(code == 1 && channels == 1 && rate == Pcm.SAMPLE_RATE && bits == 16) {
                        "测试音频必须是 PCM 16kHz 单声道 16bit，实际是 ${rate}Hz / ${channels}ch / ${bits}bit"
                    }
                    formatSeen = true
                }
                "data" -> {
                    require(formatSeen) { "WAV 缺少 fmt 块" }
                    require(size == 0xFFFFFFFFL || size % 2 == 0L) { "WAV PCM 长度必须为偶数" }
                    return object : InputStream() {
                        var remaining = if (size == 0xFFFFFFFFL) Long.MAX_VALUE else size
                        override fun read(): Int {
                            val b = ByteArray(1)
                            return if (read(b, 0, 1) < 0) -1 else b[0].toInt() and 255
                        }
                        override fun read(b: ByteArray, off: Int, len: Int): Int {
                            if (len == 0) return 0
                            if (remaining == 0L) return -1
                            val n = input.read(b, off, minOf(remaining, len.toLong()).toInt())
                            if (n < 0) require(size == 0xFFFFFFFFL) { "WAV 音频数据不完整" }
                            if (n > 0 && remaining != Long.MAX_VALUE) remaining -= n
                            return n
                        }
                        override fun close() = input.close()
                    }
                }
                else -> {
                    require(size <= 16 * 1024 * 1024) { "WAV 附加块过大" }
                    skip(size)
                }
            }
            if (size % 2 == 1L) skip(1)
        }
    }

    /** Convenience for short clips and unit tests; capture uses [openPcm] instead. */
    fun readPcm(input: InputStream): ByteArray = openPcm(input).use { it.readBytes() }
}
