package com.signalarchive.app

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.net.Uri
import org.json.JSONArray
import java.net.HttpURLConnection
import java.net.URL

internal object ReleaseUpdates {
    private val versionPattern = Regex("^android-v(\\d+)\\.(\\d+)\\.(\\d+)$")
    private fun version(text: String): List<Int>? = versionPattern.matchEntire(text)?.groupValues?.drop(1)?.mapNotNull { it.toIntOrNull() }?.takeIf { it.size == 3 }
    fun check(activity: Activity) {
        val app = activity.applicationContext
        val prefs = app.getSharedPreferences("release_updates", Activity.MODE_PRIVATE)
        val now = System.currentTimeMillis()
        if (now - prefs.getLong("checked_at", 0) in 0 until 24 * 60 * 60_000L) return
        prefs.edit().putLong("checked_at", now).apply()
        Thread {
            try {
                val connection = URL("https://api.github.com/repos/aHDpeee/messagesAutomation/releases?per_page=20").openConnection() as HttpURLConnection
                connection.connectTimeout = 5000; connection.readTimeout = 5000
                connection.setRequestProperty("Accept", "application/vnd.github+json")
                connection.setRequestProperty("User-Agent", "Tegash-Android")
                val data = try {
                    check(connection.responseCode == 200)
                    connection.inputStream.bufferedReader().use { it.readText().also { body -> require(body.length <= 1_000_000) } }
                } finally { connection.disconnect() }
                val installed = version("android-v" + app.packageManager.getPackageInfo(app.packageName, 0).versionName) ?: return@Thread
                val releases = JSONArray(data)
                val candidates = (0 until releases.length()).map { releases.getJSONObject(it) }.filter { release ->
                    !release.optBoolean("draft") && !release.optBoolean("prerelease") &&
                        (0 until release.optJSONArray("assets").let { it?.length() ?: 0 }).any { i -> release.getJSONArray("assets").getJSONObject(i).optString("name").endsWith(".apk", true) }
                }.mapNotNull { release -> version(release.optString("tag_name"))?.let { it to release.optString("tag_name") } }
                val newest = candidates.maxWithOrNull(compareBy({ it.first[0] }, { it.first[1] }, { it.first[2] })) ?: return@Thread
                if (compareValuesBy(newest.first, installed, { it[0] }, { it[1] }, { it[2] }) <= 0) return@Thread
                activity.runOnUiThread {
                    if (!activity.isFinishing && !activity.isDestroyed) AlertDialog.Builder(activity).setTitle("Доступно обновление Tegash")
                        .setMessage("Доступна версия ${newest.second.removePrefix("android-v")}. Откройте страницу релиза, чтобы скачать APK.")
                        .setNegativeButton("Позже", null)
                        .setPositiveButton("Открыть релиз") { _, _ -> runCatching { activity.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://github.com/aHDpeee/messagesAutomation/releases/tag/${newest.second}"))) } }.show()
                }
            } catch (_: Exception) { /* Network failure never blocks normal use. */ }
        }.start()
    }
}
