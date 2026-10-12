package com.oops.app.store

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.RandomAccessFile
import java.time.Instant

/**
 * Meetings the app never stopped (killed, reinstalled or swiped away mid-meeting) leave a meta file without
 * `audio_ended_at`, and the server never learns they ended, so no minutes go out. On the next start their end is
 * estimated from what reached the disk, written into the meta (`recovered`, which the recording upload then
 * carries), and sent once as an end-only meeting sync. `<id>.ended` marks a sent end; it is pruned with the session.
 */
object SessionRecovery {
    /** (session id, estimated end) for every unfinished session whose end has not been sent yet. */
    fun scan(dir: File, active: String?): List<Pair<String, String>> {
        val result = mutableListOf<Pair<String, String>>()
        val metas = dir.listFiles { f -> f.name.endsWith(".json") && f.name.count { it == '.' } == 1 }.orEmpty()
        for (metaFile in metas.sortedBy { it.lastModified() }) {
            val id = metaFile.name.removeSuffix(".json")
            if (id == active || File(dir, "$id.ended").exists()) continue
            val meta = runCatching { JSONObject(metaFile.readText()) }.getOrNull() ?: continue
            if (!meta.has("audio_ended_at")) {
                val end = estimateEnd(dir, id) ?: continue
                meta.put("audio_ended_at", end).put("recovered", true).put("bytes", File(dir, "$id.pcm").length())
                metaFile.writeText(meta.toString(2))
            }
            if (meta.optBoolean("recovered")) result += id to meta.getString("audio_ended_at")
        }
        return result
    }

    /** Only the end: no start (the server already has it), no sentences and no agenda, which the server keeps. */
    fun endBody(id: String, endedAt: String): JSONObject = JSONObject().put("session_id", id).put("ended_at", endedAt)
        .put("recovered", true).put("utterances", JSONArray()).put("notices", JSONArray())

    fun markSent(dir: File, id: String) {
        File(dir, "$id.ended").writeText(Instant.now().toString())
    }

    /** The last wall-clock time written by the ASR or timing log, else when the audio file last grew. */
    internal fun estimateEnd(dir: File, id: String): String? {
        val logged = listOf("asr.jsonl", "timing.jsonl").mapNotNull { lastAt(File(dir, "$id.$it")) }.maxOrNull()
        val ms = logged ?: File(dir, "$id.pcm").takeIf { it.isFile }?.lastModified()?.takeIf { it > 0 } ?: return null
        return Instant.ofEpochMilli(ms).toString()
    }

    private fun lastAt(file: File, tailBytes: Int = 64 * 1024): Long? {
        if (!file.isFile || file.length() == 0L) return null
        val text = RandomAccessFile(file, "r").use { raf ->
            val start = maxOf(0L, raf.length() - tailBytes)
            raf.seek(start)
            ByteArray((raf.length() - start).toInt()).also { raf.readFully(it) }.toString(Charsets.UTF_8)
        }
        return text.lineSequence().toList().asReversed().firstNotNullOfOrNull { line ->
            runCatching { JSONObject(line).optLong("at").takeIf { it > 0 } }.getOrNull()
        }
    }
}
