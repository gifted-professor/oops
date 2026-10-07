package com.oops.app.backend

import com.oops.app.buffer.*
import com.oops.app.core.Settings
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class SpeakerPreviewTest {
    private val names = Settings.TEAM.toSet()
    private val u = Utterance("u1", 0, 4000, "你好。好的。", null)
    private val p = SpeakerPreview(0, 4000, listOf(SpeakerActivity(0, 2000, "speech", "林春源", .8),
        SpeakerActivity(2000, 4000, "overlap", null, 0.0)), listOf(
        PreviewSegment(0, 2000, "你好。", "林春源", .8, "speech", "voiceprint", false),
        PreviewSegment(2000, 4000, "好的。", null, 0.0, "overlap", null, false)))

    @Test fun previewDoesNotBecomeCertifiedSegmentsOrTaskEvidence() {
        assertEquals(p, p.forUtterance(u, names))
        assertTrue(SpeakerResult(null, .3, preview = p).usableSegments(u).isEmpty())
        val json = ApiJson.candidate(u.copy(speakerPreview = p).toCandidate())
        assertFalse(json.has("preview")); assertFalse(json.has("speakerPreview"))
        assertEquals(0, json.getJSONArray("segments").length())
    }

    @Test fun malformedTimeTextIdentityAndOverlapAreRejected() {
        assertNull(p.copy(experimental = false).forUtterance(u, names))
        assertNull(p.copy(startMs = 1).forUtterance(u, names))
        assertNull(p.copy(segments = p.segments.map { it.copy(text = "改过的文字") }).forUtterance(u, names))
        assertNull(p.copy(activities = listOf(SpeakerActivity(0, 4000, "overlap", "林春源", .9))).forUtterance(u, names))
        assertNull(p.copy(activities = listOf(SpeakerActivity(0, 4000, "speech", "未注册的人", .9))).forUtterance(u, names))
        assertNull(p.copy(activities = listOf(SpeakerActivity(0, 4000, "speech", null, Double.NaN))).forUtterance(u, names))
        assertNull(p.copy(segments = p.segments.map { it.copy(endMs = 6000) }).forUtterance(u, names))
    }

    @Test fun jsonRequiresExplicitExperimentAndOverlapHasNoIdentity() {
        val json = JSONObject("""{"start_ms":0,"end_ms":4000,"experimental":true,
          "activities":[{"start_ms":0,"end_ms":4000,"kind":"overlap","speaker":null,"score":0}]}""")
        val parsed = ApiJson.speakerPreview(json)
        assertTrue(parsed.validFor(0, 4000, names)); assertNull(parsed.activities.single().speaker)
        json.remove("experimental")
        assertFalse(ApiJson.speakerPreview(json).validFor(0, 4000, names))
    }

    @Test fun previewSurvivesTagUpdatesWithoutFillingOwner() {
        val buffer = TranscriptBuffer(300000)
        val line = buffer.add(0, 4000, u.text, null)
        buffer.setSpeaker(line.id, null, speakerPreview = p)
        buffer.setTag(line.id, "commitment", .9, "test")
        assertEquals(p, buffer.get(line.id)!!.speakerPreview); assertNull(buffer.get(line.id)!!.speaker)
    }
}
