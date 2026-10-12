package com.oops.app.backend

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException

/** The real backend (洪玉麟's service), see docs/api.md. */
class HttpOopsApi(private val http: OkHttpClient, baseUrl: String, private val token: String = "") : OopsApi,
    com.oops.app.store.RecordingTransport {
    override val name = "http"
    private val base = baseUrl.trim().trimEnd('/')

    private fun request(path: String): Request.Builder {
        val builder = Request.Builder().url(base + path)
        if (token.isNotBlank()) {
            val url = builder.build().url
            require(url.isHttps || url.host in setOf("127.0.0.1", "localhost", "::1")) { "服务访问凭据需要 HTTPS" }
            builder.header("Authorization", "Bearer $token")
        }
        return builder
    }

    override suspend fun notice(request: JSONObject): JSONObject? =
        optional("/v1/notices", request)?.let { NoticeJson.validate(it, request) }
    override suspend fun syncMeeting(request: JSONObject) = optional("/v1/meetings/sync", request)
    override suspend fun historyList() = optionalGet("/v1/meetings?limit=30")
    override suspend fun historyOpen(id: String): JSONObject? {
        require(id.matches(Regex("[A-Za-z0-9_-]{1,100}")))
        return optionalGet("/v1/meetings/$id")
    }
    override suspend fun historyDelete(id: String) = optional("/v1/meetings/delete", JSONObject().put("session_id", id).put("confirm", true))
    override suspend fun historySearch(query: String) = optional("/v1/meetings/search", JSONObject().put("query", query))
    override suspend fun reviewMeeting(request: JSONObject) = optional("/v1/meetings/review", request)
    override suspend fun projects() = optionalGet("/v1/projects")
    override suspend fun project(name: String): JSONObject? {
        require(name.isNotBlank() && name.length <= 30)
        return optionalGet("/v1/projects/" + java.net.URLEncoder.encode(name, "UTF-8").replace("+", "%20"))
    }
    override suspend fun setMeetingProject(sessionId: String, project: String) =
        optional("/v1/meetings/project", JSONObject().put("session_id", sessionId).put("project", project))
    override suspend fun renameMeeting(sessionId: String, title: String) =
        optional("/v1/meetings/rename", JSONObject().put("session_id", sessionId).put("title", title))
    override suspend fun tasks(assignee: String?, status: String): JSONObject? {
        require(status in setOf("open", "done", "all"))
        val query = "status=$status" + (assignee?.let { "&assignee=" + java.net.URLEncoder.encode(it, "UTF-8") } ?: "")
        return optionalGet("/v1/tasks?$query")
    }
    override suspend fun recordingClip(sessionId: String, startMs: Long, endMs: Long): ByteArray? = withContext(Dispatchers.IO) {
        require(startMs in 0 until endMs)
        http.newCall(request(recordingPath(sessionId) + "/clip?start_ms=$startMs&end_ms=$endMs").get().build()).execute().use { response ->
            when {
                response.code == 404 || response.code == 501 -> null
                response.code == 409 -> throw HttpFailure(409, "录音还没上传到这一段")
                !response.isSuccessful -> throw HttpFailure(response.code, "原声接口返回 HTTP ${response.code}")
                else -> com.oops.app.core.Wav.openPcm(response.body.byteStream()).use { it.readBytes() }
            }
        }
    }
    private suspend fun optionalGet(path: String): JSONObject? = withContext(Dispatchers.IO) {
        http.newCall(request(path).get().build()).execute().use { response ->
            if (response.code == 404 || response.code == 501) null
            else if (!response.isSuccessful) throw HttpFailure(response.code, "会议接口返回 HTTP ${response.code}")
            else JSONObject(response.body.string())
        }
    }

    override suspend fun recall(request: RecallRequest) =
        ApiJson.recallResponse(post("/v1/recall", ApiJson.recallRequest(request)))

    override suspend fun draftAction(request: DraftRequest) =
        ApiJson.draftResponse(post("/v1/actions/draft", ApiJson.draftRequest(request)))

    override suspend fun confirmAction(request: ConfirmRequest) =
        ApiJson.actionResult(post("/v1/actions/confirm", ApiJson.confirmRequest(request)))

    /** Optional endpoint: a backend without voiceprints answers 404/501 and we simply skip. */
    override suspend fun identifySpeaker(request: SpeakerRequest): SpeakerResult? =
        optional("/v1/speaker", ApiJson.speakerRequest(request))?.let(ApiJson::speakerResult)

    override suspend fun speakerSplit(sessionId: String, utteranceId: String): SplitFetch? =
        optional("/v1/speaker/split", ApiJson.splitRequest(sessionId, utteranceId))?.let(ApiJson::splitResult)

    override suspend fun tagUtterance(request: TagRequest): TagResult? =
        optional("/v1/utterances", ApiJson.tagRequest(request))?.let(ApiJson::tagResult)

    override suspend fun previewSpeakers(request: SpeakerRequest): com.oops.app.buffer.SpeakerPreview? =
        optional("/v1/speaker-preview", ApiJson.speakerRequest(request))?.let(ApiJson::speakerPreview)

    suspend fun voiceprints(): JSONObject = call(request("/v1/voiceprints").get().build())
    suspend fun enrollVoiceprint(body: JSONObject): JSONObject = post("/v1/voiceprints/enroll", body)
    suspend fun testVoiceprint(body: JSONObject): JSONObject = post("/v1/voiceprints/test", body)
    suspend fun deleteVoiceprint(body: JSONObject): JSONObject = post("/v1/voiceprints/delete", body)

    private suspend fun optional(path: String, json: JSONObject): JSONObject? = withContext(Dispatchers.IO) {
        http.newCall(request(path).post(json.toString().toRequestBody(JSON)).build()).execute().use { response ->
            if (response.code == 404 || response.code == 501) null
            else if (!response.isSuccessful) throw HttpFailure(response.code, "分析接口返回 HTTP ${response.code}")
            else JSONObject(response.body.string())
        }
    }

    override suspend fun getAction(opId: String) =
        ApiJson.actionResult(call(request("/v1/actions/$opId").get().build()))

    // ---- raw meeting assets (blocking; call off the main thread) ----

    private fun recordingPath(id: String): String {
        require(id.matches(Regex("[A-Za-z0-9-]{8,80}")))
        return "/v1/recordings/$id"
    }

    private fun blocking(request: Request): JSONObject = http.newCall(request).execute().use { response ->
        val text = response.body.string()
        val error = runCatching { JSONObject(text).optJSONObject("error") }.getOrNull()
        if (response.code == 409 && error?.has("received") == true) throw com.oops.app.store.ResumeAt(error.getLong("received"))
        if (!response.isSuccessful) throw HttpFailure(response.code, "录音上传返回 HTTP ${response.code}：${error?.optString("message") ?: ""}")
        JSONObject(text)
    }

    override fun recordingManifest(sessionId: String, body: JSONObject) =
        blocking(request(recordingPath(sessionId) + "/manifest").post(body.toString().toRequestBody(JSON)).build())

    override fun recordingChunk(sessionId: String, name: String, offset: Long, bytes: ByteArray, sha256: String): Long {
        require(name in setOf("audio.pcm", "finals.jsonl", "asr.jsonl", "timing.jsonl", "meta.json"))
        return blocking(request(recordingPath(sessionId) + "/$name?offset=$offset").header("X-Content-SHA256", sha256)
            .put(bytes.toRequestBody(OCTET)).build()).getLong("received")
    }

    override fun recordingComplete(sessionId: String) =
        blocking(request(recordingPath(sessionId) + "/complete").post("{}".toRequestBody(JSON)).build())

    private suspend fun post(path: String, body: JSONObject): JSONObject =
        call(request(path).post(body.toString().toRequestBody(JSON)).build())

    private suspend fun call(request: Request): JSONObject = withContext(Dispatchers.IO) {
        http.newCall(request).execute().use { response ->
            val text = response.body.string()
            if (!response.isSuccessful) {
                if (response.code == 401 || response.code == 403) {
                    throw IOException("后端授权失效（HTTP ${response.code}），请检查服务授权")
                }
                val error = runCatching { JSONObject(text).optJSONObject("error") }.getOrNull()
                val detail = error?.optString("message")?.take(160)?.takeIf { it.isNotBlank() }
                    ?: "服务暂时不可用"
                throw IOException("后端返回 ${response.code}：$detail")
            }
            JSONObject(text)
        }
    }

    private companion object {
        val JSON = "application/json; charset=utf-8".toMediaType()
        val OCTET = "application/octet-stream".toMediaType()
    }
}
