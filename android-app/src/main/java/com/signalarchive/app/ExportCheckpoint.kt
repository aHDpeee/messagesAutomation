package com.signalarchive.app

import android.content.Context
import java.time.Instant

/** An older manual export must never move an automatic export checkpoint backwards. */
internal object ExportCheckpoint {
    private val lock = Any()
    fun read(context: Context, key: String): Instant? = synchronized(lock) {
        context.getSharedPreferences("export_state", Context.MODE_PRIVATE).getString(key, null)
            ?.let { runCatching { Instant.parse(it) }.getOrNull() }
    }
    fun advance(context: Context, key: String, end: Instant): Boolean = synchronized(lock) {
        val prefs = context.getSharedPreferences("export_state", Context.MODE_PRIVATE)
        val previous = prefs.getString(key, null)?.let { runCatching { Instant.parse(it) }.getOrNull() }
        if (previous != null && previous >= end) true
        else prefs.edit().putString(key, end.toString()).commit()
    }

    /** Use the oldest chat checkpoint: changing the selection must not skip older chats. */
    fun start(context: Context, ids: List<String>, legacyKey: String, now: Instant): Instant = synchronized(lock) {
        val fallback = now.minusSeconds(86400)
        val legacy = read(context, legacyKey)?.takeIf { it < now }
        CheckpointPolicy.start(ids.map { id -> read(context, "chat:$id") ?: legacy }, fallback, now)
    }

    /** Only advance a chat when this saved range touches its previous checkpoint. */
    fun advanceChats(context: Context, ids: List<String>, legacyKey: String, start: Instant, end: Instant): Boolean = synchronized(lock) {
        val prefs = context.getSharedPreferences("export_state", Context.MODE_PRIVATE)
        val legacy = read(context, legacyKey)
        val editor = prefs.edit()
        ids.forEach { id ->
            val key = "chat:$id"
            val old = read(context, key) ?: legacy
            if (CheckpointPolicy.shouldAdvance(old, start, end)) editor.putString(key, end.toString())
        }
        editor.commit()
    }
}
