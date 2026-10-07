package com.oops.app.backend

import com.sun.net.httpserver.HttpServer
import java.net.InetSocketAddress
import java.io.IOException
import kotlinx.coroutines.test.runTest
import okhttp3.OkHttpClient
import org.junit.Assert.*
import org.junit.Test

class HttpOopsApiTest {
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
