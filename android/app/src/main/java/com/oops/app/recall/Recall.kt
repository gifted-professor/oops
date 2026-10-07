package com.oops.app.recall

import com.oops.app.backend.Pick
import com.oops.app.buffer.Utterance

/** The wake word as ASR may transcribe it; "Oops" is a hotword, the rest are common mishearings. */
object OopsCommand {
    private val WAKE = Regex("(?i)oops|欧普斯|哦普斯|奥普斯")

    private val RECALL_DIRECTIVE = Regex("(?:^|[，。,.])(?:好的[，,]?)?把?(?:刚刚|刚才)(?:那个|这个|的)?(?:想法|原话|那句话|那句|承诺|方案)(?:留下来|记下来|保存下来|找回来)[。.!！]?$", RegexOption.IGNORE_CASE)
    data class Voice(val query: String?, val ask: Boolean)
    fun parseVoice(text: String): Voice? {
        if (!matches(text)) return null
        if (!containsWake(text)) return Voice(null, false)
        val query = WAKE.replace(text, "").trim(' ', '，', ',', '。', '.', '！', '!')
        return Voice(query.takeIf { it.isNotBlank() }, Regex("吗|什么|多少|谁|哪|怎么|为何|是否|[？?]").containsMatchIn(query))
    }
    fun containsWake(text: String) = WAKE.containsMatchIn(text)
    fun matches(text: String) = containsWake(text) || RECALL_DIRECTIVE.containsMatchIn(text)
}

/** Candidate windows for one recall: 先查最近 2 分钟，找不到再扩到 5 分钟（方案 3.3-2）。 */
object CandidateWindow {
    val DEFAULT_WINDOWS_MS = listOf(2 * 60_000L, 5 * 60_000L)
    private const val MIN_CHARS = 2

    fun within(
        all: List<Utterance>,
        nowMs: Long,
        windowMs: Long,
        exclude: Set<String> = emptySet(),
    ): List<Utterance> = all.filter {
        it.endMs >= nowMs - windowMs && it.startMs <= nowMs && it.id !in exclude &&
            it.text.count(Char::isLetterOrDigit) >= MIN_CHARS
    }
}

/** Turns backend probabilities into one card or 2–3 close candidates. */
object PickPolicy {
    private const val MIN_PROB = 0.1
    private const val LEAD_MARGIN = 0.2
    private const val MAX_CANDIDATES = 3

    /** Below this the window is widened once before giving up (方案: 找不到再扩). */
    const val CONFIDENT_PROB = 0.3

    data class Decision(val picks: List<Pick>, val single: Boolean)

    /** Reject invented ids, non-probabilities, expired audio and duplicate ids before thresholding. */
    fun validated(picks: List<Pick>, candidateIds: Set<String>, available: (String) -> Boolean = { true }): List<Pick> =
        picks.filter { it.id in candidateIds && it.prob.isFinite() && it.prob in 0.0..1.0 && available(it.id) }
            .groupBy { it.id }.values.map { group -> group.maxBy { it.prob } }

    fun decide(picks: List<Pick>): Decision? {
        val sorted = picks.filter { it.prob.isFinite() && it.prob in MIN_PROB..1.0 }.sortedByDescending { it.prob }
        if (sorted.isEmpty()) return null
        val single = sorted.size == 1 || sorted[0].prob - sorted[1].prob >= LEAD_MARGIN
        return Decision(if (single) sorted.take(1) else sorted.take(MAX_CANDIDATES), single)
    }

    fun isConfident(picks: List<Pick>) = (picks.maxOfOrNull { it.prob } ?: 0.0) >= CONFIDENT_PROB
}

data class CardItem(
    val utteranceId: String,
    val startMs: Long,
    val endMs: Long,
    val text: String,
    val speaker: String?,
    val prob: Double,
    val audioAvailable: Boolean = true,
)

/** A recall result shown to the user. Text always comes from the ASR original, never the backend. */
data class RecallCard(
    val id: String,
    val trigger: String,
    val query: String?,
    val nowMs: Long,
    val items: List<CardItem>,
    val single: Boolean,
    /** Neighbouring sentences so the UI can widen the quote by ±1 sentence without a round trip. */
    val context: List<Utterance>,
    val engine: String,
    val windowMs: Long,
    val latencyMs: Long,
) {
    /** Items plus context, de-duplicated and in time order. */
    fun pool(): List<Utterance> =
        (items.map { Utterance(it.utteranceId, it.startMs, it.endMs, it.text, it.speaker) } + context)
            .distinctBy { it.id }
            .sortedBy { it.startMs }
}
