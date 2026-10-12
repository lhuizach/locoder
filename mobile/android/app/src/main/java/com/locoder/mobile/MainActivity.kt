package com.locoder.mobile

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.webkit.WebView
import android.webkit.WebViewClient
import com.google.zxing.integration.android.IntentIntegrator
import org.json.JSONObject

/**
 * A thin wrapper: the whole UI is the web app (locoder/serve/web, bundled
 * as assets), shown full-screen in a WebView, talking to `locoder serve` on
 * your PC. The Android back button is handed to the page first
 * (window.onAndroidBack) so it can close a sheet or leave a chat before the
 * app itself closes. The page reaches Android through [Bridge].
 *
 * Leaving the app while a task runs starts [WatchService], which notifies
 * you when the task needs you or finishes; coming back stops it.
 */
class MainActivity : Activity() {

    private lateinit var webView: WebView
    private lateinit var notifications: Notifications
    private lateinit var bridge: Bridge
    private var pageReady = false
    private var pendingChat: String? = null

    @SuppressLint("SetJavaScriptEnabled", "JavascriptInterface")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        notifications = Notifications(this)
        webView = WebView(this).apply {
            setBackgroundColor(Color.parseColor("#0D0D0D"))
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            webViewClient = object : WebViewClient() {
                override fun onPageFinished(view: WebView, url: String) {
                    pageReady = true
                    pendingChat?.let { openChat(it) }
                    pendingChat = null
                }
            }
        }
        // Only our own bundled page is ever loaded, so exposing this is safe.
        bridge = Bridge(this, webView)
        webView.addJavascriptInterface(bridge, "LocoderAndroid")
        setContentView(webView)
        pendingChat = intent?.getStringExtra(EXTRA_CHAT)
        webView.loadUrl("file:///android_asset/index.html")
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        val chat = intent.getStringExtra(EXTRA_CHAT) ?: return
        if (pageReady) openChat(chat) else pendingChat = chat
    }

    private fun openChat(chat: String) {
        webView.evaluateJavascript("window.locoderOpenChat && window.locoderOpenChat(${JSONObject.quote(chat)})", null)
    }

    @Deprecated("Still the simplest hook on every API level this app supports")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        val scan = IntentIntegrator.parseActivityResult(requestCode, resultCode, data)
        if (scan == null) {
            @Suppress("DEPRECATION")
            super.onActivityResult(requestCode, resultCode, data)
            return
        }
        scan.contents?.let { bridge.deliverScan(it) }  // null: the scan was cancelled
    }

    override fun onResume() {
        super.onResume()
        WatchService.stop(this)
        notifications.clearAll()
    }

    override fun onPause() {
        // Still visible at this point, which is when Android lets an app
        // start a foreground service. Only while a task runs: nothing else
        // can need you.
        val prefs = Prefs(this)
        if (bridge.taskRunning && prefs.paired && prefs.notify && !isChangingConfigurations) {
            WatchService.start(this, bridge.computer)
        }
        super.onPause()
    }

    @Deprecated("Still the simplest hook on every API level this app supports")
    override fun onBackPressed() {
        webView.evaluateJavascript("window.onAndroidBack ? window.onAndroidBack() : false") { handled ->
            if (handled != "true") {
                @Suppress("DEPRECATION")
                super.onBackPressed()
            }
        }
    }

    override fun onDestroy() {
        webView.destroy()
        super.onDestroy()
    }

    companion object {
        const val EXTRA_CHAT = "chat"
    }
}
