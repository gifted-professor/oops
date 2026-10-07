package com.oops.app.backend

import com.oops.app.buffer.SpeakerSegment
import com.oops.app.buffer.WordTime
import java.io.IOException
import kotlinx.coroutines.test.runTest
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class UtteranceAnalysisTest {
    private val team = listOf("洪玉麟", "林春源", "Jason")

    @Test fun rulesRejectDatesRecipientsNegationQuotesAndCompletedWork() {
        listOf("明天天气怎么样", "我来不了", "不要让林春源提交", "他说我来做", "我已经提交了", "我明天提交吗？")
            .forEach { assertEquals(it, "uncertain", CommitmentRules.tag(it, team)) }
        assertEquals("self", CommitmentRules.evidence("我明天把资料发给洪玉麟", team).kind)
        assertNull(CommitmentRules.evidence("我明天把资料发给洪玉麟", team).owner)
        assertEquals("洪玉麟", CommitmentRules.evidence("玉麟你周五前把方案发我", team).owner)
        assertEquals("林春源", CommitmentRules.evidence("让林春源下周一提交", team).owner)
        assertNull(CommitmentRules.evidence("你周五前发我", team).owner)
        assertEquals("commitment", CommitmentRules.tag("你周五前发我", team))
    }

    @Test fun requestsCarrySessionTimesFinalTextAndCompleteDraftEvidence() {
        val words = listOf(WordTime("我", 10100, 10200))
        val segment = SpeakerSegment(10100, 11100, "我来做", "洪玉麟", .8, "voiceprint", false)
        val candidate = Candidate("u1", 10100, 11100, "洪玉麟", "我来做", "commitment", words, listOf(segment))
        val speaker = ApiJson.speakerRequest(SpeakerRequest("s", "u1", byteArrayOf(1, 2), candidate.text, 10000, words))
        assertEquals(10000, speaker.getLong("clip_start_ms"))
        assertEquals(10100, speaker.getJSONArray("words").getJSONObject(0).getLong("start_ms"))
        assertEquals("我来做", speaker.getString("text"))
        val json = ApiJson.candidate(candidate)
        assertEquals("voiceprint", json.getJSONArray("segments").getJSONObject(0).getString("source"))
        assertEquals("commitment", json.getString("tag"))
        val response = ApiJson.speakerResult(JSONObject().put("speaker", "洪玉麟").put("score", .8)
            .put("segments", json.getJSONArray("segments")))
        assertEquals(listOf(segment), response.segments)
        val u = com.oops.app.buffer.Utterance(candidate.id, candidate.startMs, candidate.endMs, candidate.text, candidate.speaker)
        assertTrue(response.usableSegments(u).isEmpty()) // cannot enable an unevaluated backend response
        assertEquals(listOf(segment), response.copy(segmentsValidated = true).usableSegments(u))
        assertTrue(response.copy(segmentsValidated = true, segments = listOf(segment.copy(text = "改写原文"))).usableSegments(u).isEmpty())
        assertTrue(response.copy(segmentsValidated = true, segments = listOf(segment.copy(endMs = u.endMs + 1))).usableSegments(u).isEmpty())
        assertNull(ApiJson.tagResult(JSONObject("""{"utterance_id":"u1","tag":"uncertain","prob":null,"engine":"rules"}""")).prob)
        assertTrue(runCatching { ApiJson.tagResult(JSONObject("""{"utterance_id":"u1","tag":"bad","prob":1}""")) }.isFailure)
    }

    @Test fun optionalRetriesOnlyIoRateLimitAndServerFailures() = runTest {
        val policy = OptionalRequests()
        var calls = 0
        assertEquals("ok", policy.request("tag") { calls++; if (calls <= 2) throw IOException("offline"); "ok" })
        assertEquals(3, calls)
        calls = 0
        assertTrue(runCatching { policy.request("bad") { calls++; throw HttpFailure(400, "bad request") } }.isFailure)
        assertEquals(1, calls)
        calls = 0
        assertEquals("ok", policy.request("rate") { calls++; if (calls == 1) throw HttpFailure(429, "busy"); "ok" })
        assertEquals(2, calls)
    }

    @Test fun unsupportedEndpointIsDisabledOnlyInThatSession() = runTest {
        val old = OptionalRequests()
        var calls = 0
        assertNull(old.request<String>("tag") { calls++; null })
        assertNull(old.request<String>("tag") { calls++; "unexpected" })
        assertEquals(1, calls)
        assertEquals("new", OptionalRequests().request("tag") { "new" })
    }
}
