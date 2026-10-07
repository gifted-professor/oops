package com.oops.app.backend

import com.oops.app.buffer.TranscriptBuffer
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.util.Base64

class SpeakerJsonTest {
    @Test
    fun `speaker request carries base64 pcm and the response tolerates an unknown speaker`() {
        val pcm = byteArrayOf(1, 0, -1, 127)
        val json = ApiJson.speakerRequest(SpeakerRequest("s1", "u7", pcm))
        assertEquals("u7", json.getString("utterance_id"))
        assertEquals(16000, json.getInt("sample_rate"))
        assertEquals(pcm.toList(), Base64.getDecoder().decode(json.getString("pcm_base64")).toList())

        assertEquals(SpeakerResult("黄丽萍", 0.81), ApiJson.speakerResult(JSONObject("""{"speaker":"黄丽萍","score":0.81}""")))
        assertNull(ApiJson.speakerResult(JSONObject("""{"speaker":null,"score":0.3}""")).speaker)
    }

    @Test
    fun `a late speaker answer updates the sentence in place`() {
        val buffer = TranscriptBuffer(retentionMs = 60_000)
        val u = buffer.add(0, 2_000, "我周五前把方案发你", null)
        assertEquals("洪玉麟", buffer.setSpeaker(u.id, "洪玉麟")?.speaker)
        assertEquals("洪玉麟", buffer.get(u.id)?.speaker)
        assertNull(buffer.setSpeaker("missing", "洪玉麟"))
    }
}
