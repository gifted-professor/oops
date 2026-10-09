package com.oops.app.engine

import com.oops.app.backend.ActionDraft
import com.oops.app.backend.ActionResult
import com.oops.app.buffer.Utterance
import com.oops.app.recall.RecallCard
import org.json.JSONObject

data class AsrGap(val fromMs: Long, val toMs: Long)

data class EngineState(
    val voiceprint: VoiceprintState = VoiceprintState(),
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
    val hasBackendToken: Boolean = false,
    val speakerInfo: Boolean = false,
    val speakerPreviewEnabled: Boolean = false,
    val recordSession: Boolean = false,
    val recordingsPending: Int = 0,
    val recordingUpload: String = "idle",
    /** The 3-hour audio cap was reached: text continues, audio no longer is recorded. */
    val recordingCapped: Boolean = false,
    /** Another app is holding the microphone (Android silences our input); the transcript has a hole. */
    val micBusy: Boolean = false,
    val liveSpeakerStatus: String? = null,
    val liveSpeakers: List<String> = emptyList(),
    val liveOverlap: Boolean = false,
    val testWavName: String = "内置合成对话",
    /** Live state of an external source, e.g. "Mac 收音：Wireless microphone". */
    val sourceDetail: String? = null,
    /** Input has delivered PCM and has not subsequently disconnected. */
    val sourceReady: Boolean = false,
    val remoteAddress: String = "127.0.0.1:7790",
    /** Session spans that were recorded but not transcribed (ASR down). */
    val asrGaps: List<AsrGap> = emptyList(),
    val digestCounts: Map<String, Int> = emptyMap(),
    val agenda: JSONObject = JSONObject(),
    val notices: List<JSONObject> = emptyList(),
    val voiceTrigger: Boolean = true,
    val noticeVibrate: Boolean = true,
    val memorySync: Boolean = true,
    val selfName: String = "",
    val syncStatus: String = "idle",
    val syncPending: Int = 0,
    val commitmentCount: Int = 0,
    val analysisPending: Int = 0,
    val analysisFailed: Int = 0,
    val tagsSupported: Boolean? = null,
    val sessionTruncated: Boolean = false,
)

data class VoiceprintState(
    val phase: String = "idle", val name: String = "", val mode: String = "enroll",
    val elapsedMs: Long = 0, val source: String = "", val message: String = "",
    val catalog: JSONObject? = null, val diagnosis: JSONObject? = null,
    val quality: JSONObject? = null, val qualityPassed: Boolean = false,
)

sealed interface OopsEvent {
    data class Json(val type: String, val payload: JSONObject) : OopsEvent
    data class Partial(val text: String, val startMs: Long, val endMs: Long) : OopsEvent
    data class Final(val utterance: Utterance) : OopsEvent
    data class SpeakerIdentified(val utteranceId: String, val speaker: String?, val score: Double?,
        val segments: List<com.oops.app.buffer.SpeakerSegment> = emptyList(),
        val speakerState: String = if (speaker != null) "matched" else "unconfirmed",
        val preview: com.oops.app.buffer.SpeakerPreview? = null,
        val candidates: List<com.oops.app.buffer.SpeakerCandidate> = emptyList(),
        val trialSegments: List<com.oops.app.buffer.SpeakerSegment> = emptyList()) : OopsEvent
    data class UtteranceTagged(val utterance: Utterance) : OopsEvent
    data class SessionSummary(val sessionId: String, val commitments: List<Utterance>, val audioIds: Set<String>,
        val grouped: Map<String, List<Utterance>> = emptyMap(), val hints: Map<String, JSONObject> = emptyMap(), val notices: List<JSONObject> = emptyList()) : OopsEvent
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
