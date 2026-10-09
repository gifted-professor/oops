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
    private val digitRun = Regex("\\d{2,}(?:[$FILLER_CHARS$PUNCT]+\\d{2,})+")
    private val mobile = Regex("1[3-9]\\d{9}")

    fun isFiller(text: String): Boolean = fillerOnly.matches(text)

    /**
     * A phone number read out in groups ("178。嗯。1855。嗯。4702。") arrives as separate numbers with
     * fillers and full stops between them; show it as one number. Only a run that is exactly a mainland
     * mobile number is joined, so "100。200。300" or a date is never merged.
     */
    fun display(text: String): String = digitRun.replace(text) { run ->
        val digits = run.value.filter { it.isDigit() }
        if (mobile.matches(digits)) digits else run.value
    }
}
