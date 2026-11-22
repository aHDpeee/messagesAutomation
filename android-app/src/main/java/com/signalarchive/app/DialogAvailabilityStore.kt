package com.signalarchive.app

import android.content.Context
import org.json.JSONObject

internal object DialogAvailabilityStore {
    fun save(context: Context, accountId: String, result: JSONObject) {
        val ids = result.optJSONArray("synced_ids") ?: return
        val values = (0 until ids.length()).map { ids.getString(it) }.toSet()
        fun set(name: String): Set<String> = result.optJSONArray(name)?.let { array -> (0 until array.length()).map { array.getString(it) }.toSet() } ?: emptySet()
        check(context.getSharedPreferences("dialog_availability", Context.MODE_PRIVATE).edit().putString("account_id", accountId).putStringSet("ids", values).putStringSet("empty_ids", set("empty_ids")).putStringSet("unanswered_bot_ids", set("unanswered_bot_ids")).commit()) { "Не удалось сохранить доступность чатов" }
    }
}
