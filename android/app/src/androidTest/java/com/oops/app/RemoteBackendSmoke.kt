package com.oops.app

import android.app.Activity
import android.app.Instrumentation
import android.content.Intent
import com.oops.app.engine.OopsEngine
import android.os.Bundle
import com.oops.app.backend.*
import com.oops.app.core.Settings
import com.oops.app.store.ActionOperationStore
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File
import java.util.UUID
import java.util.concurrent.TimeUnit

/** Device integration check. Writes only with an explicit `-e confirm true`; one durable test operation. */
class RemoteBackendSmoke : Instrumentation() {
    private var arguments = Bundle()
    private var replaySettingsBefore: List<String>? = null

    override fun onCreate(arguments: Bundle?) {
        super.onCreate(arguments)
        this.arguments = arguments ?: Bundle()
        if (this.arguments.getString("speakerReplay") == "true") {
            val settings = Settings(targetContext)
            replaySettingsBefore = listOf(settings.source, settings.testWavUri, settings.testWavName)
        }
        start()
    }

    override fun onStart() {
        val report = Bundle()
        if (arguments.getString("speakerPreviewSmoke") == "true") {
            checkSpeakerPreview(report)
            return
        }
        if (arguments.getString("speakerReplay") == "true") {
            checkSpeakerReplay(report)
            return
        }
        if (arguments.getString("voiceprintSmoke") == "true") {
            checkVoiceprints(report)
            return
        }
        if (arguments.getString("sourceSwitch") == "true") {
            checkSourceSwitch(report)
            return
        }
        if (arguments.getString("remoteSmoke") != "true") {
            report.putBoolean("skipped", true)
            finish(Activity.RESULT_OK, report)
            return
        }
        try {
            runBlocking {
                val settings = Settings(targetContext)
                val base = settings.backendBaseUrl.trimEnd('/')
                check(base == "https://giftedprofessor.xyz/oops-meeting" && !settings.mockBackend)
                check(settings.backendToken.isNotBlank())
                val http = OkHttpClient.Builder().callTimeout(10, TimeUnit.SECONDS).build()
                http.newCall(Request.Builder().url("$base/healthz")
                    .header("Authorization", "Bearer ${settings.backendToken}").build()).execute().use {
                    check(it.isSuccessful)
                    val health = JSONObject(it.body.string())
                    check(health.getString("status") == "ok" && health.getString("feishu") == "live")
                }
                val api = HttpOopsApi(http, base, settings.backendToken)
                val c = Candidate("device-check-u1", 1000, 5000, null, "春源，你周五之前把方案发我。")
                val draft = api.draftAction(DraftRequest("device-server-check", listOf(c), emptyList()))
                check(draft.who == "林春源") { "Named assignment was not resolved" }
                check(draft.assigneeCandidates.toSet() == Settings.TEAM.toSet())
                report.putString("backend", base)
                report.putString("draft_owner", draft.who)
                val store = ActionOperationStore(File(targetContext.filesDir, "actions"))
                val previous = store.list().firstOrNull { it.backendUrl == base && it.request.draft.cardId == "server-connectivity-smoke" }
                if (arguments.getString("confirm") == "true") {
                    val operation = previous ?: run {
                        val id = UUID.randomUUID().toString()
                        val d = ActionDraft("server-smoke-$id", "server-connectivity-smoke", "林春源",
                            "[Oops服务器验收] 手机连接香港后端（测试）", null, null,
                            "合成连接验收任务，不是真实工作承诺。", Settings.TEAM,
                            Evidence(listOf(c.id), c.text, c.startMs, c.endMs))
                        ActionOperationStore.Operation(ConfirmRequest(id, "device-server-check", d), base, false,
                            ActionResult(id, ActionResult.PENDING)).also(store::save)
                    }
                    val result = ActionWorkflow.execute(api, operation.request,
                        submit = previous == null || ActionWorkflow.shouldSubmit(previous.result))
                    store.save(operation.copy(result = result))
                    check(result.status == ActionResult.CREATED && result.readBackConfirmed) { "Creation/read-back not confirmed: ${result.message}" }
                    val duplicate = api.confirmAction(operation.request)
                    check(duplicate.status == ActionResult.CREATED && duplicate.taskGuid == result.taskGuid)
                    report.putString("op_id", result.opId)
                    report.putString("task_guid", result.taskGuid)
                    report.putString("task_url", result.taskUrl)
                    report.putBoolean("read_back_confirmed", true)
                    report.putBoolean("duplicate_same_task", true)
                } else if (previous != null) {
                    val result = ActionWorkflow.execute(api, previous.request, submit = false)
                    check(result.status == ActionResult.CREATED && result.readBackConfirmed)
                    report.putString("task_guid", result.taskGuid)
                    report.putBoolean("read_back_confirmed", true)
                }
                report.putBoolean("passed", true)
            }
            finish(Activity.RESULT_OK, report)
        } catch (e: Exception) {
            report.putBoolean("passed", false)
            report.putString("error", e.message ?: e.javaClass.simpleName)
            finish(Activity.RESULT_CANCELED, report)
        }
    }
    /** Read-only live preview and actual ASR finals; never enrolls or creates tasks. */
    private fun checkSpeakerPreview(report: Bundle) {
        val settings = Settings(targetContext)
        val before = listOf(settings.source, settings.testWavUri, settings.testWavName, settings.speakerPreviewUrl)
        val wasEnabled = settings.speakerPreview
        try {
            runOnMainSync {
                val config = JSONObject().put("speakerPreview", true)
                arguments.getString("previewUrl")?.let {
                    config.put("speakerPreviewUrl", it).put("speakerPreviewToken", settings.backendToken)
                }
                OopsEngine.applySettings(config)
            }
            startActivitySync(Intent(targetContext, MainActivity::class.java)
                .setAction(MainActivity.ACTION_DEBUG_START).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                .putExtra("builtin", true).putExtra("source", "wav"))
            awaitState(25_000) { it.source == "wav" && it.sourceReady && it.liveSpeakerStatus in setOf("live", "silence") }
            report.putLong("first_preview_buffered_ms", OopsEngine.state.value.bufferedMs)
            report.putInt("first_preview_final_count", OopsEngine.transcriptSnapshot().size)
            awaitState(40_000) { OopsEngine.transcriptSnapshot().any { u -> u.speakerPreview != null } }
            val sentences = OopsEngine.transcriptSnapshot().filter { it.speakerPreview != null }
            check(sentences.all { u -> u.speakerPreview!!.forUtterance(u, Settings.TEAM.toSet()) != null })
            check(sentences.all { it.segments.isEmpty() }) { "Experiment entered accepted segments" }
            check(sentences.none { ApiJson.candidate(it.toCandidate()).has("speakerPreview") })
            report.putBoolean("live_preview", true)
            report.putBoolean("final_preview_preserves_asr", true)
            report.putBoolean("display_only", true)
            report.putString("sentences", org.json.JSONArray(sentences.map { com.oops.app.bridge.BridgeJson.utterance(it) }).toString())
            report.putBoolean("passed", true)
            sendStatus(1, report)
            Thread.sleep(6000)
        } catch (e: Exception) {
            report.putBoolean("passed", false)
            report.putString("error", e.message ?: e.javaClass.simpleName)
        } finally {
            runOnMainSync {
                OopsEngine.stopCapture()
                settings.testWavUri = before[1]
                settings.testWavName = before[2]
                OopsEngine.applySettings(JSONObject().put("source", before[0])
                    .put("speakerPreview", wasEnabled).put("speakerPreviewUrl", before[3]))
            }
        }
        finish(if (report.getBoolean("passed")) Activity.RESULT_OK else Activity.RESULT_CANCELED, report)
    }
    /** Replays an explicitly supplied recording through the actual ASR -> sentence -> speaker path. */
    private fun checkSpeakerReplay(report: Bundle) {
        val settings = Settings(targetContext)
        val before = requireNotNull(replaySettingsBefore)
        try {
            awaitState(25_000) { OopsEngine.transcriptSnapshot().any { u -> u.text.contains("声音") } }
            awaitState(25_000) {
                val sentences = OopsEngine.transcriptSnapshot()
                sentences.size >= 3 && sentences.all { u -> u.speakerState != null && u.speakerState != "pending" }
            }
            val sentences = OopsEngine.transcriptSnapshot()
            val last = sentences.last { it.text.contains("声音") }
            check(last.speaker == "洪玉麟" && last.speakerState == "matched") { "Last sentence was skipped: $last" }
            check(sentences.none { it.speakerState == "audio_unavailable" })
            check(sentences.any { it.speakerState == "unconfirmed" && it.speaker == null && it.speakerScore != null })
            report.putBoolean("passed", true)
            report.putString("speaker", last.speaker)
            report.putString("sentences", org.json.JSONArray(sentences.map { com.oops.app.bridge.BridgeJson.utterance(it) }).toString())
            sendStatus(1, report)
            Thread.sleep(6000) // Keep the verified screen available for the host's evidence screenshot.
        } catch (e: Exception) {
            report.putBoolean("passed", false)
            report.putString("error", e.message ?: e.javaClass.simpleName)
        } finally {
            settings.source = before[0]
            settings.testWavUri = before[1]
            settings.testWavName = before[2]
        }
        finish(if (report.getBoolean("passed")) Activity.RESULT_OK else Activity.RESULT_CANCELED, report)
    }

    private fun checkVoiceprints(report: Bundle) {
        try {
            awaitState { it.capturing && it.source == "mic" && it.sourceReady }
            runOnMainSync { OopsEngine.loadVoiceprints() }
            awaitState { it.voiceprint.phase == "idle" && it.voiceprint.catalog != null }
            val before = requireNotNull(OopsEngine.state.value.voiceprint.catalog)
            check(before.getJSONArray("people").length() == 4)
            check(!before.toString().contains("vector"))
            runOnMainSync {
                check(runCatching { OopsEngine.beginVoiceprintRecording("林春源", false, "enroll") }.isFailure)
                check(OopsEngine.state.value.voiceprint.phase == "idle")
                OopsEngine.beginVoiceprintRecording("", true, "test")
            }
            awaitState(25_000) { it.voiceprint.phase == "idle" && it.voiceprint.diagnosis != null }
            val diagnosis = requireNotNull(OopsEngine.state.value.voiceprint.diagnosis)
            check(diagnosis.getJSONArray("scores").length() == 4)
            check(diagnosis.getJSONObject("quality").getDouble("seconds") == 10.0)
            runOnMainSync { OopsEngine.loadVoiceprints() }
            awaitState { it.voiceprint.phase == "idle" }
            check(before.getString("revision") == OopsEngine.state.value.voiceprint.catalog?.getString("revision"))
            report.putBoolean("passed", true)
            report.putBoolean("consent_required", true)
            report.putBoolean("profiles_unchanged", true)
            report.putBoolean("phone_recording_and_server_diagnosis", true)
            report.putString("reason", diagnosis.getString("reason"))
            finish(Activity.RESULT_OK, report)
        } catch (e: Exception) {
            report.putBoolean("passed", false)
            report.putString("error", e.message ?: e.javaClass.simpleName)
            finish(Activity.RESULT_CANCELED, report)
        }
    }

    private fun checkSourceSwitch(report: Bundle) {
        val originalAddress = Settings(targetContext).remoteAddress
        try {
            awaitState { it.capturing && it.source == "remote" && !it.sourceReady && it.sourceDetail?.contains("未连接") == true }
            val session = OopsEngine.state.value.sessionId
            repeat(3) { index ->
                runOnMainSync { OopsEngine.applySettings(JSONObject().put("source", "mic")) }
                awaitState { it.capturing && it.source == "mic" && it.sourceDetail == null && it.sourceReady && it.bufferedMs > index * 100 }
                check(OopsEngine.state.value.sessionId == session) { "Switch erased the session" }
                // Let the old Mac retry interval pass; no old callback may overwrite the mic.
                Thread.sleep(1200)
                check(OopsEngine.state.value.source == "mic" && OopsEngine.state.value.sourceDetail == null)
                if (index < 2) {
                    val before = OopsEngine.state.value.bufferedMs
                    runOnMainSync { OopsEngine.applySettings(JSONObject().put("source", "remote")) }
                    awaitState { it.source == "remote" && !it.sourceReady && it.sourceDetail?.contains("未连接") == true }
                    check(OopsEngine.state.value.bufferedMs >= before) { "Switch dropped buffered audio" }
                }
            }
            arguments.getString("macAddress")?.let { address ->
                runOnMainSync { OopsEngine.applySettings(JSONObject().put("source", "remote").put("remoteAddress", "127.0.0.1:1")) }
                awaitState { it.source == "remote" && !it.sourceReady && it.sourceDetail?.contains("未连接") == true }
                val before = OopsEngine.state.value.bufferedMs
                // Changing the Mac endpoint while staying in remote mode must reconnect immediately.
                runOnMainSync { OopsEngine.applySettings(JSONObject().put("remoteAddress", address)) }
                awaitState { it.source == "remote" && it.sourceReady && it.sourceDetail?.contains("Wireless microphone") == true && it.bufferedMs > before }
                report.putBoolean("mac_connected", true)
                runOnMainSync { OopsEngine.applySettings(JSONObject().put("source", "mic")) }
                awaitState { it.source == "mic" && it.sourceReady && it.sourceDetail == null }
            }
            // A finalized sentence and its audio must survive switching away from test WAV.
            val wavSettings = Settings(targetContext)
            val originalWavUri = wavSettings.testWavUri
            val originalWavName = wavSettings.testWavName
            runOnMainSync {
                OopsEngine.selectTestWav("", "内置合成对话")
                OopsEngine.applySettings(JSONObject().put("source", "wav"))
            }
            awaitState { it.source == "wav" && it.sourceReady && OopsEngine.transcriptSnapshot().any { u -> u.text.contains("预算不能超过") } }
            val ids = OopsEngine.transcriptSnapshot().map { it.id }.toSet()
            val buffered = OopsEngine.state.value.bufferedMs
            runOnMainSync { OopsEngine.applySettings(JSONObject().put("source", "mic")) }
            awaitState { it.source == "mic" && it.sourceReady && it.sourceDetail == null && it.bufferedMs > buffered }
            check(OopsEngine.state.value.sessionId == session)
            check(OopsEngine.transcriptSnapshot().map { it.id }.containsAll(ids)) { "Switch dropped finalized sentences" }
            wavSettings.testWavUri = originalWavUri
            wavSettings.testWavName = originalWavName
            report.putBoolean("transcript_preserved", true)
            report.putBoolean("passed", true)
            report.putBoolean("session_preserved", true)
            report.putString("source", OopsEngine.state.value.source)
            report.putLong("buffered_ms", OopsEngine.state.value.bufferedMs)
        } catch (e: Exception) {
            report.putBoolean("passed", false)
            report.putString("error", e.message ?: e.javaClass.simpleName)
        } finally {
            runOnMainSync { OopsEngine.applySettings(JSONObject().put("remoteAddress", originalAddress)) }
        }
        finish(if (report.getBoolean("passed")) Activity.RESULT_OK else Activity.RESULT_CANCELED, report)
    }

    private fun awaitState(timeoutMs: Long = 8_000, predicate: (com.oops.app.engine.EngineState) -> Boolean) {
        val end = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMs)
        while (!predicate(OopsEngine.state.value)) {
            check(System.nanoTime() < end) { "Timed out: source=${OopsEngine.state.value.source}, detail=${OopsEngine.state.value.sourceDetail}" }
            Thread.sleep(50)
        }
    }

}
