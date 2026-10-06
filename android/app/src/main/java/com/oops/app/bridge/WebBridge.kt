package com.oops.app.bridge

import android.annotation.SuppressLint
import android.util.Log
import android.webkit.WebView
import androidx.webkit.JavaScriptReplyProxy
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import org.json.JSONObject

/**
 * Native ↔ H5 channel (protocol: docs/bridge.md). JS talks through `window.OopsNative`,
 * exposed only to the bundled pages' origin; both directions carry `{type, payload}` JSON.
 */
class WebBridge(
    private val webView: WebView,
    private val onMessage: (type: String, payload: JSONObject) -> Unit,
) {
    private var reply: JavaScriptReplyProxy? = null

    @SuppressLint("RequiresFeature") // Feature checked immediately below before installing listener.
    fun install(): Boolean {
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
            Log.e(TAG, "WebView too old: WEB_MESSAGE_LISTENER unsupported")
            return false
        }
        WebViewCompat.addWebMessageListener(webView, "OopsNative", setOf(ORIGIN)) { _, message, _, isMainFrame, replyProxy ->
            if (!isMainFrame) return@addWebMessageListener
            reply = replyProxy // refreshed on every message, so a page reload rebinds automatically
            val json = message.data?.let { runCatching { JSONObject(it) }.getOrNull() } ?: return@addWebMessageListener
            onMessage(json.optString("type"), json.optJSONObject("payload") ?: JSONObject())
        }
        return true
    }

    /** Main thread only. Dropped until the page has said hello. */
    @SuppressLint("RequiresFeature") // reply exists only after a supported listener receives a message.
    fun send(type: String, payload: Any? = null) {
        val proxy = reply ?: return
        proxy.postMessage(JSONObject().put("type", type).put("payload", payload ?: JSONObject.NULL).toString())
    }

    companion object {
        private const val TAG = "OopsBridge"
        const val ORIGIN = "https://appassets.androidplatform.net"
        const val START_URL = "$ORIGIN/assets/web/index.html"
    }
}
