package com.oops.app.store

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.io.RandomAccessFile
import java.security.MessageDigest
import java.time.Instant

/** Server side of the raw-asset archive (see scripts/recordings.py); blocking calls. */
interface RecordingTransport {
    fun recordingManifest(sessionId: String, body: JSONObject): JSONObject
    /** Returns the server's received byte count; throws [ResumeAt] when the server holds a different offset. */
    fun recordingChunk(sessionId: String, name: String, offset: Long, bytes: ByteArray, sha256: String): Long
    fun recordingComplete(sessionId: String): JSONObject
}

class ResumeAt(val received: Long) : IOException("resume at $received")

/**
 * Uploads each finished session's raw audio, finals and raw ASR stream, resumably and verified.
 * A session is marked `<id>.uploaded` only after the server re-hashed every whole file; until then
 * [SessionRecorder.prune] keeps it no matter how old it is.
 */
class RecordingUploader(private val dir: File, private val transport: RecordingTransport,
                        private val chunkBytes: Int = 1_000_000) {
    private val names = linkedMapOf("audio.pcm" to "pcm", "finals.jsonl" to "finals.jsonl",
        "asr.jsonl" to "asr.jsonl", "timing.jsonl" to "timing.jsonl", "meta.json" to "json")

    fun pending(active: String?): List<String> = dir.listFiles { f -> f.name.endsWith(".pcm") }.orEmpty()
        .map { it.name.removeSuffix(".pcm") }
        .filter { it != active && needsUpload(it) }
        .sortedBy { File(dir, "$it.pcm").lastModified() }

    private fun files(id: String) = names.mapValues { File(dir, "$id.${it.value}") }.filterValues { it.isFile }

    private fun needsUpload(id: String): Boolean {
        val marker = File(dir, "$id.uploaded")
        return !marker.isFile || files(id).values.any { it.lastModified() > marker.lastModified() }
    }

    /**
     * Uploads one session; returns true once the server confirmed the whole archive. [shouldContinue] is
     * checked before every chunk so a meeting that starts mid-upload gets the network back at once; the
     * server keeps what it received and the next pass resumes from there.
     */
    fun upload(id: String, shouldContinue: () -> Boolean = { true }): Boolean {
        val files = files(id)
        if ("audio.pcm" !in files) return false
        val declared = files.mapValues { (_, f) -> f.length() to sha256(f) }
        val manifest = JSONObject().put("files", JSONArray(declared.map { (name, v) ->
            JSONObject().put("name", name).put("size", v.first).put("sha256", v.second) }))
            .put("meta", runCatching { JSONObject(files.getValue("meta.json").readText()) }.getOrNull() ?: JSONObject())
        val state = transport.recordingManifest(id, manifest)
        if (!state.optBoolean("complete")) {
            val received = state.optJSONObject("received") ?: JSONObject()
            for ((name, file) in files) {
                var offset = received.optLong(name, 0)
                val size = declared.getValue(name).first
                RandomAccessFile(file, "r").use { raf ->
                    while (offset < size) {
                        if (!shouldContinue()) return false
                        val bytes = ByteArray(minOf(chunkBytes.toLong(), size - offset).toInt())
                        raf.seek(offset)
                        raf.readFully(bytes)
                        offset = try {
                            transport.recordingChunk(id, name, offset, bytes, sha256(bytes))
                        } catch (resume: ResumeAt) {
                            resume.received
                        }
                    }
                }
            }
            if (!transport.recordingComplete(id).optBoolean("complete")) return false
        }
        File(dir, "$id.uploaded").writeText(JSONObject().put("uploaded_at", Instant.now().toString())
            .put("files", JSONObject(declared.mapValues { JSONObject().put("size", it.value.first).put("sha256", it.value.second) }))
            .toString(2))
        return true
    }

    companion object {
        fun sha256(file: File): String = MessageDigest.getInstance("SHA-256").let { digest ->
            file.inputStream().buffered().use { input ->
                val buffer = ByteArray(1 shl 16)
                while (true) { val n = input.read(buffer); if (n < 0) break; digest.update(buffer, 0, n) }
            }
            digest.digest().joinToString("") { "%02x".format(it) }
        }

        fun sha256(bytes: ByteArray): String =
            MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
    }
}
