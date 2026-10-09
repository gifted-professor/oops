package com.oops.app.store

import com.oops.app.buffer.Utterance
import com.oops.app.buffer.WordTime
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
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
        r.timing(JSONObject().put("id", "u1").put("stage", "speaker").put("http_ms", 420))
        r.closeAudio()
        r.flush()
        assertEquals(3200L + 3200L, File(dir, "s1.pcm").length())
        val line = JSONObject(File(dir, "s1.finals.jsonl").readLines().single())
        assertEquals("好的", line.getString("text"))
        assertEquals(150, line.getJSONArray("words").getJSONObject(0).getInt("end_ms"))
        val meta = JSONObject(File(dir, "s1.json").readText())
        assertEquals("mic", meta.getString("source"))
        assertEquals(6400L, meta.getLong("bytes"))
        assertTrue(meta.getBoolean("capped"))
        assertTrue(r.capped)
        assertTrue(meta.has("audio_ended_at"))
        assertEquals(420, JSONObject(File(dir, "s1.timing.jsonl").readLines().single()).getInt("http_ms"))
    }

    @Test
    fun `text logs keep their order and stay available after the audio is closed`() {
        val dir = Files.createTempDirectory("rec2").toFile()
        val r = SessionRecorder(dir, "s2", JSONObject().put("source", "mic"))
        r.audio(ByteArray(3200))
        r.closeAudio()
        r.closeAudio() // idempotent: the end record is written once
        val ended = JSONObject(File(dir, "s2.json").readText()).getString("audio_ended_at")
        (1..50).forEach { r.asr(1, 0, "{\"n\":$it}") }
        r.final(Utterance("late", 0, 300, "收尾", null))
        r.flush()
        assertEquals((1..50).toList(), File(dir, "s2.asr.jsonl").readLines().map { JSONObject(JSONObject(it).getString("payload")).getInt("n") })
        assertEquals("收尾", JSONObject(File(dir, "s2.finals.jsonl").readLines().single()).getString("text"))
        assertEquals(ended, JSONObject(File(dir, "s2.json").readText()).getString("audio_ended_at"))
        assertFalse(JSONObject(File(dir, "s2.json").readText()).getBoolean("capped"))
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
