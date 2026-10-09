package com.oops.app.store

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.io.IOException
import java.nio.file.Files

/** In-memory stand-in for scripts/recordings.py with the same offset and checksum rules. */
private class FakeServer : RecordingTransport {
    val files = mutableMapOf<String, ByteArray>()
    var declared = JSONObject()
    var failAfterChunks = Int.MAX_VALUE
    var chunks = 0
    var completed = false

    override fun recordingManifest(sessionId: String, body: JSONObject): JSONObject {
        declared = body
        val received = JSONObject()
        val items = body.getJSONArray("files")
        for (i in 0 until items.length()) items.getJSONObject(i).getString("name").let { received.put(it, files[it]?.size ?: 0) }
        return JSONObject().put("complete", completed).put("received", received)
    }

    override fun recordingChunk(sessionId: String, name: String, offset: Long, bytes: ByteArray, sha256: String): Long {
        if (chunks++ >= failAfterChunks) throw IOException("network down")
        assertEquals(RecordingUploader.sha256(bytes), sha256)
        val current = files[name] ?: ByteArray(0)
        if (offset != current.size.toLong()) throw ResumeAt(current.size.toLong())
        files[name] = current + bytes
        return files.getValue(name).size.toLong()
    }

    override fun recordingComplete(sessionId: String): JSONObject {
        val items = declared.getJSONArray("files")
        for (i in 0 until items.length()) {
            val f = items.getJSONObject(i)
            val data = files[f.getString("name")] ?: ByteArray(0)
            if (RecordingUploader.sha256(data) != f.getString("sha256")) return JSONObject().put("complete", false)
        }
        completed = true
        return JSONObject().put("complete", true)
    }
}

class RecordingUploaderTest {
    private fun session(): File = Files.createTempDirectory("up").toFile().also { dir ->
        File(dir, "s0000001.pcm").writeBytes(ByteArray(2_500) { (it % 251).toByte() })
        File(dir, "s0000001.finals.jsonl").writeText("{\"id\":\"u1\",\"text\":\"好\"}\n")
        File(dir, "s0000001.asr.jsonl").writeText("{\"payload\":\"{}\"}\n")
        File(dir, "s0000001.timing.jsonl").writeText("{\"stage\":\"final\"}\n")
        File(dir, "s0000001.json").writeText("{\"source\":\"mic\"}")
    }

    @Test
    fun `interrupted upload resumes and only a verified archive is marked`() {
        val dir = session()
        val server = FakeServer().apply { failAfterChunks = 2 }
        val uploader = RecordingUploader(dir, server, chunkBytes = 1_000)
        assertEquals(listOf("s0000001"), uploader.pending(active = null))
        assertTrue(runCatching { uploader.upload("s0000001") }.isFailure)
        assertFalse(File(dir, "s0000001.uploaded").exists())
        server.failAfterChunks = Int.MAX_VALUE
        assertTrue(uploader.upload("s0000001"))
        assertEquals(File(dir, "s0000001.pcm").readBytes().toList(), server.files.getValue("audio.pcm").toList())
        assertEquals(setOf("audio.pcm", "finals.jsonl", "asr.jsonl", "timing.jsonl", "meta.json"), server.files.keys)
        assertEquals(emptyList<String>(), uploader.pending(active = null))
    }

    @Test
    fun `a meeting that starts mid-upload stops it at once and the next pass resumes`() {
        val dir = session()
        val server = FakeServer()
        val uploader = RecordingUploader(dir, server, chunkBytes = 1_000)
        var allowed = 1
        assertFalse(uploader.upload("s0000001") { allowed-- > 0 })
        assertEquals(1, server.chunks)
        assertFalse(File(dir, "s0000001.uploaded").exists())
        assertEquals(listOf("s0000001"), uploader.pending(active = null))
        assertTrue(uploader.upload("s0000001"))
        assertEquals(File(dir, "s0000001.pcm").readBytes().toList(), server.files.getValue("audio.pcm").toList())
    }

    @Test
    fun `active session is skipped and a file that grew after upload is sent again`() {
        val dir = session()
        val server = FakeServer()
        val uploader = RecordingUploader(dir, server, chunkBytes = 1_000)
        assertEquals(emptyList<String>(), uploader.pending(active = "s0000001"))
        assertTrue(uploader.upload("s0000001"))
        val finals = File(dir, "s0000001.finals.jsonl")
        finals.appendText("{\"id\":\"u2\"}\n")
        finals.setLastModified(System.currentTimeMillis() + 5_000)
        server.completed = false
        assertEquals(listOf("s0000001"), uploader.pending(active = null))
        assertTrue(uploader.upload("s0000001"))
        assertEquals(finals.readBytes().toList(), server.files.getValue("finals.jsonl").toList())
    }
}
