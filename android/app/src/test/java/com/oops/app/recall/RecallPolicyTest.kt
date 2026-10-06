package com.oops.app.recall
import com.oops.app.backend.Pick
import com.oops.app.buffer.Utterance
import org.junit.Assert.*
import org.junit.Test
class RecallPolicyTest {
 private fun u(id:String,start:Long,text:String="有效原话")=Utterance(id,start,start+1000,text,null)
 @Test fun windowsAndExcludedCommands() { val all=listOf(u("old",0),u("recent",200000),u("command",210000,"Oops"),u("short",220000,"嗯"));assertEquals(listOf("recent"),CandidateWindow.within(all,300000,120000,setOf("command")).map { it.id });assertEquals(listOf("old","recent"),CandidateWindow.within(all,300000,300000,setOf("command")).map { it.id }) }
 @Test fun leadGivesOneCardAndCloseScoresGiveChoices() { assertTrue(PickPolicy.decide(listOf(Pick("a",0.8),Pick("b",0.1)))!!.single);val d=PickPolicy.decide(listOf(Pick("a",0.4),Pick("b",0.35),Pick("c",0.2)))!!;assertFalse(d.single);assertEquals(3,d.picks.size) }
 @Test fun lowScoresHaveNoResult() { assertNull(PickPolicy.decide(listOf(Pick("a",0.05))));assertFalse(PickPolicy.isConfident(listOf(Pick("a",0.29)))) }
 @Test fun inventedHighConfidenceIdCannotStopExpansion() { val valid=PickPolicy.validated(listOf(Pick("invented",0.99),Pick("a",0.2)),setOf("a"));assertFalse(PickPolicy.isConfident(valid)) }
 @Test fun invalidAndDuplicateProbabilitiesAreRemoved() { val p=PickPolicy.validated(listOf(Pick("a",Double.NaN),Pick("a",1.1),Pick("a",0.2),Pick("a",0.5),Pick("b",0.9)),setOf("a","b")) { it=="a" };assertEquals(listOf(Pick("a",0.5)),p) }
 @Test fun droppedWakeWordDirectiveIsExcluded() { assertTrue(OopsCommand.matches("把刚刚那个想法留下来"));assertFalse(OopsCommand.matches("我觉得这个想法不错"));assertFalse(OopsCommand.containsWake("把刚刚那个想法留下来")) }
 @Test fun allWakeSpellingsAreDetected() { listOf("Oops","欧普斯","哦普斯","奥普斯").forEach { assertTrue(OopsCommand.matches(it)) };assertFalse(OopsCommand.matches("一个想法")) }
}
