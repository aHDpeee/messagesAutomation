package com.signalarchive.app

import android.Manifest
import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import java.io.File

internal object ExportNotifications {
    fun keepForSharing(context: Context, source: File): Uri {
        val directory = File(context.filesDir, "shared-exports")
        check(directory.isDirectory || directory.mkdirs()) { "Не удалось подготовить архив для отправки" }
        val cutoff = System.currentTimeMillis() - 7L * 24 * 60 * 60 * 1000
        directory.listFiles()?.filter { it.name.matches(Regex("export-[0-9a-f-]{36}\\.zip")) && it.lastModified() < cutoff }?.forEach { it.delete() }
        val target = File(directory, "export-${java.util.UUID.randomUUID()}.zip")
        try { source.inputStream().use { input -> target.outputStream().use { output -> DocumentExport.copy(input, output) } } }
        catch (error: Exception) { target.delete(); throw error }
        return Uri.Builder().scheme("content").authority("${context.packageName}.exports").appendPath(target.name).build()
    }
    fun progress(context: Context, id: Int, message: String, done: Int = 0, total: Int = 0) {
        try {
            val manager = context.getSystemService(NotificationManager::class.java)
            val channel = "archive_progress"
            manager.createNotificationChannel(NotificationChannel(channel, "Ход экспорта", NotificationManager.IMPORTANCE_LOW))
            val pending = PendingIntent.getActivity(context, id, Intent(context, CatalogActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            manager.notify(id, Notification.Builder(context, channel).setSmallIcon(android.R.drawable.stat_sys_download)
                .setContentTitle("Tegash").setContentText(message).setContentIntent(pending)
                .setProgress(total.coerceAtLeast(0), done.coerceAtLeast(0), total <= 0).setOngoing(true).setOnlyAlertOnce(true).build())
        } catch (_: RuntimeException) { }
    }
    fun clear(context: Context, id: Int) {
        try { context.getSystemService(NotificationManager::class.java).cancel(id) } catch (_: RuntimeException) { }
    }
    fun requestPermission(activity: Activity) {
        if (Build.VERSION.SDK_INT >= 33 && activity.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            activity.requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 503)
    }
    fun completed(context: Context, message: String, id: Int, archive: Uri? = null) {
        try {
            val manager = context.getSystemService(NotificationManager::class.java)
            val channel = "archive_export"
            manager.createNotificationChannel(NotificationChannel(channel, "Экспорт архива", NotificationManager.IMPORTANCE_DEFAULT))
            val intent = if (archive != null) Intent(context, ShareExportActivity::class.java).setData(archive).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                else Intent(context, NativeActivity::class.java)
            val pending = PendingIntent.getActivity(context, id, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            manager.notify(id, Notification.Builder(context, channel).setSmallIcon(android.R.drawable.stat_sys_download_done)
                .setContentTitle("Tegash").setContentText(message).setContentIntent(pending).setAutoCancel(true).build())
        } catch (_: RuntimeException) { /* Notification failures do not invalidate a saved archive. */ }
    }
}
