package com.oops.app.recall

import com.oops.app.buffer.SessionIndex
import com.oops.app.buffer.Utterance

fun overlap(query: String, text: String): Double {
    fun grams(s: String) = s.lowercase().filter(Char::isLetterOrDigit).windowed(2).toSet() -
        setOf("刚刚", "刚才", "那个", "这个", "留下", "下来", "一下")
    val q = grams(query)
    return if (q.isEmpty()) 0.0 else (q intersect grams(text)).size.toDouble() / q.size
}

object AskCandidates {
    fun select(all: List<Utterance>, query: String, now: Long, limit: Int = 120): List<Utterance> {
        require(limit > 0)
        val pool = all.filter { !OopsCommand.matches(it.text) && it.text.count(Char::isLetterOrDigit) >= 4 }
        if (pool.size <= limit) return pool.sortedBy { it.startMs }
        return pool.sortedWith(compareByDescending<Utterance> { it.tag in SessionIndex.DIGEST_TAGS }
            .thenByDescending { overlap(query, it.text) }.thenBy { (now - it.endMs).coerceAtLeast(0) })
            .take(limit).sortedBy { it.startMs }
    }
}
