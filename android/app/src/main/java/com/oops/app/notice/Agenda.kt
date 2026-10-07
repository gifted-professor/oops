package com.oops.app.notice

import org.json.JSONArray
import org.json.JSONObject

data class AgendaItem(val id: String, val title: String, val plannedMs: Long? = null)
data class Agenda(val goal: String = "", val items: List<AgendaItem> = emptyList(), val plannedMs: Long? = null, val revision: Long = 0) {
    init {
        require(goal.length <= 500 && items.size <= 20 && items.map { it.id }.distinct().size == items.size)
        require(items.all { it.title.isNotBlank() && it.title.length <= 60 && it.id.matches(Regex("[A-Za-z0-9_-]{1,100}")) })
        require((items.map { it.plannedMs } + plannedMs).all { it == null || it in 60_000L..480 * 60_000L })
    }
    fun wire() = JSONObject().put("goal", goal).put("planned_ms", plannedMs ?: JSONObject.NULL)
        .put("items", JSONArray(items.map { JSONObject().put("id", it.id).put("title", it.title)
            .put("planned_ms", it.plannedMs ?: JSONObject.NULL) }))
    fun bridge(covered: Map<String, Long>, current: String?, tracking: String) = JSONObject()
        .put("goal", goal).put("plannedMs", plannedMs ?: JSONObject.NULL).put("revision", revision)
        .put("currentItemId", current ?: JSONObject.NULL).put("tracking", tracking)
        .put("items", JSONArray(items.map { JSONObject().put("id", it.id).put("title", it.title)
            .put("plannedMs", it.plannedMs ?: JSONObject.NULL).put("coveredMs", covered[it.id] ?: 0) }))
    companion object {
        fun parseLines(goal: String, lines: String, minutes: Int? = null, revision: Long = 0): Agenda {
            val pattern = Regex("^(.*?)(?:[（(]\\s*(\\d+)\\s*分钟\\s*[）)]|\\s+(\\d+))\\s*$")
            val items = lines.lineSequence().map(String::trim).filter(String::isNotBlank).mapIndexed { n, line ->
                val m = pattern.matchEntire(line)
                val duration = m?.let { (it.groupValues[2].ifBlank { it.groupValues[3] }).toInt() }
                AgendaItem("a${n + 1}", m?.groupValues?.get(1)?.trim() ?: line, duration?.times(60_000L))
            }.toList()
            return Agenda(goal.trim(), items, minutes?.times(60_000L), revision)
        }
        fun fromBridge(j: JSONObject, revision: Long): Agenda {
            val arr = j.optJSONArray("items") ?: JSONArray()
            require(arr.length() <= 20)
            val items = (0 until arr.length()).map { n ->
                val i = arr.getJSONObject(n)
                AgendaItem(i.optString("id").ifBlank { "a${n + 1}" }, i.getString("title").trim(), if (i.isNull("minutes")) null else i.getInt("minutes") * 60_000L)
            }
            return Agenda(j.optString("goal").trim(), items, if (j.isNull("minutes")) null else j.getInt("minutes") * 60_000L, revision)
        }
    }
}
