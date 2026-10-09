package com.oops.app.core

/**
 * Display-side cleanup of ASR sentences. The raw text is never changed: the archive, team sync, recall,
 * drafts and task detection all keep what the recognizer returned; only the live transcript shows less.
 * `scripts/filler_report.py` reviews archived meetings against [FILLER_CHARS].
 */
object TextClean {
    /** Interjections without content; a sentence made only of these (and punctuation) is hidden. */
    const val FILLER_CHARS = "嗯呃额啊哦噢唉哎诶欸哈嘿唔"
    private const val PUNCT = "，。！？、…,.!?;；:：\\s"

    private val fillerOnly = Regex("[$FILLER_CHARS$PUNCT]*")
    /** Digits as spoken: 幺 is the usual way to say 1 in a phone number. */
    private const val CN_DIGITS = "零〇一二三四五六七八九幺"
    private const val DIGITS = "0-9$CN_DIGITS"
    private val digitRun = Regex("[$DIGITS]{2,}(?:[$FILLER_CHARS$PUNCT]+[$DIGITS]{2,})*")
    private val mobile = Regex("1[3-9]\\d{9}")

    fun isFiller(text: String): Boolean = fillerOnly.matches(text)

    /**
     * A phone number read out in groups ("178。嗯。1855。嗯。4702。", or "幺八幺，一八五五，四七零二") arrives as
     * separate numbers with fillers and full stops between them; show it as one number. Only a run that is
     * exactly a mainland mobile number is joined, so "100。200。300" or a date is never merged.
     */
    fun display(text: String): String = digitRun.replace(text) { run ->
        val digits = run.value.mapNotNull(::digitOf).joinToString("")
        if (mobile.matches(digits)) digits else run.value
    }

    private fun digitOf(c: Char): Char? = when {
        c in '0'..'9' -> c
        c == '幺' -> '1'
        else -> CN_DIGITS.indexOf(c).takeIf { it >= 0 }?.let { "00123456789"[it] }
    }
}
