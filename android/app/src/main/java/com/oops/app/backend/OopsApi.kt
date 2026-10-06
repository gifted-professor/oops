package com.oops.app.backend

import com.oops.app.buffer.Utterance

/** App ↔ backend contract; the wire format is documented in docs/api.md. */
interface OopsApi {
    val name: String

    /** Evidence selection: the backend may only pick candidate ids (Jev, LLM fallback). */
    suspend fun recall(request: RecallRequest): RecallResponse

    /** LLM extraction of Who / What / When / Constraint. Only a draft — nothing is written. */
    suspend fun draftAction(request: DraftRequest): DraftResponse

    /** Writes to Feishu after the user confirmed. Must be idempotent per [ConfirmRequest.opId]. */
    suspend fun confirmAction(request: ConfirmRequest): ActionResult

    suspend fun getAction(opId: String): ActionResult
}

data class Candidate(
    val id: String,
    val startMs: Long,
    val endMs: Long,
    val speaker: String?,
    val text: String,
)

fun Utterance.toCandidate() = Candidate(id, startMs, endMs, speaker, text)

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
