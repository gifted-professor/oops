package com.oops.app.backend

import com.sun.net.httpserver.HttpServer
import java.net.InetSocketAddress
import java.io.IOException
import kotlinx.coroutines.test.runTest
import okhttp3.OkHttpClient
import org.junit.Assert.*
import org.junit.Test

class HttpOopsApiTest {
    @Test fun oldServerDisablesNoticesAndMeetingsWithoutFailingRecall() = runTest {
        withServer(404, "{}") { api ->
            val policy = OptionalRequests()
            var calls = 0
            repeat(2) { assertNull(policy.request("notice") { calls++; api.notice(org.json.JSONObject().put("kind", "topic")) }) }
            assertEquals(1, calls)
            assertNull(api.syncMeeting(org.json.JSONObject()))
            assertNull(api.historyList())
        }
    }
    @Test fun credentialsReachStatusAndOptionalAnalysisRequests() = runTest {
        val headers = mutableListOf<String?>()
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/") { exchange ->
            headers.add(exchange.requestHeaders.getFirst("Authorization"))
            val body = if (exchange.requestURI.path.endsWith("utterances"))
                """{"utterance_id":"u1","tag":"uncertain","prob":null,"engine":"rules"}"""
            else """{"op_id":"op-1","status":"pending","feishu_task":null}"""
            val bytes = body.toByteArray()
            exchange.sendResponseHeaders(200, bytes.size.toLong())
            exchange.responseBody.use { it.write(bytes) }
        }
        server.start()
        try {
            val api = HttpOopsApi(OkHttpClient(), "http://127.0.0.1:${server.address.port}", "test-access-key")
            api.getAction("op-1")
            api.tagUtterance(TagRequest("s", Candidate("u1", 0, 1000, null, "你好")))
            assertEquals(listOf("Bearer test-access-key", "Bearer test-access-key"), headers)
        } finally { server.stop(0) }
    }

    @Test fun voiceprintManagementUsesAuthenticatedEndpointsAndKeepsPayload() = runTest {
        val paths = mutableListOf<String>()
        val bodies = mutableListOf<String>()
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/") { exchange ->
            assertEquals("Bearer test-key", exchange.requestHeaders.getFirst("Authorization"))
            paths.add(exchange.requestURI.path)
            bodies.add(exchange.requestBody.bufferedReader().readText())
            val bytes = "{}".toByteArray()
            exchange.sendResponseHeaders(200, bytes.size.toLong())
            exchange.responseBody.use { it.write(bytes) }
        }
        server.start()
        try {
            val api = HttpOopsApi(OkHttpClient(), "http://127.0.0.1:${server.address.port}", "test-key")
            api.voiceprints()
            api.enrollVoiceprint(org.json.JSONObject().put("consent", true).put("name", "林春源"))
            api.testVoiceprint(org.json.JSONObject().put("pcm_base64", "test-pcm"))
            api.deleteVoiceprint(org.json.JSONObject().put("confirm", true))
            assertEquals(listOf("/v1/voiceprints", "/v1/voiceprints/enroll", "/v1/voiceprints/test", "/v1/voiceprints/delete"), paths)
            assertTrue(org.json.JSONObject(bodies[1]).getBoolean("consent"))
            assertEquals("test-pcm", org.json.JSONObject(bodies[2]).getString("pcm_base64"))
        } finally { server.stop(0) }
    }

    @Test fun refusesToTransmitCredentialsOverPublicPlainHttp() = runTest {
        val api = HttpOopsApi(OkHttpClient(), "http://example.com", "test-access-key")
        assertTrue(runCatching { api.getAction("op-1") }.exceptionOrNull() is IllegalArgumentException)
    }

    @Test fun reviewClipAndTasksUseTheirEndpoints() = runTest {
        val seen = mutableListOf<String>()
        val pcm = ByteArray(3200) { it.toByte() }
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/") { exchange ->
            seen.add(exchange.requestMethod + " " + exchange.requestURI.rawPath + (exchange.requestURI.rawQuery?.let { "?$it" } ?: ""))
            val path = exchange.requestURI.path
            val (code, bytes) = when {
                path.endsWith("/clip") && exchange.requestURI.rawQuery.contains("start_ms=0") -> 200 to (com.oops.app.core.Wav.header(pcm.size) + pcm)
                path.endsWith("/clip") && exchange.requestURI.rawQuery.contains("start_ms=5000") -> 409 to """{"error":{"code":"RECORDING","message":"录音还没有上传到这一段"}}""".toByteArray()
                path.endsWith("/clip") -> 404 to "{}".toByteArray()
                else -> 200 to """{"items":[]}""".toByteArray()
            }
            exchange.sendResponseHeaders(code, bytes.size.toLong())
            exchange.responseBody.use { it.write(bytes) }
        }
        server.start()
        try {
            val api = HttpOopsApi(OkHttpClient(), "http://127.0.0.1:${server.address.port}")
            assertArrayEquals(pcm, api.recordingClip("session-1", 0, 100))
            assertNull(api.recordingClip("session-1", 100, 200))
            val failure = runCatching { api.recordingClip("session-1", 5000, 6000) }.exceptionOrNull()
            assertEquals(409, (failure as HttpFailure).status)
            api.tasks("洪玉麟", "open"); api.tasks(null, "all")
            api.reviewMeeting(org.json.JSONObject().put("session_id", "s").put("utterance_id", "u1").put("speaker", "洪玉麟"))
            assertEquals(listOf("GET /v1/recordings/session-1/clip?start_ms=0&end_ms=100",
                "GET /v1/recordings/session-1/clip?start_ms=100&end_ms=200", "GET /v1/recordings/session-1/clip?start_ms=5000&end_ms=6000",
                "GET /v1/tasks?status=open&assignee=%E6%B4%AA%E7%8E%89%E9%BA%9F", "GET /v1/tasks?status=all",
                "POST /v1/meetings/review"), seen)
            assertTrue(runCatching { api.tasks(null, "late") }.isFailure)
            assertTrue(runCatching { api.recordingClip("../x", 0, 100) }.isFailure)
        } finally { server.stop(0) }
    }
    private suspend fun withServer(code: Int, body: String, block: suspend (HttpOopsApi) -> Unit) {
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/") { exchange ->
            val bytes = body.toByteArray(Charsets.UTF_8)
            exchange.sendResponseHeaders(code, bytes.size.toLong())
            exchange.responseBody.use { it.write(bytes) }
        }
        server.start()
        try { block(HttpOopsApi(OkHttpClient(), "http://127.0.0.1:${server.address.port}")) }
        finally { server.stop(0) }
    }
    @Test fun authFailureHasClearMessageAndDoesNotEchoBody() = runTest {
        withServer(401, "credential=secret-test-value") { api ->
            val error = runCatching { api.getAction("op-1") }.exceptionOrNull()
            assertTrue(error is IOException)
            assertTrue(error!!.message!!.contains("授权失效"))
            assertFalse(error.message!!.contains("secret-test-value"))
        }
    }
    @Test fun structuredServerErrorShowsItsMessage() = runTest {
        withServer(503, """{"error":{"code":"busy","message":"稍后重试"}}""") { api ->
            assertTrue(runCatching { api.getAction("op-1") }.exceptionOrNull()!!.message!!.contains("稍后重试"))
        }
    }
    @Test fun lostConfirmResponseRemainsUnknownThroughHttpApi() = runTest {
        withServer(502, "<html>gateway failed</html>") { api ->
            val request = ConfirmRequest("op-1", "s", fixtureDraft())
            val result = ActionWorkflow.execute(api, request, true)
            assertEquals(ActionResult.PENDING, result.status)
            assertFalse(result.message!!.contains("<html>"))
        }
    }
}
