package com.oops.app.buffer

/** Display-only experiment: none of these fields enter evidence or task-owner resolution. */
data class SpeakerActivity(val startMs: Long, val endMs: Long, val kind: String,
    val speaker: String?, val score: Double)

data class PreviewSegment(val startMs: Long, val endMs: Long, val text: String,
    val speaker: String?, val score: Double, val kind: String, val source: String?, val approx: Boolean)

data class SpeakerPreview(val startMs: Long, val endMs: Long, val activities: List<SpeakerActivity>,
    val segments: List<PreviewSegment> = emptyList(), val experimental: Boolean = true,
    val jevUsed: Boolean = false) {
    fun validFor(from: Long, to: Long, names: Set<String>): Boolean = experimental && startMs == from &&
        endMs == to && to > from && activities.size <= 2000 && segments.size <= 200 &&
        activities.all { it.startMs >= from && it.endMs <= to && it.endMs > it.startMs &&
            it.kind in setOf("speech", "silence", "overlap") && it.score.isFinite() &&
            (it.speaker == null || it.speaker in names && it.kind == "speech") } &&
        activities.zipWithNext().all { (a, b) -> a.endMs <= b.startMs }

    fun forUtterance(u: Utterance, names: Set<String>): SpeakerPreview? {
        if (!validFor(u.startMs, u.endMs, names) || segments.isEmpty() ||
            segments.joinToString("") { it.text } != u.text || segments.first().startMs != u.startMs ||
            segments.last().endMs != u.endMs || segments.zipWithNext().any { (a, b) -> a.endMs != b.startMs } ||
            segments.any { it.endMs <= it.startMs || it.startMs < u.startMs || it.endMs > u.endMs ||
                !it.score.isFinite() || it.kind !in setOf("speech", "overlap", "unknown") ||
                it.source !in setOf(null, "voiceprint", "voiceprint+context") ||
                it.speaker != null && (it.speaker !in names || it.kind != "speech") }) return null
        return this
    }
}
