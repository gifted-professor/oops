package com.oops.app.backend

import org.json.JSONArray
import org.json.JSONObject

/** One session and endpoint per queue. Ack removes only the exact revision sent. Text only. */
class MeetingSync(val sessionId: String, private val startedAt: String, private val timeZone: String) {
    data class Batch(val body: JSONObject, internal val utterances: Map<String, String>, internal val notices: Map<String, String>, internal val revision: Long)
    private val utterances = linkedMapOf<String, String>()
    private val notices = linkedMapOf<String, String>()
    private var agenda: String = "null"
    private var revision = 1L
    private var ackRevision = 0L
    private var endedAt: String? = null
    private var lastAttempt = -20_000L
    private var nextAttempt = 0L
    private var failures = 0
    private var sending = false
    /** The server refused a batch: sentences stop for good, but the meeting's end is still sent on its own. */
    var failed = false
        private set
    private var endAbandoned = false
    @Synchronized fun halt(endToo: Boolean = false) { sending = false; failed = true; if (endToo) endAbandoned = true }
    /** True while the end of the meeting has not reached the server; the bot needs it to send the minutes. */
    @Synchronized fun endPending() = endedAt != null && revision != ackRevision && !endAbandoned && !unsupported
    /** Nothing to send for now; stopping the meeting (which restarts the worker) may add its end. */
    @Synchronized fun finished() = unsupported || failed && !endPending()
    var unsupported = false
        private set
    @Synchronized fun upsert(candidate: Candidate) {
        // Explicit allowlist: production attribution only, never PCM, words or preview.
        val j = JSONObject().put("id", candidate.id).put("text", candidate.text)
            .put("start_ms", candidate.startMs).put("end_ms", candidate.endMs)
            .put("speaker", candidate.speaker ?: JSONObject.NULL).put("tag", candidate.tag ?: JSONObject.NULL)
            .put("segments", JSONArray(candidate.segments.map(ApiJson::segment)))
        utterances[candidate.id] = j.toString()
    }
    @Synchronized fun notice(j: JSONObject) { notices[j.getString("id")] = j.toString() }
    @Synchronized fun agenda(j: JSONObject) { val value = j.toString(); if (value != agenda) { agenda = value; revision++ } }
    /** The end goes out at once, not after a backoff left over from an earlier failure. */
    @Synchronized fun stop(at: String) { endedAt = at; revision++; nextAttempt = 0 }
    @Synchronized fun pendingCount() = utterances.size + notices.size + if (revision != ackRevision) 1 else 0
    @Synchronized fun batch(now: Long, force: Boolean = false): Batch? {
        if (sending || unsupported || pendingCount() == 0 || now < nextAttempt) return null
        if (failed) {
            if (!endPending()) return null
            sending = true; lastAttempt = now
            // End only, and no agenda: the server keeps the one it has.
            val body = JSONObject().put("session_id", sessionId).put("started_at", startedAt).put("time_zone", timeZone)
                .put("ended_at", endedAt).put("utterances", JSONArray()).put("notices", JSONArray())
            return Batch(body, emptyMap(), emptyMap(), revision)
        }
        if (!force && utterances.size < 30 && now - lastAttempt < 20_000) return null
        sending = true; lastAttempt = now
        val u = utterances.entries.take(500).associate { it.toPair() }
        val n = notices.entries.take(100).associate { it.toPair() }
        val body = JSONObject().put("session_id", sessionId).put("started_at", startedAt).put("time_zone", timeZone)
            .put("ended_at", endedAt ?: JSONObject.NULL).put("agenda", if (agenda == "null") JSONObject.NULL else JSONObject(agenda))
            .put("utterances", JSONArray(u.values.map(::JSONObject))).put("notices", JSONArray(n.values.map(::JSONObject)))
        return Batch(body, u, n, revision)
    }
    @Synchronized fun complete(batch: Batch, now: Long, success: Boolean, supported: Boolean = true) {
        require(batch.body.getString("session_id") == sessionId)
        sending = false
        if (!supported) { unsupported = true; return }
        if (success) {
            batch.utterances.forEach { (id, value) -> if (utterances[id] == value) utterances.remove(id) }
            batch.notices.forEach { (id, value) -> if (notices[id] == value) notices.remove(id) }
            ackRevision = batch.revision; failures = 0; nextAttempt = 0
        } else {
            failures++; nextAttempt = now + minOf(1_000L shl failures.coerceAtMost(8), 300_000L)
        }
    }
}
