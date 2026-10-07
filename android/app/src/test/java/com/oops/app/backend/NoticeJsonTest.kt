package com.oops.app.backend

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class NoticeJsonTest {
    @Test fun topicRejectsInventedIdsAndInvalidProbabilities() {
        val request = JSONObject("""{"kind":"topic","agenda":{"items":[{"id":"a1"}]}}""")
        for ((id, p) in listOf("invented" to .9, "a1" to 1.1, "a1" to -.1)) {
            assertTrue(runCatching { NoticeJson.validate(JSONObject().put("topic", id).put("prob", p), request) }.isFailure)
        }
        assertEquals("a1", NoticeJson.validate(JSONObject().put("topic", "a1").put("prob", .8), request).getString("topic"))
    }
    @Test fun conflictRejectsUnsentLocalIdsAndLowConfidence() {
        val request = JSONObject("""{"kind":"conflict","previous":[]}""")
        val response = JSONObject("""{"conflict":true,"with":{"id":"invented","text":"预算","start_ms":0,"end_ms":1000},"prob":0.8}""")
        assertTrue(runCatching { NoticeJson.validate(response, request) }.isFailure)
    }
    @Test fun moneyRuleExcludesQuestionsHypothesesAndRevisedCaps() {
        val cap = "预算不能超过10万。"
        assertTrue(ConflictRules.amountConflict("方案要13万", cap))
        assertFalse(ConflictRules.amountConflict("方案要8万", cap))
        for (s in listOf("方案要13万？", "如果方案要13万", "预算不超过13万")) assertFalse(ConflictRules.amountConflict(s, cap))
    }
}
