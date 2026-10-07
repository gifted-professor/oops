package com.oops.app.engine

import android.app.Application
import android.util.Base64
import com.oops.app.audio.VoiceprintCapture
import androidx.core.net.toUri
import android.os.SystemClock
import android.util.Log
import com.oops.app.asr.RawUtterance
import com.oops.app.asr.VolcAsrClient
import com.oops.app.asr.VolcRequest
import com.oops.app.audio.AudioSource
import com.oops.app.audio.MicAudioSource
import com.oops.app.audio.RemoteAudioSource
import com.oops.app.audio.WavFileAudioSource
import com.oops.app.backend.ActionDraft
import com.oops.app.backend.ActionWorkflow
import com.oops.app.backend.ActionResult
import com.oops.app.backend.ConfirmRequest
import com.oops.app.backend.DraftRequest
import com.oops.app.backend.Evidence
import com.oops.app.backend.HttpOopsApi
import com.oops.app.backend.MockOopsApi
import com.oops.app.backend.OopsApi
import com.oops.app.backend.RecallRequest
import com.oops.app.backend.RecallResponse
import com.oops.app.backend.SpeakerRequest
import com.oops.app.backend.toCandidate
import com.oops.app.buffer.AudioRingBuffer
import com.oops.app.buffer.TranscriptBuffer
import com.oops.app.buffer.Utterance
import com.oops.app.core.Pcm
import com.oops.app.core.Settings
import com.oops.app.playback.ClipPlayer
import com.oops.app.recall.CandidateWindow
import com.oops.app.recall.CardItem
import com.oops.app.recall.OopsCommand
import com.oops.app.recall.PickPolicy
import com.oops.app.recall.RecallCard
import com.oops.app.service.CaptureService
import com.oops.app.store.ActionOperationStore
import com.oops.app.store.SavedClipStore
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.Instant
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit

/**
 * AudioSource → 5-minute ring buffer + streaming ASR → transcript → recall → action.
 * Process-wide; CaptureService keeps the process alive while capturing.
 */
object OopsEngine {
    private const val TAG = "Oops"
    const val RETENTION_MS = 5 * 60_000L
    private const val CLIP_PAD_MS = 500L
    /** Shorter sentences carry too little voice to identify reliably. */
    private const val SPEAKER_MIN_MS = 1_000L
    private const val TEST_WAV = "test/dialog.wav"

    private lateinit var app: Application
    lateinit var settings: Settings
        private set
    private lateinit var store: SavedClipStore
    private lateinit var operations: ActionOperationStore
    private val submitted = ConcurrentHashMap<String, ActionOperationStore.Operation>()

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .callTimeout(10, TimeUnit.SECONDS)
        .build()

    /** The ASR socket can stay silent through long pauses: no read timeout. */
    private val asrHttp = http.newBuilder().readTimeout(0, TimeUnit.SECONDS).callTimeout(0, TimeUnit.SECONDS)
        .pingInterval(10, TimeUnit.SECONDS).build()

    private val ring = AudioRingBuffer((RETENTION_MS / 1000).toInt() * Pcm.SAMPLE_RATE)
    private val transcript = TranscriptBuffer(RETENTION_MS)
    private val sessionIndex = com.oops.app.buffer.SessionIndex()
    @Volatile private var optionalRequests = com.oops.app.backend.OptionalRequests()
    private val player = ClipPlayer(::onClipState)

    @Volatile private var api: OopsApi = MockOopsApi(Settings.TEAM)
    @Volatile private var previewApi: OopsApi = api

    private val _state = MutableStateFlow(EngineState())
    val state: StateFlow<EngineState> = _state.asStateFlow()
    private val _events = MutableSharedFlow<OopsEvent>(
        extraBufferCapacity = 256,
        onBufferOverflow = BufferOverflow.DROP_OLDEST,
    )
    val events: SharedFlow<OopsEvent> = _events.asSharedFlow()

    // Guarded by `pipeline`: each frame reaches the ring and the current ASR connection
    // atomically, so a connection's time zero is an exact offset on the session timeline.
    private val pipeline = Any()
    private var source: AudioSource? = null
    private var voiceprintCapture: VoiceprintCapture? = null
    private var voiceprintPcm: ByteArray? = null
    private var voiceprintClient: HttpOopsApi? = null
    private var voiceprintSampleId = ""
    private var voiceprintRevision = ""
    private var asr: VolcAsrClient? = null
    private var asrGeneration = 0
    private var asrDownSinceMs: Long? = null
    private var reconnectAttempt = 0
    private var reconnectJob: Job? = null
    private var tickJob: Job? = null
    private val previewBusy = java.util.concurrent.atomic.AtomicBoolean(false)
    private var previewEpoch = 0L
    private var previewAudioStartMs = 0L
    private var previewRequestedMs = 0L
    private var previewResultMs = 0L

    @Volatile private var sessionId = ""
    @Volatile private var level = 0f

    private val cards = ConcurrentHashMap<String, RecallCard>()
    private val drafts = ConcurrentHashMap<String, ActionDraft>()
    private val opIds = ConcurrentHashMap<String, String>() // draftId → op_id, reused on retry
    private val inFlight: MutableSet<String> = ConcurrentHashMap.newKeySet()
    private val recallLatencies = ArrayDeque<Long>()

    /** "Now" on the session timeline = audio received so far. */
    val nowMs: Long get() = Pcm.samplesToMs(ring.endSample)

    fun init(application: Application) {
        if (::app.isInitialized) return
        app = application
        settings = Settings(application)
        store = SavedClipStore(File(application.filesDir, "saved"))
        operations = ActionOperationStore(File(application.filesDir, "actions"))
        operations.list().forEach { operation ->
            val draft = operation.request.draft
            submitted[draft.draftId] = operation
            drafts[draft.draftId] = draft
            opIds[draft.draftId] = operation.request.opId
        }
        rebuildApi()
        _state.update {
            it.copy(
                source = settings.source,
                savedCount = store.count(),
                testWavName = settings.testWavName,
                remoteAddress = settings.remoteAddress,
            )
        }
    }

    // ---- capture -------------------------------------------------------------------------

    fun startCapture(kind: String) {
        synchronized(pipeline) {
            if (source != null) return
            settings.source = kind
            ring.reset()
            resetSpeakerPreview()
            transcript.clear()
            cards.clear()
            drafts.keys.removeAll { !submitted.containsKey(it) }
            sessionId = UUID.randomUUID().toString()
            sessionIndex.reset(sessionId)
            optionalRequests = com.oops.app.backend.OptionalRequests()
            reconnectAttempt = 0
            asrDownSinceMs = null
            val src = createAudioSource(kind)
            _state.update {
                it.copy(
                    capturing = true, source = kind, sessionId = sessionId, sessionMs = 0, bufferedMs = 0,
                    asr = "connecting", asrDetail = null, asrGaps = emptyList(), sourceDetail = null, sourceReady = false,
                    commitmentCount = 0, analysisPending = 0, analysisFailed = 0, tagsSupported = null, sessionTruncated = false,
                )
            }
            try {
                source = src
                openAsrLocked()
                src.start(audioListenerFor(src))
            } catch (e: Throwable) {
                Log.e(TAG, "capture failed to start", e)
                source = null
                src.stop()
                asr?.cancel()
                asr = null
                _state.update { it.copy(capturing = false, asr = "off", sourceDetail = null, sourceReady = false) }
                fail("capture", "无法开始收音：${e.message}")
                CaptureService.stop(app)
                return
            }
        }
        Log.i(TAG, "capture started: source=$kind session=$sessionId")
        tickJob?.cancel()
        tickJob = scope.launch {
            while (isActive) {
                delay(500)
                tick()
            }
        }
    }

    fun stopCapture() {
        val src: AudioSource
        val client: VolcAsrClient?
        synchronized(pipeline) {
            src = source ?: return
            source = null
            if (voiceprintCapture != null) cancelVoiceprintRecording()
            client = asr
            reconnectJob?.cancel()
        }
        src.stop()
        client?.finish() // the server flushes the sentence in progress, then closes
        tickJob?.cancel()
        player.stop()
        level = 0f
        _state.update { it.copy(capturing = false, level = 0f, sourceDetail = null, sourceReady = false, asr = if (client != null) it.asr else "off") }
        resetSpeakerPreview()
        if (client != null) {
            scope.launch {
                delay(5_000)
                synchronized(pipeline) {
                    if (asr === client) {
                        client.cancel()
                        asr = null
                        _state.update { it.copy(asr = "off") }
                    }
                }
            }
        }
        Log.i(TAG, "capture stopped")
    }

    private fun createAudioSource(kind: String): AudioSource = when (kind) {
        Settings.SOURCE_WAV -> WavFileAudioSource {
            val uri = settings.testWavUri
            if (uri.isBlank()) app.assets.open(TEST_WAV)
            else requireNotNull(app.contentResolver.openInputStream(uri.toUri())) { "无法打开测试 WAV" }
        }
        Settings.SOURCE_REMOTE -> {
            val (host, port) = Settings.parseAddress(settings.remoteAddress)
                ?: ("127.0.0.1" to RemoteAudioSource.DEFAULT_PORT)
            RemoteAudioSource(host, port)
        }
        else -> MicAudioSource()
    }

    /** Replace only the input; the timeline, transcript, retained clips and pending work survive. */
    private fun switchCaptureSource(kind: String) {
        val previous = synchronized(pipeline) {
            val active = source ?: return
            source = null // invalidate callbacks before closing the old worker
            _state.update { it.copy(sourceReady = false, sourceDetail = null, level = 0f) }
            active
        }
        previous.stop()
        val next = createAudioSource(kind)
        synchronized(pipeline) {
            source = next
            resetSpeakerPreview()
            level = 0f
            _state.update { it.copy(source = kind, sourceDetail = null, sourceReady = false, level = 0f) }
            try {
                // Flush the old input's unfinished sentence, keeping session coordinates intact.
                reconnectJob?.cancel()
                restartAsrLocked()
                next.start(audioListenerFor(next))
                Log.i(TAG, "capture source switched: ${previous.kind} -> $kind session=$sessionId")
            } catch (error: Exception) {
                Log.e(TAG, "source switch failed", error)
                fail("capture", "无法切换音源：${error.message}")
                CaptureService.stop(app)
            }
        }
    }

    private fun audioListenerFor(owner: AudioSource) = object : AudioSource.Listener {
        override fun onAudio(pcm: ByteArray) {
            synchronized(pipeline) {
                if (source !== owner) return
                _state.update { if (it.sourceReady) it else it.copy(sourceReady = true) }
                voiceprintCapture?.let { recording ->
                    val complete = recording.append(pcm)
                    _state.update { it.copy(voiceprint = it.voiceprint.copy(phase = "recording", elapsedMs = recording.elapsedMs)) }
                    if (complete) finishVoiceprintRecording()
                }
                ring.append(pcm)
                // 回听时给 ASR 送静音：外放的原声不会被再转写一遍，连接和时间轴也不断。
                asr?.sendAudio(if (player.isPlaying) ByteArray(pcm.size) else pcm)
                level = maxOf((Pcm.rms(pcm) * 5).toFloat().coerceAtMost(1f), level * 0.7f)
            }
        }

        override fun onMark(mark: AudioSource.Mark) {
            synchronized(pipeline) {
                if (source === owner && mark == AudioSource.Mark.DOUBLE_TAP) triggerRecall("double_tap", null)
            }
        }

        override fun onGap(missingMs: Long) {
            synchronized(pipeline) {
                if (source !== owner) return
                ring.appendSilence(Pcm.msToSamples(missingMs))
                resetSpeakerPreview()
                // ASR times are relative to the audio it received, so a gap needs a fresh connection.
                if (asrDownSinceMs == null) asrDownSinceMs = nowMs - missingMs
                asr?.cancel()
                asr = null
                openAsrLocked()
            }
        }

        override fun onStatus(detail: String?) {
            synchronized(pipeline) {
                if (source !== owner) return
                if (detail != _state.value.sourceDetail) Log.i(TAG, "source: ${detail ?: "-"}")
                _state.update { it.copy(sourceDetail = detail) }
            }
        }

        override fun onReady(ready: Boolean) {
            synchronized(pipeline) {
                if (source !== owner) return
                _state.update { it.copy(sourceReady = ready) }
                if (!ready) _state.update { it.copy(liveSpeakers = emptyList(), liveOverlap = false, liveSpeakerStatus = "waiting") }
            }
        }

        override fun onError(error: Throwable) {
            synchronized(pipeline) {
                if (source !== owner) return
                Log.e(TAG, "audio source failed", error)
                fail("capture", "收音中断：${error.message}")
                CaptureService.stop(app)
            }
        }
    }

    private fun tick() {
        val now = nowMs
        val removed = transcript.prune(now)
        if (removed.isNotEmpty()) {
            emit(OopsEvent.Pruned(removed.map { it.id }))
            Log.i(TAG, "pruned ${removed.size} utterance(s); retained=${transcript.snapshot().size}")
            val cutoff = now - RETENTION_MS
            cards.entries.removeAll { (_, card) -> card.items.all { it.endMs < cutoff && sessionIndex.get(it.utteranceId) == null } }
            drafts.entries.removeAll { (id, draft) -> !submitted.containsKey(id) && draft.evidence.clipEndMs < cutoff &&
                draft.evidence.utteranceIds.all { sessionIndex.get(it) == null } }
        }
        level *= 0.85f
        _state.update {
            it.copy(
                sessionMs = now,
                bufferedMs = Pcm.samplesToMs(ring.endSample - ring.startSample),
                level = level,
                playing = player.isPlaying,
            )
        }
        requestSpeakerPreview()
    }

    private fun resetSpeakerPreview() = synchronized(pipeline) {
        previewEpoch++
        previewAudioStartMs = nowMs
        previewRequestedMs = nowMs
        previewResultMs = 0
        _state.update { it.copy(liveSpeakerStatus = null, liveSpeakers = emptyList(), liveOverlap = false) }
    }

    /** One rolling display request at a time; ASR and the audio thread never wait for it. */
    private fun requestSpeakerPreview() {
        val client = previewApi
        if (!settings.speakerPreview || client is MockOopsApi) return
        val request = synchronized(pipeline) {
            val end = nowMs
            if (source == null || !_state.value.sourceReady || player.isPlaying) return
            if (previewResultMs > 0 && end - previewResultMs > 5000) {
                _state.update { it.copy(liveSpeakerStatus = "waiting", liveSpeakers = emptyList(), liveOverlap = false) }
            }
            if (end - previewAudioStartMs < 3000 || end - previewRequestedMs < 2000 || !previewBusy.compareAndSet(false, true)) return
            previewRequestedMs = end
            Triple(sessionId, previewEpoch, maxOf(previewAudioStartMs, end - 10000) to end)
        }
        val policy = optionalRequests
        scope.launch(Dispatchers.IO) {
            try {
                val (session, epoch, bounds) = request
                val result = policy.request("speaker-preview") {
                    val pcm = synchronized(pipeline) {
                        if (session != sessionId || epoch != previewEpoch) null else
                            ring.read(Pcm.msToSamples(bounds.first), Pcm.msToSamples(bounds.second))
                    } ?: throw SpeakerAudioUnavailable()
                    client.previewSpeakers(SpeakerRequest(session, "live", pcm, clipStartMs = bounds.first))
                }
                synchronized(pipeline) {
                    if (session != sessionId || epoch != previewEpoch || !settings.speakerPreview || !_state.value.sourceReady) return@synchronized
                    if (result == null) {
                        _state.update { it.copy(liveSpeakerStatus = "unsupported", liveSpeakers = emptyList(), liveOverlap = false) }
                    } else {
                        require(result.validFor(bounds.first, bounds.second, Settings.TEAM.toSet())) { "试验预览时间或身份无效" }
                        if (nowMs - bounds.second > 5000) return@synchronized
                        val recent = result.activities.filter { it.endMs > bounds.second - 1500 }
                        val overlap = recent.any { it.kind == "overlap" }
                        val names = if (overlap) emptyList() else recent.mapNotNull { it.speaker }.distinct()
                        previewResultMs = bounds.second
                        _state.update { it.copy(liveSpeakerStatus = if (recent.any { a -> a.kind != "silence" }) "live" else "silence",
                            liveSpeakers = names, liveOverlap = overlap) }
                        Log.i(TAG, "speaker preview: overlap=$overlap names=${names.joinToString()} window=${bounds.first}-${bounds.second}")
                    }
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                synchronized(pipeline) {
                    if (request.first == sessionId && request.second == previewEpoch) {
                        _state.update { it.copy(liveSpeakerStatus = "error", liveSpeakers = emptyList(), liveOverlap = false) }
                    }
                }
                Log.w(TAG, "speaker preview failed: ${error.javaClass.simpleName}")
            } finally { previewBusy.set(false) }
        }
    }

    // ---- streaming ASR ---------------------------------------------------------------------

    private fun openAsrLocked() {
        val credentials = settings.volc
        if (!credentials.isConfigured) {
            _state.update { it.copy(asr = "unconfigured", asrDetail = "android/local.properties 里还没配置火山 ASR 密钥") }
            return
        }
        val generation = ++asrGeneration
        val session = sessionId
        val baseMs = nowMs
        asrDownSinceMs?.let { down ->
            if (baseMs - down >= 300) _state.update { it.copy(asrGaps = (it.asrGaps + AsrGap(down, baseMs)).takeLast(10)) }
            asrDownSinceMs = null
        }
        val client = VolcAsrClient(
            asrHttp,
            credentials,
            VolcRequest.json(uid = session, speakerInfo = settings.speakerInfo, hotwords = Settings.HOTWORDS),
            object : VolcAsrClient.Listener {
                override fun onOpen() = onAsrOpen(generation)

                override fun onPartial(text: String, startMs: Long, endMs: Long) {
                    if (session == sessionId) emit(OopsEvent.Partial(text, baseMs + startMs, baseMs + endMs))
                }

                override fun onFinal(utterance: RawUtterance) = onAsrFinal(session, baseMs, utterance)

                override fun onClosed(error: String?, fatal: Boolean) = onAsrClosed(generation, error, fatal)
            },
        )
        asr = client
        _state.update { it.copy(asr = if (reconnectAttempt > 0) "reconnecting" else "connecting") }
        client.connect()
    }

    private fun onAsrOpen(generation: Int) {
        synchronized(pipeline) {
            if (generation != asrGeneration) return
            reconnectAttempt = 0
        }
        _state.update { it.copy(asr = "live", asrDetail = null) }
        Log.i(TAG, "ASR connected")
    }

    private fun onAsrFinal(session: String, baseMs: Long, raw: RawUtterance) {
        val u = synchronized(pipeline) {
            if (session != sessionId) return
            val shifted = raw.shift(baseMs)
            transcript.add(shifted.startMs, shifted.endMs, shifted.text, shifted.speakerId, shifted.words)
                .let { added -> if (api is MockOopsApi) added else
                    transcript.setSpeaker(added.id, added.speaker, speakerState = "pending")!! }
                .also { sessionIndex.add(session, it) }
        }
        Log.i(TAG, "final ${u.id} [${u.startMs}-${u.endMs}] ${u.text}")
        emit(OopsEvent.Final(u))
        tagUtterance(session, u)
        identifySpeaker(session, u)
    }

    private fun tagUtterance(session: String, u: Utterance) {
        val client = api
        val policy = optionalRequests
        val reservation = policy.tryReserve("tag") ?: run {
            synchronized(pipeline) {
                if (session == sessionId) _state.update { it.copy(analysisFailed = it.analysisFailed + 1) }
            }
            return
        }
        val context = sessionIndex.preceding(u.id, 4)
        if (!sessionIndex.beginAnalysis(session, u.id)) { reservation.close(); return }
        publishSessionSummary(session)
        scope.launch(Dispatchers.IO) {
            try {
                val request = com.oops.app.backend.TagRequest(session, u.toCandidate(), context.map { it.toCandidate() })
                val result = policy.request(reservation) { client.tagUtterance(request) }
                synchronized(pipeline) {
                    if (session != sessionId) return@synchronized
                    if (result == null) {
                        _state.update { it.copy(tagsSupported = false) }
                    } else {
                        require(result.utteranceId == u.id) { "分析结果句子编号不匹配" }
                        val updated = sessionIndex.setTag(session, u.id, result.tag, result.prob, result.engine)
                        transcript.setTag(u.id, result.tag, result.prob, result.engine)
                        _state.update { it.copy(tagsSupported = true) }
                        if (updated != null) {
                            if (result.tag == "commitment") {
                                val slice = ring.readAvailable(Pcm.msToSamples((u.startMs - CLIP_PAD_MS).coerceAtLeast(0)),
                                    Pcm.msToSamples(u.endMs + CLIP_PAD_MS))
                                if (slice != null && slice.first <= Pcm.msToSamples(u.startMs) &&
                                    slice.first + slice.second.size / 2 >= Pcm.msToSamples(u.endMs)) {
                                    sessionIndex.retainAudio(session, u.id, Pcm.samplesToMs(slice.first), slice.second)
                                }
                            }
                            emit(OopsEvent.UtteranceTagged(updated))
                        }
                    }
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                Log.w(TAG, "tag failed for ${u.id}: ${error.javaClass.simpleName}")
                synchronized(pipeline) {
                    if (session == sessionId) _state.update { it.copy(analysisFailed = it.analysisFailed + 1) }
                }
            } finally {
                sessionIndex.finishAnalysis(session, u.id)
                publishSessionSummary(session)
            }
        }.invokeOnCompletion { reservation.close() }
    }

    fun sessionSummary(): OopsEvent.SessionSummary {
        val summary = sessionIndex.summary()
        return OopsEvent.SessionSummary(summary.sessionId, summary.commitments, summary.audioIds)
    }

    private fun publishSessionSummary(session: String) {
        synchronized(pipeline) {
            if (session != sessionId) return
            _state.update { it.copy(commitmentCount = sessionIndex.discoveredCommitments,
                analysisPending = sessionIndex.pendingCount(), sessionTruncated = sessionIndex.truncated) }
        }
        // Copy/serialize the commitment list outside the audio pipeline lock.
        val summary = sessionSummary()
        if (summary.sessionId == session) emit(summary)
    }

    /** Asks the backend whose voice this sentence is; the caption gains a name when it answers. */
    private fun identifySpeaker(session: String, u: Utterance) {
        val client = api
        val policy = optionalRequests
        if (client is MockOopsApi) return
        if (u.endMs - u.startMs < SPEAKER_MIN_MS) {
            publishSpeakerMatch(session, u, null, "too_short")
            return
        }
        val reservation = policy.tryReserve("speaker") ?: run {
            publishSpeakerMatch(session, u, null, "busy")
            return
        }
        scope.launch(Dispatchers.IO) {
            runCatching { policy.request(reservation) {
                // A queued job retains only text/id; PCM is copied only with an execution permit.
                val pcm = synchronized(pipeline) {
                    if (session != sessionId) null else ring.readWithEndTolerance(Pcm.msToSamples(u.startMs),
                        Pcm.msToSamples(u.endMs), Pcm.msToSamples(Pcm.FRAME_MS.toLong()))
                }
                    ?: throw SpeakerAudioUnavailable()
                val request = SpeakerRequest(session, u.id, pcm, u.text, u.startMs, u.words,
                    sessionIndex.preceding(u.id, 4).map { it.toCandidate() }, settings.speakerPreview, u.endMs)
                val result = client.identifySpeaker(request)
                if (settings.speakerPreview && result != null && result.preview == null) {
                    val preview = runCatching { previewApi.previewSpeakers(request) }.getOrNull()
                    result.copy(preview = preview)
                } else result
            } }
                .onSuccess { result ->
                    publishSpeakerMatch(session, u, result, when {
                        result == null -> "unsupported"
                        result.speaker != null || result.usableSegments(u).isNotEmpty() -> "matched"
                        else -> "unconfirmed"
                    })
                }
                .onFailure { error ->
                    Log.w(TAG, "speaker id failed for ${u.id}: ${error.message}")
                    publishSpeakerMatch(session, u, null, if (error is SpeakerAudioUnavailable) "audio_unavailable" else "error")
                }
        }.invokeOnCompletion { reservation.close() }
    }

    private class SpeakerAudioUnavailable : IllegalStateException("声纹音频已过期或尚未完整到达")

    private fun publishSpeakerMatch(session: String, u: Utterance, result: com.oops.app.backend.SpeakerResult?, status: String) {
        synchronized(pipeline) {
            if (session != sessionId) return
            val name = result?.speaker ?: u.speaker
            val segments = result?.usableSegments(u).orEmpty()
            val score = result?.score?.takeIf { it.isFinite() }
            val preview = if (settings.speakerPreview) result?.preview?.forUtterance(u, Settings.TEAM.toSet()) else null
            sessionIndex.setSpeaker(session, u.id, name, segments, status, score, preview)
            transcript.setSpeaker(u.id, name, segments, status, score, preview)?.let {
                Log.i(TAG, "speaker ${u.id}: $status name=$name score=$score")
                emit(OopsEvent.SpeakerIdentified(u.id, name, score, segments, status, preview))
            }
        }
        publishSessionSummary(session)
    }

    private fun onAsrClosed(generation: Int, error: String?, fatal: Boolean) {
        synchronized(pipeline) {
            if (generation != asrGeneration) return
            asr = null
            if (source == null) {
                _state.update { it.copy(asr = "off", asrDetail = null) }
                return
            }
            if (asrDownSinceMs == null) asrDownSinceMs = nowMs
            if (fatal) {
                _state.update { it.copy(asr = "error", asrDetail = error) }
                fail("asr", "语音识别连接失败：$error")
                return
            }
            reconnectAttempt++
            val wait = minOf(1_000L shl minOf(reconnectAttempt - 1, 4), 15_000L)
            _state.update { it.copy(asr = "reconnecting", asrDetail = error ?: "连接已断开") }
            reconnectJob = scope.launch {
                delay(wait)
                synchronized(pipeline) {
                    if (source != null && asr == null && generation == asrGeneration) openAsrLocked()
                }
            }
        }
        Log.w(TAG, "ASR closed (fatal=$fatal): $error")
    }

    /** Applies new ASR options mid-session; the old connection still flushes its last sentence. */
    private fun restartAsrLocked() {
        if (source == null) return
        asr?.finish()
        asr = null
        openAsrLocked()
    }

    // ---- recall ----------------------------------------------------------------------------

    fun triggerRecall(trigger: String, query: String?) {
        if (!inFlight.add("recall")) {
            emit(OopsEvent.Toast("正在召回，请稍等"))
            return
        }
        val startedAt = SystemClock.elapsedRealtime()
        emit(OopsEvent.RecallPending(trigger))
        Log.i(TAG, "recall triggered by $trigger, query=$query")
        scope.launch {
            try {
                recall(trigger, query, startedAt)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                Log.w(TAG, "recall failed", e)
                fail("recall", "召回失败：${e.message ?: e.javaClass.simpleName}")
            } finally {
                inFlight.remove("recall")
            }
        }
    }

    private suspend fun recall(trigger: String, query: String?, startedAt: Long) {
        val now = nowMs
        val all = transcript.snapshot()
        // The recall command itself ("Oops，把刚刚那个想法留下来") is never evidence.
        val commands = all.filter { OopsCommand.matches(it.text) }.map { it.id }.toSet()
        var windowMs = 0L
        var candidates = emptyList<Utterance>()
        var response: RecallResponse? = null
        for (w in CandidateWindow.DEFAULT_WINDOWS_MS) {
            val inWindow = CandidateWindow.within(all, now, w, exclude = commands)
            if (inWindow.isEmpty() || inWindow.size == candidates.size) continue // nothing new to ask about
            windowMs = w
            candidates = inWindow
            val received = api.recall(RecallRequest(sessionId, trigger, query, now, inWindow.map { it.toCandidate() }))
            val byId = inWindow.associateBy { it.id }
            response = received.copy(picks = PickPolicy.validated(received.picks, byId.keys) { id ->
                val u = byId.getValue(id)
                u.endMs > u.startMs && ring.contains(Pcm.msToSamples(u.startMs), Pcm.msToSamples(u.endMs))
            })
            if (PickPolicy.isConfident(response.picks)) break
        }
        if (response == null) {
            val reason = if (all.isEmpty()) "还没有转写内容，先说几句话试试" else "最近 5 分钟内没有可召回的内容"
            emit(OopsEvent.RecallNotFound(reason))
            return
        }
        // 后端只返回编号：编号必须存在、时间有效、原声仍在缓存里。
        val byId = candidates.associateBy { it.id }
        val valid = response.picks.filter { pick ->
            val u = byId[pick.id]
            u != null && u.endMs > u.startMs &&
                ring.contains(Pcm.msToSamples(u.startMs), Pcm.msToSamples(u.endMs))
        }
        val decision = PickPolicy.decide(valid)
        if (decision == null) {
            emit(OopsEvent.RecallNotFound("没有找到对得上的原话"))
            return
        }
        val items = decision.picks.map { pick ->
            val u = byId.getValue(pick.id)
            CardItem(u.id, u.startMs, u.endMs, u.text, u.speaker, pick.prob)
        }
        val card = RecallCard(
            id = "c" + UUID.randomUUID().toString().take(8),
            trigger = trigger,
            query = query,
            nowMs = now,
            items = items,
            single = decision.single,
            context = transcript.neighbors(items.map { it.utteranceId }, before = 2, after = 2),
            engine = response.engine,
            windowMs = windowMs,
            latencyMs = SystemClock.elapsedRealtime() - startedAt,
        )
        cards[card.id] = card
        Log.i(TAG, "recall ${card.id} in ${card.latencyMs}ms via ${card.engine}: " + items.joinToString(" | ") { it.text })
        emit(OopsEvent.RecallReady(card))
        recordLatency(card.latencyMs)
    }

    private fun recordLatency(ms: Long) {
        val recent = synchronized(recallLatencies) {
            recallLatencies.addLast(ms)
            while (recallLatencies.size > 50) recallLatencies.removeFirst()
            recallLatencies.toList()
        }
        val sorted = recent.sorted()
        emit(OopsEvent.Latency(recent.takeLast(10), sorted[sorted.size / 2]))
    }

    fun playClip(key: String, startMs: Long, endMs: Long) {
        val slice = evidenceAudio(startMs, endMs)
        if (slice == null) {
            emit(OopsEvent.Clip(key, "expired"))
            return
        }
        player.play(key, slice)
    }

    private fun evidenceAudio(startMs: Long, endMs: Long): ByteArray? {
        if (ring.contains(Pcm.msToSamples(startMs), Pcm.msToSamples(endMs))) {
            return ring.readAvailable(Pcm.msToSamples((startMs - CLIP_PAD_MS).coerceAtLeast(0)),
                Pcm.msToSamples(endMs + CLIP_PAD_MS))?.second
        }
        return sessionIndex.audio(startMs, endMs)?.pcm
    }

    fun openCommitment(utteranceId: String) {
        val u = sessionIndex.get(utteranceId)?.takeIf { it.tag == "commitment" }
            ?: return fail("recall", "这条承诺已不在本场索引里")
        val card = RecallCard("c" + UUID.randomUUID().toString().take(8), "commitment", null, nowMs,
            listOf(CardItem(u.id, u.startMs, u.endMs, u.text, u.speaker, u.tagProb ?: 1.0)), true,
            sessionIndex.neighbors(listOf(u.id), before = 2, after = 2), u.tagEngine ?: "session", 0, 0)
        cards[card.id] = card
        emit(OopsEvent.RecallReady(card))
    }

    fun stopClip() = player.stop()

    private fun onClipState(key: String, state: String) {
        Log.i(TAG, "clip $key → $state")
        emit(OopsEvent.Clip(key, state))
        _state.update { it.copy(playing = player.isPlaying) }
    }

    /** 用户确认后才进入长期记忆：原句 + 原声写到 filesDir/saved。 */
    fun saveRecall(cardId: String, utteranceIds: List<String>, startMs: Long, endMs: Long) {
        val card = cards[cardId] ?: return fail("save", "这张卡片已失效")
        scope.launch(Dispatchers.IO) {
            val chosen = card.pool().filter { it.id in utteranceIds }.map { sessionIndex.get(it.id) ?: it }
            if (chosen.isEmpty()) {
                fail("save", "没有选中任何原话")
                return@launch
            }
            val pcm = evidenceAudio(startMs, endMs)
            val utterances = JSONArray(
                chosen.map {
                    com.oops.app.backend.ApiJson.candidate(it.toCandidate())
                },
            )
            val meta = JSONObject()
                .put("id", cardId)
                .put("saved_at", Instant.now().toString())
                .put("session_id", sessionId)
                .put("trigger", card.trigger)
                .put("source", "语音转写")
                .put("clip_start_ms", startMs)
                .put("clip_end_ms", endMs)
                .put("utterances", utterances)
                .put("audio_expired", pcm == null)
            store.save(cardId, meta, pcm)
            val count = store.count()
            _state.update { it.copy(savedCount = count) }
            Log.i(TAG, "saved $cardId: ${chosen.size} utterance(s), audio=${pcm != null}")
            emit(OopsEvent.Saved(cardId, count))
        }
    }

    fun dismissRecall(cardId: String) {
        cards.remove(cardId)
    }

    fun savedItems(): List<JSONObject> = store.list()

    fun transcriptSnapshot(): List<Utterance> = transcript.snapshot()

    // ---- action ----------------------------------------------------------------------------

    fun draftAction(cardId: String, utteranceIds: List<String>) {
        val card = cards[cardId] ?: return fail("action", "这张卡片已失效")
        if (!inFlight.add("draft:$cardId")) return
        scope.launch {
            try {
                val pool = card.pool().map { sessionIndex.get(it.id) ?: it }
                val chosen = pool.filter { it.id in utteranceIds }
                if (chosen.isEmpty()) {
                    fail("action", "没有选中任何原话")
                    return@launch
                }
                val chosenIds = chosen.map { it.id }.toSet()
                val context = (pool + sessionIndex.neighbors(chosenIds, before = 4, after = 1))
                    .distinctBy { it.id }
                    .filter { it.id !in chosenIds }
                    .sortedBy { it.startMs }
                val response = api.draftAction(
                    DraftRequest(sessionId, chosen.map { it.toCandidate() }, context.map { it.toCandidate() }),
                )
                val draft = ActionDraft(
                    draftId = "d" + UUID.randomUUID().toString().take(8),
                    cardId = cardId,
                    who = response.who.orEmpty(),
                    what = response.what,
                    whenIso = response.whenIso,
                    whenRaw = response.whenRaw,
                    constraint = response.constraint,
                    assigneeCandidates = response.assigneeCandidates.ifEmpty { Settings.TEAM },
                    evidence = Evidence(
                        utteranceIds = chosen.map { it.id },
                        quote = chosen.joinToString(" ") { it.text },
                        clipStartMs = chosen.first().startMs,
                        clipEndMs = chosen.last().endMs,
                    ),
                )
                drafts[draft.draftId] = draft
                Log.i(TAG, "draft ${draft.draftId}: who=${draft.who} what=${draft.what} when=${draft.whenIso}")
                emit(OopsEvent.DraftReady(draft))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                fail("action", "生成行动草稿失败：${e.message ?: e.javaClass.simpleName}")
            } finally {
                inFlight.remove("draft:$cardId")
            }
        }
    }

    /** Only a confirmed draft is written to Feishu. Retries reuse the draft's op_id. */
    fun confirmAction(
        draftId: String,
        who: String,
        what: String,
        whenIso: String?,
        whenRaw: String?,
        constraint: String?,
    ) {
        val base = drafts[draftId] ?: return fail("action", "这份草稿已失效")
        if (who.isBlank() || what.isBlank()) return fail("action", "请先填写负责人和事项")
        if (!inFlight.add("confirm:$draftId")) return // 防连点
        val existing = submitted[draftId]
        val opId = opIds.getOrPut(draftId) { UUID.randomUUID().toString() }
        val draft = existing?.request?.draft ?: base.copy(
            who = who.trim(), what = what.trim(), whenIso = whenIso?.ifBlank { null },
            whenRaw = whenRaw?.ifBlank { null }, constraint = constraint?.ifBlank { null },
        )
        val operation = existing ?: ActionOperationStore.Operation(
            ConfirmRequest(opId, sessionId, draft), settings.backendBaseUrl, api.name == "mock",
            ActionResult(opId, ActionResult.PENDING, mock = api.name == "mock"),
        )
        // Persist before the network call. A retry uses this frozen request, even after a restart.
        try { operations.save(operation) } catch (e: Exception) {
            inFlight.remove("confirm:$draftId")
            return fail("action", "无法保存操作编号：${e.message}")
        }
        submitted[draftId] = operation
        drafts[draftId] = draft
        runAction(operation, submit = ActionWorkflow.shouldSubmit(existing?.result))
    }

    fun queryAction(draftId: String) {
        val operation = submitted[draftId] ?: return fail("action", "没有已提交的操作")
        if (!inFlight.add("confirm:$draftId")) return
        runAction(operation, submit = false)
    }

    private fun runAction(operation: ActionOperationStore.Operation, submit: Boolean) {
        val request = operation.request
        val draftId = request.draft.draftId
        val client = if (operation.mock) api.takeIf { it.name == "mock" } ?: MockOopsApi(Settings.TEAM)
            else HttpOopsApi(http, operation.backendUrl, settings.backendTokenFor(operation.backendUrl))
        emit(OopsEvent.ActionUpdate(draftId,
            ActionResult(request.opId, ActionResult.PENDING, polling = true, mock = operation.mock)))
        scope.launch {
            val result = try {
                if (operation.result.status == ActionResult.CREATED && operation.mock) operation.result
                else ActionWorkflow.execute(client, request, submit)
            } finally {
                inFlight.remove("confirm:$draftId")
            }
            val updated = operation.copy(result = result.copy(mock = operation.mock))
            submitted[draftId] = updated
            runCatching { operations.save(updated) }.onFailure { Log.w(TAG, "action journal update failed", it) }
            Log.i(TAG, "action $draftId → ${result.status} op_id=${request.opId} readback=${result.readBackConfirmed}")
            emit(OopsEvent.ActionUpdate(draftId, updated.result))
        }
    }

    fun pendingActions(): List<Pair<ActionDraft, ActionResult>> = submitted.values
        .filter { it.result.status == ActionResult.PENDING }
        .map { it.request.draft to it.result.copy(polling = false, message = "上次操作结果尚未确认，请继续查询") }

    fun selectTestWav(uri: String, name: String) {
        settings.testWavUri = uri
        settings.testWavName = name
        settings.source = Settings.SOURCE_WAV
        _state.update { it.copy(testWavName = name, source = if (it.capturing) it.source else Settings.SOURCE_WAV) }
        emit(OopsEvent.Toast("已选择测试音频，下次开始收音时生效"))
    }

    // ---- self-service voiceprint recording -------------------------------------------------

    fun loadVoiceprints() {
        require(_state.value.voiceprint.phase !in setOf("saving", "testing")) { "请等待当前请求结束" }
        cancelVoiceprintRecording()
        val client = api as? HttpOopsApi ?: run { voiceprintError("请先连接真实服务器，Mock 不支持录入声纹"); return }
        val id = UUID.randomUUID().toString()
        synchronized(pipeline) {
            voiceprintSampleId = id
            _state.update { it.copy(voiceprint = it.voiceprint.copy(phase = "loading", message = "")) }
        }
        scope.launch {
            runCatching { client.voiceprints() }.onSuccess { catalog ->
                synchronized(pipeline) {
                    if (voiceprintSampleId == id) _state.update { it.copy(voiceprint = VoiceprintState(catalog = catalog)) }
                }
            }.onFailure { synchronized(pipeline) { if (voiceprintSampleId == id) voiceprintError(it.message ?: "加载失败") } }
        }
    }

    fun beginVoiceprintRecording(name: String, consent: Boolean, mode: String) {
        synchronized(pipeline) {
            require(consent) { "请确认本人同意上传声音用于声纹匹配" }
            require(mode in setOf("enroll", "test")) { "录音模式无效" }
            require(mode == "test" || name in Settings.TEAM) { "请先选择本人姓名" }
            require(_state.value.voiceprint.phase !in setOf("saving", "testing", "checking", "loading", "recording")) { "请等待当前操作结束" }
            val kind = source?.kind ?: Settings.SOURCE_MIC
            require(kind in setOf(Settings.SOURCE_MIC, Settings.SOURCE_REMOTE)) { "请先切换到手机或 Mac 麦克风，不能用测试 WAV 注册" }
            val client = api as? HttpOopsApi ?: error("请先连接真实服务器")
            val catalog = _state.value.voiceprint.catalog ?: error("请先刷新声纹列表")
            voiceprintClient = client
            voiceprintRevision = catalog.getString("revision")
            voiceprintSampleId = UUID.randomUUID().toString()
            voiceprintPcm = null
            voiceprintCapture = VoiceprintCapture(if (mode == "test") 10_000 else 30_000)
            _state.update { it.copy(voiceprint = VoiceprintState("recording", name, mode, source = kind, catalog = catalog)) }
        }
    }

    fun finishVoiceprintRecording() {
        synchronized(pipeline) {
            val recording = voiceprintCapture ?: return
            val elapsed = recording.elapsedMs
            voiceprintPcm = recording.finish()
            voiceprintCapture = null
            _state.update { it.copy(voiceprint = it.voiceprint.copy(phase = "review", elapsedMs = elapsed,
                message = if (elapsed < 15_000 && it.voiceprint.mode == "enroll") "录音不足 15 秒，请重新朗读" else "录音已完成")) }
            if (_state.value.voiceprint.mode == "test" && elapsed >= 1_000) submitVoiceprintRecording()
            else if (_state.value.voiceprint.mode == "enroll" && elapsed >= 15_000) checkVoiceprintRecording()
        }
    }

    private fun checkVoiceprintRecording() {
        val id = voiceprintSampleId
        val client = requireNotNull(voiceprintClient)
        val body = JSONObject().put("sample_rate", 16000).put("pcm_base64",
            Base64.encodeToString(requireNotNull(voiceprintPcm), Base64.NO_WRAP))
        _state.update { it.copy(voiceprint = it.voiceprint.copy(phase = "checking", message = "")) }
        scope.launch {
            runCatching { client.testVoiceprint(body) }.onSuccess { result ->
                synchronized(pipeline) {
                    if (id != voiceprintSampleId) return@synchronized
                    val q = result.getJSONObject("quality")
                    val passed = q.getDouble("seconds") >= 15 && q.getDouble("active_seconds") >= 8 && q.getDouble("clipped_ratio") <= .01
                    _state.update { it.copy(voiceprint = it.voiceprint.copy(phase = "review", quality = q, qualityPassed = passed,
                        message = if (passed) "音量与时长检查通过；保存时还会检查片段一致性" else "有效声音不足或音量过大，请靠近麦克风自然朗读后重录")) }
                }
            }.onFailure { error -> synchronized(pipeline) { if (id == voiceprintSampleId) _state.update {
                it.copy(voiceprint = it.voiceprint.copy(phase = "review", qualityPassed = false,
                    message = "质量检查失败：${error.message}，请放弃并重录"))
            } } }
        }
    }

    fun submitVoiceprintRecording() {
        val id: String
        val client: HttpOopsApi
        val body: JSONObject
        val testing: Boolean
        synchronized(pipeline) {
            val state = _state.value.voiceprint
            require(state.phase == "review") { "请先完成录音" }
            val pcm = voiceprintPcm ?: error("录音已清除，请重新录入")
            testing = state.mode == "test"
            require(testing || state.qualityPassed) { "录音质量尚未通过，请重新朗读" }
            require(pcm.size >= if (testing) 32000 else 15 * 32000) { "录音太短，请重新朗读" }
            client = requireNotNull(voiceprintClient)
            id = voiceprintSampleId
            body = JSONObject().put("name", state.name).put("source", state.source).put("sample_id", id)
                .put("sample_rate", 16000).put("pcm_base64", Base64.encodeToString(pcm, Base64.NO_WRAP))
                .put("consent", true).put("single_speaker", true).put("expected_revision", voiceprintRevision)
            _state.update { it.copy(voiceprint = state.copy(phase = if (testing) "testing" else "saving", message = "")) }
        }
        scope.launch {
            runCatching { if (testing) client.testVoiceprint(body) else client.enrollVoiceprint(body) }.onSuccess { result ->
                synchronized(pipeline) {
                    if (id != voiceprintSampleId) return@synchronized
                    voiceprintPcm = null
                    _state.update { it.copy(voiceprint = it.voiceprint.copy(phase = "idle",
                        catalog = if (testing) it.voiceprint.catalog else result,
                        diagnosis = if (testing) result else null,
                        message = if (testing) "匹配测试完成" else "声纹已保存，可换一段文字测试")) }
                }
            }.onFailure { error ->
                synchronized(pipeline) {
                    if (id == voiceprintSampleId) _state.update { it.copy(voiceprint = it.voiceprint.copy(phase = "review",
                        message = (error.message ?: "请求失败") + "；可重试同一录音，或刷新核对保存结果")) }
                }
            }
        }
    }

    fun deleteVoiceprint(name: String) {
        require(_state.value.voiceprint.phase == "idle") { "请等待当前操作结束" }
        require(name in Settings.TEAM) { "成员无效" }
        val client = api as? HttpOopsApi ?: error("请先连接真实服务器")
        val catalog = requireNotNull(_state.value.voiceprint.catalog)
        val id = UUID.randomUUID().toString()
        synchronized(pipeline) {
            voiceprintSampleId = id
            voiceprintCapture = null
            voiceprintPcm = null
            _state.update { it.copy(voiceprint = it.voiceprint.copy(phase = "saving", message = "")) }
        }
        scope.launch {
            runCatching { client.deleteVoiceprint(JSONObject().put("name", name).put("confirm", true)
                .put("expected_revision", catalog.getString("revision"))) }.onSuccess { result ->
                synchronized(pipeline) { if (id == voiceprintSampleId) _state.update {
                    it.copy(voiceprint = VoiceprintState(catalog = result, message = "已删除 $name 的声纹"))
                } }
            }.onFailure { synchronized(pipeline) { if (id == voiceprintSampleId) voiceprintError(it.message ?: "删除失败，请刷新核对") } }
        }
    }

    fun cancelVoiceprintRecording() {
        synchronized(pipeline) {
            voiceprintSampleId = UUID.randomUUID().toString()
            voiceprintCapture = null
            voiceprintPcm = null
            voiceprintClient = null
            _state.update { it.copy(voiceprint = VoiceprintState(catalog = it.voiceprint.catalog)) }
        }
    }

    private fun voiceprintError(message: String) {
        _state.update { it.copy(voiceprint = it.voiceprint.copy(phase = "idle", message = message)) }
    }

    // ---- settings --------------------------------------------------------------------------

    fun applySettings(json: JSONObject) {
        val previousBackend = settings.backendBaseUrl
        val previousMock = settings.mockBackend
        val activeKind = synchronized(pipeline) { source?.kind }
        val previousAddress = settings.remoteAddress
        if (json.has("source")) {
            val kind = json.getString("source")
            require(kind in setOf(Settings.SOURCE_MIC, Settings.SOURCE_WAV, Settings.SOURCE_REMOTE)) { "音源无效" }
            settings.source = kind
        }
        if (json.has("remoteAddress")) {
            val address = json.getString("remoteAddress").trim()
            if (Settings.parseAddress(address) != null) settings.remoteAddress = address
            else fail("settings", "Mac 收音地址格式应为 主机:端口，例如 127.0.0.1:7790")
        }
        if (json.has("backendBaseUrl")) settings.backendBaseUrl = json.getString("backendBaseUrl")
        if (json.has("backendToken")) settings.backendToken = json.getString("backendToken")
        if (json.has("mockBackend")) settings.mockBackend = json.getBoolean("mockBackend")
        val url = settings.backendBaseUrl
        if (!settings.mockBackend && !(url.startsWith("http://") || url.startsWith("https://"))) {
            settings.mockBackend = true
            fail("settings", "后端地址要以 http:// 或 https:// 开头，已切回 Mock")
        }
        val speakerChanged = json.has("speakerInfo") && json.getBoolean("speakerInfo") != settings.speakerInfo
        if (json.has("speakerInfo")) settings.speakerInfo = json.getBoolean("speakerInfo")
        if (json.has("speakerPreview")) settings.speakerPreview = json.getBoolean("speakerPreview")
        if (com.oops.app.BuildConfig.DEBUG && json.has("speakerPreviewUrl")) {
            val value = json.getString("speakerPreviewUrl").trim()
            require(value.isEmpty() || value.toUri().host in setOf("127.0.0.1", "localhost") && value.startsWith("http://")) {
                "独立试验分析地址仅允许本机回环"
            }
            settings.speakerPreviewUrl = value
            if (json.has("speakerPreviewToken") && value.isNotEmpty()) settings.setBackendTokenFor(value, json.getString("speakerPreviewToken"))
        }
        if (json.has("speakerPreview") || json.has("speakerPreviewUrl") || previousBackend != settings.backendBaseUrl || previousMock != settings.mockBackend) {
            optionalRequests = com.oops.app.backend.OptionalRequests()
            resetSpeakerPreview()
        }
        if (previousBackend != settings.backendBaseUrl || previousMock != settings.mockBackend) {
            cancelVoiceprintRecording()
            _state.update { it.copy(voiceprint = VoiceprintState(message = "服务器已切换，请刷新声纹列表")) }
        }
        rebuildApi()
        val sourceChanged = activeKind != null && ((json.has("source") && activeKind != settings.source) ||
            (activeKind == Settings.SOURCE_REMOTE && previousAddress != settings.remoteAddress))
        if (sourceChanged) {
            cancelVoiceprintRecording()
            switchCaptureSource(settings.source)
        }
        else if (speakerChanged) synchronized(pipeline) { restartAsrLocked() }
        emit(OopsEvent.Toast("设置已保存"))
    }

    private fun rebuildApi() {
        val url = settings.backendBaseUrl
        api = if (settings.mockBackend || url.isBlank()) MockOopsApi(Settings.TEAM) else HttpOopsApi(http, url, settings.backendToken)
        val previewUrl = if (com.oops.app.BuildConfig.DEBUG) settings.speakerPreviewUrl else ""
        previewApi = if (previewUrl.isBlank()) api else HttpOopsApi(http, previewUrl, settings.backendTokenFor(previewUrl))
        _state.update {
            it.copy(
                backend = api.name,
                backendUrl = url,
                hasBackendToken = settings.backendToken.isNotBlank(),
                speakerInfo = settings.speakerInfo,
                speakerPreviewEnabled = settings.speakerPreview,
                source = if (it.capturing) it.source else settings.source,
                remoteAddress = settings.remoteAddress,
            )
        }
    }

    private fun emit(event: OopsEvent) {
        _events.tryEmit(event)
    }

    private fun fail(scope: String, message: String) {
        Log.w(TAG, "$scope: $message")
        emit(OopsEvent.Failure(scope, message))
    }
}
