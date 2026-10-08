package com.oops.app.store

import com.oops.app.backend.*
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** Durable confirmed requests. The exact payload is frozen before any external write. */
class ActionOperationStore(private val dir: File) {
    data class Operation(
        val request: ConfirmRequest,
        val backendUrl: String,
        val mock: Boolean,
        val result: ActionResult,
    )

    init { dir.mkdirs() }

    @Synchronized fun save(operation: Operation) {
        val r = operation.request
        require(r.opId.matches(Regex("[A-Za-z0-9-]+")))
        val json = JSONObject()
            .put("request", ApiJson.confirmRequest(r))
            .put("draft_id", r.draft.draftId).put("card_id", r.draft.cardId)
            .put("assignees", JSONArray(r.draft.assigneeCandidates))
            .put("model_revision", r.draft.modelRevision)
            .put("backend_url", operation.backendUrl).put("mock", operation.mock)
            .put("result", JSONObject().put("op_id", r.opId).put("status", operation.result.status)
                .put("feishu_task", JSONObject().put("guid", operation.result.taskGuid ?: JSONObject.NULL)
                    .put("url", operation.result.taskUrl ?: JSONObject.NULL))
                .put("message", operation.result.message ?: JSONObject.NULL)
                .put("read_back_confirmed", operation.result.readBackConfirmed))
        val target = File(dir, "${r.opId}.json")
        val temp = File(dir, "${r.opId}.tmp")
        temp.writeText(json.toString())
        check(temp.renameTo(target)) { "无法保存操作编号" }
    }

    @Synchronized fun list(): List<Operation> = dir.listFiles { f -> f.extension == "json" }.orEmpty()
        .sortedByDescending { it.lastModified() }.mapNotNull { f ->
            runCatching {
                val j = JSONObject(f.readText())
                val r = j.getJSONObject("request")
                val d = r.getJSONObject("draft")
                val e = r.getJSONObject("evidence")
                val time = d.optJSONObject("when")
                fun JSONObject.nullable(k: String) = if (isNull(k)) null else optString(k).takeIf { it.isNotEmpty() }
                fun JSONArray.strings() = (0 until length()).map { getString(it) }
                val draft = ActionDraft(j.getString("draft_id"), j.getString("card_id"),
                    d.getString("who"), d.getString("what"), time?.nullable("iso"), time?.nullable("raw"),
                    d.nullable("constraint"), j.getJSONArray("assignees").strings(),
                    Evidence(e.getJSONArray("utterance_ids").strings(), e.getString("quote"),
                        e.getLong("clip_start_ms"), e.getLong("clip_end_ms")),
                    d.nullable("suggested_owner"), d.optString("owner_source", "none"),
                    if (d.isNull("owner_confidence")) null else d.optDouble("owner_confidence").takeIf { it.isFinite() && it in 0.0..1.0 },
                    d.optString("tier", "C"), d.optString("reason", "负责人待确认"),
                    ApiJson.readSpeakerCandidates(d.optJSONArray("owner_candidates")), j.optLong("model_revision", 0),
                    d.optString("review_status", "confirmed"), d.optString("owner_selection_source", "user"))
                val mock = j.getBoolean("mock")
                val result = ApiJson.actionResult(j.getJSONObject("result")).copy(
                    mock = mock, readBackConfirmed = j.getJSONObject("result").optBoolean("read_back_confirmed"))
                Operation(ConfirmRequest(r.getString("op_id"), r.getString("session_id"), draft).also { it.freezeWire(r.toString()) },
                    j.getString("backend_url"), mock, result)
            }.getOrNull()
        }
}
