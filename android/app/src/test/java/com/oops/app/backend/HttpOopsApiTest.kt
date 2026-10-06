package com.oops.app.backend

import com.sun.net.httpserver.HttpServer
import java.net.InetSocketAddress
import java.io.IOException
import kotlinx.coroutines.test.runTest
import okhttp3.OkHttpClient
import org.junit.Assert.*
import org.junit.Test

class HttpOopsApiTest {
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
