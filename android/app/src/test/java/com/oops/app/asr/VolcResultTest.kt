package com.oops.app.asr
import org.junit.Assert.*
import org.junit.Test
class VolcResultTest {
 @Test fun wordsUseOmittedZeroAndShiftAcrossReconnects() {
  val u=VolcResult.parse("""{"result":{"utterances":[{"end_time":1000,"text":"我来做。","definite":true,"words":[{"text":"我","end_time":100},{"text":"来做","start_time":100,"end_time":1000}]}]}}""").finals.single().shift(120000)
  assertEquals(120000,u.words.first().startMs);assertEquals(121000,u.words.last().endMs)
  assertEquals(u.startMs,u.words.first().startMs);assertEquals(u.endMs,u.words.last().endMs)
 }
 @Test fun omittedZeroAndSpeakerAreParsed() { val u=VolcResult.parse("""{"result":{"utterances":[{"end_time":2100,"text":"你好。","definite":true,"additions":{"speaker_id":"0"}}]}}""").finals.single();assertEquals(0,u.startMs);assertEquals(2100,u.endMs);assertEquals("0",u.speakerId) }
 @Test fun partialAndFinalAreSeparatedAndEmptySkipped() { val r=VolcResult.parse("""{"result":{"utterances":[{"text":"定稿","definite":true,"end_time":500},{"text":"进行中","end_time":800},{"text":"  ","definite":true}]}}""");assertEquals(1,r.finals.size);assertEquals("进行中",r.partial!!.text) }
 @Test fun emptyResponsesProduceNoSpeech() { assertTrue(VolcResult.parse("{}").finals.isEmpty());assertNull(VolcResult.parse("").partial) }
 @Test fun shiftedTimestampsPreserveText() { val u=RawUtterance(0,100,"句子",true,null).shift(250);assertEquals(250,u.startMs);assertEquals(350,u.endMs) }
}
