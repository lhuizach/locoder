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
 * before the app itself closes.
 */
class MainActivity : Activity() {

    private lateinit var webView: WebView

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        webView = WebView(this).apply {
            setBackgroundColor(Color.parseColor("#0D0D0D"))
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            // Links and navigation stay inside the app.
            webViewClient = WebViewClient()
        }
        setContentView(webView)
        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState)
        } else {
            webView.loadUrl("file:///android_asset/index.html")
        }
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
