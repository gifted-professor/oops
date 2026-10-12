package com.oops.app.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TextCleanTest {
    @Test
    fun `only sentences made of interjections and punctuation are fillers`() {
        listOf("嗯", "嗯。", "呃，", "哈哈哈", "嗯嗯，啊。", "  ", "诶…").forEach { assertTrue(it, TextClean.isFiller(it)) }
        listOf("对", "好的。", "嗯，对。", "178", "呃，我去香港", "是是是").forEach { assertFalse(it, TextClean.isFiller(it)) }
    }

    @Test
    fun `a phone number read in groups becomes one number`() {
        assertEquals("手机号，来。呃，17818554702。", TextClean.display("手机号，来。呃，178。嗯。1855。嗯。4702。"))
        assertEquals("17818554702", TextClean.display("178 1855 4702"))
        assertEquals("我的号码是13912345678，记一下", TextClean.display("我的号码是139，1234，5678，记一下"))
    }

    @Test
    fun `a number spoken in Chinese digits is joined too`() {
        assertEquals("18118554702", TextClean.display("幺八幺。嗯。一八五五。嗯。四七零二"))
        assertEquals("号码是17818554702，对吧", TextClean.display("号码是178，幺八五五，4702，对吧"))
        assertEquals("18118554702", TextClean.display("幺八幺一八五五四七零二"))
    }

    @Test
    fun `numbers that are not one mobile number are left alone`() {
        listOf("100。200。300", "2026年。10月", "二零二六年。十月", "一二三四五六七八九零一二", "幺八幺。一八五五", "报价 1234。5678", "178。1855。47021", "21855。4702。178", "只说了178。")
            .forEach { assertEquals(it, TextClean.display(it)) }
    }
}
