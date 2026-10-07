package com.oops.app.audio

import com.oops.app.core.Pcm
import org.json.JSONObject
import java.io.DataInputStream
import java.net.InetSocketAddress
import java.net.Socket
import kotlin.concurrent.thread

/**
 * Audio captured on a computer — e.g. the wireless lavalier plugged into the Mac mini — and
 * streamed to the phone over TCP, normally through `adb reverse tcp:7790 tcp:7790` on the USB
 * debugging cable. It exercises the same path the 录音豆 will: an external device streams,
 * can drop and come back (→ [AudioSource.Listener.onGap]) and can send a recall mark.
 *
 * Wire format (scripts/mac_audio_bridge.py): 1-byte type | 4-byte big-endian length | payload.
 *   0x01 AUDIO  16 kHz mono s16le PCM
 *   0x02 MARK   UTF-8 JSON {"kind":"double_tap"}
 *   0x03 HELLO  UTF-8 JSON {"device":"Wireless microphone", ...}
 */
class RemoteAudioSource(
    private val host: String,
    private val port: Int,
    private val retryBaseMs: Long = 500,
    private val nowMs: () -> Long = { System.nanoTime() / 1_000_000 },
) : AudioSource {
    override val kind = "remote"

    @Volatile private var running = false
    @Volatile private var socket: Socket? = null
    private var worker: Thread? = null

    // Worker-thread state. Kept outside the read loop because a dropped connection ends it with an exception.
    private var lastAudioAt: Long? = null
    private var failures = 0

    override fun start(listener: AudioSource.Listener) {
        running = true
        lastAudioAt = null
        failures = 0
        worker = thread(name = "oops-remote") { loop(listener) }
    }

    override fun stop() {
        running = false
        runCatching { socket?.close() }
        worker?.interrupt()
        worker?.join(1_000)
        worker = null
    }

    private fun loop(listener: AudioSource.Listener) {
        while (running) {
            try {
                listener.onReady(false)
                listener.onStatus("正在连接 Mac 收音 $host:$port")
                Socket().use { s ->
                    socket = s
                    s.tcpNoDelay = true
                    s.connect(InetSocketAddress(host, port), CONNECT_TIMEOUT_MS)
                    s.soTimeout = READ_TIMEOUT_MS // a stalled sender counts as a disconnect
                    receive(DataInputStream(s.getInputStream().buffered()), listener)
                }
            } catch (e: Exception) {
                if (!running) break
                failures++
                listener.onReady(false)
                listener.onStatus("Mac 收音未连接，重试中（${e.message ?: e.javaClass.simpleName}）")
                try {
                    Thread.sleep(minOf(retryBaseMs shl minOf(failures - 1, 4), MAX_RETRY_MS))
                } catch (_: InterruptedException) {
                    break
                }
            } finally {
                socket = null
            }
        }
    }

    /** Reads frames until the connection ends (normally with an exception). */
    private fun receive(input: DataInputStream, listener: AudioSource.Listener) {
        var firstAudio = true
        val frame = ByteArray(Pcm.FRAME_BYTES)
        var filled = 0
        while (running) {
            val type = input.readUnsignedByte()
            val length = input.readInt()
            require(length in 0..MAX_PAYLOAD_BYTES) { "Mac 收音帧长度异常：$length" }
            val payload = ByteArray(length).also { input.readFully(it) }
            when (type) {
                TYPE_HELLO -> {
                    failures = 0
                    val device = runCatching { JSONObject(String(payload)).optString("device") }.getOrNull()
                    listener.onStatus("Mac 收音：" + (device?.takeIf { it.isNotBlank() } ?: "已连接"))
                }
                TYPE_AUDIO -> {
                    require(length % 2 == 0) { "PCM 长度必须是偶数" }
                    if (firstAudio) {
                        firstAudio = false
                        failures = 0
                        // Audio arrives in real time, so the time between connections is lost audio.
                        lastAudioAt?.let { last ->
                            val missing = nowMs() - last - Pcm.FRAME_MS
                            if (missing >= MIN_GAP_MS) listener.onGap(missing)
                        }
                    }
                    var offset = 0
                    while (offset < payload.size) {
                        val n = minOf(payload.size - offset, frame.size - filled)
                        System.arraycopy(payload, offset, frame, filled, n)
                        filled += n
                        offset += n
                        if (filled == frame.size) {
                            listener.onAudio(frame.copyOf())
                            filled = 0
                        }
                    }
                    lastAudioAt = nowMs()
                }
                TYPE_MARK -> {
                    val kind = runCatching { JSONObject(String(payload)).optString("kind") }.getOrNull()
                    if (kind == "double_tap") listener.onMark(AudioSource.Mark.DOUBLE_TAP)
                }
                // Unknown types are skipped so the bridge can grow without breaking older apps.
            }
        }
    }

    companion object {
        const val DEFAULT_PORT = 7790
        private const val TYPE_AUDIO = 0x01
        private const val TYPE_MARK = 0x02
        private const val TYPE_HELLO = 0x03
        private const val CONNECT_TIMEOUT_MS = 3_000
        private const val READ_TIMEOUT_MS = 3_000
        private const val MAX_RETRY_MS = 5_000L
        private const val MIN_GAP_MS = 300L
        private const val MAX_PAYLOAD_BYTES = 1 shl 20
    }
}
