package com.oops.app.buffer

import org.junit.Assert.*
import org.junit.Test

class SessionIndexTest {
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
