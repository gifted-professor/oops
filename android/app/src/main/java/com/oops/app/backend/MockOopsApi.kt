package com.oops.app.backend

import com.oops.app.recall.OopsCommand
import kotlinx.coroutines.delay
import java.time.DayOfWeek
import java.time.LocalDate
import java.time.temporal.TemporalAdjusters
import java.util.concurrent.ConcurrentHashMap
import kotlin.math.exp

/**
 * In-app stand-in for the backend so the Android side can be built and demoed alone.
 * Rules only — the real service uses Jev for picks and an LLM for extraction.
 */
class MockOopsApi(
    private val team: List<String>,
    private val today: () -> LocalDate = { LocalDate.now() },
    private val latencyMs: Long = 300,
) : OopsApi {
    override val name = "mock"
    private val actions = ConcurrentHashMap<String, ActionResult>()

    override suspend fun recall(request: RecallRequest): RecallResponse {
        delay(latencyMs)
        if (request.candidates.isEmpty()) return RecallResponse(emptyList(), name)
        if (request.trigger in setOf("ask", "voice") && !request.query.isNullOrBlank()) {
            return RecallResponse(request.candidates.map { Pick(it.id, com.oops.app.recall.overlap(request.query, it.text)) }
                .filter { it.prob >= .15 }.sortedByDescending { it.prob }.take(3), name)
        }
        val scored = request.candidates.map { it.id to score(it, request) }
        val best = scored.maxOf { it.second }
        val weights = scored.map { (id, s) -> id to exp((s - best) / TEMPERATURE) }
        val total = weights.sumOf { it.second }
        val picks = weights.map { (id, w) -> Pick(id, w / total) }.sortedByDescending { it.prob }.take(5)
        return RecallResponse(picks, name)
    }

    override suspend fun notice(request: org.json.JSONObject): org.json.JSONObject {
        delay(latencyMs)
        val result = org.json.JSONObject().put("engine", "rules-mock").put("prob", org.json.JSONObject.NULL)
        when (request.getString("kind")) {
            "topic" -> result.put("topic", "unclear")
            "resolution" -> result.put("resolved", org.json.JSONObject.NULL)
            "conflict" -> {
                val current = request.getJSONObject("utterance").getString("text")
                val previous = request.getJSONArray("previous")
                val matched = (0 until previous.length()).map(previous::getJSONObject).firstOrNull {
                    ConflictRules.amountConflict(current, it.getString("text")) }
                result.put("conflict", matched != null).put("with", matched ?: org.json.JSONObject.NULL)
            }
        }
        return result
    }

    override suspend fun tagUtterance(request: TagRequest): TagResult {
        delay(latencyMs)
        return TagResult(request.utterance.id, CommitmentRules.tag(request.utterance.text, team), null, "rules-mock")
    }

    /** Recent, idea-like or commitment-like sentences score higher; the query boosts lexical overlap. */
    internal fun score(c: Candidate, request: RecallRequest): Double {
        var s = -(request.nowMs - c.endMs).coerceAtLeast(0) / 60_000.0
        if (IDEA.containsMatchIn(c.text)) s += 1.5
        if (COMMITMENT.containsMatchIn(c.text)) s += 1.2
        s += mapOf("commitment" to 1.2, "decision" to 1.2, "idea" to 1.0, "constraint" to .8)[c.tag] ?: 0.0
        if (c.text.length < 6) s -= 1.0
        if (OopsCommand.matches(c.text)) s -= 3.0 // the recall command itself is never the evidence
        request.query?.takeIf { it.isNotBlank() }?.let { s += 3.0 * overlap(it, c.text) }
        return s
    }

    override suspend fun draftAction(request: DraftRequest): DraftResponse {
        delay(latencyMs)
        val main = request.utterances.joinToString("") { it.text }
        val whenRaw = CommitmentRules.deadline(main)
        val constraint = (request.utterances + request.context)
            .firstNotNullOfOrNull { CONSTRAINT.find(it.text)?.value }
        // No self auto-owner until independent split/owner validation has passed.
        val who = CommitmentRules.evidence(main, team).owner
        return DraftResponse(
            who = who,
            what = main.trim().trimEnd('。', '.', '！', '!'),
            whenIso = whenRaw?.let { resolveDate(it, today())?.toString() },
            whenRaw = whenRaw,
            constraint = constraint,
            assigneeCandidates = team,
        )
    }

    override suspend fun confirmAction(request: ConfirmRequest): ActionResult {
        actions[request.opId]?.let { return it } // 同一 op_id 只建一次
        delay(latencyMs * 2)
        val guid = "mock-" + request.opId.take(8)
        val created = ActionResult(
            opId = request.opId,
            status = ActionResult.CREATED,
            taskGuid = guid,
            taskUrl = "https://applink.feishu.cn/client/todo/detail?guid=$guid",
            message = "Mock 后端：没有真正写入飞书",
            mock = true,
        )
        return actions.putIfAbsent(request.opId, created) ?: created
    }

    override suspend fun getAction(opId: String): ActionResult =
        actions[opId] ?: ActionResult(opId, ActionResult.FAILED, message = "没有这个操作编号")

    companion object {
        private const val TEMPERATURE = 0.6
        private val IDEA = Regex("想法|觉得|建议|应该|不如|要不|我认为|可以先|方案")
        private val COMMITMENT = Regex("下?周[一二三四五六日天]|明天|后天|今天|月底|我来|发你|给你|负责")
        private val CONSTRAINT = Regex("(预算|成本|费用)[^，。,.]{0,12}|(不能超过|不超过|控制在)[^，。,.]{0,10}")
        private val WEEKDAY = Regex("(下)?周([一二三四五六日天])")
        private val MONTH_DAY = Regex("(\\d{1,2})月(\\d{1,2})[日号]")
        private val DAY_OF_MONTH = Regex("(\\d{1,2})[日号]")
        fun overlap(query: String, text: String) = com.oops.app.recall.overlap(query, text)

        /** "周五前" / "下周一" / "明天" / "10月9日" … → a calendar date on or after [today]. */
        fun resolveDate(raw: String, today: LocalDate): LocalDate? {
            val text = raw.removeSuffix("之前").removeSuffix("前")
            WEEKDAY.matchEntire(text)?.let { m ->
                val target = DayOfWeek.of("一二三四五六日".indexOf(m.groupValues[2].replace("天", "日")) + 1)
                val from = if (m.groupValues[1] == "下") today.with(TemporalAdjusters.next(DayOfWeek.MONDAY)) else today
                return from.with(TemporalAdjusters.nextOrSame(target))
            }
            when (text) {
                "今天" -> return today
                "明天" -> return today.plusDays(1)
                "后天" -> return today.plusDays(2)
                "月底" -> return today.with(TemporalAdjusters.lastDayOfMonth())
            }
            MONTH_DAY.matchEntire(text)?.let { m ->
                val date = runCatching { LocalDate.of(today.year, m.groupValues[1].toInt(), m.groupValues[2].toInt()) }
                    .getOrNull() ?: return null
                return if (date.isBefore(today)) date.plusYears(1) else date
            }
            DAY_OF_MONTH.matchEntire(text)?.let { m ->
                val day = m.groupValues[1].toInt()
                val thisMonth = runCatching { today.withDayOfMonth(day) }.getOrNull()
                if (thisMonth != null && !thisMonth.isBefore(today)) return thisMonth
                return runCatching { today.plusMonths(1).withDayOfMonth(day) }.getOrNull()
            }
            return null
        }
    }
}
