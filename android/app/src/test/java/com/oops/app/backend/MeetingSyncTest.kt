package com.oops.app.backend

import org.junit.Assert.*
import org.junit.Test

class MeetingSyncTest {
    private fun queue() = MeetingSync("s", "2026-10-07T00:00:00Z", "UTC")
    private fun u(n: Int, tag: String? = null) = Candidate("u$n", n * 1000L, n * 1000L + 500, null, "内容$n", tag)
    @Test fun ackPreservesUpdatesThatArriveInFlightAndExcludesAudioPreview() {
        val q = queue(); q.upsert(u(1))
        val batch = q.batch(0)!!
        val sent = batch.body.getJSONArray("utterances").getJSONObject(0)
        assertFalse(sent.has("pcm")); assertFalse(sent.has("speaker_preview")); assertFalse(sent.has("words"))
        q.upsert(u(1, "decision")); q.complete(batch, 100, true)
        assertEquals(1, q.pendingCount())
        assertNull(q.batch(100))
        val retry = q.batch(20100)!!
        assertEquals("decision", retry.body.getJSONArray("utterances").getJSONObject(0).getString("tag"))
        q.complete(retry, 20100, true); assertEquals(0, q.pendingCount())
    }
    @Test fun batchesBackOffDisableAndIsolateSessions() {
        val q = queue(); (1..501).forEach { q.upsert(u(it)) }
        val b = q.batch(0)!!; assertEquals(500, b.body.getJSONArray("utterances").length())
        assertNull(q.batch(0, true)); q.complete(b, 100, false)
        assertNull(q.batch(1000, true)); val retry = q.batch(2100, true)!!
        q.complete(retry, 2100, true); assertEquals(1, q.pendingCount())
        q.stop("2026-10-07T01:00:00Z"); val last = q.batch(2200, true)!!
        assertEquals("2026-10-07T01:00:00Z", last.body.getString("ended_at"))
        q.complete(last, 2200, false, false); assertTrue(q.unsupported); assertNull(q.batch(999999, true))
        val next = MeetingSync("next", "2026-10-07T01:00:00Z", "UTC")
        assertEquals(0, next.batch(0)!!.body.getJSONArray("utterances").length())
        assertTrue(runCatching { next.complete(b, 0, true) }.isFailure)
    }
}
