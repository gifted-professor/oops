package com.oops.app.asr
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
class VolcRequestTest {
 @Test fun speakerInfoIsOnlySentWhenEnabled() { val off=JSONObject(VolcRequest.json("s",false,emptyList())).getJSONObject("request");assertFalse(off.has("enable_speaker_info"));assertEquals("single",off.getString("result_type"));assertTrue(off.getBoolean("enable_nonstream"));val on=JSONObject(VolcRequest.json("s",true,emptyList())).getJSONObject("request");assertTrue(on.getBoolean("enable_speaker_info")) }
 @Test fun hotwordsContextIsJsonString() { val r=JSONObject(VolcRequest.json("s",false,listOf("Oops","洪玉麟")));val context=r.getJSONObject("request").getJSONObject("corpus").get("context");assertTrue(context is String);assertEquals("洪玉麟",JSONObject(context as String).getJSONArray("hotwords").getJSONObject(1).getString("word"));assertEquals(16000,r.getJSONObject("audio").getInt("rate")) }
}
