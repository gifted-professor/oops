package com.oops.app.recall

import com.oops.app.buffer.Utterance
import org.junit.Assert.*
import org.junit.Test

class AskTest {
    @Test fun candidatesExcludeCommandsAndPrioritizeDigestBeforeTruncation() {
        val all = (1..200).map { Utterance("u$it", it * 1000L, it * 1000L + 900, "普通对话内容$it", null) }
        val tagged = all.first().copy(tag = "constraint")
        val pool = AskCandidates.select(listOf(tagged) + all.drop(1) + listOf(
            Utterance("wake", 201000, 202000, "Oops 预算多少？", null), Utterance("short", 203000, 204000, "好", null)), "预算", 210000)
        assertEquals(120, pool.size); assertTrue(pool.any { it.id == tagged.id })
        assertFalse(pool.any { it.id in setOf("wake", "short") }); assertEquals(pool.sortedBy { it.startMs }, pool)
        assertEquals(3, AskCandidates.select(all.take(3), "问题", 210000).size)
    }
    @Test fun wakeAndWholeSentenceCommandsRouteConservatively() {
        for (s in listOf("Oops，预算多少？", "欧普斯，负责人是谁", "哦普斯怎么做", "奥普斯什么时间")) assertTrue(OopsCommand.parseVoice(s)!!.ask)
        assertEquals("找回方案", OopsCommand.parseVoice("Oops，找回方案")!!.query)
        assertFalse(OopsCommand.parseVoice("Oops")!!.ask)
        assertNull(OopsCommand.parseVoice("把刚刚那个想法留下来")!!.query)
        assertNull(OopsCommand.parseVoice("预算多少？"))
        assertNull(OopsCommand.parseVoice("我们稍后把刚刚那个想法留下来再讨论"))
    }
}
