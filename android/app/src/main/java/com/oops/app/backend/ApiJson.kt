package com.oops.app.backend

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

    private fun JSONObject.stringOrNull(key: String): String? =
        if (isNull(key)) null else optString(key).takeIf { it.isNotEmpty() }
}
