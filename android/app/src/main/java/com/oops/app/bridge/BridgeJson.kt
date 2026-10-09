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
        .put("voiceprint", JSONObject().put("phase", s.voiceprint.phase).put("name", s.voiceprint.name)
            .put("mode", s.voiceprint.mode).put("elapsedMs", s.voiceprint.elapsedMs).put("source", s.voiceprint.source)
            .put("message", s.voiceprint.message).put("catalog", s.voiceprint.catalog ?: NULL)
            .put("diagnosis", s.voiceprint.diagnosis ?: NULL).put("quality", s.voiceprint.quality ?: NULL)
            .put("qualityPassed", s.voiceprint.qualityPassed))
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
        .put("hasBackendToken", s.hasBackendToken)
        .put("speakerInfo", s.speakerInfo)
        .put("speakerPreviewEnabled", s.speakerPreviewEnabled).put("recordSession", s.recordSession)
        .put("recordingsPending", s.recordingsPending).put("recordingUpload", s.recordingUpload).put("recordingCapped", s.recordingCapped).put("micBusy", s.micBusy).put("liveSpeakerStatus", s.liveSpeakerStatus ?: NULL)
        .put("liveSpeakers", JSONArray(s.liveSpeakers)).put("liveOverlap", s.liveOverlap)
        .put("testWavName", s.testWavName)
        .put("sourceDetail", s.sourceDetail ?: NULL)
        .put("sourceReady", s.sourceReady)
        .put("remoteAddress", s.remoteAddress)
        .put("asrGaps", JSONArray(s.asrGaps.map { JSONObject().put("fromMs", it.fromMs).put("toMs", it.toMs) }))
        .put("digestCounts", JSONObject(s.digestCounts)).put("agenda", s.agenda).put("notices", JSONArray(s.notices))
        .put("voiceTrigger", s.voiceTrigger).put("noticeVibrate", s.noticeVibrate).put("memorySync", s.memorySync).put("selfName", s.selfName)
        .put("syncStatus", s.syncStatus).put("syncPending", s.syncPending)
        .put("commitmentCount", s.commitmentCount).put("analysisPending", s.analysisPending)
        .put("analysisFailed", s.analysisFailed).put("tagsSupported", s.tagsSupported ?: NULL)
        .put("sessionTruncated", s.sessionTruncated)

    fun utterance(u: Utterance): JSONObject = JSONObject()
        .put("id", u.id)
        .put("startMs", u.startMs)
        .put("endMs", u.endMs)
        .put("text", u.text)
        // Shown instead of `text` when different (a phone number joined); fillers are hidden by the page.
        .put("display", com.oops.app.core.TextClean.display(u.text)).put("filler", com.oops.app.core.TextClean.isFiller(u.text))
        .put("speaker", u.speaker ?: NULL)
        .put("speakerState", u.speakerState ?: NULL).put("speakerScore", u.speakerScore ?: NULL)
        .put("speakerPreview", u.speakerPreview?.let(::preview) ?: NULL)
        .put("tag", u.tag ?: NULL).put("tagProb", u.tagProb ?: NULL).put("tagEngine", u.tagEngine ?: NULL)
        .put("words", JSONArray(u.words.map { JSONObject().put("text", it.text).put("startMs", it.startMs).put("endMs", it.endMs) }))
        .put("segments", segments(u.segments))
        .put("speakerCandidates", com.oops.app.backend.ApiJson.speakerCandidates(u.speakerCandidates))
        .put("trialSegments", segments(u.trialSegments))
        .put("manualSpeaker", u.manualSpeaker ?: NULL)

    private fun segments(items: List<com.oops.app.buffer.SpeakerSegment>) = JSONArray(items.map {
        JSONObject().put("startMs", it.startMs).put("endMs", it.endMs).put("text", it.text)
            .put("speaker", it.speaker ?: NULL).put("score", it.score).put("source", it.source ?: NULL).put("approx", it.approx)
            .put("confidence", it.confidence ?: NULL).put("tier", it.tier).put("overlap", it.overlap)
            .put("candidates", com.oops.app.backend.ApiJson.speakerCandidates(it.candidates))
    })

    fun summary(s: OopsEvent.SessionSummary): JSONObject {
        fun rows(items: List<Utterance>) = JSONArray(items.map { u -> utterance(u)
            .put("audioExpired", u.id !in s.audioIds).also { row ->
                s.hints[u.id]?.let { hints -> hints.keys().forEach { row.put(it, hints.get(it)) } }
            } })
        return JSONObject().put("sessionId", s.sessionId).put("commitments", rows(s.commitments))
            .put("decisions", rows(s.grouped["decision"].orEmpty())).put("constraints", rows(s.grouped["constraint"].orEmpty()))
            .put("ideas", rows(s.grouped["idea"].orEmpty())).put("notices", JSONArray(s.notices))
    }

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
                        .put("text", it.text).put("speaker", it.speaker ?: NULL).put("prob", it.prob).put("audioAvailable", it.audioAvailable)
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
        .put("owner", d.who.ifBlank { null } ?: NULL).put("suggestedOwner", d.suggestedOwner ?: NULL)
        .put("ownerSource", d.ownerSource).put("ownerConfidence", d.ownerConfidence ?: NULL)
        .put("tier", d.tier).put("reason", d.reason)
        .put("ownerCandidates", com.oops.app.backend.ApiJson.speakerCandidates(d.ownerCandidates))
        .put("modelRevision", d.modelRevision).put("reviewStatus", d.reviewStatus)
        .put("ownerSelectionSource", d.ownerSelectionSource)
        .put("taskContext", d.context?.let { c -> JSONObject().put("quote", c.quote)
            .put("resolutions", JSONArray(c.resolutions.map { JSONObject().put("phrase", it.phrase).put("meaning", it.meaning).put("evidence", it.evidence) }))
            .put("missing", JSONArray(c.missing)).put("ownerRole", c.ownerRole).put("clauseSpeaker", c.clauseSpeaker ?: NULL)
            .put("otherTasks", JSONArray(c.otherTasks)) } ?: NULL)
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
        is OopsEvent.Json -> e.type to e.payload
        is OopsEvent.Partial ->
            "asr.partial" to JSONObject().put("text", e.text).put("startMs", e.startMs).put("endMs", e.endMs)
        is OopsEvent.Final -> "asr.final" to utterance(e.utterance)
        is OopsEvent.SpeakerIdentified ->
            "asr.speaker" to JSONObject().put("id", e.utteranceId).put("speaker", e.speaker ?: NULL).put("score", e.score ?: NULL)
                .put("speakerState", e.speakerState)
                .put("speakerPreview", e.preview?.let(::preview) ?: NULL)
                .put("segments", segments(e.segments))
                .put("candidates", com.oops.app.backend.ApiJson.speakerCandidates(e.candidates))
                .put("trialSegments", segments(e.trialSegments))
        is OopsEvent.UtteranceTagged -> "asr.tag" to utterance(e.utterance)
        is OopsEvent.SessionSummary -> "session.summary" to summary(e)
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

    private fun preview(p: com.oops.app.buffer.SpeakerPreview): JSONObject = JSONObject()
        .put("experimental", p.experimental).put("jevUsed", p.jevUsed)
        .put("startMs", p.startMs).put("endMs", p.endMs)
        .put("segments", JSONArray(p.segments.map { s -> JSONObject().put("startMs", s.startMs)
            .put("endMs", s.endMs).put("text", s.text).put("speaker", s.speaker ?: NULL)
            .put("score", s.score).put("kind", s.kind).put("source", s.source ?: NULL).put("approx", s.approx) }))
}
