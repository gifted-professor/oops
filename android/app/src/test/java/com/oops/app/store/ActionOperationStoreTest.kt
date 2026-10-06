package com.oops.app.store
import com.oops.app.backend.*
import org.junit.Assert.*
import org.junit.Test
import org.junit.Rule
import org.junit.rules.TemporaryFolder
class ActionOperationStoreTest {
 @get:Rule val folder=TemporaryFolder()
 @Test fun confirmedPayloadAndOperationSurviveRestart() { val dir=folder.newFolder();val store=ActionOperationStore(dir);val d=ActionDraft("d1","c1","Jason","发方案",null,null,null,listOf("Jason"),Evidence(listOf("u1"),"原话",0,100));val record=ActionOperationStore.Operation(ConfirmRequest("op-1","session",d),"http://localhost:8000",false,ActionResult("op-1","pending"));store.save(record);assertEquals(record,ActionOperationStore(dir).list().single());store.save(record.copy(result=ActionResult("op-1","created","guid",readBackConfirmed=true)));assertEquals(1,store.list().size);assertTrue(store.list().single().result.readBackConfirmed) }
 @Test fun corruptedRecordDoesNotHideGoodOnes() { val dir=folder.newFolder();java.io.File(dir,"broken.json").writeText("oops");assertTrue(ActionOperationStore(dir).list().isEmpty()) }
}
