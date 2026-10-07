package com.oops.app.audio

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.DataOutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit.SECONDS
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong

class RemoteAudioSourceTest {
    @Test
    fun `re-chunks audio into 100 ms frames, forwards marks and reports the reconnect gap`() {
        ServerSocket(0, 1, InetAddress.getLoopbackAddress()).use { server ->
            val frames = LinkedBlockingQueue<ByteArray>()
            val gaps = LinkedBlockingQueue<Long>()
            val marks = AtomicInteger()
            val statuses = CopyOnWriteArrayList<String?>()
            val now = AtomicLong(0)
            val source = RemoteAudioSource("127.0.0.1", server.localPort, retryBaseMs = 20, nowMs = { now.get() })
            source.start(object : AudioSource.Listener {
                override fun onAudio(pcm: ByteArray) {
                    frames.add(pcm)
                }

                override fun onMark(mark: AudioSource.Mark) {
                    marks.incrementAndGet()
                }

                override fun onGap(missingMs: Long) {
                    gaps.add(missingMs)
                }

                override fun onStatus(detail: String?) {
                    statuses.add(detail)
                }

                override fun onError(error: Throwable) = throw AssertionError(error)
            })
            try {
                server.accept().use { mac ->
                    val out = DataOutputStream(mac.getOutputStream())
                    out.frame(0x03, """{"device":"Wireless microphone"}""".toByteArray())
                    out.frame(0x01, ByteArray(1000) { 1 })
                    out.frame(0x01, ByteArray(5400) { 2 }) // 6400 bytes = exactly two frames
                    out.frame(0x09, byteArrayOf(1, 2, 3)) // unknown types are skipped
                    out.frame(0x02, """{"kind":"double_tap"}""".toByteArray())
                    out.flush()

                    val first = frames.poll(2, SECONDS)!!
                    val second = frames.poll(2, SECONDS)!!
                    assertEquals(3200, first.size)
                    assertEquals(1, first[999].toInt())
                    assertEquals(2, first[1000].toInt())
                    assertTrue(second.all { it.toInt() == 2 })
                    waitUntil { marks.get() == 1 }
                }

                // The Mac side went away; two seconds pass before audio flows again.
                now.addAndGet(2_000)
                server.accept().use { mac ->
                    val out = DataOutputStream(mac.getOutputStream())
                    out.frame(0x01, ByteArray(3200))
                    out.flush()
                    assertEquals(1_900L, gaps.poll(2, SECONDS)) // minus the 100 ms frame itself
                    frames.poll(2, SECONDS)!!
                }
            } finally {
                source.stop()
            }
            assertTrue(statuses.contains("Mac 收音：Wireless microphone"))
            assertEquals(1, marks.get())
        }
    }

    private fun DataOutputStream.frame(type: Int, payload: ByteArray) {
        writeByte(type)
        writeInt(payload.size)
        write(payload)
    }

    private fun waitUntil(condition: () -> Boolean) {
        val deadline = System.currentTimeMillis() + 2_000
        while (!condition()) {
            check(System.currentTimeMillis() < deadline) { "timed out" }
            Thread.sleep(10)
        }
    }
}
