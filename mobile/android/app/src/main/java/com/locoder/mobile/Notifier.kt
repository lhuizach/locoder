package com.locoder.mobile

import android.Manifest
import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.webkit.JavascriptInterface

/**
 * Android notifications for the web app, exposed to it as
 * `window.LocoderAndroid`. Two channels: "approvals" (pops up - a task is
 * blocked until you answer) and "tasks" (a task finished).
 *
 * Notifications are only posted while the app is in the background; in the
 * foreground the page shows the same thing inline.
 */
class Notifier(private val activity: Activity) {

    @Volatile
    var inForeground = true

    private val manager = activity.getSystemService(NotificationManager::class.java)
    private var nextId = 1

    init {
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_APPROVALS, "Approvals", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "A task is waiting for you to approve an edit or a command"
            },
        )
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_TASKS, "Finished tasks", NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = "A task you started has finished"
            },
        )
    }

    /** kind is "approval" or "done". Returns true if a notification was posted. */
    @JavascriptInterface
    fun notify(kind: String, title: String, body: String): Boolean {
        if (inForeground || !hasPermission()) return false
        val channel = if (kind == "approval") CHANNEL_APPROVALS else CHANNEL_TASKS
        val open = PendingIntent.getActivity(
            activity, 0,
            Intent(activity, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notification = Notification.Builder(activity, channel)
            .setSmallIcon(R.drawable.ic_notification)
            .setColor(activity.getColor(R.color.accent))
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(Notification.BigTextStyle().bigText(body))
            .setContentIntent(open)
            .setAutoCancel(true)
            .setCategory(if (kind == "approval") Notification.CATEGORY_REMINDER else Notification.CATEGORY_STATUS)
            .build()
        manager.notify(nextId++, notification)
        return true
    }

    @JavascriptInterface
    fun hasPermission(): Boolean =
        Build.VERSION.SDK_INT < 33 ||
            activity.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    /** Asks for the Android 13+ notification permission (no-op if already granted). */
    @JavascriptInterface
    fun requestPermission() {
        if (Build.VERSION.SDK_INT >= 33 && !hasPermission()) {
            activity.runOnUiThread {
                activity.requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQUEST_CODE)
            }
        }
    }

    /** Clears notifications once you're back in the app. */
    fun clearAll() = manager.cancelAll()

    companion object {
        const val CHANNEL_APPROVALS = "approvals"
        const val CHANNEL_TASKS = "tasks"
        const val REQUEST_CODE = 1
    }
}
