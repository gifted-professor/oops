package com.oops.app.backend

import com.oops.app.buffer.SpeakerSegment
import com.oops.app.buffer.WordTime
import com.oops.app.buffer.ValueTags
import org.json.JSONArray
import org.json.JSONObject

/** snake_case wire format of docs/api.md. */
object ApiJson {
    fun candidate(c: Candidate): JSONObject = JSONObject()
        .put("id", c.id)
        .put("start_ms", c.startMs)
        .put("end_ms", c.endMs)
        .put("speaker", c.speaker ?: JSONObject.NULL)
        .put("text", c.text)
        .put("tag", c.tag ?: JSONObject.NULL)
        .put("words", JSONArray(c.words.map(::word)))
        .put("segments", JSONArray(c.segments.map(::segment)))

    fun word(w: WordTime): JSONObject = JSONObject().put("text", w.text).put("start_ms", w.startMs).put("end_ms", w.endMs)
    fun segment(s: SpeakerSegment): JSONObject = JSONObject()
        .put("start_ms", s.startMs).put("end_ms", s.endMs).put("text", s.text)
        .put("speaker", s.speaker ?: JSONObject.NULL).put("score", s.score)
        .put("source", s.source ?: JSONObject.NULL).put("approx", s.approx)

    fun tagRequest(r: TagRequest): JSONObject = JSONObject()
        .put("session_id", r.sessionId).put("utterance_id", r.utterance.id)
        .put("start_ms", r.utterance.startMs).put("end_ms", r.utterance.endMs)
        .put("text", r.utterance.text).put("speaker", r.utterance.speaker ?: JSONObject.NULL)
        .put("context", JSONArray(r.context.takeLast(4).map {
            JSONObject().put("text", it.text).put("speaker", it.speaker ?: JSONObject.NULL)
        }))

    fun tagResult(json: JSONObject): TagResult {
        val tag = json.getString("tag")
        require(tag in ValueTags.ALL) { "后端返回未知价值标签" }
        val prob = if (json.isNull("prob")) null else json.getDouble("prob")
        require(prob == null || (prob.isFinite() && prob in 0.0..1.0)) { "标签概率无效" }
        return TagResult(json.getString("utterance_id"), tag, prob, json.optString("engine", "unknown"))
    }

    fun recallRequest(r: RecallRequest): JSONObject = JSONObject()
        .put("session_id", r.sessionId)
        .put("trigger", r.trigger)
        .put("query", r.query ?: JSONObject.NULL)
        .put("now_ms", r.nowMs)
        .put("candidates", JSONArray(r.candidates.map(::candidate)))

    fun recallResponse(json: JSONObject): RecallResponse {
        val picks = json.optJSONArray("picks") ?: JSONArray()
        return RecallResponse(
            picks = (0 until picks.length()).mapNotNull { i ->
                picks.optJSONObject(i)?.let { Pick(it.getString("id"), it.optDouble("prob", 0.0)) }
            },
            engine = json.optString("engine", "unknown"),
        )
    }

    fun draftRequest(r: DraftRequest): JSONObject = JSONObject()
        .put("session_id", r.sessionId)
        .put("utterances", JSONArray(r.utterances.map(::candidate)))
        .put("context", JSONArray(r.context.map(::candidate)))
        .put("reference_time", r.referenceTime)
        .put("time_zone", r.timeZone)

    fun draftResponse(json: JSONObject): DraftResponse {
        val time = json.optJSONObject("when")
        val assignees = json.optJSONArray("assignee_candidates") ?: JSONArray()
        return DraftResponse(
            who = json.stringOrNull("who"),
            what = json.optString("what"),
            whenIso = time?.stringOrNull("iso"),
            whenRaw = time?.stringOrNull("raw"),
            constraint = json.stringOrNull("constraint"),
            assigneeCandidates = (0 until assignees.length()).map { assignees.getString(it) },
        )
    }

    fun confirmRequest(r: ConfirmRequest): JSONObject {
        val d = r.draft
        return JSONObject()
            .put("op_id", r.opId)
            .put("session_id", r.sessionId)
            .put(
                "draft",
                JSONObject()
                    .put("who", d.who)
                    .put("what", d.what)
                    .put("when", JSONObject().put("iso", d.whenIso ?: JSONObject.NULL).put("raw", d.whenRaw ?: JSONObject.NULL))
                    .put("constraint", d.constraint ?: JSONObject.NULL),
            )
            .put(
                "evidence",
                JSONObject()
                    .put("utterance_ids", JSONArray(d.evidence.utteranceIds))
                    .put("quote", d.evidence.quote)
                    .put("clip_start_ms", d.evidence.clipStartMs)
                    .put("clip_end_ms", d.evidence.clipEndMs),
            )
    }

    fun actionResult(json: JSONObject): ActionResult {
        val task = json.optJSONObject("feishu_task")
        return ActionResult(
            opId = json.getString("op_id"),
            status = json.optString("status", ActionResult.FAILED),
            taskGuid = task?.stringOrNull("guid"),
            taskUrl = task?.stringOrNull("url"),
            message = json.stringOrNull("message"),
        )
    }

    fun speakerRequest(r: SpeakerRequest): JSONObject = JSONObject()
        .put("session_id", r.sessionId)
        .put("utterance_id", r.utteranceId)
        .put("sample_rate", 16000)
        .put("pcm_base64", java.util.Base64.getEncoder().encodeToString(r.pcm))
        .put("text", r.text).put("clip_start_ms", r.clipStartMs)
        .put("words", JSONArray(r.words.map(::word)))
        .put("context", JSONArray(r.context.map(::candidate)))
        .put("preview", r.preview).put("clip_end_ms", r.clipEndMs)

    fun speakerResult(json: JSONObject): SpeakerResult =
        SpeakerResult(json.stringOrNull("speaker"), json.optDouble("score", 0.0),
            json.optJSONArray("segments")?.let { segments ->
                (0 until segments.length()).map { i ->
                    val s = segments.getJSONObject(i)
                    SpeakerSegment(s.getLong("start_ms"), s.getLong("end_ms"), s.getString("text"),
                        s.stringOrNull("speaker"), s.optDouble("score", 0.0), s.stringOrNull("source"), s.optBoolean("approx", true))
                }
            }.orEmpty(), json.optBoolean("segments_validated", false),
            json.optJSONObject("preview")?.let { runCatching { speakerPreview(it) }.getOrNull() })

    fun speakerPreview(json: JSONObject): com.oops.app.buffer.SpeakerPreview {
        val activities = json.getJSONArray("activities")
        require(activities.length() <= 2000)
        val segments = json.optJSONArray("segments") ?: JSONArray()
        require(segments.length() <= 200)
        return com.oops.app.buffer.SpeakerPreview(json.getLong("start_ms"), json.getLong("end_ms"),
            (0 until activities.length()).map { i -> activities.getJSONObject(i).let { a ->
                com.oops.app.buffer.SpeakerActivity(a.getLong("start_ms"), a.getLong("end_ms"),
                    a.getString("kind"), a.stringOrNull("speaker"), a.getDouble("score")) } },
            (0 until segments.length()).map { i -> segments.getJSONObject(i).let { s ->
                com.oops.app.buffer.PreviewSegment(s.getLong("start_ms"), s.getLong("end_ms"), s.getString("text"),
                    s.stringOrNull("speaker"), s.getDouble("score"), s.getString("kind"), s.stringOrNull("source"),
                    s.optBoolean("approx", true)) } }, json.optBoolean("experimental", false), json.optBoolean("jev_used", false))
    }

    private fun JSONObject.stringOrNull(key: String): String? =
        if (isNull(key)) null else optString(key).takeIf { it.isNotEmpty() }
}
