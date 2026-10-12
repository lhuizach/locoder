package com.locoder.mobile

import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log

/**
 * Keeps following the PC's event stream after you leave the app, while a
 * task is running there, and turns "needs your OK" and "finished" into
 * notifications. Stops by itself once nothing is running (or when you come
 * back to the app). Everything stays on your network - no push servers.
 */
class WatchService : Service() {

    @Volatile
    private var running = false
    private var worker: Thread? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val prefs = Prefs(this)
        val server = prefs.server
        val key = prefs.key
        val notifications = Notifications(this)
        val computer = intent?.getStringExtra(EXTRA_COMPUTER) ?: "your PC"
        val status = notifications.status(computer)
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(Notifications.STATUS_ID, status, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(Notifications.STATUS_ID, status)
        }
        if (server == null || key == null) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (!running) {
            running = true
            worker = Thread({ watch(Api(server, key, prefs.remote), notifications) }, "locoder-watch").apply { start() }
        }
        return START_NOT_STICKY
    }

    private fun watch(api: Api, notifications: Notifications) {
        var since = 0L
        var failures = 0
        while (running) {
            try {
                val reconnect = since > 0
                api.events(since) { event ->
                    failures = 0
                    if (event.has("seq") && event.optString("type") != "hello") since = event.getLong("seq")
                    handle(event, notifications, reconnect)
                }
                if (!running) break
            } catch (e: Api.Unauthorized) {
                break
            } catch (e: Exception) {
                Log.i(TAG, "event stream dropped: ${e.message}")
            }
            // PC asleep or Wi-Fi gone: keep trying for a few minutes, then give up.
            if (++failures > 12 || !running) break
            Thread.sleep(minOf(30_000L, 2_000L * failures))
        }
        running = false
        stopSelf()
    }

    /** Returns false to stop following. */
    private fun handle(event: org.json.JSONObject, notifications: Notifications, reconnect: Boolean): Boolean {
        when (event.optString("type")) {
            "hello" -> {
                val state = event.getJSONObject("state")
                // Nothing to watch - unless this is a reconnect, where the
                // missed events (maybe the run_end) are replayed next.
                if (state.isNull("running") && !reconnect) return false.also { running = false }
                val pending = state.optJSONArray("pending")
                for (i in 0 until (pending?.length() ?: 0)) notifications.approval(pending!!.getJSONObject(i))
            }
            "confirm" -> notifications.approval(event.getJSONObject("request"))
            "confirm_done" -> notifications.cancelApproval(event.optString("id"))
            "run_end" -> {
                notifications.finished(event)
                running = false
                return false
            }
        }
        return running
    }

    override fun onDestroy() {
        running = false
        super.onDestroy()
    }

    companion object {
        private const val TAG = "locoder"
        private const val EXTRA_COMPUTER = "computer"

        fun start(context: Context, computer: String?) {
            val intent = Intent(context, WatchService::class.java).putExtra(EXTRA_COMPUTER, computer)
            try {
                context.startForegroundService(intent)
            } catch (e: Exception) {
                Log.w(TAG, "couldn't start watching: ${e.message}")
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, WatchService::class.java))
        }
    }
}
