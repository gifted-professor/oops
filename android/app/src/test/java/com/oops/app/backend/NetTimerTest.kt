package com.oops.app.backend

import com.sun.net.httpserver.HttpServer
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.net.InetSocketAddress

class NetTimerTest {
    private lateinit var server: HttpServer
    private val reports = mutableListOf<Pair<String, Map<String, Any?>>>()

    @Before fun start() {
        server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/v1/speaker") { ex ->
            ex.requestBody.readBytes()
            Thread.sleep(60) // server think time shows up as wait_ms
            val body = "{}".toByteArray()
            ex.sendResponseHeaders(200, body.size.toLong())
            ex.responseBody.use { it.write(body) }
        }
        server.createContext("/v1/recordings") { ex -> ex.sendResponseHeaders(204, -1); ex.close() }
        server.start()
    }

    @After fun stop() = server.stop(0)

    private fun client() = OkHttpClient.Builder()
        .eventListenerFactory { NetTimer({ it.startsWith("/v1/speaker") }, { path, fields -> synchronized(reports) { reports += path to fields } }) }
        .build()

    private fun post(client: OkHttpClient, path: String) {
        val url = "http://127.0.0.1:${server.address.port}$path"
        client.newCall(Request.Builder().url(url).post("x".repeat(2000).toRequestBody("text/plain".toMediaType())).build())
            .execute().use { it.body.string() }
    }

    @Test
    fun `a new connection is reported with its phases and the next call reuses it`() {
        val client = client()
        post(client, "/v1/speaker")
        post(client, "/v1/speaker")
        assertEquals(2, reports.size)
        val (first, second) = reports.map { it.second }
        assertEquals("/v1/speaker", reports[0].first)
        assertFalse(first["reused"] as Boolean)
        assertNotNull(first["connect_ms"])
        assertNull(first["tls_ms"]) // plain HTTP in the test; a real HTTPS call fills this in
        assertTrue(second["reused"] as Boolean)
        assertNull(second["connect_ms"])
        assertTrue("server think time must land in wait_ms: ${second["wait_ms"]}", (second["wait_ms"] as Long) >= 50)
        assertTrue((second["total_ms"] as Long) >= (second["wait_ms"] as Long))
    }

    @Test
    fun `unwatched paths and failures are handled`() {
        val client = client()
        client.newCall(Request.Builder().url("http://127.0.0.1:${server.address.port}/v1/recordings").get().build()).execute().close()
        assertEquals(0, reports.size)
        val port = server.address.port
        server.stop(0)
        runCatching { client.newCall(Request.Builder().url("http://127.0.0.1:$port/v1/speaker").get().build()).execute() }
        assertEquals(1, reports.size)
        assertNotNull(reports[0].second["error"])
    }
}
