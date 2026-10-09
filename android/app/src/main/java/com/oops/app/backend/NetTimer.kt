package com.oops.app.backend

import okhttp3.Call
import okhttp3.Connection
import okhttp3.EventListener
import java.io.IOException
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Proxy

/**
 * Where a request's time goes on the network: name lookup, TCP connect, TLS, sending the body, waiting
 * for the server's first byte, reading the answer, and whether an existing connection was reused. The
 * server only sees its own part; the rest is invisible without this. One listener per call.
 */
class NetTimer(private val watched: (String) -> Boolean, private val report: (String, Map<String, Any?>) -> Unit,
               private val now: () -> Long = { System.nanoTime() / 1_000_000 }) : EventListener() {
    private var path = ""
    private var t0 = 0L
    private val marks = HashMap<String, Long>()
    private var connecting = false
    private var requestSent = 0L
    private var firstByte = 0L

    override fun callStart(call: Call) {
        path = call.request().url.encodedPath
        t0 = now()
    }

    private fun mark(name: String) { marks[name] = now() }
    private fun span(from: String, to: String): Long? = marks[from]?.let { a -> marks[to]?.let { b -> b - a } }

    override fun dnsStart(call: Call, domainName: String) = mark("dns0")
    override fun dnsEnd(call: Call, domainName: String, inetAddressList: List<InetAddress>) = mark("dns1")
    override fun connectStart(call: Call, inetSocketAddress: InetSocketAddress, proxy: Proxy) { connecting = true; mark("tcp0") }
    override fun secureConnectStart(call: Call) = mark("tls0")
    override fun secureConnectEnd(call: Call, handshake: okhttp3.Handshake?) = mark("tls1")
    override fun connectEnd(call: Call, inetSocketAddress: InetSocketAddress, proxy: Proxy, protocol: okhttp3.Protocol?) = mark("tcp1")
    override fun connectionAcquired(call: Call, connection: Connection) = mark("acquired")
    override fun requestBodyEnd(call: Call, byteCount: Long) { requestSent = now() }
    override fun requestHeadersEnd(call: Call, request: okhttp3.Request) { if (requestSent == 0L) requestSent = now() }
    override fun responseHeadersStart(call: Call) { firstByte = now() }
    override fun responseBodyEnd(call: Call, byteCount: Long) = finish(null)
    override fun callFailed(call: Call, ioe: IOException) = finish(ioe.javaClass.simpleName)

    private fun finish(error: String?) {
        if (!watched(path)) return
        val end = now()
        report(path, mapOf(
            "reused" to !connecting,
            "dns_ms" to span("dns0", "dns1"),
            "connect_ms" to span("tcp0", "tcp1")?.let { it - (span("tls0", "tls1") ?: 0) },
            "tls_ms" to span("tls0", "tls1"),
            "send_ms" to if (requestSent > 0) requestSent - (marks["acquired"] ?: t0) else null,
            "wait_ms" to if (firstByte > 0 && requestSent > 0) firstByte - requestSent else null,
            "read_ms" to if (firstByte > 0) end - firstByte else null,
            "total_ms" to end - t0,
            "error" to error,
        ))
    }
}
