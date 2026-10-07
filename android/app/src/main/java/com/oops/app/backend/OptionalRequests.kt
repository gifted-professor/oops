package com.oops.app.backend

import java.io.IOException
import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit

class HttpFailure(val status: Int, message: String) : IOException(message)

/** One instance per capture session; old requests cannot disable an endpoint in a new session. */
class OptionalRequests {
    private val disabled = ConcurrentHashMap.newKeySet<String>()
    private val permits = ConcurrentHashMap<String, Semaphore>()

    suspend fun <T> request(endpoint: String, block: suspend () -> T?): T? =
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
