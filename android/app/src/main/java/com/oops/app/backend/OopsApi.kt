package com.oops.app.backend

import com.oops.app.buffer.Utterance
import com.oops.app.buffer.WordTime
import com.oops.app.buffer.SpeakerSegment

/** App ↔ backend contract; the wire format is documented in docs/api.md. */
interface OopsApi {
    val name: String
    suspend fun notice(request: org.json.JSONObject): org.json.JSONObject? = null
    suspend fun syncMeeting(request: org.json.JSONObject): org.json.JSONObject? = null
    suspend fun historyList(): org.json.JSONObject? = null
    suspend fun historyOpen(id: String): org.json.JSONObject? = null
    suspend fun historyDelete(id: String): org.json.JSONObject? = null
    suspend fun historySearch(query: String): org.json.JSONObject? = null

    /** Evidence selection: the backend may only pick candidate ids (Jev, LLM fallback). */
    suspend fun recall(request: RecallRequest): RecallResponse

    /** LLM extraction of Who / What / When / Constraint. Only a draft — nothing is written. */
    suspend fun draftAction(request: DraftRequest): DraftResponse

    /** Writes to Feishu after the user confirmed. Must be idempotent per [ConfirmRequest.opId]. */
    suspend fun confirmAction(request: ConfirmRequest): ActionResult

    suspend fun getAction(opId: String): ActionResult

    /** Voiceprint lookup for one sentence's audio (16 kHz mono s16le); null when the backend can't tell. */
    suspend fun identifySpeaker(request: SpeakerRequest): SpeakerResult? = null
    suspend fun tagUtterance(request: TagRequest): TagResult? = null
    suspend fun previewSpeakers(request: SpeakerRequest): com.oops.app.buffer.SpeakerPreview? = null
}

class SpeakerRequest(
    val sessionId: String, val utteranceId: String, val pcm: ByteArray,
    val text: String = "", val clipStartMs: Long = 0,
    val words: List<WordTime> = emptyList(), val context: List<Candidate> = emptyList(),
    val preview: Boolean = false, val clipEndMs: Long = clipStartMs + pcm.size / 32,
)

data class SpeakerResult(val speaker: String?, val score: Double, val segments: List<SpeakerSegment> = emptyList(),
    val segmentsValidated: Boolean = false, val preview: com.oops.app.buffer.SpeakerPreview? = null) {
    fun usableSegments(u: Utterance): List<SpeakerSegment> {
        if (!segmentsValidated || segments.isEmpty() || segments.joinToString("") { it.text } != u.text) return emptyList()
        if (segments.first().startMs != u.startMs || segments.last().endMs != u.endMs) return emptyList()
        if (segments.any { it.startMs < u.startMs || it.endMs > u.endMs || it.endMs <= it.startMs ||
                !it.score.isFinite() || it.source !in setOf(null, "voiceprint", "voiceprint+context") }) return emptyList()
        if (segments.zipWithNext().any { (a, b) -> a.endMs != b.startMs }) return emptyList()
        return segments
    }
}

data class TagRequest(val sessionId: String, val utterance: Candidate, val context: List<Candidate> = emptyList())
data class TagResult(val utteranceId: String, val tag: String, val prob: Double?, val engine: String)

data class Candidate(
    val id: String,
    val startMs: Long,
    val endMs: Long,
    val speaker: String?,
    val text: String,
    val tag: String? = null,
    val words: List<WordTime> = emptyList(),
    val segments: List<SpeakerSegment> = emptyList(),
)

fun Utterance.toCandidate() = Candidate(id, startMs, endMs, speaker, text, tag, words, segments)

data class RecallRequest(
    val sessionId: String,
    val trigger: String,
    val query: String?,
    val nowMs: Long,
    val candidates: List<Candidate>,
)

data class Pick(val id: String, val prob: Double)

data class RecallResponse(val picks: List<Pick>, val engine: String)

data class DraftRequest(
    val sessionId: String,
    val utterances: List<Candidate>,
    val context: List<Candidate>,
    val referenceTime: String = java.time.Instant.now().toString(),
    val timeZone: String = java.time.ZoneId.systemDefault().id,
)

data class DraftResponse(
    val who: String?,
    val what: String,
    val whenIso: String?,
    val whenRaw: String?,
    val constraint: String?,
    val assigneeCandidates: List<String>,
)

data class Evidence(
    val utteranceIds: List<String>,
    val quote: String,
    val clipStartMs: Long,
    val clipEndMs: Long,
)

data class ActionDraft(
    val draftId: String,
    val cardId: String,
    val who: String,
    val what: String,
    val whenIso: String?,
    val whenRaw: String?,
    val constraint: String?,
    val assigneeCandidates: List<String>,
    val evidence: Evidence,
)

data class ConfirmRequest(val opId: String, val sessionId: String, val draft: ActionDraft)

data class ActionResult(
    val opId: String,
    val status: String,
    val taskGuid: String? = null,
    val taskUrl: String? = null,
    val message: String? = null,
    /** Local verification flags; never sent in the confirm request. */
    val readBackConfirmed: Boolean = false,
    val polling: Boolean = false,
    val mock: Boolean = false,
) {
    companion object {
        const val PENDING = "pending"
        const val CREATED = "created"
        const val FAILED = "failed"
    }
}
