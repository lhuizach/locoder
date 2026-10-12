package com.locoder.mobile

import android.content.Context

/**
 * The pairing (which PC, and the key it gave this phone) and the "notify me"
 * switch, in app-private storage. The page keeps its own copy in
 * localStorage; this one is for [WatchService], which runs without it.
 */
class Prefs(context: Context) {
    private val prefs = context.getSharedPreferences("locoder", Context.MODE_PRIVATE)

    var server: String?
        get() = prefs.getString("server", null)
        set(value) = prefs.edit().putString("server", value).apply()

    var key: String?
        get() = prefs.getString("key", null)
        set(value) = prefs.edit().putString("key", value).apply()

    var notify: Boolean
        get() = prefs.getBoolean("notify", true)
        set(value) = prefs.edit().putBoolean("notify", value).apply()

    val paired: Boolean
        get() = !server.isNullOrEmpty() && !key.isNullOrEmpty()

    fun clear() = prefs.edit().remove("server").remove("key").apply()
}
