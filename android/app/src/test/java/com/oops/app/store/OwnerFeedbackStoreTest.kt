package com.oops.app.store

import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class OwnerFeedbackStoreTest {
    @get:Rule val folder = TemporaryFolder()
    private fun record(store: OwnerFeedbackStore, previous: String?, selected: String?) = store.record("s1", "d1", listOf("u1"),
        previous, selected, "洪玉麟", "B", null, 2)

    @Test fun durableSessionCountDeduplicatesSelectEventsAndKeepsUnverifiedEvidence() {
        val directory = folder.newFolder()
        val store = OwnerFeedbackStore(directory) { "2026-10-08T12:00:00Z" }
        assertTrue(record(store, null, "林春源")); assertFalse(record(store, null, "林春源"))
        val restarted = OwnerFeedbackStore(directory)
        assertEquals(1, restarted.count("s1")); assertEquals(0, restarted.count("s2"))
        val data = restarted.snapshot("s1")
        assertEquals("unverified_owner_feedback", data.getString("purpose"))
        val event = data.getJSONArray("events").getJSONObject(0)
        assertEquals("洪玉麟", event.getString("model_suggestion")); assertEquals("B", event.getString("model_tier"))
        assertTrue(event.isNull("model_confidence")); assertFalse(event.getBoolean("confirmed"))
        assertFalse(event.has("audio")); assertFalse(event.has("token")); assertFalse(event.has("text"))
        assertTrue(record(restarted, "林春源", "洪玉麟")); assertEquals(2, restarted.count("s1"))
    }

    @Test fun confirmationSurvivesRestartAndDoesNotPromoteProbabilityOrTier() {
        val directory = folder.newFolder(); val store = OwnerFeedbackStore(directory)
        record(store, null, "林春源"); store.confirm("s1", "d1", "林春源")
        val event = OwnerFeedbackStore(directory).snapshot("s1").getJSONArray("events").getJSONObject(0)
        assertTrue(event.getBoolean("confirmed")); assertFalse(event.isNull("confirmed_at"))
        assertEquals("B", event.getString("model_tier")); assertTrue(event.isNull("model_confidence"))
        assertEquals(1, store.count("s1"))
        val confirmedAt = event.getString("confirmed_at")
        OwnerFeedbackStore(directory) { "2099-01-01T00:00:00Z" }.confirm("s1", "d1", "林春源")
        assertEquals(confirmedAt, store.snapshot("s1").getJSONArray("events").getJSONObject(0).getString("confirmed_at"))
        // Native confirmation and asynchronously queued feedback can arrive in either order.
        store.confirm("s2", "d2", "洪玉麟")
        store.record("s2", "d2", listOf("u2"), null, "洪玉麟", "洪玉麟", "A", .98, 3)
        assertTrue(store.snapshot("s2").getJSONArray("events").getJSONObject(0).getBoolean("confirmed"))
        assertEquals(.98, store.snapshot("s2").getJSONArray("events").getJSONObject(0).getDouble("model_confidence"), .00001)
    }
}
