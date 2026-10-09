package com.oops.app.core

import android.content.Context
import androidx.core.content.edit
import com.oops.app.BuildConfig
import com.oops.app.asr.VolcCredentials

/** Debug-drawer settings layered over BuildConfig defaults from local.properties. */
class Settings(context: Context) {
    private val prefs = context.getSharedPreferences("oops_settings", Context.MODE_PRIVATE)

    var voiceTrigger: Boolean
        get() = prefs.getBoolean("voice_trigger", true)
        set(value) = prefs.edit { putBoolean("voice_trigger", value) }
    var noticeVibrate: Boolean
        get() = prefs.getBoolean("notice_vibrate", true)
        set(value) = prefs.edit { putBoolean("notice_vibrate", value) }
    var memorySync: Boolean
        get() = prefs.getBoolean("memory_sync", true)
        set(value) = prefs.edit { putBoolean("memory_sync", value) }
    /** Who uses this phone (a team name, or empty): picks "my" tasks. */
    var selfName: String
        get() = prefs.getString("self_name", "").orEmpty()
        set(value) = prefs.edit { putString("self_name", value) }
    var agendaJson: String
        get() = prefs.getString("agenda_json", "{}").orEmpty()
        set(value) = prefs.edit { putString("agenda_json", value) }

    var mockBackend: Boolean
        get() = prefs.getBoolean(KEY_MOCK, BuildConfig.BACKEND_BASE_URL.isBlank())
        set(value) = prefs.edit { putBoolean(KEY_MOCK, value) }

    var backendBaseUrl: String
        get() = prefs.getString(KEY_URL, null) ?: BuildConfig.BACKEND_BASE_URL
        set(value) = prefs.edit { putString(KEY_URL, value.trim()) }

    /** Credentials belong to one endpoint; pending operations never use another server's token. */
    fun backendTokenFor(url: String): String = prefs.getString("backend_token:${url.trim().trimEnd('/')}", "").orEmpty()

    fun setBackendTokenFor(url: String, value: String) = prefs.edit {
        putString("backend_token:${url.trim().trimEnd('/')}", value.trim())
    }

    var backendToken: String
        get() = backendTokenFor(backendBaseUrl)
        set(value) = prefs.edit { putString("backend_token:${backendBaseUrl.trim().trimEnd('/')}", value.trim()) }

    /** Off by default — see VolcRequest. */
    var speakerInfo: Boolean
        get() = prefs.getBoolean(KEY_SPEAKER, false)
        set(value) = prefs.edit { putBoolean(KEY_SPEAKER, value) }

    var speakerPreview: Boolean
        get() = prefs.getBoolean("speaker_preview", false)
        set(value) = prefs.edit { putBoolean("speaker_preview", value) }

    /** Keep each session's audio + finals in app storage for offline tuning (on unless turned off). */
    var recordSession: Boolean
        get() = prefs.getBoolean("record_session", true)
        set(value) = prefs.edit { putBoolean("record_session", value) }

    /** Optional debug-only analysis service; business calls keep their normal backend. */
    var speakerPreviewUrl: String
        get() = prefs.getString("speaker_preview_url", "").orEmpty()
        set(value) = prefs.edit { putString("speaker_preview_url", value.trim()) }

    var source: String
        get() = prefs.getString(KEY_SOURCE, SOURCE_MIC) ?: SOURCE_MIC
        set(value) = prefs.edit { putString(KEY_SOURCE, value) }

    /** Where [com.oops.app.audio.RemoteAudioSource] connects; 127.0.0.1 rides `adb reverse` over USB. */
    var remoteAddress: String
        get() = prefs.getString(KEY_REMOTE, DEFAULT_REMOTE_ADDRESS) ?: DEFAULT_REMOTE_ADDRESS
        set(value) = prefs.edit { putString(KEY_REMOTE, value.trim()) }

    var testWavUri: String
        get() = prefs.getString("test_wav_uri", "").orEmpty()
        set(value) = prefs.edit { putString("test_wav_uri", value) }

    var testWavName: String
        get() = prefs.getString("test_wav_name", "内置合成对话").orEmpty()
        set(value) = prefs.edit { putString("test_wav_name", value) }

    val volc: VolcCredentials
        get() = VolcCredentials(
            apiKey = BuildConfig.VOLC_API_KEY,
            appId = BuildConfig.VOLC_APP_ID,
            accessToken = BuildConfig.VOLC_ACCESS_TOKEN,
            resourceId = BuildConfig.VOLC_RESOURCE_ID,
        )

    companion object {
        const val SOURCE_MIC = "mic"
        const val SOURCE_WAV = "wav"
        const val SOURCE_REMOTE = "remote"
        const val DEFAULT_REMOTE_ADDRESS = "127.0.0.1:7790"

        /** "host:port" → pair, or null when malformed. */
        fun parseAddress(address: String): Pair<String, Int>? {
            val host = address.substringBeforeLast(':', "").trim()
            val port = address.substringAfterLast(':', "").trim().toIntOrNull()
            return if (host.isNotEmpty() && port != null && port in 1..65535) host to port else null
        }

        /** 预置团队成员（24h 原型：负责人从这里选，不接飞书通讯录）。Also used as ASR hotwords. */
        val TEAM = listOf("黄丽萍", "江古月", "洪玉麟", "林春源")
        val HOTWORDS = listOf("Oops", "欧普斯", "Lydia") + TEAM

        private const val KEY_MOCK = "mock_backend"
        private const val KEY_URL = "backend_base_url"
        private const val KEY_SPEAKER = "speaker_info"
        private const val KEY_SOURCE = "source"
        private const val KEY_REMOTE = "remote_address"
    }
}
