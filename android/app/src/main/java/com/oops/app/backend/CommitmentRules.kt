package com.oops.app.backend

/** Conservative offline fallback; keep cases synchronized with local_backend.py. */
object CommitmentRules {
    data class Evidence(val kind: String, val owner: String? = null)
    private const val ACTION = "(?:发给|发你|给你|负责|整理|提交|跟进|完成|交付|准备|发送|处理|联系|确认|更新|写|做|发|交)"
    private val deadlineBefore = Regex("(下?周[一二三四五六日天]|明天|后天|今天|月底|\\d{1,2}月\\d{1,2}[日号]|\\d{1,2}[日号])之前")
    private val self = Regex("我(?:来|会|负责|[^，。！？,!?；;]{0,12})$ACTION")
    private val you = Regex("你(?:来|[^，。！？,!?；;]{0,12}(?:前|把))[^，。！？,!?；;]{0,8}$ACTION")
    private val excluded = Regex("[？?]|不是|不要|不用|不必|不能|不想|不会|不负责|来不了|没法|无法|没(?:有)?(?:做|写|交|发)|" +
        "已经|已(?:经)?(?:做|写|交|发|完成|提交)|昨天|之前|上周|如果|假如|要是|觉得|认为|建议|" +
        "(?:他|她|他们|她们|玉麟|春源|古月|丽萍|Jason|[Ll]ydia).{0,4}(?:说|表示)|[“”\"「」『』]")
    private val aliases = mapOf("玉麟" to "洪玉麟", "春源" to "林春源", "古月" to "江古月", "丽萍" to "黄丽萍", "Lydia" to "黄丽萍")
    private val constraint = Regex("(预算|成本|费用)[^，。,.]{0,12}|(不能超过|不超过|控制在)[^，。,.]{0,10}")

    private val whenPattern = Regex("下?周[一二三四五六日天](之?前)?|明天|后天|今天|月底|\\d{1,2}月\\d{1,2}[日号](之?前)?|\\d{1,2}[日号](之?前)?")
    fun deadline(text: String): String? = whenPattern.find(text)?.value

    fun evidence(text: String, team: List<String>): Evidence {
        if (excluded.containsMatchIn(deadlineBefore.replace(text) { it.groupValues[1] + "前" })) return Evidence("unknown")
        val names = (team.associateWith { it } + aliases.filterValues { it in team })
        val owners = names.mapNotNull { (alias, name) ->
            val escaped = Regex.escape(alias)
            val prefix = "(?:让|请|麻烦|交给|由)\\s*$escaped[^，。！？,!?；;]{0,12}$ACTION"
            val subject = "$escaped\\s*(?:[，,：:]\\s*)?(?:你|来|负责|[^，。！？,!?；;]{0,8}(?:前|把))[^，。！？,!?；;]{0,12}$ACTION"
            if (Regex("$prefix|$subject", RegexOption.IGNORE_CASE).findAll(text).any { match ->
                !Regex("(?:给|找|跟|和|告诉|问)\\s*$").containsMatchIn(text.substring((match.range.first - 3).coerceAtLeast(0), match.range.first))
            }) name else null
        }.toSet()
        val own = self.containsMatchIn(text)
        if (owners.isNotEmpty() && own || owners.size > 1) return Evidence("unknown")
        if (owners.size == 1) return Evidence("named", owners.first())
        if (own) return Evidence("self")
        return Evidence(if (you.containsMatchIn(text)) "assigned_unknown" else "unknown")
    }

    fun tag(text: String, team: List<String>): String = when {
        evidence(text, team).kind != "unknown" -> "commitment"
        constraint.containsMatchIn(text) -> "constraint"
        else -> "uncertain"
    }
}
