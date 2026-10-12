package com.locoder.mobile

import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import org.json.JSONObject

/** The few calls to `locoder serve` the native side makes itself. */
class Api(private val server: String, private val key: String) {

    class Unauthorized : Exception("not paired")

    private fun open(path: String, method: String): HttpURLConnection =
        (URL(server + path).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 8_000
            setRequestProperty("Authorization", "Bearer $key")
        }

    fun confirm(requestId: String, approve: Boolean) {
        val conn = open("/api/confirm/$requestId", "POST")
        try {
            conn.readTimeout = 10_000
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            conn.outputStream.use { it.write(JSONObject().put("approve", approve).toString().toByteArray()) }
            if (conn.responseCode == 401) throw Unauthorized()
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
        val conn = open("/api/events?since=$since", "GET")
        try {
            conn.readTimeout = 45_000
            if (conn.responseCode == 401) throw Unauthorized()
            if (conn.responseCode != 200) throw java.io.IOException("events: HTTP ${conn.responseCode}")
            BufferedReader(InputStreamReader(conn.inputStream, Charsets.UTF_8)).use { reader ->
                while (true) {
                    val line = reader.readLine() ?: return
                    if (!line.startsWith("data: ")) continue
                    val event = try {
                        JSONObject(line.substring(6))
                    } catch (e: org.json.JSONException) {
                        continue
                    }
                    if (!onEvent(event)) return
                }
            }
        } finally {
            conn.disconnect()
        }
    }
}
