package com.oops.app.core

import android.content.Context
import androidx.core.content.edit
import com.oops.app.BuildConfig
import com.oops.app.asr.VolcCredentials

/** Debug-drawer settings layered over BuildConfig defaults from local.properties. */
class Settings(context: Context) {
    private val prefs = context.getSharedPreferences("oops_settings", Context.MODE_PRIVATE)

    var mockBackend: Boolean
        get() = prefs.getBoolean(KEY_MOCK, BuildConfig.BACKEND_BASE_URL.isBlank())
        set(value) = prefs.edit { putBoolean(KEY_MOCK, value) }

    var backendBaseUrl: String
        get() = prefs.getString(KEY_URL, null) ?: BuildConfig.BACKEND_BASE_URL
        set(value) = prefs.edit { putString(KEY_URL, value.trim()) }

    /** Off by default — see VolcRequest. */
    var speakerInfo: Boolean
        get() = prefs.getBoolean(KEY_SPEAKER, false)
        set(value) = prefs.edit { putBoolean(KEY_SPEAKER, value) }

    var source: String
        get() = prefs.getString(KEY_SOURCE, SOURCE_MIC) ?: SOURCE_MIC
        set(value) = prefs.edit { putString(KEY_SOURCE, value) }

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

        /** 预置团队成员（24h 原型：负责人从这里选，不接飞书通讯录）。Also used as ASR hotwords. */
        val TEAM = listOf("Jason", "黄丽萍", "江古月", "洪玉麟", "林春源")
        val HOTWORDS = listOf("Oops", "欧普斯") + TEAM

        private const val KEY_MOCK = "mock_backend"
        private const val KEY_URL = "backend_base_url"
        private const val KEY_SPEAKER = "speaker_info"
        private const val KEY_SOURCE = "source"
    }
}
