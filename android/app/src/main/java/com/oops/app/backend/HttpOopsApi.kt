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
class HttpOopsApi(private val http: OkHttpClient, baseUrl: String) : OopsApi {
    override val name = "http"
    private val base = baseUrl.trim().trimEnd('/')

    override suspend fun recall(request: RecallRequest) =
        ApiJson.recallResponse(post("/v1/recall", ApiJson.recallRequest(request)))

    override suspend fun draftAction(request: DraftRequest) =
        ApiJson.draftResponse(post("/v1/actions/draft", ApiJson.draftRequest(request)))

    override suspend fun confirmAction(request: ConfirmRequest) =
        ApiJson.actionResult(post("/v1/actions/confirm", ApiJson.confirmRequest(request)))

    /** Optional endpoint: a backend without voiceprints answers 404/501 and we simply skip. */
    override suspend fun identifySpeaker(request: SpeakerRequest): SpeakerResult? = withContext(Dispatchers.IO) {
        val body = ApiJson.speakerRequest(request).toString().toRequestBody(JSON)
        http.newCall(Request.Builder().url("$base/v1/speaker").post(body).build()).execute().use { response ->
            when {
                response.code == 404 || response.code == 501 -> null
                !response.isSuccessful -> throw IOException("声纹识别返回 ${response.code}")
                else -> ApiJson.speakerResult(JSONObject(response.body.string()))
            }
        }
    }

    override suspend fun getAction(opId: String) =
        ApiJson.actionResult(call(Request.Builder().url("$base/v1/actions/$opId").get().build()))

    private suspend fun post(path: String, body: JSONObject): JSONObject =
        call(Request.Builder().url(base + path).post(body.toString().toRequestBody(JSON)).build())

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
