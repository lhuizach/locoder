package com.locoder.mobile

import android.Manifest
import android.app.Activity
import android.os.Build
import android.webkit.JavascriptInterface
import android.webkit.WebView
import com.google.zxing.integration.android.IntentIntegrator
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.NetworkInterface
import java.net.SocketTimeoutException
import org.json.JSONArray
import org.json.JSONObject

/**
 * What the web app can ask of Android, as `window.LocoderAndroid`: finding
 * the PC on the Wi-Fi, remembering the pairing for [WatchService], and the
 * notification switch and permission.
 */
class Bridge(private val activity: Activity, private val webView: WebView) {

    private val prefs = Prefs(activity)

    /** Whether a task is running on the PC (and the PC's name), as the page last said. */
    @Volatile
    var taskRunning = false
        private set

    @Volatile
    var computer: String? = null
        private set

    @JavascriptInterface
    fun setTaskRunning(running: Boolean, computer: String?) {
        taskRunning = running
        this.computer = computer
    }

    @JavascriptInterface
    fun deviceName(): String = "${Build.MANUFACTURER.replaceFirstChar { it.uppercase() }} ${Build.MODEL}"

    @JavascriptInterface
    fun setPairing(server: String, key: String) {
        prefs.server = server
        prefs.key = key
    }

    @JavascriptInterface
    fun setRemote(url: String) {
        prefs.remote = url
    }

    @JavascriptInterface
    fun clearPairing() {
        prefs.clear()
        WatchService.stop(activity)
    }

    @JavascriptInterface
    fun setNotify(on: Boolean) {
        prefs.notify = on
    }

    @JavascriptInterface
    fun hasPermission(): Boolean = Notifications(activity).hasPermission()

    /** Asks for the Android 13+ notification permission (no-op if already granted). */
    @JavascriptInterface
    fun requestPermission() {
        if (Build.VERSION.SDK_INT >= 33 && !hasPermission()) {
            activity.runOnUiThread {
                activity.requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
            }
        }
    }

    /**
     * Opens the QR scanner (ZXing). [MainActivity] gets the result and hands
     * it to [deliverScan], which passes it on to window.onLocoderScanned.
     */
    @JavascriptInterface
    fun scanQr() {
        activity.runOnUiThread {
            IntentIntegrator(activity)
                .setDesiredBarcodeFormats(IntentIntegrator.QR_CODE)
                .setPrompt("point at the QR code locoder serve shows")
                .setBeepEnabled(false)
                .setOrientationLocked(false)
                .initiateScan()
        }
    }

    fun deliverScan(text: String) = callJs("onLocoderScanned", text)

    private fun callJs(function: String, arg: String) {
        webView.post {
            webView.evaluateJavascript("window.$function && window.$function(${JSONObject.quote(arg)})", null)
        }
    }

    /**
     * Broadcasts "locoder?" on the local network; every `locoder serve`
     * answers with its name and port. Results go to window.onLocoderFound.
     */
    @JavascriptInterface
    fun discover() {
        Thread({
            val found = linkedMapOf<String, JSONObject>()
            try {
                DatagramSocket().use { socket ->
                    socket.broadcast = true
                    socket.soTimeout = 400
                    val ask = "locoder?".toByteArray()
                    val deadline = System.currentTimeMillis() + 2_000
                    var nextSend = 0L
                    while (System.currentTimeMillis() < deadline) {
                        if (System.currentTimeMillis() >= nextSend) {
                            for (target in broadcastAddresses()) {
                                try {
                                    socket.send(DatagramPacket(ask, ask.size, target, DISCOVERY_PORT))
                                } catch (e: Exception) {
                                    // that network can't broadcast; try the others
                                }
                            }
                            nextSend = System.currentTimeMillis() + 700
                        }
                        val buf = ByteArray(512)
                        val packet = DatagramPacket(buf, buf.size)
                        try {
                            socket.receive(packet)
                        } catch (e: SocketTimeoutException) {
                            continue
                        }
                        val reply = try {
                            JSONObject(String(packet.data, 0, packet.length, Charsets.UTF_8))
                        } catch (e: org.json.JSONException) {
                            continue
                        }
                        if (reply.optString("app") != "locoder") continue
                        val host = packet.address.hostAddress ?: continue
                        found["$host:${reply.optInt("port", DISCOVERY_PORT)}"] = JSONObject()
                            .put("host", host)
                            .put("port", reply.optInt("port", DISCOVERY_PORT))
                            .put("computer", reply.optString("computer", host))
                    }
                }
            } catch (e: Exception) {
                // no network at all; report nothing found
            }
            val json = JSONArray(found.values.toList()).toString()
            webView.post { webView.evaluateJavascript("window.onLocoderFound && window.onLocoderFound($json)", null) }
        }, "locoder-discover").start()
    }

    private fun broadcastAddresses(): List<InetAddress> {
        val out = mutableListOf<InetAddress>(InetAddress.getByName("255.255.255.255"))
        try {
            for (nif in NetworkInterface.getNetworkInterfaces()) {
                if (!nif.isUp || nif.isLoopback) continue
                for (addr in nif.interfaceAddresses) addr.broadcast?.let { out.add(it) }
            }
        } catch (e: Exception) {
            // fall back to the general broadcast address only
        }
        return out.distinct()
    }

    companion object {
        // locoder serve answers on the same port number it serves HTTP on.
        const val DISCOVERY_PORT = 8765
    }
}
