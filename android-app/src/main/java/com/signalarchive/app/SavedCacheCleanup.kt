package com.signalarchive.app

import android.content.Context
import org.json.JSONObject
import java.io.File

/** Only already-saved temporary archives are eligible for delayed cleanup. */
internal object SavedCacheCleanup {
    private const val PREFS = "saved_cache_archives"
    private val namePattern = Regex("(?:telegram-export-|members-)[0-9]+\\.zip")
    fun eligible(name: String, savedAt: Long, now: Long): Boolean =
        namePattern.matches(name) && savedAt > 0 && now >= savedAt && now - savedAt >= 7L * 86400 * 1000
    @Synchronized fun record(context: Context, file: File) {
        if (!namePattern.matches(file.name) || file.parentFile?.canonicalFile != context.cacheDir.canonicalFile) return
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putLong(file.name, System.currentTimeMillis()).apply()
    }
    @Synchronized fun cleanup(context: Context, now: Long = System.currentTimeMillis()) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val editor = prefs.edit()
        for ((name, timestamp) in prefs.all) {
            if (timestamp !is Long || !eligible(name, timestamp, now)) continue
            val file = File(context.cacheDir, name)
            if (!file.exists() || (!java.nio.file.Files.isSymbolicLink(file.toPath()) && file.delete())) editor.remove(name)
        }
        editor.apply()
    }
}
