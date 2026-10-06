package com.oops.app.asr

import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.util.zip.GZIPInputStream
import java.util.zip.GZIPOutputStream

/**
 * Binary frame codec for 豆包流式语音识别 (`/api/v3/sauc/bigmodel_async`).
 *
 * Frame = 4-byte header | [4-byte sequence] | 4-byte big-endian payload size | payload.
 * Header bits: version(4)=1, header size(4)=1 (×4 bytes), message type(4), flags(4),
 * serialization(4), compression(4), reserved(8).
 */
object VolcProtocol {
    const val TYPE_FULL_CLIENT_REQUEST = 0b0001
    const val TYPE_AUDIO_ONLY = 0b0010
    const val TYPE_FULL_SERVER_RESPONSE = 0b1001
    const val TYPE_SERVER_ERROR = 0b1111

    const val FLAG_NONE = 0b0000
    const val FLAG_POSITIVE_SEQUENCE = 0b0001
    const val FLAG_LAST_PACKET = 0b0010
    const val FLAG_NEGATIVE_SEQUENCE = 0b0011

    const val SERIALIZATION_NONE = 0b0000
    const val SERIALIZATION_JSON = 0b0001
    const val COMPRESSION_NONE = 0b0000
    const val COMPRESSION_GZIP = 0b0001

    sealed interface ServerMessage {
        data class Response(val sequence: Int?, val isLast: Boolean, val payload: String) : ServerMessage
        data class Error(val code: Long, val message: String) : ServerMessage
    }

    fun encode(
        type: Int,
        flags: Int,
        serialization: Int,
        compression: Int,
        payload: ByteArray,
        sequence: Int? = null,
    ): ByteArray {
        val out = ByteArrayOutputStream(12 + payload.size)
        out.write(0x11)
        out.write((type shl 4) or flags)
        out.write((serialization shl 4) or compression)
        out.write(0)
        if (sequence != null) out.write(int32(sequence))
        out.write(int32(payload.size))
        out.write(payload)
        return out.toByteArray()
    }

    fun fullClientRequest(json: String): ByteArray = encode(
        TYPE_FULL_CLIENT_REQUEST, FLAG_NONE, SERIALIZATION_JSON, COMPRESSION_GZIP,
        gzip(json.toByteArray(Charsets.UTF_8)),
    )

    /** Raw PCM packet; [last] marks the final (negative) packet that ends the stream. */
    fun audio(pcm: ByteArray, last: Boolean): ByteArray = encode(
        TYPE_AUDIO_ONLY, if (last) FLAG_LAST_PACKET else FLAG_NONE, SERIALIZATION_NONE, COMPRESSION_NONE, pcm,
    )

    fun decode(frame: ByteArray): ServerMessage {
        require(frame.size >= 8) { "frame too short: ${frame.size} bytes" }
        val headerSize = (frame[0].toInt() and 0x0F) * 4
        val type = (frame[1].toInt() and 0xFF) ushr 4
        val flags = frame[1].toInt() and 0x0F
        val compression = frame[2].toInt() and 0x0F
        var offset = headerSize
        if (type == TYPE_SERVER_ERROR) {
            val code = uint32(frame, offset)
            val size = uint32(frame, offset + 4).toInt()
            val body = frame.copyOfRange(offset + 8, minOf(frame.size, offset + 8 + size))
            return ServerMessage.Error(code, String(maybeGunzip(body, compression), Charsets.UTF_8))
        }
        var sequence: Int? = null
        if ((flags and FLAG_POSITIVE_SEQUENCE) != 0) {
            sequence = int32At(frame, offset)
            offset += 4
        }
        val size = uint32(frame, offset).toInt()
        val body = frame.copyOfRange(offset + 4, minOf(frame.size, offset + 4 + size))
        return ServerMessage.Response(
            sequence = sequence,
            isLast = (flags and FLAG_LAST_PACKET) != 0,
            payload = String(maybeGunzip(body, compression), Charsets.UTF_8),
        )
    }

    fun gzip(data: ByteArray): ByteArray =
        ByteArrayOutputStream().also { bytes -> GZIPOutputStream(bytes).use { it.write(data) } }.toByteArray()

    fun gunzip(data: ByteArray): ByteArray = GZIPInputStream(ByteArrayInputStream(data)).use { it.readBytes() }

    private fun maybeGunzip(body: ByteArray, compression: Int) =
        if (compression == COMPRESSION_GZIP && body.isNotEmpty()) gunzip(body) else body

    private fun int32(v: Int) =
        byteArrayOf((v ushr 24).toByte(), (v ushr 16).toByte(), (v ushr 8).toByte(), v.toByte())

    private fun int32At(b: ByteArray, o: Int): Int =
        ((b[o].toInt() and 0xFF) shl 24) or ((b[o + 1].toInt() and 0xFF) shl 16) or
            ((b[o + 2].toInt() and 0xFF) shl 8) or (b[o + 3].toInt() and 0xFF)

    private fun uint32(b: ByteArray, o: Int): Long = int32At(b, o).toLong() and 0xFFFFFFFFL
}
