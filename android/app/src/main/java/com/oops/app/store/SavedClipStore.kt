package com.oops.app.store

import com.oops.app.core.Wav
import org.json.JSONObject
import java.io.File

/** Long-term memory = only what the user confirmed: `<id>.json` metadata + `<id>.wav` original audio. */
class SavedClipStore(private val dir: File) {
    init {
        dir.mkdirs()
    }

    fun save(id: String, meta: JSONObject, pcm: ByteArray?): File {
        if (pcm != null) {
            Wav.write(File(dir, "$id.wav"), pcm)
            meta.put("audio", "$id.wav")
        }
        return File(dir, "$id.json").apply { writeText(meta.toString(2)) }
    }

    fun count(): Int = metaFiles().size

    fun list(): List<JSONObject> = metaFiles()
        .sortedByDescending { it.lastModified() }
        .mapNotNull { runCatching { JSONObject(it.readText()) }.getOrNull() }

    private fun metaFiles(): List<File> = dir.listFiles { f -> f.extension == "json" }?.toList().orEmpty()
}
