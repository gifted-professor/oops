package com.oops.app.asr

import com.oops.app.asr.VolcProtocol.ServerMessage
import com.oops.app.core.Pcm
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.util.UUID
import java.util.concurrent.atomic.AtomicBoolean

/**
 * One streaming-ASR connection. A conversation uses a single connection and keeps sending
 * audio through pauses (otherwise the server times out with 45000081 and speaker ids reset).
 * All reported times are relative to the first audio sent on this connection.
 */
class VolcAsrClient(
    private val http: OkHttpClient,
    private val credentials: VolcCredentials,
    private val requestJson: String,
    private val listener: Listener,
) {
    interface Listener {
        fun onOpen()
        fun onPartial(text: String, startMs: Long, endMs: Long)
        fun onFinal(utterance: RawUtterance)

        /** Every raw server response payload, before parsing (kept as a raw asset). */
        fun onRaw(payload: String) {}

        /** Called exactly once. [error] is null after a normal [finish]; [fatal] means retrying won't help. */
        fun onClosed(error: String?, fatal: Boolean)
    }

    private val lock = Any()
    private var socket: WebSocket? = null
    private var opened = false
    private var finishing = false
    private val pending = ArrayDeque<ByteString>()
    private var pendingAudioBytes = 0

    /** Audio dropped while still connecting shifts the server clock; added back to every timestamp. */
    @Volatile private var droppedMs = 0L
    private var lastFinalEndMs = -1L
    private val closed = AtomicBoolean(false)

    fun connect() {
        val request = Request.Builder().url(ENDPOINT).apply {
            header("X-Api-Resource-Id", credentials.resourceId)
            header("X-Api-Connect-Id", UUID.randomUUID().toString())
            if (credentials.apiKey.isNotBlank()) {
                header("X-Api-Key", credentials.apiKey)
            } else {
                header("X-Api-App-Key", credentials.appId)
                header("X-Api-Access-Key", credentials.accessToken)
            }
        }.build()
        synchronized(lock) { pending.addLast(VolcProtocol.fullClientRequest(requestJson).toByteString()) }
        socket = http.newWebSocket(request, socketListener)
    }

    fun sendAudio(pcm: ByteArray) {
        synchronized(lock) {
            if (finishing || closed.get()) return
            enqueueLocked(VolcProtocol.audio(pcm, last = false).toByteString(), isAudio = true)
        }
    }

    /** Sends the last (negative) packet; the server flushes remaining results and closes. */
    fun finish() {
        synchronized(lock) {
            if (finishing || closed.get()) return
            finishing = true
            enqueueLocked(VolcProtocol.audio(ByteArray(0), last = true).toByteString(), isAudio = false)
        }
    }

    /** Drops the connection without reporting back. */
    fun cancel() {
        if (closed.compareAndSet(false, true)) socket?.cancel()
    }

    private fun enqueueLocked(frame: ByteString, isAudio: Boolean) {
        if (opened) {
            socket?.send(frame)
            return
        }
        if (isAudio && pendingAudioBytes + frame.size > MAX_PENDING_AUDIO_BYTES && pending.size > 1) {
            // Still connecting after ~10 s: drop the oldest audio, keep the request frame at the head.
            val dropped = pending.removeAt(1)
            pendingAudioBytes -= dropped.size
            droppedMs += Pcm.samplesToMs(((dropped.size - AUDIO_FRAME_OVERHEAD) / 2).toLong())
        }
        pending.addLast(frame)
        if (isAudio) pendingAudioBytes += frame.size
    }

    private fun report(error: String?, fatal: Boolean) {
        if (closed.compareAndSet(false, true)) listener.onClosed(error, fatal)
    }

    private val socketListener = object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            synchronized(lock) {
                opened = true
                while (pending.isNotEmpty()) webSocket.send(pending.removeFirst())
                pendingAudioBytes = 0
            }
            listener.onOpen()
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            if (closed.get()) return
            val message = runCatching { VolcProtocol.decode(bytes.toByteArray()) }.getOrNull() ?: return
            when (message) {
                is ServerMessage.Error -> {
                    webSocket.cancel()
                    report("ASR 错误 ${message.code}: ${message.message}", fatal = false)
                }
                is ServerMessage.Response -> {
                    runCatching { listener.onRaw(message.payload) }
                    val update = runCatching { VolcResult.parse(message.payload) }.getOrNull()
                    update?.finals?.forEach { u ->
                        // `single` mode sends each definite sentence once; guard against repeats anyway.
                        if (u.endMs > lastFinalEndMs) {
                            lastFinalEndMs = u.endMs
                            listener.onFinal(u.shift(droppedMs))
                        }
                    }
                    update?.partial?.let { listener.onPartial(it.text, it.startMs + droppedMs, it.endMs + droppedMs) }
                    if (message.isLast) {
                        webSocket.close(1000, null)
                        report(null, fatal = false)
                    }
                }
            }
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(1000, null)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            report(if (finishing) null else "连接被服务端关闭（$code $reason）", fatal = false)
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            val httpError = response?.let { r -> "HTTP ${r.code} " + runCatching { r.body.string() }.getOrDefault("") }
            val detail = listOfNotNull(httpError, t.message).joinToString(" ").ifBlank { t.javaClass.simpleName }
            report(detail, fatal = response?.code?.let { it in FATAL_HTTP_CODES } == true)
        }
    }

    companion object {
        const val ENDPOINT = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
        private const val AUDIO_FRAME_OVERHEAD = 8
        private const val MAX_PENDING_AUDIO_BYTES = 10 * Pcm.SAMPLE_RATE * Pcm.BYTES_PER_SAMPLE
        private val FATAL_HTTP_CODES = setOf(400, 401, 403)
    }
}
