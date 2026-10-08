package com.oops.app.store

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.Instant
import java.util.UUID

/** Local, unverified feedback only. Never feeds speaker validation or meeting sync. */
class OwnerFeedbackStore(private val directory: File, private val now: () -> String = { Instant.now().toString() }) {
    init { directory.mkdirs() }
    private fun file(sessionId: String): File {
        require(sessionId.matches(Regex("[A-Za-z0-9_-]{1,100}")))
        return File(directory, "$sessionId.json")
    }
    private fun load(sessionId: String): JSONObject = file(sessionId).takeIf { it.exists() }?.readText()?.let(::JSONObject)
        ?: JSONObject().put("schema", 1).put("session_id", sessionId).put("purpose", "unverified_owner_feedback")
            .put("owner_change_count", 0).put("events", JSONArray()).put("confirmations", JSONObject())
    private fun save(sessionId: String, data: JSONObject) {
        val target = file(sessionId)
        val temporary = File(directory, "$sessionId.tmp")
        temporary.writeText(data.toString())
        check(temporary.renameTo(target)) { "无法保存负责人反馈" }
    }

    @Synchronized fun record(sessionId: String, draftId: String, utteranceIds: List<String>, previousOwner: String?,
        selectedOwner: String?, modelSuggestion: String?, tier: String, confidence: Double?, revision: Long): Boolean {
        require(draftId.matches(Regex("[A-Za-z0-9_-]{1,100}")))
        require(utteranceIds.size <= 100 && utteranceIds.all { it.matches(Regex("[A-Za-z0-9_-]{1,100}")) })
        require(listOf(previousOwner, selectedOwner, modelSuggestion).all { it == null || it.length <= 60 })
        require(tier in setOf("A", "B", "C"))
        require(confidence == null || (confidence.isFinite() && confidence in 0.0..1.0))
        if (previousOwner.orEmpty() == selectedOwner.orEmpty()) return false
        val data = load(sessionId)
        val events = data.getJSONArray("events")
        val previous = (events.length() - 1 downTo 0).map { events.getJSONObject(it) }
            .firstOrNull { it.getString("draft_id") == draftId }
        // Both input and change can report the same select transition; count it once, even after restart.
        if (previous != null && previous.optString("selected_owner") == selectedOwner.orEmpty()) return false
        require(events.length() < 5000) { "本场负责人反馈数量超限" }
        val confirmation = data.getJSONObject("confirmations").optJSONObject(draftId)
        val confirmed = confirmation != null && confirmation.optString("owner") == selectedOwner.orEmpty()
        events.put(JSONObject().put("id", UUID.randomUUID().toString()).put("draft_id", draftId)
            .put("utterance_ids", JSONArray(utteranceIds)).put("previous_owner", previousOwner ?: JSONObject.NULL)
            .put("selected_owner", selectedOwner ?: JSONObject.NULL).put("model_suggestion", modelSuggestion ?: JSONObject.NULL)
            .put("model_tier", tier).put("model_confidence", confidence ?: JSONObject.NULL).put("model_revision", revision)
            .put("changed_from_model", selectedOwner.orEmpty() != modelSuggestion.orEmpty())
            .put("at", now()).put("confirmed", confirmed)
            .put("confirmed_at", if (confirmed) confirmation!!.getString("at") else JSONObject.NULL))
        data.put("owner_change_count", data.getInt("owner_change_count") + 1)
        save(sessionId, data)
        return true
    }

    @Synchronized fun confirm(sessionId: String, draftId: String, owner: String) {
        require(draftId.matches(Regex("[A-Za-z0-9_-]{1,100}")) && owner.length <= 60)
        val data = load(sessionId)
        val existing = data.getJSONObject("confirmations").optJSONObject(draftId)
        require(existing == null || existing.optString("owner") == owner) { "已确认的负责人反馈不可改写" }
        val confirmation = existing ?: JSONObject().put("owner", owner).put("at", now())
        data.getJSONObject("confirmations").put(draftId, confirmation)
        val events = data.getJSONArray("events")
        (events.length() - 1 downTo 0).map { events.getJSONObject(it) }
            .firstOrNull { it.getString("draft_id") == draftId && it.optString("selected_owner") == owner }
            ?.put("confirmed", true)?.put("confirmed_at", confirmation.getString("at"))
        save(sessionId, data)
    }

    @Synchronized fun count(sessionId: String) = load(sessionId).getInt("owner_change_count")
    @Synchronized fun snapshot(sessionId: String): JSONObject = load(sessionId)
}
