package com.locoder.mobile

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import org.json.JSONObject

/**
 * The app's notifications. Three channels: "approvals" (pops up - a task on
 * your PC is blocked until you answer), "tasks" (a task finished) and
 * "status" (the quiet one Android requires while [WatchService] runs).
 */
class Notifications(private val context: Context) {

    private val manager = context.getSystemService(NotificationManager::class.java)

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
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_STATUS, "Watching a task", NotificationManager.IMPORTANCE_MIN).apply {
                description = "Shown while locoder keeps an eye on a task running on your PC"
                setShowBadge(false)
            },
        )
    }

    fun hasPermission(): Boolean =
        Build.VERSION.SDK_INT < 33 ||
            context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    /** Opens the app, on [chat] if given. */
    private fun openIntent(chat: String?, requestCode: Int): PendingIntent {
        val intent = Intent(context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_NEW_TASK)
        if (chat != null) intent.putExtra(MainActivity.EXTRA_CHAT, chat)
        return PendingIntent.getActivity(
            context, requestCode, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
    }

    private fun base(channel: String) = Notification.Builder(context, channel)
        .setSmallIcon(R.drawable.ic_notification)
        .setColor(context.getColor(R.color.accent))

    fun status(computer: String): Notification = base(CHANNEL_STATUS)
        .setContentTitle("watching your task")
        .setContentText("on $computer - you'll get a notification when it needs you")
        .setContentIntent(openIntent(null, 0))
        .setOngoing(true)
        .build()

    /** "locoder needs your OK", with approve / decline buttons. */
    fun approval(request: JSONObject) {
        if (!hasPermission()) return
        val id = request.optString("id")
        val chat = request.optString("chat")
        val title = request.optString("title")
        val chatTitle = request.optString("chat_title")
        val danger = request.optBoolean("danger")
        val preview = request.optString("preview").lines().take(12).joinToString("\n").take(700)
        val body = if (chatTitle.isNotEmpty()) "$title · $chatTitle" else title

        val builder = base(CHANNEL_APPROVALS)
            .setContentTitle(if (danger) "locoder needs your OK - careful, destructive" else "locoder needs your OK")
            .setContentText(body)
            .setStyle(Notification.BigTextStyle().bigText("$body\n\n$preview"))
            .setContentIntent(openIntent(chat, notificationId(id)))
            .setAutoCancel(true)
            .setCategory(Notification.CATEGORY_REMINDER)
            .addAction(action(id, approve = false, "Decline"))
        // A destructive command has to be read in the app first.
        if (!danger) builder.addAction(action(id, approve = true, "Approve"))
        manager.notify(notificationId(id), builder.build())
    }

    private fun action(requestId: String, approve: Boolean, label: String): Notification.Action {
        val intent = Intent(context, ActionReceiver::class.java)
            .setAction(if (approve) ActionReceiver.APPROVE else ActionReceiver.DECLINE)
            .putExtra(ActionReceiver.EXTRA_REQUEST, requestId)
        val pending = PendingIntent.getBroadcast(
            context, notificationId(requestId) * 2 + if (approve) 1 else 0, intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return Notification.Action.Builder(null, label, pending).build()
    }

    fun cancelApproval(requestId: String) = manager.cancel(notificationId(requestId))

    /** A task finished (or stopped, or failed). */
    fun finished(event: JSONObject) {
        if (!hasPermission()) return
        val chat = event.optString("chat")
        val title = event.optString("title").ifEmpty { "your task" }
        val text = when (event.optString("status")) {
            "stopped" -> "stopped"
            "error" -> "stopped with an error - open the chat to see it"
            else -> event.optString("preview").ifEmpty { "done" }
        }
        manager.notify(
            notificationId("done:$chat"),
            base(CHANNEL_TASKS)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(Notification.BigTextStyle().bigText(text))
                .setContentIntent(openIntent(chat, notificationId("done:$chat")))
                .setAutoCancel(true)
                .setCategory(Notification.CATEGORY_STATUS)
                .build(),
        )
    }

    fun clearAll() = manager.cancelAll()

    companion object {
        const val CHANNEL_APPROVALS = "approvals"
        const val CHANNEL_TASKS = "tasks"
        const val CHANNEL_STATUS = "status"
        const val STATUS_ID = 1

        fun notificationId(key: String) = (key.hashCode() and 0x3fffffff) + 2
    }
}
