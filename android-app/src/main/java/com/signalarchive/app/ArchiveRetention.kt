package com.signalarchive.app

import android.content.Context
import android.net.Uri
import android.provider.DocumentsContract
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant

/** Only documents recorded as created by this app in the selected tree are eligible. */
internal object ArchiveRetention {
    private const val PREFS = "scheduled_archive_inventory"
    private const val KEY = "documents"
    fun record(context: Context, tree: Uri, uri: Uri, timestamp: Instant, size: Long, sha256: String): Boolean {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val list = JSONArray(prefs.getString(KEY, "[]"))
        list.put(JSONObject().put("tree", tree.toString()).put("uri", uri.toString()).put("created", timestamp.toString()).put("size", size).put("sha256", sha256))
        return prefs.edit().putString(KEY, list.toString()).commit()
    }
    data class Result(val deleted: Int, val failed: Int)
    fun cleanup(context: Context, tree: Uri, cutoff: Instant): Result {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val list = JSONArray(prefs.getString(KEY, "[]")); val kept = JSONArray(); var deleted = 0; var failed = 0
        for (index in 0 until list.length()) {
            val item = list.getJSONObject(index)
            val created = runCatching { Instant.parse(item.getString("created")) }.getOrNull()
            val uri = runCatching { Uri.parse(item.getString("uri")) }.getOrNull()
            val eligible = RetentionPolicy.eligible(item.optString("tree"), tree.toString(), created, cutoff)
            val removed = if (eligible && uri != null) runCatching {
                val unchanged = context.contentResolver.openInputStream(uri)?.use { RetentionPolicy.matches(it, item.optLong("size", -1), item.optString("sha256")) } ?: false
                unchanged && DocumentsContract.deleteDocument(context.contentResolver, uri)
            }.getOrElse { if (it is InterruptedException) throw it else false } else false
            if (eligible && !removed) failed++
            if (removed) deleted++ else kept.put(item)
        }
        check(prefs.edit().putString(KEY, kept.toString()).commit()) { "Не удалось сохранить журнал архивов" }
        return Result(deleted, failed)
    }
}
