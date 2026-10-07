package com.oops.app.asr

import com.oops.app.buffer.WordTime
import org.json.JSONArray
import org.json.JSONObject

/** Times are milliseconds relative to the audio this connection has received. */
data class RawUtterance(
    val startMs: Long,
    val endMs: Long,
    val text: String,
    val definite: Boolean,
    val speakerId: String?,
    val words: List<WordTime> = emptyList(),
) {
    fun shift(ms: Long) = if (ms == 0L) this else copy(startMs = startMs + ms, endMs = endMs + ms, words = words.map { it.shift(ms) })
}

data class AsrUpdate(val finals: List<RawUtterance>, val partial: RawUtterance?)

data class VolcCredentials(
    val apiKey: String,
    val appId: String,
    val accessToken: String,
    val resourceId: String,
) {
    /** New console: a single X-Api-Key. Legacy console: APP ID + Access Token. */
    val isConfigured: Boolean get() = apiKey.isNotBlank() || (appId.isNotBlank() && accessToken.isNotBlank())
}

/** Parses `result_type=single` responses (see docs/README for observed quirks). */
object VolcResult {
    fun parse(json: String): AsrUpdate {
        if (json.isBlank()) return AsrUpdate(emptyList(), null)
        val result = JSONObject(json).optJSONObject("result") ?: return AsrUpdate(emptyList(), null)
        val utterances = result.optJSONArray("utterances") ?: return AsrUpdate(emptyList(), null)
        val finals = ArrayList<RawUtterance>()
        var partial: RawUtterance? = null
        for (i in 0 until utterances.length()) {
            val u = utterances.optJSONObject(i) ?: continue
            // The server omits zero-valued fields, e.g. start_time of the very first sentence.
            val start = u.optLong("start_time", 0L)
            val parsed = RawUtterance(
                startMs = start,
                endMs = maxOf(start, u.optLong("end_time", start)),
                text = u.stringOrNull("text").orEmpty().trim(),
                definite = u.optBoolean("definite", false),
                speakerId = u.optJSONObject("additions")?.let { it.stringOrNull("speaker_id") ?: it.stringOrNull("speaker") },
                words = u.optJSONArray("words")?.let { words ->
                    (0 until words.length()).mapNotNull { j ->
                        val w = words.optJSONObject(j) ?: return@mapNotNull null
                        val word = w.stringOrNull("text") ?: w.stringOrNull("word") ?: return@mapNotNull null
                        if (!w.has("end_time")) return@mapNotNull null
                        val a = w.optLong("start_time", 0)
                        val b = w.optLong("end_time", a)
                        if (a < 0 || b < a) null else WordTime(word, a, b)
                    }
                }.orEmpty(),
            )
            if (parsed.text.isEmpty()) continue
            if (parsed.definite) finals += parsed else partial = parsed
        }
        return AsrUpdate(finals, partial)
    }

    private fun JSONObject.stringOrNull(key: String): String? =
        if (isNull(key)) null else optString(key).takeIf { it.isNotEmpty() }
}

object VolcRequest {
    /**
     * Speaker diarization stays off by default: in our 2026-10-06 test, enabling
     * `enable_speaker_info` + `ssd_version=200` lost 2 of 5 sentences.
     */
    fun json(uid: String, speakerInfo: Boolean, hotwords: List<String>): String {
        val request = JSONObject()
            .put("model_name", "bigmodel")
            .put("enable_nonstream", true) // 二遍识别: only second-pass results are `definite`
            .put("enable_itn", true)
            .put("enable_punc", true)
            .put("show_utterances", true)
            .put("result_type", "single") // incremental; `full` grows without bound over a long session
        if (speakerInfo) {
            request.put("enable_speaker_info", true).put("ssd_version", "200")
        }
        if (hotwords.isNotEmpty()) {
            val words = JSONArray().apply { hotwords.forEach { put(JSONObject().put("word", it)) } }
            request.put("corpus", JSONObject().put("context", JSONObject().put("hotwords", words).toString()))
        }
        return JSONObject()
            .put("user", JSONObject().put("uid", uid))
            .put(
                "audio",
                JSONObject().put("format", "pcm").put("codec", "raw")
                    .put("rate", 16000).put("bits", 16).put("channel", 1),
            )
            .put("request", request)
            .toString()
    }
}
