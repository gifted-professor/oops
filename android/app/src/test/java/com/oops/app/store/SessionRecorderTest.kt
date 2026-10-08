package com.oops.app.store

import com.oops.app.buffer.Utterance
import com.oops.app.buffer.WordTime
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.File
import java.nio.file.Files

class SessionRecorderTest {
    @Test
    fun `audio and gaps stay on the session timeline and stop at the cap`() {
        val dir = Files.createTempDirectory("rec").toFile()
        val r = SessionRecorder(dir, "s1", JSONObject().put("source", "mic"), maxBytes = 64_000)
        r.audio(ByteArray(3200) { 1 })
        r.silence(1600)
        r.final(Utterance("u1", 0, 300, "好的", null, listOf(WordTime("好", 0, 150))))
        r.audio(ByteArray(64_000))
        r.closeAudio()
        assertEquals(3200L + 3200L, File(dir, "s1.pcm").length())
        val line = JSONObject(File(dir, "s1.finals.jsonl").readLines().single())
        assertEquals("好的", line.getString("text"))
        assertEquals(150, line.getJSONArray("words").getJSONObject(0).getInt("end_ms"))
        assertEquals("mic", JSONObject(File(dir, "s1.json").readText()).getString("source"))
    }

    @Test
    fun `prune drops expired then oldest sessions until the folder fits`() {
        val dir = Files.createTempDirectory("prune").toFile()
        fun session(id: String, bytes: Int, ageDays: Long) = listOf("pcm", "json").forEach {
            File(dir, "$id.$it").apply { writeBytes(ByteArray(if (it == "pcm") bytes else 10)); setLastModified(1_000_000_000_000L - ageDays * 86_400_000) }
        }
        session("old", 100, 20); session("mid", 300, 3); session("new", 300, 1); session("unsent", 300, 30)
        listOf("old", "mid", "new").forEach { File(dir, "$it.uploaded").apply { writeText("{}"); setLastModified(File(dir, "$it.pcm").lastModified()) } }
        val removed = SessionRecorder.prune(dir, keep = "new", nowMs = 1_000_000_000_000L, maxBytes = 700)
        assertEquals(listOf("old", "mid"), removed)
        // never uploaded: kept although it is the oldest and over budget
        assertEquals(setOf("new.pcm", "new.json", "new.uploaded", "unsent.pcm", "unsent.json"), dir.list()!!.toSet())
    }
}
