package com.oops.app.backend
import kotlinx.coroutines.test.runTest
import org.junit.Assert.*
import org.junit.Test
import java.io.IOException
class ActionWorkflowTest {
 private val request=ConfirmRequest("op-1","s",fixtureDraft())
 private class Api(var result:ActionResult):OopsApi {
  override val name="test";var confirms=0;var reads=0;var failRead=false;var failWrite=false
  override suspend fun recall(request:RecallRequest)=error("unused")
  override suspend fun draftAction(request:DraftRequest)=error("unused")
  override suspend fun confirmAction(request:ConfirmRequest):ActionResult { confirms++;if(failWrite)throw IOException("response lost");return result }
  override suspend fun getAction(opId:String):ActionResult { reads++;if(failRead)throw IOException("offline");return result }
 }
 @Test fun pendingFinishesWithRecoverableStatus()=runTest { val api=Api(ActionResult("op-1","pending"));val r=ActionWorkflow.execute(api,request,true,2);assertEquals("pending",r.status);assertFalse(r.polling);assertEquals(1,api.confirms);assertEquals(2,api.reads) }
 @Test fun lostWriteResponseIsUnknownNotFailed()=runTest { val api=Api(ActionResult("op-1","created","task"));api.failWrite=true;assertEquals("pending",ActionWorkflow.execute(api,request,true).status) }
 @Test fun reconciliationNeverResubmits()=runTest { val api=Api(ActionResult("op-1","created","task"));val r=ActionWorkflow.execute(api,request,false);assertEquals(0,api.confirms);assertTrue(r.readBackConfirmed) }
 @Test fun readbackFailureDoesNotClaimVerification()=runTest { val api=Api(ActionResult("op-1","created","task"));api.failRead=true;val r=ActionWorkflow.execute(api,request,true);assertEquals("created",r.status);assertFalse(r.readBackConfirmed) }
 @Test fun wrongOperationOrUnknownStatusIsNotAccepted()=runTest { val api=Api(ActionResult("wrong","created","task"));assertEquals("pending",ActionWorkflow.execute(api,request,true).status);api.result=ActionResult("op-1","surprise");assertEquals("pending",ActionWorkflow.execute(api,request,true).status) }
 @Test fun retrySubmitsOnlyAfterDefiniteFailure() {
  assertTrue(ActionWorkflow.shouldSubmit(null))
  assertTrue(ActionWorkflow.shouldSubmit(ActionResult("op-1","failed")))
  assertFalse(ActionWorkflow.shouldSubmit(ActionResult("op-1","pending")))
  assertFalse(ActionWorkflow.shouldSubmit(ActionResult("op-1","created","task")))
 }
}
