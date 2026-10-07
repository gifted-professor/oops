package com.oops.app.notice

import org.junit.Assert.*
import org.junit.Test

class AgendaTest {
    @Test fun parseBothDurationFormsAndEnforceLimits() {
        val a = Agenda.parseLines("确认上线", "方案评审 10\n负责人确认（5分钟）\n其他", 30)
        assertEquals(listOf(600000L, 300000L, null), a.items.map { it.plannedMs })
        assertEquals(1800000L, a.plannedMs)
        for (line in listOf("一项 0", "一项 481", "字".repeat(61), (1..21).joinToString("\n") { "议程$it" })) {
            assertTrue(runCatching { Agenda.parseLines("", line) }.isFailure)
        }
    }
}
