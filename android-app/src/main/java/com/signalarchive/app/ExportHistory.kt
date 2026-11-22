package com.signalarchive.app

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/** A short local log of successfully saved archives, not an index of their contents. */
internal object ExportHistory {
    data class Entry(val label: String, val uri: Uri)
    private const val PREFS = "export_history"
    private val lock = Any()
    fun add(context: Context, kind: String, location: String, time: Instant = Instant.now()) = synchronized(lock) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val previous = runCatching { JSONArray(prefs.getString("entries", "[]")) }.getOrDefault(JSONArray())
        val uri = Uri.parse(location)
        val displayName = runCatching {
            context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
                if (cursor.moveToFirst()) cursor.getString(0) else null
            }
        }.getOrNull()?.takeIf { it.isNotBlank() } ?: uri.lastPathSegment.orEmpty()
        val next = JSONArray().put(JSONObject().put("kind", kind).put("location", location).put("name", displayName).put("time", time.toString()))
        for (index in 0 until minOf(previous.length(), 19)) next.put(previous.getJSONObject(index))
        prefs.edit().putString("entries", next.toString()).apply()
    }
    fun entries(context: Context): List<Entry> = synchronized(lock) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val entries = runCatching { JSONArray(prefs.getString("entries", "[]")) }.getOrDefault(JSONArray())
        (0 until entries.length()).mapNotNull { index ->
            runCatching { entries.getJSONObject(index).let {
                val time = runCatching { DateTimeFormatter.ofPattern("dd.MM.yyyy HH:mm").withZone(ZoneId.systemDefault()).format(Instant.parse(it.getString("time"))) }.getOrDefault(it.optString("time"))
                val name = it.optString("name").ifBlank { Uri.parse(it.getString("location")).lastPathSegment.orEmpty() }
                val uri = Uri.parse(it.getString("location"))
                require(uri.scheme == "content")
                Entry("$time · ${it.getString("kind")}\n$name", uri)
            } }.getOrNull()
        }
    }
    fun latest(context: Context): Instant? = synchronized(lock) {
        val raw = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("entries", "[]")
        val entries = runCatching { JSONArray(raw) }.getOrDefault(JSONArray())
        (0 until entries.length()).mapNotNull { index ->
            runCatching { Instant.parse(entries.getJSONObject(index).getString("time")) }.getOrNull()
        }.maxOrNull()
    }
}
