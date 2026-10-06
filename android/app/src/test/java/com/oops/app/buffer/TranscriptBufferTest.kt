package com.oops.app.buffer
import org.junit.Assert.*
import org.junit.Test
class TranscriptBufferTest {
 @Test fun lateFinalsAreSorted() { val b=TranscriptBuffer(100);b.add(30,40,"三",null);b.add(10,20,"一",null);b.add(20,30,"二",null);assertEquals(listOf("一","二","三"),b.snapshot().map { it.text }) }
 @Test fun pruneRetainsBoundaryEnd() { val b=TranscriptBuffer(100);val a=b.add(0,9,"旧",null);val keep=b.add(0,10,"边界",null);assertEquals(listOf(a),b.prune(110));assertEquals(listOf(keep),b.snapshot()) }
 @Test fun neighborsClipAtBothEnds() { val b=TranscriptBuffer(100);val a=b.add(0,1,"一",null);val c=b.add(1,2,"二",null);b.add(2,3,"三",null);assertEquals(2,b.neighbors(listOf(a.id),10,1).size);assertEquals(3,b.neighbors(listOf(c.id),1,10).size);assertTrue(b.neighbors(listOf("missing"),1,1).isEmpty()) }
 @Test fun idsDoNotRepeatAcrossSessionReset() { val b=TranscriptBuffer(100);val a=b.add(0,1,"一",null);b.clear();assertNotEquals(a.id,b.add(0,1,"二",null).id) }
}
