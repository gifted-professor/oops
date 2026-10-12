package com.oops.app.buffer

import com.oops.app.backend.toCandidate
import org.junit.Assert.*
import org.junit.Test

class SessionIndexTest {
    @Test fun digestTagMovesAndDecisionAudioSurvivesRing() {
        val index = SessionIndex(); index.reset("s")
        index.add("s", Utterance("u1", 0, 1000, "决定上线", null))
        index.setTag("s", "u1", "decision", .9, "jev")
        assertTrue(index.retainAudio("s", "u1", 0, ByteArray(32000)))
        assertEquals(listOf("u1"), index.summary().grouped["decision"]!!.map { it.id })
        index.setTag("s", "u1", "constraint", .9, "jev")
        assertTrue(index.summary().grouped["decision"]!!.isEmpty())
        assertTrue(index.hasAudio("u1"))
        index.setTag("s", "u1", "idea", .9, "jev")
        assertFalse(index.hasAudio("u1")); assertTrue(index.summary().grouped["constraint"]!!.isEmpty())
    }
    @Test fun lateTagKeepsTrialSplitButDraftsNeverSeeIt() {
        val index = SessionIndex(); index.reset("s")
        index.add("s", Utterance("u1", 0, 2000, "我来。好的。", null))
        val trial = listOf(SpeakerSegment(0, 1000, "我来。", "林春源", .6, "voiceprint", false, tier = "B"),
            SpeakerSegment(1000, 2000, "好的。", "洪玉麟", .6, "voiceprint", false, tier = "B"))
        index.setSpeaker("s", "u1", "林春源", trialSegments = trial)
        val tagged = requireNotNull(index.setTag("s", "u1", "commitment", .9, "jev"))
        assertEquals(trial, tagged.trialSegments)
        assertTrue(tagged.toCandidate().segments.isEmpty())
    }
    @Test fun manualSpeakerSurvivesLateModelResultsAndIsNeverSentAsTheModelSpeaker() {
        val index = SessionIndex(); index.reset("s")
        index.add("s", Utterance("u1", 0, 2000, "我来做。", null))
        assertNull(index.setManual("other", "u1", "洪玉麟"))
        index.setManual("s", "u1", "洪玉麟")
        index.setSpeaker("s", "u1", "林春源", speakerState = "matched")
        val tagged = requireNotNull(index.setTag("s", "u1", "commitment", .9, "jev"))
        assertEquals("洪玉麟", tagged.manualSpeaker)
        assertEquals("林春源", tagged.toCandidate().speaker) // the server keeps corrections apart from the model's answer
        assertNull(index.setManual("s", "u1", null)!!.manualSpeaker)
        val buffer = TranscriptBuffer(300_000)
        val line = buffer.add(0, 1000, "好的", null)
        buffer.setManual(line.id, "multi")
        assertEquals("multi", buffer.setSpeaker(line.id, "林春源")!!.manualSpeaker)
    }
    @Test fun chronologicalIndexHandlesLateResultsUpdatesAndEviction() {
        val index = SessionIndex(maxUtterances = 4)
        index.reset("s")
        for (i in listOf(4, 1, 3, 2)) index.add("s", Utterance("u$i", i * 1000L, i * 1000L + 500, "句$i", null))
        assertEquals(listOf("u1", "u2", "u3", "u4"), index.snapshot().map { it.id })
        assertEquals(listOf("u2", "u3"), index.preceding("u4", 2).map { it.id })
        for (i in listOf(4, 1)) index.setTag("s", "u$i", "commitment", null, "rules")
        assertEquals(listOf("u1", "u4"), index.commitments().map { it.id })
        index.setSpeaker("s", "u1", "洪玉麟")
        assertEquals("洪玉麟", index.summary().commitments.first().speaker)
        index.add("s", Utterance("u5", 5000, 5500, "句5", null)) // evicts first-arriving u4
        assertNull(index.get("u4"))
        assertEquals(listOf("u1"), index.commitments().map { it.id })
        index.add("s", requireNotNull(index.get("u3")).copy(startMs = 900, endMs = 950))
        assertEquals(listOf("u3", "u1", "u2", "u5"), index.snapshot().map { it.id })
        assertEquals(listOf("u3"), index.preceding("u1").map { it.id })
        index.setTag("s", "u1", "uncertain", null, "rules")
        assertTrue(index.commitments().isEmpty())
    }
    @Test fun commitmentOutlivesRingAndDraftUsesLatestSpeaker() {
        val index = SessionIndex()
        index.reset("s")
        val original = Utterance("u1", 1000, 2000, "我明天提交", null, listOf(WordTime("我", 1000, 1100)))
        index.add("s", original)
        index.beginAnalysis("s", "u1")
        index.setTag("s", "u1", "commitment", .9, "jev")
        index.retainAudio("s", "u1", 500, ByteArray(2000 * 32) { 1 })
        val ring = AudioRingBuffer(5 * 60 * 16000)
        ring.appendSilence(6 * 60 * 16000)
        assertNull(ring.read(16000, 32000))
        assertEquals(32000, index.audio(1000, 2000)!!.pcm.size)
        val segment = SpeakerSegment(1000, 2000, original.text, "洪玉麟", .8, "voiceprint")
        index.setSpeaker("s", "u1", "洪玉麟", listOf(segment))
        index.finishAnalysis("s", "u1")
        assertEquals(listOf(segment), index.get("u1")!!.segments)
        assertEquals(original.words, index.get("u1")!!.words)
        assertEquals(0, index.pendingCount())
        assertEquals(1, index.discoveredCommitments)
        index.setTag("s", "u1", "commitment", .9, "jev")
        assertEquals(1, index.discoveredCommitments)
    }

    @Test fun stoppedSessionAcceptsResultButNewSessionRejectsOldResults() {
        val index = SessionIndex()
        index.reset("old")
        index.add("old", Utterance("u1", 0, 1000, "我来做", null))
        index.beginAnalysis("old", "u1")
        // Stopping capture does not reset the index.
        assertNotNull(index.setTag("old", "u1", "commitment", null, "rules"))
        index.reset("new")
        index.add("new", Utterance("u1", 0, 1000, "新的会话", null))
        index.beginAnalysis("new", "u1")
        assertNull(index.setTag("old", "u1", "commitment", null, "rules"))
        assertNull(index.setSpeaker("old", "u1", "洪玉麟"))
        index.finishAnalysis("old", "u1")
        assertEquals(1, index.pendingCount())
        assertNull(index.get("u1")!!.tag)
    }

    @Test fun boundedAudioEvictsPcmButKeepsTextAndRejectsWidenedGap() {
        val index = SessionIndex(maxClips = 1, maxAudioBytes = 64000)
        index.reset("s")
        for (i in 1..2) {
            index.add("s", Utterance("u$i", i * 1000L, (i + 1) * 1000L, "我来做", null))
            index.setTag("s", "u$i", "commitment", null, "rules")
            assertTrue(index.retainAudio("s", "u$i", i * 1000L, ByteArray(32000)))
        }
        assertNotNull(index.get("u1"))
        assertFalse(index.hasAudio("u1"))
        assertTrue(index.hasAudio("u2"))
        assertNull(index.audio(1000, 3000))
        assertEquals(2, index.commitments().size)
    }
}
