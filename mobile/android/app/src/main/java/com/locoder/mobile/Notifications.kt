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
 * The app's notifications. Four channels: "approvals" (pops up - a task on
 * your PC is blocked until you answer an approval or a question), "tasks" (a
 * task finished), "updates" (progress the model reports on a long task) and
 * "status" (the quiet one Android requires while [WatchService] runs).
 */
class Notifications(private val context: Context) {

    private val manager = context.getSystemService(NotificationManager::class.java)
    private val appName = context.getString(R.string.app_name)  // locoder or lochat

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
            NotificationChannel(CHANNEL_UPDATES, "Progress updates", NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = "Updates the model sends during a long task"
            },
        )
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_STATUS, "Watching a task", NotificationManager.IMPORTANCE_MIN).apply {
                description = "Shown while $appName keeps an eye on a task running on your PC"
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
        val reasons = request.optJSONArray("reasons")
        val careful = (0 until (reasons?.length() ?: 0)).joinToString("\n") { "• " + reasons!!.getString(it) }
        val body = (if (chatTitle.isNotEmpty()) "$title · $chatTitle" else title) +
            if (careful.isNotEmpty()) "\n$careful" else ""

        val builder = base(CHANNEL_APPROVALS)
            .setContentTitle(if (danger) "$appName needs your OK - careful, risky" else "$appName needs your OK")
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

    /** The model asked a question (ask_user); the task waits until it's answered in the app. */
    fun question(question: JSONObject) {
        if (!hasPermission()) return
        val id = question.optString("id")
        val chat = question.optString("chat")
        val text = question.optString("question")
        val options = question.optJSONArray("options")
        val choices = (0 until (options?.length() ?: 0)).joinToString("\n") { "• " + options!!.getString(it) }
        val body = if (choices.isNotEmpty()) "$text\n$choices" else text
        manager.notify(
            notificationId(id),
            base(CHANNEL_APPROVALS)
                .setContentTitle("$appName has a question")
                .setContentText(text)
                .setStyle(Notification.BigTextStyle().bigText(body))
                .setContentIntent(openIntent(chat, notificationId(id)))
                .setAutoCancel(true)
                .setCategory(Notification.CATEGORY_REMINDER)
                .build(),
        )
    }

    /** A progress update the model sent with notify_user. */
    fun update(event: JSONObject) {
        if (!hasPermission()) return
        val chat = event.optString("chat")
        val message = event.optString("message")
        val key = "update:$chat:${event.optLong("seq")}"
        manager.notify(
            notificationId(key),
            base(CHANNEL_UPDATES)
                .setContentTitle(event.optString("title").ifEmpty { appName })
                .setContentText(message)
                .setStyle(Notification.BigTextStyle().bigText(message))
                .setContentIntent(openIntent(chat, notificationId(key)))
                .setAutoCancel(true)
                .setCategory(Notification.CATEGORY_STATUS)
                .build(),
        )
    }

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
        const val CHANNEL_UPDATES = "updates"
        const val CHANNEL_STATUS = "status"
        const val STATUS_ID = 1

        fun notificationId(key: String) = (key.hashCode() and 0x3fffffff) + 2
    }
}
