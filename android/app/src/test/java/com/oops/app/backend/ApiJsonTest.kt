package com.oops.app.backend
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
class ApiJsonTest {
 @Test fun requestsUseSnakeCaseAndSessionMilliseconds() { val c=Candidate("u1",12,34,null,"原话");val j=ApiJson.recallRequest(RecallRequest("s","button",null,50,listOf(c)));assertEquals("s",j.getString("session_id"));assertEquals(12,j.getJSONArray("candidates").getJSONObject(0).getLong("start_ms"));assertFalse(j.has("sessionId"));assertTrue(j.isNull("query")) }
 @Test fun draftNullTimeCanBeParsed() { val d=ApiJson.draftResponse(JSONObject("""{"who":null,"what":"写方案","when":null,"constraint":null,"assignee_candidates":[]}"""));assertNull(d.whenIso);assertNull(d.who);assertEquals("写方案",d.what) }
 @Test fun draftCarriesDateReferenceAndTimezone() { val j=ApiJson.draftRequest(DraftRequest("s",emptyList(),emptyList(),"2026-10-06T10:00:00Z","Asia/Shanghai"));assertEquals("Asia/Shanghai",j.getString("time_zone"));assertEquals("2026-10-06T10:00:00Z",j.getString("reference_time")) }
 @Test fun confirmationContainsExactEvidenceAndId() { val j=ApiJson.confirmRequest(ConfirmRequest("op-1","s",fixtureDraft()));assertEquals("op-1",j.getString("op_id"));assertEquals("周五前发方案",j.getJSONObject("evidence").getString("quote"));assertTrue(j.getJSONObject("draft").isNull("constraint")) }
}
