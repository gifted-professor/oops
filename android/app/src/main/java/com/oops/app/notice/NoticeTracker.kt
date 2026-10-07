package com.oops.app.notice

import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

data class Notice(val id: String = "n" + UUID.randomUUID().toString(), val kind: String, val title: String,
    val detail: String = "", val atMs: Long, val evidenceIds: List<String> = emptyList(),
    val itemId: String? = null, val history: JSONObject? = null, var dismissed: Boolean = false, var useful: Boolean? = null) {
    fun bridge() = JSONObject().put("id", id).put("kind", kind).put("title", title).put("detail", detail)
        .put("atMs", atMs).put("evidenceIds", JSONArray(evidenceIds)).put("itemId", itemId ?: JSONObject.NULL)
        .put("history", history ?: JSONObject.NULL).put("dismissed", dismissed).put("useful", useful ?: JSONObject.NULL)
    fun wire() = JSONObject().put("id", id).put("kind", kind).put("title", title).put("detail", detail)
        .put("at_ms", atMs).put("evidence_ids", JSONArray(evidenceIds)).put("dismissed", dismissed).put("useful", useful ?: JSONObject.NULL)
}

/** Session-local pure rules. All time values are on the audio timeline. */
class NoticeTracker(@Volatile var agenda: Agenda = Agenda()) {
    private val fired = mutableSetOf<String>()
    private val coverage = mutableMapOf<String, Long>()
    private val resolved = mutableMapOf<String, Boolean>()
    private var lastAt = 0L
    private var offCount = 0
    private var offLast = Long.MIN_VALUE / 2
    var currentItemId: String? = null
        private set
    @Volatile var tracking: String = "waiting"
    private val notices = mutableListOf<Notice>()
    @Synchronized fun list() = notices.toList()
    @Synchronized fun state(now: Long): JSONObject {
        advance(now)
        return agenda.bridge(coverage, currentItemId, tracking)
    }
    private fun advance(now: Long) {
        currentItemId?.let { coverage[it] = (coverage[it] ?: 0) + (now - lastAt).coerceAtLeast(0) }
        lastAt = maxOf(lastAt, now)
    }
    @Synchronized fun update(value: Agenda, now: Long) {
        advance(now)
        val same = agenda.items.filter { old -> value.items.any { it.id == old.id && it.title == old.title } }.map { it.id }.toSet()
        coverage.keys.retainAll(same); resolved.keys.retainAll(same)
        if (currentItemId !in same) currentItemId = null
        agenda = value; offCount = 0; tracking = "waiting"
    }
    @Synchronized fun add(notice: Notice): Notice {
        notices.add(notice)
        return notice
    }
    private fun once(key: String, now: Long, title: String, detail: String = "", kind: String = "time"): Notice? =
        if (fired.add(key)) add(Notice(kind = kind, title = title, detail = detail, atMs = now)) else null
    @Synchronized fun onTick(now: Long): List<Notice> {
        advance(now)
        val out = mutableListOf<Notice>()
        agenda.plannedMs?.let { total ->
            if (total >= 600_000 && now >= total - 300_000) once("five", now, "会议还剩 5 分钟",
                agenda.items.filter { (coverage[it.id] ?: 0) == 0L }.joinToString("、") { it.title }.let { if (it.isEmpty()) "" else "尚未讨论：$it" })?.let(out::add)
            if (now >= total) once("end", now, "会议计划时间已到")?.let(out::add)
        }
        var cumulative = 0L
        for ((index, item) in agenda.items.withIndex()) {
            val duration = item.plannedMs ?: break // no assumed duration for an unplanned item
            cumulative += duration
            if (now >= cumulative) once("item:${item.id}:${item.title}", now, "「${item.title}」时间已到",
                (agenda.items.getOrNull(index + 1)?.let { "下一项「${it.title}」" } ?: "已到最后一项") +
                    if (currentItemId == item.id) " · 仍在讨论 ${item.title}" else "")?.let(out::add)
        }
        return out
    }
    @Synchronized fun onTopic(topic: String, prob: Double?, now: Long): Pair<List<Notice>, String?> {
        advance(now); tracking = "supported"
        if (topic == "off_topic" && prob != null && prob >= .6) {
            currentItemId = null
            offCount++
            if (offCount >= 2 && now - offLast >= 300_000) {
                offLast = now
                return listOf(add(Notice(kind = "topic", title = "可能偏离本场目标", detail = agenda.goal, atMs = now))) to null
            }
            return emptyList<Notice>() to null
        }
        offCount = 0
        val next = agenda.items.firstOrNull { it.id == topic }?.id ?: run { currentItemId = null; return emptyList<Notice>() to null }
        val old = currentItemId
        currentItemId = next
        return emptyList<Notice>() to old?.takeIf { it != next && (coverage[it] ?: 0) >= 120_000 }
    }
    @Synchronized fun resolutionCandidates(now: Long): List<String> {
        advance(now)
        return agenda.items.filter { (coverage[it.id] ?: 0) > 0 && resolved[it.id] != true }.map { it.id }
    }
    @Synchronized fun onResolution(itemId: String, value: Boolean?, prob: Double?, now: Long): Notice? {
        if (value == null || prob == null || prob < .6) return null
        resolved[itemId] = value
        val item = agenda.items.firstOrNull { it.id == itemId } ?: return null
        return if (!value) once("resolution:$itemId:${item.title}", now, "「${item.title}」可能尚未形成结论", kind = "resolution") else null
    }
    @Synchronized fun onStop(now: Long, missingOwner: Int, missingDeadline: Int): List<Notice> {
        advance(now)
        if (missingOwner + missingDeadline == 0) return emptyList()
        return listOfNotNull(once("stop", now, "待办信息待补齐", "$missingOwner 条缺负责人 · $missingDeadline 条缺截止时间", "summary"))
    }
    @Synchronized fun dismiss(id: String): Notice? = notices.find { it.id == id }?.also { it.dismissed = true }
    @Synchronized fun feedback(id: String, useful: Boolean): Notice? = notices.find { it.id == id }?.also { it.useful = useful }
}
