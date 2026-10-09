package com.oops.app.backend

import com.oops.app.buffer.SpeakerSegment
import com.oops.app.buffer.SpeakerCandidate
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
        .put("confidence", s.confidence ?: JSONObject.NULL).put("tier", s.tier).put("overlap", s.overlap)
        .put("candidates", speakerCandidates(s.candidates))

    fun speakerCandidates(items: List<SpeakerCandidate>) = JSONArray(items.map {
        JSONObject().put("name", it.name).put("score", it.score)
    })

    fun readSpeakerCandidates(items: JSONArray?): List<SpeakerCandidate> =
        (0 until (items?.length() ?: 0)).mapNotNull { i -> items?.optJSONObject(i)?.let { c ->
            val name = namedSpeaker(c.stringOrNull("name"))
            val score = c.optDouble("score", Double.NaN)
            if (name != null && score.isFinite()) SpeakerCandidate(name, score) else null
        } }.sortedByDescending { it.score }.distinctBy { it.name }.take(2)

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
            who = namedSpeaker(json.stringOrNull(if (json.has("owner")) "owner" else "who"))?.takeUnless {
                json.optString("tier") in setOf("B", "C")
            },
            what = json.optString("what"),
            whenIso = time?.stringOrNull("iso"),
            whenRaw = time?.stringOrNull("raw"),
            constraint = json.stringOrNull("constraint"),
            assigneeCandidates = (0 until assignees.length()).map { assignees.getString(it) },
            suggestedOwner = namedSpeaker(json.stringOrNull("suggested_owner")),
            ownerSource = json.optString("owner_source", "none").takeIf { it in setOf("named", "self", "none") } ?: "none",
            ownerConfidence = json.probabilityOrNull("owner_confidence"),
            tier = json.optString("tier", "C").takeIf { it in setOf("A", "B", "C") } ?: "C",
            reason = json.optString("reason", "负责人待确认"),
            ownerCandidates = readSpeakerCandidates(json.optJSONArray("owner_candidates")),
            context = json.optJSONObject("task_context")?.let { runCatching { taskContext(it) }.getOrNull() },
        )
    }

    private fun strings(items: JSONArray?, limit: Int) =
        (0 until minOf(items?.length() ?: 0, limit)).mapNotNull { items?.optString(it)?.trim()?.takeIf(String::isNotEmpty)?.take(80) }

    fun taskContext(json: JSONObject): TaskContext {
        val items = json.optJSONArray("resolutions") ?: JSONArray()
        return TaskContext(json.getString("quote").take(200),
            (0 until minOf(items.length(), 6)).map { i ->
                val r = items.getJSONObject(i)
                TaskResolution(r.optString("phrase").take(12), r.getString("meaning").take(60),
                    r.getJSONArray("evidence").getJSONObject(0).getString("quote").take(60))
            },
            strings(json.optJSONArray("missing"), 6),
            json.optString("owner_role", "unknown").takeIf { it in setOf("speaker", "addressee", "named", "unknown") } ?: "unknown",
            namedSpeaker(json.stringOrNull("clause_speaker")),
            strings(json.optJSONArray("other_tasks"), 2))
    }

    fun confirmRequest(r: ConfirmRequest): JSONObject {
        r.frozenWire()?.let { return JSONObject(it) }
        val d = r.draft
        return JSONObject()
            .put("op_id", r.opId)
            .put("session_id", r.sessionId)
            .put(
                "draft",
                JSONObject()
                    .put("who", d.who)
                    .put("owner", d.who).put("suggested_owner", d.suggestedOwner ?: JSONObject.NULL)
                    .put("owner_source", d.ownerSource).put("owner_confidence", d.ownerConfidence ?: JSONObject.NULL)
                    .put("tier", d.tier).put("reason", d.reason).put("owner_candidates", speakerCandidates(d.ownerCandidates))
                    .put("review_status", d.reviewStatus).put("owner_selection_source", d.ownerSelectionSource)
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
            ).also { r.freezeWire(it.toString()) }
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
        // The whole-sentence name must not wait for the 4-7 s trial split; it is fetched afterwards.
        .put("split", "deferred")

    fun splitRequest(sessionId: String, utteranceId: String): JSONObject =
        JSONObject().put("session_id", sessionId).put("utterance_id", utteranceId)

    fun splitResult(json: JSONObject): SplitFetch = SplitFetch(json.optString("status", "unavailable"),
        runCatching { readSegments(json.optJSONArray("trial_segments")) }.getOrDefault(emptyList()), timing(json))

    private fun timing(json: JSONObject): Map<String, Long> = json.optJSONObject("timing_ms")?.let { o ->
        o.keys().asSequence().associateWith { o.optLong(it, -1) }.filterValues { it >= 0 } } ?: emptyMap()

    private fun readSegments(segments: JSONArray?): List<SpeakerSegment> =
        (0 until (segments?.length() ?: 0)).map { i ->
            val s = segments!!.getJSONObject(i)
            SpeakerSegment(s.optLong("start_ms", -1), s.optLong("end_ms", -1), s.optString("text"),
                s.stringOrNull("speaker"), s.optDouble("score", Double.NaN), s.stringOrNull("source"), s.optBoolean("approx", true),
                s.rawProbability("confidence"), s.optString("tier", "C"), s.optBoolean("overlap", false),
                readSpeakerCandidates(s.optJSONArray("candidates")))
        }

    fun speakerResult(json: JSONObject): SpeakerResult =
        SpeakerResult(json.stringOrNull("speaker"), json.optDouble("score", 0.0),
            readSegments(json.optJSONArray("segments")), json.optBoolean("segments_validated", false),
            json.optJSONObject("preview")?.let { runCatching { speakerPreview(it) }.getOrNull() },
            json.rawProbability("runner_up")?.takeIf { it.isFinite() },
            readSpeakerCandidates(json.optJSONArray("candidates")),
            runCatching { readSegments(json.optJSONArray("trial_segments")) }.getOrDefault(emptyList()),
            json.optBoolean("split_pending", false), timing(json))

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

    private fun JSONObject.rawProbability(key: String): Double? =
        if (!has(key) || isNull(key)) null else optDouble(key, Double.NaN)

    private fun JSONObject.probabilityOrNull(key: String): Double? =
        rawProbability(key)?.takeIf { it.isFinite() && it in 0.0..1.0 }
}
