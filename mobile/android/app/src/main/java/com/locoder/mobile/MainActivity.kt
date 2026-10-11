package com.locoder.mobile

import android.annotation.SuppressLint
import android.app.Activity
import android.graphics.Color
import android.os.Bundle
import android.webkit.WebView
import android.webkit.WebViewClient

/**
 * A thin wrapper: the whole UI is the web app in assets/ (mobile/prototype),
 * shown full-screen in a WebView. The Android back button is handed to the
 * page first (window.onAndroidBack) so it can close a sheet or leave a chat
 * before the app itself closes. Notifications go through [Notifier], which
 * the page sees as window.LocoderAndroid.
 */
class MainActivity : Activity() {

    private lateinit var webView: WebView
    private lateinit var notifier: Notifier

    @SuppressLint("SetJavaScriptEnabled", "JavascriptInterface")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        notifier = Notifier(this)
        webView = WebView(this).apply {
            setBackgroundColor(Color.parseColor("#0D0D0D"))
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            // Links and navigation stay inside the app.
            webViewClient = WebViewClient()
            // Only our own bundled page is ever loaded, so exposing this is safe.
            addJavascriptInterface(notifier, "LocoderAndroid")
        }
        setContentView(webView)
        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState)
        } else {
            webView.loadUrl("file:///android_asset/index.html")
        }
    }

    override fun onResume() {
        super.onResume()
        notifier.inForeground = true
        notifier.clearAll()
    }

    override fun onPause() {
        // The page keeps running in the background (the WebView isn't paused),
        // so a task can still finish and notify.
        notifier.inForeground = false
        super.onPause()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        webView.saveState(outState)
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
}
