package com.oops.app.backend

import com.oops.app.buffer.TranscriptBuffer
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.util.Base64

class SpeakerJsonTest {
    @Test
    fun `speaker request carries base64 pcm and the response tolerates an unknown speaker`() {
        val pcm = byteArrayOf(1, 0, -1, 127)
        val json = ApiJson.speakerRequest(SpeakerRequest("s1", "u7", pcm))
        assertEquals("u7", json.getString("utterance_id"))
        assertEquals(16000, json.getInt("sample_rate"))
        assertEquals(pcm.toList(), Base64.getDecoder().decode(json.getString("pcm_base64")).toList())

        assertEquals(SpeakerResult("黄丽萍", 0.81), ApiJson.speakerResult(JSONObject("""{"speaker":"黄丽萍","score":0.81}""")))
        assertNull(ApiJson.speakerResult(JSONObject("""{"speaker":null,"score":0.3}""")).speaker)
    }

    @Test
    fun `below-threshold ranking is parsed as display hints without becoming the speaker`() {
        val result = ApiJson.speakerResult(JSONObject("""{"speaker":null,"score":0.49,"runner_up":0.31,
            "candidates":[{"name":"林春源","score":0.49},{"name":"0","score":0.4},{"name":"洪玉麟","score":0.31}]}"""))
        assertNull(result.speaker)
        assertEquals(listOf("林春源", "洪玉麟"), result.candidates.map { it.name })
        val buffer = TranscriptBuffer(retentionMs = 60_000)
        val u = buffer.add(0, 2_000, "好的", null)
        val updated = buffer.setSpeaker(u.id, null, speakerState = "unconfirmed", speakerCandidates = result.candidates)
        assertNull(updated?.speaker)
        assertNull(updated?.toCandidate()?.speaker)
        assertEquals("林春源", updated?.speakerCandidates?.first()?.name)
    }

    @Test
    fun `trial split is shown only when it rebuilds the sentence and never becomes owner evidence`() {
        val buffer = TranscriptBuffer(retentionMs = 60_000)
        val u = buffer.add(0, 4_000, "我来做。好的。", null)
        val wire = """{"speaker":"林春源","score":0.7,"segments":[],"segments_validated":false,"trial_segments":[
            {"start_ms":0,"end_ms":2000,"text":"我来做。","speaker":"林春源","score":0.7,"tier":"A","confidence":0.99,"overlap":false},
            {"start_ms":2000,"end_ms":4000,"text":"好的。","speaker":null,"score":0.3,"tier":"C","overlap":true,
             "candidates":[{"name":"洪玉麟","score":0.4},{"name":"林春源","score":0.35}]}]}"""
        val result = ApiJson.speakerResult(JSONObject(wire))
        val trial = result.usableTrial(u)
        assertEquals(listOf("B", "C"), trial.map { it.tier })
        assertNull(trial.first().confidence)
        assertEquals(listOf("洪玉麟", "林春源"), trial.last().candidates.map { it.name })
        assertEquals(emptyList<Any>(), result.usableSegments(u))
        val updated = buffer.setSpeaker(u.id, "林春源", trialSegments = trial)
        assertEquals(emptyList<Any>(), updated?.toCandidate()?.segments)
        val broken = ApiJson.speakerResult(JSONObject(wire.replace("好的。", "好")))
        assertEquals(emptyList<Any>(), broken.usableTrial(u))
    }

    @Test
    fun `whole sentence is not held for the split which is fetched afterwards`() {
        val request = ApiJson.speakerRequest(SpeakerRequest("s1", "u7", ByteArray(4)))
        assertEquals("deferred", request.getString("split"))
        val split = ApiJson.splitRequest("s1", "u7")
        assertEquals("s1", split.getString("session_id"))
        assertEquals("u7", split.getString("utterance_id"))
        assertEquals(2, split.length())

        val early = ApiJson.speakerResult(JSONObject("""{"speaker":"林春源","score":0.7,"split_pending":true,
            "timing_ms":{"identify_ms":420}}"""))
        assertEquals(true, early.splitPending)
        assertEquals(mapOf("identify_ms" to 420L), early.timingMs)
        assertEquals(emptyList<Any>(), early.trialSegments)
        // An older server answers synchronously: no flag, the split is already in the response.
        assertEquals(false, ApiJson.speakerResult(JSONObject("""{"speaker":null,"score":0.3}""")).splitPending)

        val buffer = TranscriptBuffer(retentionMs = 60_000)
        val u = buffer.add(0, 4_000, "我来做。好的。", null)
        val late = ApiJson.splitResult(JSONObject("""{"status":"done","timing_ms":{"split_ms":3900,"split_queue_ms":10},"trial_segments":[
            {"start_ms":0,"end_ms":2000,"text":"我来做。","speaker":"林春源","score":0.7,"tier":"B","overlap":false},
            {"start_ms":2000,"end_ms":4000,"text":"好的。","speaker":"洪玉麟","score":0.6,"tier":"B","overlap":false}]}"""))
        assertEquals("done", late.status)
        assertEquals(3900L, late.timingMs["split_ms"])
        assertEquals(listOf("林春源", "洪玉麟"), late.usableTrial(u).map { it.speaker })
        assertEquals(emptyList<Any>(), ApiJson.splitResult(JSONObject("""{"status":"pending","trial_segments":[]}""")).usableTrial(u))

        buffer.setSpeaker(u.id, "林春源", speakerState = "matched", speakerScore = 0.7)
        val withTrial = buffer.setTrial(u.id, late.usableTrial(u))!!
        assertEquals("林春源", withTrial.speaker) // the late split never rewrites the whole-sentence label
        assertEquals("matched", withTrial.speakerState)
        assertEquals(2, withTrial.trialSegments.size)
    }

    @Test
    fun `task context is parsed for display and a malformed one is dropped`() {
        val ok = ApiJson.draftResponse(JSONObject("""{"what":"把运营选题资料发给对方","tier":"B","suggested_owner":"林春源",
            "assignee_candidates":[],"task_context":{"quote":"那你把那个顺便发我吧","owner_role":"addressee",
            "clause_speaker":"洪玉麟","missing":[],"other_tasks":["先做 App"],
            "resolutions":[{"phrase":"那个","meaning":"运营选题资料","evidence":[{"id":"u53","quote":"你说运营那个"}]}]}}"""))
        assertEquals("运营选题资料", ok.context?.resolutions?.single()?.meaning)
        assertEquals("你说运营那个", ok.context?.resolutions?.single()?.evidence)
        assertEquals("洪玉麟", ok.context?.clauseSpeaker)
        assertNull(ok.who)
        val bad = ApiJson.draftResponse(JSONObject("""{"what":"x","assignee_candidates":[],"task_context":{"resolutions":[]}}"""))
        assertNull(bad.context)
    }

    @Test
    fun `a late speaker answer updates the sentence in place`() {
        val buffer = TranscriptBuffer(retentionMs = 60_000)
        val u = buffer.add(0, 2_000, "我周五前把方案发你", null)
        assertEquals("洪玉麟", buffer.setSpeaker(u.id, "洪玉麟")?.speaker)
        assertEquals("洪玉麟", buffer.get(u.id)?.speaker)
        assertNull(buffer.setSpeaker("missing", "洪玉麟"))
    }
}
