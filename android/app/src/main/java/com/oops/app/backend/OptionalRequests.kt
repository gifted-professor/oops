package com.oops.app.backend

import java.io.IOException
import java.util.concurrent.ConcurrentHashMap
import java.io.Closeable
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit

class HttpFailure(val status: Int, message: String) : IOException(message)

/** One instance per capture session; old requests cannot disable an endpoint in a new session. */
class OptionalRequests(private val maxPending: Int = 8) {
    init { require(maxPending >= 2) }
    private val disabled = ConcurrentHashMap.newKeySet<String>()
    private val permits = ConcurrentHashMap<String, Semaphore>()
    private val admissions = ConcurrentHashMap<String, Semaphore>()

    class Reservation internal constructor(val endpoint: String, private val slots: Semaphore) : Closeable {
        private val closed = AtomicBoolean(false)
        override fun close() { if (closed.compareAndSet(false, true)) slots.release() }
    }

    /** Reserve before creating a job or copying audio; includes running and queued requests. */
    fun tryReserve(endpoint: String): Reservation? {
        val slots = admissions.computeIfAbsent(endpoint) { Semaphore(maxPending) }
        return if (slots.tryAcquire()) Reservation(endpoint, slots) else null
    }

    class QueueFull : IOException("分析队列已满")

    suspend fun <T> request(endpoint: String, block: suspend () -> T?): T? {
        val reservation = tryReserve(endpoint) ?: throw QueueFull()
        return request(reservation, block)
    }

    suspend fun <T> request(reservation: Reservation, block: suspend () -> T?): T? = reservation.use {
        val endpoint = reservation.endpoint
        permits.computeIfAbsent(endpoint) { Semaphore(2) }.withPermit {
            if (endpoint in disabled) return@withPermit null
            for (attempt in 0..2) {
                if (endpoint in disabled) return@withPermit null
                try {
                    val result = block()
                    if (result == null) disabled.add(endpoint) // only unsupported endpoints return null
                    return@withPermit result
                } catch (error: IOException) {
                    val retryable = error !is HttpFailure || error.status == 429 || error.status >= 500
                    if (!retryable || attempt == 2) throw error
                    delay(if (attempt == 0) 1000 else 3000)
                }
            }
            null
        }
    }
}
