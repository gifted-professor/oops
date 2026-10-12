package com.oops.app.backend

import com.oops.app.buffer.*
import com.oops.app.bridge.BridgeJson
import com.oops.app.store.ActionOperationStore
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.Rule
import org.junit.rules.TemporaryFolder

class SpeakerAttributionTest {
    @get:Rule val folder = TemporaryFolder()
    private val u = Utterance("u1", 0, 4000, "我来做好的", null)
    private val first = SpeakerSegment(0, 2000, "我来做", "洪玉麟", .8, "voiceprint", false, .98, "A", false,
        listOf(SpeakerCandidate("林春源", .4), SpeakerCandidate("洪玉麟", .8)))
    private val second = SpeakerSegment(2000, 4000, "好的", "林春源", .7, "voiceprint", false, .8, "B")

    @Test fun localFailureRetainsAllWordsAndOtherValidIdentities() {
        val result = SpeakerResult(null, .8, listOf(first, second.copy(source = "untrusted-model")), true)
        val segments = result.usableSegments(u)
        assertEquals(u.text, segments.joinToString("") { it.text })
        assertEquals("洪玉麟", segments[0].speaker)
        assertEquals("A", segments[0].tier)
        assertEquals(listOf("洪玉麟", "林春源"), segments[0].candidates.map { it.name })
        assertNull(segments[1].speaker); assertEquals("C", segments[1].tier)
        assertNull(segments[1].confidence)
    }

    @Test fun corruptPartitionBecomesOneUnknownWithOriginalTextAndCoordinates() {
        for (parts in listOf(listOf(first.copy(text = "改写"), second), listOf(first, second.copy(startMs = 2100)))) {
            val fallback = SpeakerResult(null, .8, parts, true).usableSegments(u).single()
            assertEquals(u.text, fallback.text); assertEquals(u.startMs, fallback.startMs); assertEquals(u.endMs, fallback.endMs)
            assertNull(fallback.speaker); assertEquals("C", fallback.tier); assertNull(fallback.confidence)
        }
    }

    @Test fun overlapApproxAndMissingCalibrationNeverBecomeA() {
        for (part in listOf(first.copy(overlap = true), first.copy(approx = true), first.copy(source = null))) {
            val normalized = SpeakerResult(null, .8, listOf(part, second), true).usableSegments(u).first()
            assertEquals("C", normalized.tier); assertNull(normalized.confidence)
        }
        for (tier in listOf("A", "B")) {
            val suggested = SpeakerResult(null, .8, listOf(first.copy(tier = tier, confidence = null), second), true).usableSegments(u).first()
            assertEquals("B", suggested.tier); assertEquals("洪玉麟", suggested.speaker); assertNull(suggested.confidence)
        }
        assertNull(namedSpeaker("0")); assertNull(namedSpeaker("speaker_2")); assertEquals("林春源", namedSpeaker("林春源"))
        assertNull(SpeakerResult(null, .8, listOf(first.copy(speaker = "1"), second), true).usableSegments(u).first().speaker)
    }

    @Test fun segmentMetadataSurvivesApiBridgeIndexAndMeetingQueue() {
        val candidate = u.copy(segments = listOf(first, second)).toCandidate()
        val wire = ApiJson.candidate(candidate)
        val response = ApiJson.speakerResult(JSONObject().put("speaker", JSONObject.NULL).put("score", .8)
            .put("runner_up", .4).put("segments_validated", true).put("segments", wire.getJSONArray("segments")))
        assertEquals(.4, response.runnerUp!!, .00001)
        val normalized = response.usableSegments(u)
        val index = SessionIndex(); index.reset("s"); index.add("s", u); index.setSpeaker("s", u.id, null, normalized)
        val stored = index.get(u.id)!!
        assertEquals(.98, BridgeJson.utterance(stored).getJSONArray("segments").getJSONObject(0).getDouble("confidence"), .00001)
        val q = MeetingSync("s", "2026-10-08T00:00:00Z", "UTC"); q.upsert(stored.toCandidate())
        val synced = q.batch(0)!!.body.getJSONArray("utterances").getJSONObject(0)
        assertEquals("A", synced.getJSONArray("segments").getJSONObject(0).getString("tier"))
        assertFalse(synced.has("words")); assertFalse(synced.has("speaker_preview"))
    }

    @Test fun selfBAndUncalibratedLegacyRemainSuggestionsWhileHumanChoiceIsFrozen() {
        val response = ApiJson.draftResponse(JSONObject("""{"owner":"洪玉麟","who":"洪玉麟","suggested_owner":"洪玉麟","owner_source":"self","owner_confidence":0.8,"tier":"B","reason":"本人待核对","owner_candidates":[{"name":"洪玉麟","score":0.8}],"what":"写方案","assignee_candidates":[]}"""))
        assertNull(response.who); assertNull(DraftPolicy.prefilledOwner(response)); assertEquals("洪玉麟", response.suggestedOwner)
        assertNull(DraftPolicy.prefilledOwner(response.copy(who = "洪玉麟", ownerSource = "named")))
        assertNull(DraftPolicy.prefilledOwner(response.copy(who = "洪玉麟", tier = "A", ownerConfidence = null)))
        val draft = fixtureDraft().copy(who = "", tier = "B", ownerSource = "self", ownerConfidence = .8, suggestedOwner = "洪玉麟")
        val confirmed = DraftPolicy.confirm(draft, "林春源", "人工改写", null, null, "限制", "user")
        assertEquals(.8, confirmed.ownerConfidence!!, .00001); assertEquals("B", confirmed.tier)
        assertEquals(confirmed, DraftPolicy.updateModel(confirmed, response.copy(tier = "A", ownerConfidence = .99), 100))
        val store = ActionOperationStore(folder.newFolder())
        val operation = ActionOperationStore.Operation(ConfirmRequest("op-1", "s", confirmed), "http://localhost", false, ActionResult("op-1", "pending"))
        store.save(operation); assertEquals(operation, store.list().single())
    }

    @Test fun oldConfirmedWireIsNotRewrittenOnRetryAfterUpgrade() {
        val directory = folder.newFolder()
        val legacy = JSONObject("""{"op_id":"op-old","session_id":"s","draft":{"who":"洪玉麟","what":"写方案","when":{"iso":null,"raw":null},"constraint":null},"evidence":{"utterance_ids":["u1"],"quote":"我来做","clip_start_ms":0,"clip_end_ms":2000}}""")
        val record = JSONObject().put("request", legacy).put("draft_id", "d1").put("card_id", "c1")
            .put("assignees", org.json.JSONArray(listOf("洪玉麟"))).put("backend_url", "http://localhost").put("mock", false)
            .put("result", JSONObject().put("op_id", "op-old").put("status", "failed"))
        java.io.File(directory, "op-old.json").writeText(record.toString())
        val store = ActionOperationStore(directory)
        val restored = store.list().single()
        assertEquals(legacy.toString(), ApiJson.confirmRequest(restored.request).toString())
        assertFalse(ApiJson.confirmRequest(restored.request).getJSONObject("draft").has("owner_source"))
        store.save(restored)
        assertEquals(legacy.toString(), ApiJson.confirmRequest(store.list().single().request).toString())
    }
}
