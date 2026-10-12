package com.locoder.mobile

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/** The Approve / Decline buttons on a "needs your OK" notification. */
class ActionReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val requestId = intent.getStringExtra(EXTRA_REQUEST) ?: return
        val approve = intent.action == APPROVE
        val prefs = Prefs(context)
        val server = prefs.server ?: return
        val key = prefs.key ?: return
        val notifications = Notifications(context)
        notifications.cancelApproval(requestId)
        val result = goAsync()
        Thread {
            try {
                Api(server, key, prefs.remote).confirm(requestId, approve)
            } catch (e: Exception) {
                Log.w("locoder", "couldn't answer the request: ${e.message}")
            } finally {
                result.finish()
            }
        }.start()
    }

    companion object {
        const val APPROVE = "com.locoder.mobile.APPROVE"
        const val DECLINE = "com.locoder.mobile.DECLINE"
        const val EXTRA_REQUEST = "request"
    }
}
