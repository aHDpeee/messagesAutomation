package com.signalarchive.app

import android.content.Context
import org.json.JSONObject
import java.security.MessageDigest
import java.time.Instant

/** Private references to invitations awaiting administrator approval. */
internal object PendingRequests {
    private const val PREFS = "pending_join_requests"
    private fun key(reference: String): String = MessageDigest.getInstance("SHA-256").digest(reference.trim().toByteArray()).joinToString("") { "%02x".format(it.toInt() and 255) }
    fun add(context: Context, reference: String, title: String, accountId: String, chatId: String, targetBlock: String = "") {
        val key = key(reference)
        val value = JSONObject().put("reference", reference).put("title", title).put("date", Instant.now().toString()).put("account_id", accountId).put("chat_id", chatId).put("target_block", targetBlock).toString()
        check(context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(key, value).commit()) { "Не удалось сохранить запрос" }
    }
    fun list(context: Context): List<Pair<String, JSONObject>> = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).all.mapNotNull { (key, value) ->
        runCatching { key to JSONObject(value as String) }.getOrNull()
    }
    fun remove(context: Context, key: String) { context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().remove(key).apply() }
    fun removeReference(context: Context, reference: String) { remove(context, key(reference)) }
    fun contains(context: Context, reference: String): Boolean = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).contains(key(reference))
    fun ordered(context: Context, items: List<Pair<String, JSONObject>>, limit: Int): List<Pair<String, JSONObject>> {
        val byKey = items.toMap()
        val cursor = context.getSharedPreferences("pending_join_cursor", Context.MODE_PRIVATE).getString("last", null)
        return PendingRotation.select(byKey.keys.toList(), cursor, limit).mapNotNull { key -> byKey[key]?.let { key to it } }
    }
    fun markChecked(context: Context, key: String) { context.getSharedPreferences("pending_join_cursor", Context.MODE_PRIVATE).edit().putString("last", key).apply() }
    fun reconcile(context: Context, accountId: String, syncedIds: Set<String>) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val editor = prefs.edit()
        list(context).forEach { (key, entry) ->
            val id = entry.optString("chat_id")
            if (entry.optString("account_id") == accountId && id.isNotBlank() && id in syncedIds) editor.remove(key)
        }
        editor.apply()
    }
}
