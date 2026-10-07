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
class HttpOopsApi(private val http: OkHttpClient, baseUrl: String, private val token: String = "") : OopsApi {
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
    }
}
