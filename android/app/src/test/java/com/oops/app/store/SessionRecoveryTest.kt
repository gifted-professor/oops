package com.oops.app.store

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.time.Instant

class SessionRecoveryTest {
    @Test
    fun `a session the app never stopped is found once, its end estimated from the logs`() {
        val dir = Files.createTempDirectory("recover").toFile()
        val stopped = SessionRecorder(dir, "stopped", JSONObject().put("source", "mic"))
        stopped.closeAudio()
        File(dir, "killed.json").writeText(JSONObject().put("session_id", "killed").toString())
        File(dir, "killed.pcm").writeBytes(ByteArray(3200))
        File(dir, "killed.asr.jsonl").writeText("{\"at\":1791600000000}\nnot json\n{\"at\":1791600060000,\"payload\":\"x\"}\n")
        File(dir, "killed.timing.jsonl").writeText("{\"at\":1791600030000}\n")
        File(dir, "live.json").writeText(JSONObject().put("session_id", "live").toString())
        File(dir, "live.pcm").writeBytes(ByteArray(10))

        val ended = SessionRecovery.scan(dir, active = "live")
        val end = Instant.ofEpochMilli(1791600060000).toString()
        assertEquals(listOf("killed" to end), ended)
        val meta = JSONObject(File(dir, "killed.json").readText())
        assertEquals(end, meta.getString("audio_ended_at"))  // the recording upload carries it to the server too
        assertTrue(meta.getBoolean("recovered"))

        // Not sent yet (offline): found again. Sent: never again.
        assertEquals(listOf("killed" to end), SessionRecovery.scan(dir, active = null).filter { it.first == "killed" })
        SessionRecovery.markSent(dir, "killed")
        assertEquals(listOf("live"), SessionRecovery.scan(dir, active = null).map { it.first })

        val body = SessionRecovery.endBody("killed", end)
        assertEquals(end, body.getString("ended_at"))
        assertTrue(body.getBoolean("recovered"))
        assertFalse(body.has("agenda") || body.has("started_at"))
    }

    @Test
    fun `without logs the end is when the audio last grew`() {
        val dir = Files.createTempDirectory("recover2").toFile()
        File(dir, "s.json").writeText("{}")
        File(dir, "s.pcm").writeBytes(ByteArray(32)).also { File(dir, "s.pcm").setLastModified(1791600000000) }
        assertEquals(Instant.ofEpochMilli(1791600000000).toString(), SessionRecovery.estimateEnd(dir, "s"))
        assertEquals(null, SessionRecovery.estimateEnd(dir, "missing"))
    }

    @Test
    fun `a capture that never started leaves nothing to upload`() {
        val dir = Files.createTempDirectory("discard").toFile()
        val r = SessionRecorder(dir, "s", JSONObject())
        r.discard()
        r.flush()
        assertEquals(emptyList<String>(), dir.list().orEmpty().toList())
    }
}
