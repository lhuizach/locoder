package com.locoder.mobile

import java.io.BufferedReader
import java.io.IOException
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import org.json.JSONObject

/**
 * The few calls to `locoder serve` the native side makes itself. Two routes,
 * like the web app: [server] (home Wi-Fi or the USB link, key in a header)
 * and [remote], the Cloudflare tunnel, where everything is end-to-end
 * encrypted ([E2e]). Home is tried first.
 */
class Api(private val server: String, private val key: String, private val remote: String?) {

    class Unauthorized : Exception("not paired")

    private val channel by lazy { E2e.channel(key) }
    private val deviceId by lazy { E2e.deviceId(key) }

    private fun open(url: String, method: String): HttpURLConnection =
        (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 8_000
        }

    /** True when the home route answers (quickly). */
    private fun homeReachable(): Boolean = try {
        val conn = open("$server/api/hello", "GET").apply {
            connectTimeout = 2_500
            readTimeout = 2_500
        }
        try {
            conn.responseCode == 200
        } finally {
            conn.disconnect()
        }
    } catch (e: IOException) {
        false
    }

    private fun useRemote(): Boolean = remote != null && !homeReachable()

    fun confirm(requestId: String, approve: Boolean) {
        val body = JSONObject().put("approve", approve)
        if (useRemote()) {
            sealed("POST", "/api/confirm/$requestId", body)
            return
        }
        val conn = open("$server/api/confirm/$requestId", "POST")
        try {
            conn.readTimeout = 10_000
            conn.doOutput = true
            conn.setRequestProperty("Authorization", "Bearer $key")
            conn.setRequestProperty("Content-Type", "application/json")
            conn.outputStream.use { it.write(body.toString().toByteArray()) }
            if (conn.responseCode == 401) throw Unauthorized()
        } finally {
            conn.disconnect()
        }
    }

    private fun post(conn: HttpURLConnection, envelope: JSONObject) {
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", "application/json")
        conn.outputStream.use { it.write(envelope.toString().toByteArray()) }
    }

    private fun sealed(method: String, path: String, body: JSONObject?): JSONObject {
        val request = channel.sealRequest(deviceId, method, path, body)
        val conn = open("$remote/api/e2e", "POST")
        try {
            conn.readTimeout = 15_000
            post(conn, request.envelope)
            if (conn.responseCode == 401) throw Unauthorized()
            if (conn.responseCode != 200) throw IOException("HTTP ${conn.responseCode}")
            val reply = channel.open(JSONObject(conn.inputStream.bufferedReader().readText()), E2e.RESPONSE, request.nonce)
            if (reply.optInt("s") == 401) throw Unauthorized()
            return reply
        } finally {
            conn.disconnect()
        }
    }

    /**
     * Follows the live event stream, calling [onEvent] for each event, until
     * [onEvent] returns false or the connection drops. The server pings every
     * 15 seconds, so a read that times out means the connection is dead.
     */
    fun events(since: Long, onEvent: (JSONObject) -> Boolean) {
        val remoteRoute = useRemote()
        val request = if (remoteRoute) channel.sealRequest(deviceId, "GET", "/api/events?since=$since", null) else null
        val conn = if (request != null) {
            open("$remote/api/e2e", "POST").also { post(it, request.envelope) }
        } else {
            open("$server/api/events?since=$since", "GET").also { it.setRequestProperty("Authorization", "Bearer $key") }
        }
        try {
            conn.readTimeout = 45_000
            if (conn.responseCode == 401) throw Unauthorized()
            if (conn.responseCode != 200) throw IOException("events: HTTP ${conn.responseCode}")
            BufferedReader(InputStreamReader(conn.inputStream, Charsets.UTF_8)).use { reader ->
                while (true) {
                    val line = reader.readLine() ?: return
                    if (!line.startsWith("data: ")) continue
                    val event = try {
                        val data = JSONObject(line.substring(6))
                        if (request != null) channel.open(data, E2e.EVENT, request.nonce) else data
                    } catch (e: Exception) {
                        continue  // malformed, or didn't decrypt: never act on it
                    }
                    if (!onEvent(event)) return
                }
            }
        } finally {
            conn.disconnect()
        }
    }
}
