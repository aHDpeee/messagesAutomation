package com.signalarchive.app

import android.content.Context
import org.json.JSONObject

internal object UserGreeting {
    fun save(context: Context, account: JSONObject?) {
        if (account == null) return
        val first = account.optString("first_name", "").takeUnless { it == "null" }.orEmpty().trim()
        val username = account.optString("username", "").takeUnless { it == "null" }.orEmpty().trim()
        val display = username.takeIf { it.isNotEmpty() }?.let { "@$it" } ?: first
        context.getSharedPreferences("user_greeting", Context.MODE_PRIVATE).edit().putString("name", display).apply()
    }
    fun text(context: Context): String {
        val name = name(context)
        return if (name.isBlank()) "Привет!" else "Привет, $name!"
    }
    fun name(context: Context): String = context.getSharedPreferences("user_greeting", Context.MODE_PRIVATE).getString("name", "").orEmpty()
}
