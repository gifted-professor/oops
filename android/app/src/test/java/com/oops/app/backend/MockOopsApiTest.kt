package com.oops.app.backend
import kotlinx.coroutines.test.runTest
import org.junit.Assert.*
import org.junit.Test
import java.time.LocalDate
class MockOopsApiTest {
 private val api=MockOopsApi(listOf("Jason"),{LocalDate.of(2026,10,6)},1)
 @Test fun noticeAmountRuleMatchesBackendAndAskAllowsNoMatch() = runTest {
   val request=org.json.JSONObject("""{"kind":"conflict","utterance":{"text":"方案大概要13万"},"previous":[{"id":"u1","text":"预算不能超过10万"}]}""")
   assertTrue(api.notice(request).getBoolean("conflict"))
   request.getJSONObject("utterance").put("text","如果方案要13万")
   assertFalse(api.notice(request).getBoolean("conflict"))
   val result=api.recall(RecallRequest("s","ask","明天天气如何？",10000,listOf(Candidate("u1",0,1000,null,"预算不能超过10万"))))
   assertTrue(result.picks.isEmpty())
 }
 @Test fun ideasAndPromisesRankBeforeChatAndCommands()=runTest { val c=listOf(Candidate("chat",0,1000,null,"今天天气不错"),Candidate("idea",1000,2000,null,"我觉得应该先做一个方案"),Candidate("promise",2000,3000,null,"我来负责明天发你"),Candidate("command",3000,4000,null,"Oops 把刚才的想法留下"));val p=api.recall(RecallRequest("s","button",null,5000,c)).picks;assertTrue(p.first().id in setOf("idea","promise"));assertEquals("command",p.last().id) }
 @Test fun datesResolveAgainstKnownTuesday() { val d=LocalDate.of(2026,10,6);mapOf("周五前" to "2026-10-09","下周一" to "2026-10-12","明天" to "2026-10-07","3号" to "2026-11-03").forEach { (raw,want)->assertEquals(want,MockOopsApi.resolveDate(raw,d).toString()) };assertNull(MockOopsApi.resolveDate("2月30日",d)) }
 @Test fun repeatedConfirmationHasOneTask()=runTest { val r=ConfirmRequest("op-1","s",fixtureDraft());val a=api.confirmAction(r);assertEquals(a,api.confirmAction(r));assertEquals(a,api.getAction("op-1"));assertEquals(ActionResult.FAILED,api.getAction("missing").status);assertTrue(a.mock) }
}
internal fun fixtureDraft()=ActionDraft("d1","c1","Jason","发方案","2026-10-09","周五前",null,listOf("Jason"),Evidence(listOf("u1"),"周五前发方案",100,200))
