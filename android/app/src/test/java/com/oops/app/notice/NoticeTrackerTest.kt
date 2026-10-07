package com.oops.app.notice

import org.junit.Assert.*
import org.junit.Test

class NoticeTrackerTest {
    private fun tracker() = NoticeTracker(Agenda.parseLines("上线方案", "评审 10\n负责人 5", 20))
    @Test fun timeAlertsFireOnceAndShowRemainingAgenda() {
        val t = tracker()
        assertTrue(t.onTick(599999).isEmpty())
        assertEquals(1, t.onTick(600000).size)
        val five = t.onTick(900000)
        assertTrue(five.any { it.title == "会议还剩 5 分钟" && it.detail.contains("尚未讨论") })
        assertTrue(t.onTick(900000).isEmpty())
        assertEquals(1, t.onTick(1200000).size)
        assertTrue(t.onTick(1300000).isEmpty())
    }
    @Test fun topicRequiresConsecutiveConfidenceAndFiveMinuteCooldown() {
        val t = tracker()
        assertTrue(t.onTopic("off_topic", .8, 60000).first.isEmpty())
        t.onTopic("unclear", .9, 120000)
        assertTrue(t.onTopic("off_topic", .8, 180000).first.isEmpty())
        assertEquals(1, t.onTopic("off_topic", .8, 240000).first.size)
        assertTrue(t.onTopic("off_topic", .8, 300000).first.isEmpty())
        assertEquals(1, t.onTopic("off_topic", .8, 540000).first.size)
    }
    @Test fun unknownAndOffTopicDoNotAccumulateAgendaCoverage() {
        val t = tracker(); t.onTopic("a1", .9, 0)
        t.onTopic("off_topic", .9, 10000)
        assertNull(t.currentItemId)
        assertEquals(10000L, t.state(20000).getJSONArray("items").getJSONObject(0).getLong("coveredMs"))
    }
    @Test fun coverageSurvivesEditAndSwitchChecksResolution() {
        val t = tracker()
        t.onTopic("a1", .9, 0)
        assertEquals("a1", t.onTopic("a2", .9, 120000).second)
        t.update(Agenda.parseLines("新目标", "评审 15\n负责人 5", 30, 2), 180000)
        assertEquals(120000L, t.state(180000).getJSONArray("items").getJSONObject(0).getLong("coveredMs"))
        assertNull(t.onResolution("a1", null, null, 180000))
        assertNotNull(t.onResolution("a1", false, .9, 180000))
        assertNull(t.onResolution("a1", false, .9, 180000))
        assertEquals(1, t.onStop(180000, 2, 1).size)
        assertTrue(t.onStop(180000, 2, 1).isEmpty())
    }
}
