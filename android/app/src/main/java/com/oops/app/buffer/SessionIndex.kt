package com.oops.app.buffer

/** Memory-only session evidence. Text outlives the ring; only commitment PCM is copied. */
class SessionIndex(
    private val maxUtterances: Int = 20_000,
    private val maxClips: Int = 50,
    private val maxAudioBytes: Int = 16 * 1024 * 1024,
) {
    init { require(maxUtterances > 0 && maxClips >= 0 && maxAudioBytes >= 0) }
    data class Audio(val startMs: Long, val pcm: ByteArray) {
        val endMs: Long get() = startMs + pcm.size / 32L // 16 kHz mono s16le
    }
    private val items = LinkedHashMap<String, Utterance>()
    private val clips = LinkedHashMap<String, Audio>()
    private val pending = mutableSetOf<String>()
    private var bytes = 0
    var sessionId: String = ""
        private set
    var truncated: Boolean = false
        private set
    var discoveredCommitments: Int = 0
        private set
    private val counted = mutableSetOf<String>()

    @Synchronized fun reset(session: String) {
        sessionId = session
        items.clear(); clips.clear(); pending.clear(); counted.clear()
        bytes = 0; discoveredCommitments = 0; truncated = false
    }

    @Synchronized fun add(session: String, u: Utterance): Boolean {
        if (session != sessionId) return false
        if (items.size >= maxUtterances && u.id !in items) {
            // Do not claim an evicted text entry is still available for opening.
            val oldest = items.keys.first()
            items.remove(oldest); pending.remove(oldest); counted.remove(oldest)
            clips.remove(oldest)?.let { bytes -= it.pcm.size }
            truncated = true
        }
        items[u.id] = u
        return true
    }

    @Synchronized fun get(id: String): Utterance? = items[id]
    @Synchronized fun snapshot(): List<Utterance> = items.values.sortedBy { it.startMs }
    @Synchronized fun commitments(): List<Utterance> = snapshot().filter { it.tag == "commitment" }
    @Synchronized fun pendingCount(): Int = pending.size
    @Synchronized fun beginAnalysis(session: String, id: String): Boolean =
        session == sessionId && items.containsKey(id) && pending.add(id)
    @Synchronized fun finishAnalysis(session: String, id: String) {
        if (session == sessionId) pending.remove(id)
    }

    @Synchronized fun setTag(session: String, id: String, tag: String, prob: Double?, engine: String): Utterance? {
        if (session != sessionId || tag !in ValueTags.ALL) return null
        val u = items[id] ?: return null
        if (tag == "commitment" && counted.add(id)) discoveredCommitments++
        return u.copy(tag = tag, tagProb = prob, tagEngine = engine).also { items[id] = it }
    }

    @Synchronized fun setSpeaker(session: String, id: String, speaker: String?, segments: List<SpeakerSegment> = emptyList()): Utterance? {
        if (session != sessionId) return null
        val u = items[id] ?: return null
        return u.copy(speaker = speaker, segments = segments).also { items[id] = it }
    }

    @Synchronized fun neighbors(ids: Collection<String>, before: Int = 4, after: Int = 1): List<Utterance> {
        val all = snapshot()
        val indices = all.indices.filter { all[it].id in ids }
        if (indices.isEmpty()) return emptyList()
        return all.subList((indices.min() - before).coerceAtLeast(0), (indices.max() + after + 1).coerceAtMost(all.size))
    }

    @Synchronized fun retainAudio(session: String, id: String, startMs: Long, pcm: ByteArray): Boolean {
        if (session != sessionId || items[id]?.tag != "commitment" || pcm.isEmpty() || pcm.size > maxAudioBytes) return false
        clips.remove(id)?.let { bytes -= it.pcm.size }
        while (clips.isNotEmpty() && (clips.size >= maxClips || bytes + pcm.size > maxAudioBytes)) {
            bytes -= clips.remove(clips.keys.first())!!.pcm.size
        }
        if (maxClips <= 0) return false
        clips[id] = Audio(startMs, pcm.copyOf())
        bytes += pcm.size
        return true
    }

    /** Full requested evidence must be covered; never silently join audio across gaps. */
    @Synchronized fun audio(startMs: Long, endMs: Long): Audio? {
        val candidates = clips.values.filter { it.startMs <= startMs && it.endMs >= endMs }
        val clip = candidates.minByOrNull { it.pcm.size } ?: return null
        val from = ((startMs - clip.startMs) * 32).toInt()
        val to = ((endMs - clip.startMs) * 32).toInt()
        if (from < 0 || to <= from || to > clip.pcm.size) return null
        return Audio(startMs, clip.pcm.copyOfRange(from, to))
    }

    @Synchronized fun hasAudio(id: String): Boolean = clips.containsKey(id)
}
