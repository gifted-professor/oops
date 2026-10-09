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
    /** A person's correction of one sentence; null when the server does not have that meeting (yet). */
    /** Projects in use, one project's page (meetings, confirmed decisions, tasks), and a meeting's project. */
    suspend fun projects(): org.json.JSONObject? = null
    suspend fun project(name: String): org.json.JSONObject? = null
    suspend fun setMeetingProject(sessionId: String, project: String): org.json.JSONObject? = null
    /** A person's name for a past meeting; an empty title returns to the automatic one. */
    suspend fun renameMeeting(sessionId: String, title: String): org.json.JSONObject? = null
    suspend fun reviewMeeting(request: org.json.JSONObject): org.json.JSONObject? = null
    /** One archived sentence as 16 kHz mono PCM, cut on the server; null when it has no recording. */
    suspend fun recordingClip(sessionId: String, startMs: Long, endMs: Long): ByteArray? = null
    /** Created Feishu tasks with their current Feishu state; null when unsupported. */
    suspend fun tasks(assignee: String?, status: String): org.json.JSONObject? = null

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

    /** Late trial split of a sentence whose [SpeakerResult.splitPending] was true; null when unsupported. */
    suspend fun speakerSplit(sessionId: String, utteranceId: String): SplitFetch? = null
}

/** `status` is done, pending (still computing; ask again) or unavailable (give up, keep the whole-sentence label). */
class SplitFetch(val status: String, val trialSegments: List<SpeakerSegment> = emptyList(),
                 val timingMs: Map<String, Long> = emptyMap()) {
    fun usableTrial(u: Utterance) = SpeakerResult(null, 0.0, trialSegments = trialSegments).usableTrial(u)
}

class SpeakerRequest(
    val sessionId: String, val utteranceId: String, val pcm: ByteArray,
    val text: String = "", val clipStartMs: Long = 0,
    val words: List<WordTime> = emptyList(), val context: List<Candidate> = emptyList(),
    val preview: Boolean = false, val clipEndMs: Long = clipStartMs + pcm.size / 32,
)

data class SpeakerResult(val speaker: String?, val score: Double, val segments: List<SpeakerSegment> = emptyList(),
    val segmentsValidated: Boolean = false, val preview: com.oops.app.buffer.SpeakerPreview? = null,
    val runnerUp: Double? = null,
    /** Whole-sentence top-2 voiceprint ranking: display-only suggestions, never identity or owner evidence. */
    val candidates: List<com.oops.app.buffer.SpeakerCandidate> = emptyList(),
    /** Unvalidated split shown as suggestions only; never segments, owner evidence or synced history. */
    val trialSegments: List<SpeakerSegment> = emptyList(),
    /** The server answered the whole sentence first; the trial split follows via [OopsApi.speakerSplit]. */
    val splitPending: Boolean = false,
    val timingMs: Map<String, Long> = emptyMap()) {
    /** Trial parts must still rebuild the exact sentence; otherwise nothing is shown. */
    fun usableTrial(u: Utterance): List<SpeakerSegment> {
        if (trialSegments.size < 2 || trialSegments.joinToString("") { it.text } != u.text) return emptyList()
        return trialSegments.map { s ->
            s.copy(speaker = namedSpeaker(s.speaker), tier = if (s.overlap || namedSpeaker(s.speaker) == null) "C" else "B",
                confidence = null, candidates = s.candidates.filter { namedSpeaker(it.name) != null && it.score.isFinite() }
                    .sortedByDescending { it.score }.distinctBy { it.name }.take(2))
        }
    }
    fun usableSegments(u: Utterance): List<SpeakerSegment> {
        if (segments.isEmpty()) return emptyList()
        fun unknown() = listOf(SpeakerSegment(u.startMs, u.endMs, u.text, null, 0.0, null,
            approx = true, overlap = segments.any { it.overlap }))
        // A local failure must not erase words; untrusted partition coordinates invalidate the whole partition.
        if (!segmentsValidated || segments.joinToString("") { it.text } != u.text ||
            segments.first().startMs != u.startMs || segments.last().endMs != u.endMs ||
            segments.any { it.startMs < u.startMs || it.endMs > u.endMs || it.endMs <= it.startMs } ||
            segments.zipWithNext().any { (a, b) -> a.endMs != b.startMs }) return unknown()
        return segments.map { s ->
            val candidates = s.candidates.filter { namedSpeaker(it.name) != null && it.score.isFinite() }
                .sortedByDescending { it.score }.distinctBy { it.name }.take(2)
            val valid = s.score.isFinite() && s.source in setOf(null, "voiceprint", "voiceprint+context") &&
                s.tier in setOf("A", "B", "C") &&
                (s.confidence == null || (s.confidence.isFinite() && s.confidence in 0.0..1.0)) &&
                (s.speaker == null || namedSpeaker(s.speaker) != null)
            if (!valid) s.copy(speaker = null, score = 0.0, source = null, confidence = null,
                tier = "C", approx = true, candidates = candidates)
            else {
                val uncertain = s.overlap || s.approx || s.speaker == null || s.source == null
                s.copy(tier = if (uncertain) "C" else if (s.tier == "A" && s.confidence == null) "B" else s.tier,
                    confidence = if (uncertain) null else s.confidence, candidates = candidates)
            }
        }
    }
}

/** ASR diarization ids are anonymous labels, never registered identities. */
fun namedSpeaker(value: String?): String? = value?.trim()?.takeIf {
    it.isNotEmpty() && !it.matches(Regex("(?:\\d+|speaker[_ -]?\\d+)", RegexOption.IGNORE_CASE))
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
    val suggestedOwner: String? = null,
    val ownerSource: String = "none",
    val ownerConfidence: Double? = null,
    val tier: String = "C",
    val reason: String = "负责人待确认",
    val ownerCandidates: List<com.oops.app.buffer.SpeakerCandidate> = emptyList(),
    val context: TaskContext? = null,
)

/** Server-verified context resolution of the task wording: display only, never confirmed data. */
data class TaskContext(val quote: String, val resolutions: List<TaskResolution>, val missing: List<String>,
                       val ownerRole: String, val clauseSpeaker: String?, val otherTasks: List<String>)
data class TaskResolution(val phrase: String, val meaning: String, val evidence: String)

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
    val suggestedOwner: String? = null,
    val ownerSource: String = "none",
    val ownerConfidence: Double? = null,
    val tier: String = "C",
    val reason: String = "负责人待确认",
    val ownerCandidates: List<com.oops.app.buffer.SpeakerCandidate> = emptyList(),
    val modelRevision: Long = 0,
    val reviewStatus: String = "unreviewed",
    val ownerSelectionSource: String = "model",
    val context: TaskContext? = null,
)

data class ConfirmRequest(val opId: String, val sessionId: String, val draft: ActionDraft) {
    // Kept outside data-class equality: old confirmed wire payloads must survive schema upgrades verbatim.
    private var wireSnapshot: String? = null
    @Synchronized internal fun frozenWire(): String? = wireSnapshot
    @Synchronized internal fun freezeWire(value: String) {
        require(wireSnapshot == null || wireSnapshot == value) { "确认快照不可变" }
        wireSnapshot = value
    }
}

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
