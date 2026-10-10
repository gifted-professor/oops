package com.oops.app.store

import com.oops.app.buffer.Utterance
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedOutputStream
import java.io.File
import java.io.FileOutputStream
import java.time.Instant
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Local copy of one session's raw assets, uploaded after the meeting (see [RecordingUploader]):
 * `<session>.pcm` is 16 kHz mono s16le on the session timeline (gaps written as silence, like the ring
 * buffer), `<session>.finals.jsonl` each final sentence with word times, `<session>.asr.jsonl` the raw
 * ASR responses and `<session>.timing.jsonl` per-stage latencies. `<session>.json` records how the
 * session ended. Files stay in app-private storage until the server confirmed them and they age out.
 *
 * PCM is written on the audio thread under its own lock; the text logs are appended on a background
 * thread so a slow disk write can never stall capture.
 */
class SessionRecorder(dir: File, val sessionId: String, private val meta: JSONObject,
                      private val maxBytes: Long = 3L * 3600 * 32000) {
    private val pcmFile = File(dir.apply { mkdirs() }, "$sessionId.pcm")
    private val finalsFile = File(dir, "$sessionId.finals.jsonl")
    private val asrFile = File(dir, "$sessionId.asr.jsonl")
    private val timingFile = File(dir, "$sessionId.timing.jsonl")
    private val metaFile = File(dir, "$sessionId.json")
    private val audioLock = Any()
    private var out: BufferedOutputStream? = BufferedOutputStream(FileOutputStream(pcmFile), 64 * 1024)
    var bytes = 0L
        private set
    /** True once the size cap stopped the audio; text logs continue. */
    @Volatile var capped = false
        private set

    init {
        metaFile.writeText(meta.put("format", "pcm_s16le_16000_mono").toString(2))
    }

    fun audio(pcm: ByteArray) {
        synchronized(audioLock) {
            val stream = out ?: return
            if (bytes + pcm.size > maxBytes) {
                capped = true
                return closeAudioLocked()
            }
            stream.write(pcm)
            bytes += pcm.size
        }
    }

    fun silence(samples: Long) {
        var left = samples * 2
        val zeros = ByteArray(32000)
        while (left > 0 && out != null) {
            val n = minOf(left, zeros.size.toLong()).toInt()
            audio(if (n == zeros.size) zeros else zeros.copyOf(n))
            left -= n
        }
    }

    fun final(u: Utterance) {
        val words = JSONArray(u.words.map { JSONObject().put("text", it.text).put("start_ms", it.startMs).put("end_ms", it.endMs) })
        append(finalsFile, JSONObject().put("id", u.id).put("start_ms", u.startMs).put("end_ms", u.endMs)
            .put("text", u.text).put("words", words).toString())
    }

    /** Every raw ASR response as received, with the connection's offset on the session timeline. */
    fun asr(connection: Int, baseMs: Long, payload: String) {
        append(asrFile, JSONObject().put("at", System.currentTimeMillis()).put("connection", connection)
            .put("base_ms", baseMs).put("payload", payload).toString())
    }

    /** One latency record (stage, durations in ms); see `scripts/latency_report.py`. */
    fun timing(line: JSONObject) = append(timingFile, line.toString())

    /** Closes the audio and records how the session ended; the text logs may still receive late lines. */
    fun closeAudio() {
        synchronized(audioLock) { closeAudioLocked() }
    }

    private fun closeAudioLocked() {
        val stream = out ?: return
        out = null
        runCatching { stream.flush() }
        runCatching { stream.close() }
        runCatching {
            metaFile.writeText(JSONObject(meta.toString()).put("audio_ended_at", Instant.now().toString())
                .put("bytes", bytes).put("capped", capped).toString(2))
        }
    }

    /** Removes a session that never got going (capture failed to start), so no empty recording is uploaded. */
    fun discard() {
        synchronized(audioLock) {
            runCatching { out?.close() }
            out = null
        }
        IO.execute { listOf(pcmFile, finalsFile, asrFile, timingFile, metaFile).forEach { it.delete() } }
    }

    /** Waits until every queued text line has been written (tests, and before reading the files back). */
    fun flush(timeoutMs: Long = 5_000) {
        runCatching { IO.submit {}.get(timeoutMs, TimeUnit.MILLISECONDS) }
    }

    private fun append(file: File, line: String) {
        IO.execute { runCatching { file.appendText(line + "\n") } }
    }

    companion object {
        /** One writer thread for every session's text logs: ordered, and off the audio and ASR threads. */
        private val IO = Executors.newSingleThreadExecutor { r -> Thread(r, "oops-recorder-io").apply { isDaemon = true } }

        /**
         * Frees only sessions the server confirmed (`<id>.uploaded`): oldest first, beyond [maxAgeMs] or
         * until the folder fits in [maxBytes]. A session that never reached the server is never deleted.
         */
        fun prune(dir: File, keep: String?, nowMs: Long = System.currentTimeMillis(),
                  maxAgeMs: Long = 7L * 24 * 3600 * 1000, maxBytes: Long = 2L * 1024 * 1024 * 1024): List<String> {
            val sessions = dir.listFiles().orEmpty().groupBy { it.name.substringBefore('.') }
                .filterKeys { it != keep }
                .filterValues { files -> files.any { it.name.endsWith(".uploaded") } }
                .map { (id, files) -> Triple(id, files, files.maxOf { it.lastModified() }) }
                .sortedBy { it.third }
            var total = dir.listFiles().orEmpty().sumOf { it.length() }
            val removed = mutableListOf<String>()
            for ((id, files, modified) in sessions) {
                if (nowMs - modified <= maxAgeMs && total <= maxBytes) continue
                total -= files.sumOf { it.length() }
                files.forEach { it.delete() }
                removed += id
            }
            return removed
        }
    }
}
