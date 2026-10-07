package com.oops.app.backend

import org.json.JSONObject

object NoticeJson {
    fun validate(j: JSONObject, request: JSONObject): JSONObject {
        fun prob(): Double? = if (j.isNull("prob")) null else j.getDouble("prob").also { require(it.isFinite() && it in 0.0..1.0) }
        prob()
        when (request.getString("kind")) {
            "topic" -> {
                val items = request.getJSONObject("agenda").getJSONArray("items")
                val ids = (0 until items.length()).map { items.getJSONObject(it).getString("id") }.toSet()
                require(j.getString("topic") in ids + setOf("off_topic", "unclear") + if (ids.isEmpty()) setOf("on_topic") else emptySet())
                j.optJSONObject("probabilities")?.let { probabilities ->
                    val allowed = ids + setOf("off_topic", "unclear") + if (ids.isEmpty()) setOf("on_topic") else emptySet()
                    require(probabilities.keys().asSequence().toSet() == allowed)
                    for (id in allowed) require(probabilities.getDouble(id).let { it.isFinite() && it in 0.0..1.0 })
                }
            }
            "resolution" -> require(j.isNull("resolved") || j.get("resolved") is Boolean)
            "conflict" -> {
                require(j.get("conflict") is Boolean)
                if (j.getBoolean("conflict")) {
                    val other = j.getJSONObject("with")
                    val id = other.getString("id")
                    val prev = request.getJSONArray("previous")
                    val sent = (0 until prev.length()).map { prev.getJSONObject(it).getString("id") }
                    if (id !in sent) {
                        require(id.matches(Regex("h:[A-Za-z0-9_-]{1,100}:[A-Za-z0-9_-]{1,100}")))
                        require(id == "h:${other.getString("session_id")}:${id.substringAfterLast(':')}")
                        java.time.Instant.parse(other.getString("started_at"))
                    }
                    require(other.getString("text").length in 1..2000 && other.getLong("start_ms") >= 0)
                    require(other.getLong("end_ms") > other.getLong("start_ms"))
                    if (!j.isNull("prob")) require(j.getDouble("prob") >= .6)
                }
            }
            else -> error("未知提醒类型")
        }
        return j
    }
}
