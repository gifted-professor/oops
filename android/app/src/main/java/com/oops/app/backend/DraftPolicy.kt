package com.oops.app.backend

/** Model suggestions and human confirmation are independent state transitions. */
object DraftPolicy {
    fun prefilledOwner(response: DraftResponse): String? = namedSpeaker(response.who)?.takeIf {
        response.ownerConfidence?.let { p -> p.isFinite() && p in 0.0..1.0 } == true &&
            response.tier == "A" && response.ownerSource in setOf("named", "self")
    }

    fun updateModel(draft: ActionDraft, response: DraftResponse, revision: Long): ActionDraft {
        if (draft.reviewStatus != "unreviewed" || revision <= draft.modelRevision) return draft
        return draft.copy(who = prefilledOwner(response).orEmpty(), what = response.what,
            whenIso = response.whenIso, whenRaw = response.whenRaw, constraint = response.constraint,
            assigneeCandidates = response.assigneeCandidates.ifEmpty { draft.assigneeCandidates },
            suggestedOwner = response.suggestedOwner ?: response.who, ownerSource = response.ownerSource,
            ownerConfidence = response.ownerConfidence, tier = response.tier, reason = response.reason,
            ownerCandidates = response.ownerCandidates, modelRevision = revision,
            context = response.context ?: draft.context)
    }

    fun confirm(draft: ActionDraft, who: String, what: String, whenIso: String?, whenRaw: String?,
                constraint: String?, ownerSelectionSource: String): ActionDraft = draft.copy(
        who = who.trim(), what = what.trim(), whenIso = whenIso?.ifBlank { null },
        whenRaw = whenRaw?.ifBlank { null }, constraint = constraint?.ifBlank { null },
        reviewStatus = "confirmed", ownerSelectionSource = if (ownerSelectionSource == "user") "user" else "model")
}
