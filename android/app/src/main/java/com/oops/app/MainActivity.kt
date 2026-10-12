package com.oops.app

import android.annotation.SuppressLint
import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.OpenableColumns
import java.io.File
import android.util.Log
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.RenderProcessGoneDetail
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.net.toUri
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import androidx.webkit.WebViewAssetLoader
import androidx.webkit.WebViewClientCompat
import com.oops.app.bridge.BridgeJson
import com.oops.app.bridge.WebBridge
import com.oops.app.core.Settings
import com.oops.app.engine.OopsEngine
import com.oops.app.service.CaptureService
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

/** Native shell: hosts the H5 UI in a WebView and routes bridge messages to [OopsEngine]. */
class MainActivity : ComponentActivity() {
    private lateinit var webView: WebView
    private lateinit var bridge: WebBridge
    private var insets = JSONObject().put("top", 0).put("bottom", 0)
    private var pendingSource: String? = null
    private var pendingVoiceprintPage = false

    private val permissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
            val source = pendingSource ?: return@registerForActivityResult
            pendingSource = null
            if (hasPermission(Manifest.permission.RECORD_AUDIO)) {
                CaptureService.start(this, source)
            } else {
                OopsEngine.cancelVoiceprintRecording()
                bridge.send("error", JSONObject().put("scope", "permission").put("message", "需要麦克风权限才能收音"))
            }
        }

    private val audioPicker = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) {
            runCatching {
                contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
                val name = contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
                    if (cursor.moveToFirst()) cursor.getString(0) else "外部 WAV"
                } ?: "外部 WAV"
                OopsEngine.selectTestWav(uri.toString(), name)
            }.onFailure { bridge.send("error", JSONObject().put("scope", "settings").put("message", it.message)) }
        }
    }

    @SuppressLint("SetJavaScriptEnabled", "MissingOnRenderProcessGone") // Bundled H5 only; bridge is restricted to the asset origin.
    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)

        webView = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
        }
        setContentView(webView)
        // Back closes the open sheet, then steps back through the pages; only from the home page it leaves the app
        // (moveTaskToBack: a running capture and its UI stay as they are).
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                webView.evaluateJavascript("(window.OopsBack ? window.OopsBack() : false)") { handled ->
                    if (handled != "true") moveTaskToBack(true)
                }
            }
        })
        bridge = WebBridge(webView, ::onBridgeMessage) // before any callback below can use it

        // The page draws edge to edge and pads itself with these (CSS px).
        ViewCompat.setOnApplyWindowInsetsListener(webView) { _, windowInsets ->
            val bars = windowInsets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            val density = resources.displayMetrics.density
            insets = JSONObject().put("top", bars.top / density).put("bottom", bars.bottom / density)
            bridge.send("insets", insets)
            windowInsets
        }

        val assetLoader = WebViewAssetLoader.Builder()
            .addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this))
            .build()
        webView.webViewClient = object : WebViewClientCompat() {
            override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
                Log.w(TAG, "WebView renderer stopped, reloading UI; capture service remains active")
                // A dead renderer's WebView must not be reused. The new page receives a fresh snapshot.
                finish()
                startActivity(Intent(this@MainActivity, MainActivity::class.java))
                return true
            }

            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest) =
                assetLoader.shouldInterceptRequest(request.url)

            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                if (request.url.toString().startsWith(WebBridge.ORIGIN)) return false
                openExternal(request.url.toString()) // e.g. the Feishu task link
                return true
            }
        }

        if (!bridge.install()) {
            webView.loadData("<h3>当前系统 WebView 版本过旧，请先更新 Android System WebView。</h3>", "text/html; charset=utf-8", "UTF-8")
            return
        }
        webView.loadUrl(WebBridge.START_URL)

        lifecycleScope.launch { OopsEngine.state.collect { bridge.send("state", BridgeJson.state(it)) } }
        lifecycleScope.launch {
            OopsEngine.events.collect { event ->
                val (type, payload) = BridgeJson.event(event)
                bridge.send(type, payload)
            }
        }
        if (savedInstanceState == null) handleIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    override fun onDestroy() {
        webView.destroy()
        super.onDestroy()
    }

    /** Notification "Oops 一下", plus adb hooks in debug builds for scripted end-to-end runs. */
    private fun handleIntent(intent: Intent?) {
        when (intent?.action) {
            ACTION_RECALL -> OopsEngine.triggerRecall("notification", intent.getStringExtra(EXTRA_QUERY))
            ACTION_DEBUG_START -> if (BuildConfig.DEBUG) {
                // Scripted setup, e.g. --es remote 127.0.0.1:7790 --es backend http://127.0.0.1:8787 --ez mock false
                val debugSettings = JSONObject()
                intent.getStringExtra("remote")?.let { debugSettings.put("remoteAddress", it) }
                intent.getStringExtra("backend")?.let { debugSettings.put("backendBaseUrl", it) }
                if (intent.hasExtra("mock")) debugSettings.put("mockBackend", intent.getBooleanExtra("mock", true))
                if (debugSettings.length() > 0) OopsEngine.applySettings(debugSettings)
                if (intent.getBooleanExtra("builtin", false)) {
                    OopsEngine.selectTestWav("", "内置合成对话")
                }
                intent.getStringExtra("wav_path")?.let { path ->
                    val file = File(path).canonicalFile
                    val allowed = listOf(File(filesDir, "tests"), File(getExternalFilesDir(null), "tests"))
                        .map { it.canonicalFile }
                    require(allowed.any { file.path.startsWith(it.path + File.separator) }) {
                        "测试 WAV 必须放在 App 自己的 files/tests 目录"
                    }
                    OopsEngine.selectTestWav(Uri.fromFile(file).toString(), file.name)
                }
                requestCapture(intent.getStringExtra(EXTRA_SOURCE) ?: OopsEngine.settings.source)
            }
            ACTION_DEBUG_VOICEPRINT -> if (BuildConfig.DEBUG) {
                pendingVoiceprintPage = true
                bridge.send("voiceprint.open")
                OopsEngine.loadVoiceprints()
            }
            ACTION_DEBUG_STOP -> if (BuildConfig.DEBUG) CaptureService.stop(this)
        }
    }

    private fun onBridgeMessage(type: String, p: JSONObject) {
        try {
            when (type) {
                "hello" -> {
                    bridge.send(
                    "snapshot",
                    JSONObject()
                        .put("state", BridgeJson.state(OopsEngine.state.value))
                        .put("transcript", JSONArray(OopsEngine.transcriptSnapshot().map(BridgeJson::utterance)))
                        .put("summary", BridgeJson.summary(OopsEngine.sessionSummary()))
                        .put("team", JSONArray(Settings.TEAM))
                        .put("pendingActions", JSONArray(OopsEngine.pendingActions().map { (draft, result) ->
                            JSONObject().put("draft", BridgeJson.draft(draft))
                                .put("result", BridgeJson.actionResult(draft.draftId, result))
                        }))
                        .put("insets", insets),
                    )
                    if (pendingVoiceprintPage) {
                        pendingVoiceprintPage = false
                        bridge.send("voiceprint.open")
                    }
                }
                "capture.start" -> requestCapture(p.optString("source").ifBlank { OopsEngine.settings.source })
                "capture.stop" -> CaptureService.stop(this)
                "recall.trigger" -> OopsEngine.triggerRecall("button", p.optString("query").ifBlank { null })
                "ask.session" -> OopsEngine.askSession(p.getString("query"))
                "agenda.set" -> OopsEngine.setAgenda(p)
                "agenda.clear" -> OopsEngine.clearAgenda()
                "notice.dismiss" -> OopsEngine.dismissNotice(p.getString("id"))
                "notice.feedback" -> OopsEngine.noticeFeedback(p.getString("id"), p.getBoolean("useful"))
                "digest.openMany" -> OopsEngine.openDigestItems(p.getJSONArray("ids").strings())
                "history.list" -> OopsEngine.history("list")
                "history.open" -> OopsEngine.history("open", p.getString("sessionId"))
                "history.delete" -> OopsEngine.history("delete", p.getString("sessionId"), p.optBoolean("confirm"))
                "projects.list" -> OopsEngine.projects()
                "project.open" -> OopsEngine.projects(p.getString("name"))
                "meeting.project" -> OopsEngine.setMeetingProject(p.getString("sessionId"), p.getString("project"))
                "history.rename" -> OopsEngine.renameMeeting(p.getString("sessionId"), p.getString("title"))
                "history.search" -> OopsEngine.history("search", p.getString("query"))
                "digest.open", "commitment.open" -> OopsEngine.openCommitment(p.getString("utteranceId"))
                "clip.play" -> OopsEngine.playClip(p.getString("key"), p.getLong("startMs"), p.getLong("endMs"),
                    p.optString("sessionId").ifBlank { null })
                "clip.stop" -> OopsEngine.stopClip()
                "clip.remote" -> OopsEngine.playRemoteClip(p.getString("key"), p.getString("sessionId"), p.getLong("startMs"), p.getLong("endMs"))
                "speaker.correct" -> OopsEngine.correctSpeaker(p.getString("sessionId"), p.getString("utteranceId"),
                    if (p.isNull("speaker")) null else p.getString("speaker"))
                "review.set" -> OopsEngine.review(p.getString("sessionId"), p.getString("utteranceId"), p.getJSONObject("fields"))
                "tasks.list" -> OopsEngine.tasks(p.optBoolean("mine"), p.optString("status").ifBlank { "open" })
                "recall.save" -> OopsEngine.saveRecall(
                    p.getString("cardId"), p.getJSONArray("utteranceIds").strings(), p.getLong("startMs"), p.getLong("endMs"),
                )
                "recall.dismiss" -> OopsEngine.dismissRecall(p.optString("cardId"))
                "saved.list" -> bridge.send("saved.items", BridgeJson.saved(OopsEngine.savedItems()))
                "action.draft" -> OopsEngine.draftAction(p.getString("cardId"), p.getJSONArray("utteranceIds").strings())
                "action.ownerFeedback" -> OopsEngine.recordOwnerFeedback(p)
                "action.confirm" -> OopsEngine.confirmAction(
                    draftId = p.getString("draftId"),
                    who = p.optString("who"),
                    what = p.optString("what"),
                    whenIso = p.optString("whenIso").ifBlank { null },
                    whenRaw = p.optString("whenRaw").ifBlank { null },
                    constraint = p.optString("constraint").ifBlank { null },
                    ownerSelectionSource = p.optString("ownerSelectionSource", "user"),
                )
                "action.status" -> OopsEngine.queryAction(p.getString("draftId"))
                "testAudio.pick" -> audioPicker.launch(arrayOf("audio/wav", "audio/x-wav", "audio/*"))
                "testAudio.reset" -> OopsEngine.selectTestWav("", "内置合成对话")
                "voiceprint.list" -> OopsEngine.loadVoiceprints()
                "voiceprint.record" -> {
                    OopsEngine.beginVoiceprintRecording(p.optString("name"), p.optBoolean("consent"), p.optString("mode", "enroll"))
                    if (!OopsEngine.state.value.capturing) requestCapture(Settings.SOURCE_MIC)
                }
                "voiceprint.stop" -> OopsEngine.finishVoiceprintRecording()
                "voiceprint.save" -> OopsEngine.submitVoiceprintRecording()
                "voiceprint.cancel" -> OopsEngine.cancelVoiceprintRecording()
                "voiceprint.delete" -> {
                    require(p.optBoolean("confirm")) { "请确认删除声纹" }
                    OopsEngine.deleteVoiceprint(p.getString("name"))
                }
                "settings.set" -> OopsEngine.applySettings(p)
                "open.url" -> openExternal(p.getString("url"))
                "log" -> Log.d("OopsWeb", p.optString("message"))
                else -> Log.w(TAG, "unknown bridge message: $type")
            }
        } catch (e: Exception) {
            Log.w(TAG, "bridge message $type failed", e)
            bridge.send("error", JSONObject().put("scope", type).put("message", e.message ?: e.javaClass.simpleName))
        }
    }

    /** Only the mic is required; the notification permission is asked for alongside but never blocks. */
    private fun requestCapture(source: String) {
        val notifications = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            !hasPermission(Manifest.permission.POST_NOTIFICATIONS)) Manifest.permission.POST_NOTIFICATIONS else null
        if (hasPermission(Manifest.permission.RECORD_AUDIO)) {
            CaptureService.start(this, source)
            notifications?.let { permissionLauncher.launch(arrayOf(it)) }
        } else {
            pendingSource = source
            permissionLauncher.launch(listOfNotNull(Manifest.permission.RECORD_AUDIO, notifications).toTypedArray())
        }
    }

    private fun hasPermission(permission: String) =
        ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED

    private fun openExternal(url: String) {
        if (url.toUri().scheme !in setOf("http", "https")) return
        runCatching { startActivity(Intent(Intent.ACTION_VIEW, url.toUri())) }
            .onFailure { bridge.send("toast", JSONObject().put("message", "没有可以打开这个链接的应用")) }
    }

    private fun JSONArray.strings(): List<String> = (0 until length()).map { getString(it) }

    companion object {
        private const val TAG = "Oops"
        const val ACTION_RECALL = "com.oops.app.action.RECALL"
        const val ACTION_DEBUG_START = "com.oops.app.action.DEBUG_START"
        const val ACTION_DEBUG_VOICEPRINT = "com.oops.app.action.DEBUG_VOICEPRINT"
        const val ACTION_DEBUG_STOP = "com.oops.app.action.DEBUG_STOP"
        const val EXTRA_SOURCE = "source"
        const val EXTRA_QUERY = "query"
    }
}
