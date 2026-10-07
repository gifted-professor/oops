package com.oops.app.buffer

/** One definite (two-pass) ASR sentence, timed on the session timeline. Text is the ASR original. */
data class Utterance(
    val id: String,
    val startMs: Long,
    val endMs: Long,
    val text: String,
    val speaker: String?,
)

/**
 * Evidence index for recall: only definite sentences go in, and anything that ended more
 * than [retentionMs] ago is dropped together with its audio.
 */
class TranscriptBuffer(private val retentionMs: Long) {
    private val items = ArrayList<Utterance>()
    private var nextId = 1L

    @Synchronized
    fun add(startMs: Long, endMs: Long, text: String, speaker: String?): Utterance {
        val utterance = Utterance("u${nextId++}", startMs, maxOf(startMs, endMs), text, speaker)
        // Finals normally arrive in order; after an ASR reconnect they may interleave.
        val index = items.indexOfLast { it.startMs <= utterance.startMs } + 1
        items.add(index, utterance)
        return utterance
    }

    @Synchronized
    fun get(id: String): Utterance? = items.firstOrNull { it.id == id }

    /** Fills in who said it once the voiceprint lookup returns; null if the sentence already expired. */
    @Synchronized
    fun setSpeaker(id: String, speaker: String): Utterance? {
        val index = items.indexOfFirst { it.id == id }
        if (index < 0) return null
        return items[index].copy(speaker = speaker).also { items[index] = it }
    }

    @Synchronized
    fun snapshot(): List<Utterance> = items.toList()

    /** Removes utterances that ended before `nowMs - retention`; returns what was removed. */
    @Synchronized
    fun prune(nowMs: Long): List<Utterance> {
        val cutoff = nowMs - retentionMs
        val removed = items.filter { it.endMs < cutoff }
        if (removed.isNotEmpty()) items.removeAll(removed.toSet())
        return removed
    }

    /** The given utterances plus up to [before] / [after] neighbours, in time order. */
    @Synchronized
    fun neighbors(ids: Collection<String>, before: Int, after: Int): List<Utterance> {
        val indices = ids.mapNotNull { id -> items.indexOfFirst { it.id == id }.takeIf { it >= 0 } }
        if (indices.isEmpty()) return emptyList()
        val from = maxOf(0, indices.min() - before)
        val to = minOf(items.size - 1, indices.max() + after)
        return items.subList(from, to + 1).toList()
    }

    @Synchronized
    fun clear() {
        items.clear()
    }
}
