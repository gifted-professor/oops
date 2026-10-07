package com.oops.app.backend

/** Keep synchronized with meeting_features.amount_conflict; do not infer general contradictions. */
object ConflictRules {
    fun amountConflict(current: String, previous: String): Boolean {
        if (Regex("[?？]|如果|假如|要是|比如|能否|是否|不超过|不能超过|上限|控制在").containsMatchIn(current)) return false
        val cap = Regex("(?:预算|成本|费用).{0,8}(?:不超过|不能超过|上限(?:为)?|控制在)\\s*(\\d+(?:\\.\\d+)?)\\s*(万|元)").find(previous) ?: return false
        fun amount(n: String, unit: String) = n.toDouble() * if (unit == "万") 10000 else 1
        val values = Regex("(\\d+(?:\\.\\d+)?)\\s*(万|元)").findAll(current).map { amount(it.groupValues[1], it.groupValues[2]) }
        return values.any { it > amount(cap.groupValues[1], cap.groupValues[2]) }
    }
}
