package com.oops.app.buffer

import java.util.TreeMap

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
    data class Summary(val sessionId: String, val commitments: List<Utterance>, val audioIds: Set<String>)
    private val items = LinkedHashMap<String, Utterance>()
    private val timeline = TreeMap<Long, LinkedHashSet<String>>()
    private val commitmentTimeline = TreeMap<Long, LinkedHashSet<String>>()
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
        timeline.clear(); commitmentTimeline.clear()
        bytes = 0; discoveredCommitments = 0; truncated = false
    }

    @Synchronized fun add(session: String, u: Utterance): Boolean {
        if (session != sessionId) return false
        if (items.size >= maxUtterances && u.id !in items) {
            // Do not claim an evicted text entry is still available for opening.
            val oldest = items.keys.first()
            items.remove(oldest)?.let { removeTime(timeline, it); removeTime(commitmentTimeline, it) }
            pending.remove(oldest); counted.remove(oldest)
            clips.remove(oldest)?.let { bytes -= it.pcm.size }
            truncated = true
        }
        items[u.id]?.let { removeTime(timeline, it); removeTime(commitmentTimeline, it) }
        items[u.id] = u
        timeline.getOrPut(u.startMs) { linkedSetOf() }.add(u.id)
        if (u.tag == "commitment") commitmentTimeline.getOrPut(u.startMs) { linkedSetOf() }.add(u.id)
        return true
    }

    @Synchronized fun get(id: String): Utterance? = items[id]
    private fun removeTime(index: TreeMap<Long, LinkedHashSet<String>>, u: Utterance) {
        index[u.startMs]?.let { ids -> if (ids.remove(u.id) && ids.isEmpty()) index.remove(u.startMs) }
    }

    private fun ordered(index: TreeMap<Long, LinkedHashSet<String>>) = index.values.flatMap { ids -> ids.mapNotNull(items::get) }
    @Synchronized fun snapshot(): List<Utterance> = ordered(timeline)
    @Synchronized fun commitments(): List<Utterance> = ordered(commitmentTimeline)
    @Synchronized fun summary(): Summary {
        val commitments = commitments()
        return Summary(sessionId, commitments, commitments.filter { it.id in clips }.map { it.id }.toSet())
    }
    /** O(log n + count) previous context, excluding entries at the same timestamp. */
    @Synchronized fun preceding(id: String, count: Int = 4): List<Utterance> {
        require(count >= 0)
        val u = items[id] ?: return emptyList()
        return timeline.headMap(u.startMs, false).descendingMap().values.asSequence()
            .flatMap { it.toList().asReversed().asSequence() }.take(count).mapNotNull(items::get).toList().asReversed()
    }
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
        if (tag == "commitment") commitmentTimeline.getOrPut(u.startMs) { linkedSetOf() }.add(id)
        else removeTime(commitmentTimeline, u)
        return u.copy(tag = tag, tagProb = prob, tagEngine = engine).also { items[id] = it }
    }

    @Synchronized fun setSpeaker(session: String, id: String, speaker: String?, segments: List<SpeakerSegment> = emptyList(),
                                speakerState: String? = null, speakerScore: Double? = null,
                                speakerPreview: SpeakerPreview? = null): Utterance? {
        if (session != sessionId) return null
        val u = items[id] ?: return null
        return u.copy(speaker = speaker, segments = segments, speakerState = speakerState,
            speakerScore = speakerScore, speakerPreview = speakerPreview).also { items[id] = it }
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
