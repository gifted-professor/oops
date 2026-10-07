package com.oops.app.engine

import com.oops.app.backend.ActionDraft
import com.oops.app.backend.ActionResult
import com.oops.app.buffer.Utterance
import com.oops.app.recall.RecallCard

data class AsrGap(val fromMs: Long, val toMs: Long)

data class EngineState(
    val capturing: Boolean = false,
    val source: String = "mic",
    val sessionId: String = "",
    val sessionMs: Long = 0,
    val bufferedMs: Long = 0,
    /** off | unconfigured | connecting | live | reconnecting | error */
    val asr: String = "off",
    val asrDetail: String? = null,
    val level: Float = 0f,
    val playing: Boolean = false,
    val savedCount: Int = 0,
    val backend: String = "mock",
    val backendUrl: String = "",
    val speakerInfo: Boolean = false,
    val testWavName: String = "内置合成对话",
    /** Live state of an external source, e.g. "Mac 收音：Wireless microphone". */
    val sourceDetail: String? = null,
    val remoteAddress: String = "127.0.0.1:7790",
    /** Session spans that were recorded but not transcribed (ASR down). */
    val asrGaps: List<AsrGap> = emptyList(),
)

sealed interface OopsEvent {
    data class Partial(val text: String, val startMs: Long, val endMs: Long) : OopsEvent
    data class Final(val utterance: Utterance) : OopsEvent
    data class SpeakerIdentified(val utteranceId: String, val speaker: String, val score: Double) : OopsEvent
    data class Pruned(val ids: List<String>) : OopsEvent
    data class RecallPending(val trigger: String) : OopsEvent
    data class RecallReady(val card: RecallCard) : OopsEvent
    data class RecallNotFound(val reason: String) : OopsEvent
    data class Clip(val key: String, val state: String) : OopsEvent
    data class Saved(val cardId: String, val savedCount: Int) : OopsEvent
    data class DraftReady(val draft: ActionDraft) : OopsEvent
    data class ActionUpdate(val draftId: String, val result: ActionResult) : OopsEvent
    data class Latency(val recentMs: List<Long>, val medianMs: Long) : OopsEvent
    data class Toast(val message: String) : OopsEvent
    data class Failure(val scope: String, val message: String) : OopsEvent
}
