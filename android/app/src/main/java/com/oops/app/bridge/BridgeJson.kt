package com.oops.app.bridge

import com.oops.app.backend.ActionDraft
import com.oops.app.backend.ActionResult
import com.oops.app.buffer.Utterance
import com.oops.app.engine.EngineState
import com.oops.app.engine.OopsEngine
import com.oops.app.engine.OopsEvent
import com.oops.app.recall.RecallCard
import org.json.JSONArray
import org.json.JSONObject

/** Engine models → camelCase JSON for the H5 page (docs/bridge.md). */
object BridgeJson {
    private val NULL = JSONObject.NULL

    fun state(s: EngineState): JSONObject = JSONObject()
        .put("capturing", s.capturing)
        .put("sessionId", s.sessionId)
        .put("source", s.source)
        .put("sessionMs", s.sessionMs)
        .put("bufferedMs", s.bufferedMs)
        .put("retentionMs", OopsEngine.RETENTION_MS)
        .put("asr", s.asr)
        .put("asrDetail", s.asrDetail ?: NULL)
        .put("level", s.level.toDouble())
        .put("playing", s.playing)
        .put("savedCount", s.savedCount)
        .put("backend", s.backend)
        .put("backendUrl", s.backendUrl)
        .put("speakerInfo", s.speakerInfo)
        .put("testWavName", s.testWavName)
        .put("sourceDetail", s.sourceDetail ?: NULL)
        .put("remoteAddress", s.remoteAddress)
        .put("asrGaps", JSONArray(s.asrGaps.map { JSONObject().put("fromMs", it.fromMs).put("toMs", it.toMs) }))

    fun utterance(u: Utterance): JSONObject = JSONObject()
        .put("id", u.id)
        .put("startMs", u.startMs)
        .put("endMs", u.endMs)
        .put("text", u.text)
        .put("speaker", u.speaker ?: NULL)

    fun card(c: RecallCard): JSONObject = JSONObject()
        .put("id", c.id)
        .put("trigger", c.trigger)
        .put("query", c.query ?: NULL)
        .put("nowMs", c.nowMs)
        .put("single", c.single)
        .put("engine", c.engine)
        .put("windowMs", c.windowMs)
        .put("latencyMs", c.latencyMs)
        .put(
            "items",
            JSONArray(
                c.items.map {
                    JSONObject().put("id", it.utteranceId).put("startMs", it.startMs).put("endMs", it.endMs)
                        .put("text", it.text).put("speaker", it.speaker ?: NULL).put("prob", it.prob)
                },
            ),
        )
        .put("pool", JSONArray(c.pool().map(::utterance)))

    fun draft(d: ActionDraft): JSONObject = JSONObject()
        .put("draftId", d.draftId)
        .put("cardId", d.cardId)
        .put("who", d.who)
        .put("what", d.what)
        .put("whenIso", d.whenIso ?: NULL)
        .put("whenRaw", d.whenRaw ?: NULL)
        .put("constraint", d.constraint ?: NULL)
        .put("assigneeCandidates", JSONArray(d.assigneeCandidates))
        .put(
            "evidence",
            JSONObject()
                .put("utteranceIds", JSONArray(d.evidence.utteranceIds))
                .put("quote", d.evidence.quote)
                .put("startMs", d.evidence.clipStartMs)
                .put("endMs", d.evidence.clipEndMs),
        )

    fun actionResult(draftId: String, r: ActionResult): JSONObject = JSONObject()
        .put("draftId", draftId)
        .put("opId", r.opId)
        .put("status", r.status)
        .put("taskGuid", r.taskGuid ?: NULL)
        .put("taskUrl", r.taskUrl ?: NULL)
        .put("message", r.message ?: NULL)
        .put("readBackConfirmed", r.readBackConfirmed)
        .put("polling", r.polling)
        .put("mock", r.mock)

    fun saved(items: List<JSONObject>): JSONObject = JSONObject().put("items", JSONArray(items))

    fun event(e: OopsEvent): Pair<String, JSONObject> = when (e) {
        is OopsEvent.Partial ->
            "asr.partial" to JSONObject().put("text", e.text).put("startMs", e.startMs).put("endMs", e.endMs)
        is OopsEvent.Final -> "asr.final" to utterance(e.utterance)
        is OopsEvent.SpeakerIdentified ->
            "asr.speaker" to JSONObject().put("id", e.utteranceId).put("speaker", e.speaker).put("score", e.score)
        is OopsEvent.Pruned -> "transcript.pruned" to JSONObject().put("ids", JSONArray(e.ids))
        is OopsEvent.RecallPending -> "recall.pending" to JSONObject().put("trigger", e.trigger)
        is OopsEvent.RecallReady -> "recall.card" to card(e.card)
        is OopsEvent.RecallNotFound -> "recall.notFound" to JSONObject().put("reason", e.reason)
        is OopsEvent.Clip -> "clip.state" to JSONObject().put("key", e.key).put("state", e.state)
        is OopsEvent.Saved ->
            "recall.saved" to JSONObject().put("cardId", e.cardId).put("savedCount", e.savedCount)
        is OopsEvent.DraftReady -> "action.draft" to draft(e.draft)
        is OopsEvent.ActionUpdate -> "action.result" to actionResult(e.draftId, e.result)
        is OopsEvent.Latency ->
            "latency" to JSONObject().put("recentMs", JSONArray(e.recentMs)).put("medianMs", e.medianMs)
        is OopsEvent.Toast -> "toast" to JSONObject().put("message", e.message)
        is OopsEvent.Failure -> "error" to JSONObject().put("scope", e.scope).put("message", e.message)
    }
}
