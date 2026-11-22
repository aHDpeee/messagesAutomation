package com.signalarchive.app

import android.app.job.JobInfo
import android.app.job.JobParameters
import android.app.job.JobScheduler
import android.app.job.JobService
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.content.ComponentName
import android.content.Context
import android.net.Uri
import android.provider.DocumentsContract
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.time.Instant
import java.time.LocalTime
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.util.concurrent.Executors
import java.util.concurrent.Future

/** Schedules inexact, network-constrained daily work. The OS controls actual execution time. */
internal object ExportSchedule {
    const val JOB_ID = 0x534152
    private const val PREFS = "daily_export"
    fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    fun schedule(context: Context) {
        val prefs = prefs(context)
        val scheduler = context.getSystemService(JobScheduler::class.java)
        scheduler.cancel(JOB_ID)
        if (!prefs.getBoolean("enabled", false)) { prefs.edit().remove("next_due").apply(); return }
        val time = LocalTime.parse(prefs.getString("time", "03:15"))
        val now = ZonedDateTime.now()
        val next = ScheduleClock.next(now, time)
        val delay = java.time.Duration.between(now, next).toMillis().coerceAtLeast(1L)
        val job = JobInfo.Builder(JOB_ID, ComponentName(context, ScheduledExportService::class.java))
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
            .setMinimumLatency(delay)
            .setPersisted(true)
            .build()
        check(scheduler.schedule(job) == JobScheduler.RESULT_SUCCESS) { "Не удалось включить расписание" }
        prefs.edit().putString("next_due", next.toInstant().toString()).apply()
    }
}

class ScheduledExportService : JobService() {
    private val executor = Executors.newSingleThreadExecutor()
    private class Run { @Volatile var stopped = false; var future: Future<*>? = null }
    @Volatile private var running: Run? = null
    private fun notifyResult(message: String) {
        try {
            val manager = getSystemService(NotificationManager::class.java)
            val channel = "archive_export"
            manager.createNotificationChannel(NotificationChannel(channel, "Экспорт архива", NotificationManager.IMPORTANCE_DEFAULT))
            val intent = PendingIntent.getActivity(this, 0, Intent(this, ScheduleActivity::class.java), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            val notification = Notification.Builder(this, channel)
                .setSmallIcon(android.R.drawable.stat_sys_download_done)
                .setContentTitle("Tegash")
                .setContentText(message)
                .setContentIntent(intent)
                .setAutoCancel(true)
                .build()
            manager.notify(0x534152, notification)
        } catch (_: SecurityException) {
            // Notification permission is optional; export status stays in settings.
        }
    }
    override fun onStartJob(params: JobParameters): Boolean {
        val run = Run()
        running = run
        run.future = executor.submit {
            val prefs = ExportSchedule.prefs(this)
            var temporary: File? = null
            var target: Uri? = null
            var savedArchive: Uri? = null
            var hadMessages = false
            var mailFailed = false
            var retryWhenAvailable = false
            val intermediates = mutableListOf<File>()
            try {
                if (!prefs.getBoolean("enabled", false)) return@submit
                val tree = Uri.parse(prefs.getString("tree", null) ?: error("Выберите папку для экспорта"))
                val chats = ChatCatalog(File(filesDir, "chats.csv")).load().filter { !it["blocks"].isNullOrBlank() }
                    .map { GroupedArchive.Chat(it["id"].orEmpty(), it["title"].orEmpty(), it["blocks"].orEmpty(), it["username"].orEmpty(), it["type"].orEmpty()) }
                require(chats.isNotEmpty()) { "Нет чатов для экспорта" }
                val credentials = SecureCredentialStore(this).load() ?: error("Сначала войдите в Telegram")
                val key = MessageDigest.getInstance("SHA-256").digest(chats.map { it.id }.sorted().joinToString(",").toByteArray()).joinToString("") { "%02x".format(it.toInt() and 255) }
                val end = Instant.now()
                temporary = File.createTempFile("scheduled-export-", ".zip", cacheDir)
                ExportLock.acquire(filesDir).use {
                  val progressId = ExportProgress.start("чатов", chats.size)
                  try {
                    val ids = chats.map { it.id }
                    val start = ExportCheckpoint.start(applicationContext, ids, key, end)
                    val account = TelegramBridge.runJob(applicationContext, "account.status", JSONObject().put("api_id", credentials.apiId).put("api_hash", credentials.apiHash))
                    check(account.optString("status") == "authenticated") { "Сначала войдите в Telegram" }
                    val accountId = account.optJSONObject("account")?.optString("telegram_id").orEmpty()
                    val stored = getSharedPreferences("catalog_account", MODE_PRIVATE).getString("telegram_id", null)
                    CatalogAccountBinding.validate(stored, accountId, true)
                    val result = GroupedArchive.create(temporary, chats, { chat ->
                        if (Thread.currentThread().isInterrupted) throw InterruptedException()
                        val request = JSONObject().put("dialog_id", chat.id.toLong()).put("format", "csv").put("fetch_source", true)
                            .put("api_id", credentials.apiId).put("api_hash", credentials.apiHash)
                            .put("expected_account_id", accountId)
                            .put("period_start", start.toString()).put("period_end", end.toString())
                        try {
                            val result = TelegramBridge.runExportWithRetry(applicationContext, "messages.export", request) { note ->
                                if (note.startsWith("Пауза Telegram")) ExportNotifications.progress(applicationContext, 0x534165, note)
                            }
                            IntermediateExports.checked(filesDir, result.getString("file_path")).also(intermediates::add)
                        } catch (error: TelegramJobException) {
                            if (error.code == "empty_message_range") null else throw error
                        }
                    }, allowEmpty = true, prompts = BlockPrompts.forChats(applicationContext, accountId, chats)) { index, total, _ ->
                        ExportProgress.update(progressId, total - index)
                        ExportNotifications.progress(applicationContext, 0x534165, "Осталось чатов: ${total - index}", index, total)
                    }
                    if (Thread.currentThread().isInterrupted) throw InterruptedException()
                    if (result.chats == 0) {
                        check(ExportCheckpoint.advanceChats(applicationContext, ids, key, start, end)) { "Не удалось сохранить время экспорта" }
                        prefs.edit().putString("status", "Новых сообщений нет: $end").commit()
                        return@use
                    }
                    hadMessages = true
                    val expectedSize = temporary.length()
                    val sha256 = temporary.inputStream().use(RetentionPolicy::sha256)
                    val parent = DocumentsContract.buildDocumentUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree))
                    val finalName = ArchiveNames.reserveInFolder(applicationContext, tree, ArchiveNames.base(chats.map { it.group }, start, end))
                    // A process kill may interrupt the copy. A .part suffix
                    // prevents a partial upload from looking like a complete ZIP.
                    target = DocumentsContract.createDocument(contentResolver, parent, "application/octet-stream", "$finalName.part")
                        ?: error("Не удалось создать ZIP в выбранной папке")
                    contentResolver.openOutputStream(target, "w")?.use { out -> temporary.inputStream().use { DocumentExport.copy(it, out) } }
                        ?: error("Не удалось открыть созданный файл")
                    if (Thread.currentThread().isInterrupted) throw InterruptedException()
                    val verified = contentResolver.openInputStream(target)?.use { RetentionPolicy.matches(it, expectedSize, sha256) } ?: false
                    check(verified) { "Не удалось проверить записанный ZIP; время экспорта не изменено" }
                    if (Thread.currentThread().isInterrupted) throw InterruptedException()
                    target = DocumentsContract.renameDocument(contentResolver, target, finalName)
                        ?: error("Папка не поддерживает безопасное переименование. Выберите другую папку.")
                    val completedUri = target
                    target = null // The successfully written file belongs to the user.
                    savedArchive = completedUri
                    runCatching { ExportHistory.add(applicationContext, "По расписанию", completedUri.toString(), end) }
                    check(ArchiveRetention.record(applicationContext, tree, completedUri, end, expectedSize, sha256)) { "ZIP сохранён, но не удалось сохранить журнал архивов" }
                    check(ExportCheckpoint.advanceChats(applicationContext, ids, key, start, end)) { "ZIP сохранён, но не удалось сохранить время экспорта" }
                    if (prefs.getBoolean("email_enabled", false)) {
                        val mailRetention = if (prefs.getBoolean("retention_enabled", false)) prefs.getInt("retention_days", 30) else 0
                        try { MailSettings.send(applicationContext, temporary, finalName, mailRetention) }
                        catch (_: Exception) { mailFailed = true }
                    }
                  } finally { IntermediateExports.remove(intermediates); intermediates.clear(); ExportProgress.finish(progressId); ExportNotifications.clear(applicationContext, 0x534165) }
                }
                if (hadMessages) prefs.edit().putString("status", if (mailFailed) "Архив сохранён, но отправка на почту не удалась. Проверьте настройки и размер файла; повторно автоматически не отправляем." else "Экспорт сохранён: $end").commit()
                if (prefs.getBoolean("retention_enabled", false)) {
                    val days = prefs.getInt("retention_days", 30)
                    if (days in 1..36500) {
                        try {
                            val result = ArchiveRetention.cleanup(applicationContext, tree, end.minusSeconds(days.toLong() * 86400))
                            if (result.failed > 0) prefs.edit().putString("status", "Экспорт выполнен; не удалось проверить или удалить старые архивы: ${result.failed}").commit()
                        }
                        catch (error: Exception) {
                            if (error is InterruptedException) throw error
                            prefs.edit().putString("status", "Экспорт выполнен, но не удалось очистить старые архивы").commit()
                        }
                    }
                }
                if (hadMessages && savedArchive != null) ExportNotifications.completed(applicationContext, if (mailFailed) "Архив сохранён, почта не отправлена · нажмите, чтобы поделиться" else "Экспорт сохранён · нажмите, чтобы отправить", 0x534152, savedArchive)
                else notifyResult("Новых сообщений нет")
            } catch (error: ExportBusyException) {
                retryWhenAvailable = true
                prefs.edit().putString("status", "Другой экспорт выполняется; Android повторит попытку позже.").commit()
            } catch (error: TelegramJobException) {
                target?.let { runCatching { DocumentsContract.deleteDocument(contentResolver, it) } }
                retryWhenAvailable = error.code == "flood_wait"
                val suffix = if (retryWhenAvailable) " Android повторит попытку позже." else ""
                prefs.edit().putString("status", "Экспорт не завершён: ${error.message}.$suffix").commit()
                if (!run.stopped && !retryWhenAvailable) notifyResult("Экспорт не завершён. Подробности в настройках.")
            } catch (error: Exception) {
                target?.let { runCatching { DocumentsContract.deleteDocument(contentResolver, it) } }
                prefs.edit().putString("status", "Экспорт не завершён: ${error.message ?: error.javaClass.simpleName}").commit()
                if (!run.stopped) notifyResult("Экспорт не завершён. Подробности в настройках.")
            } finally {
                temporary?.delete()
                if (!run.stopped && running === run) {
                    jobFinished(params, retryWhenAvailable)
                    if (!retryWhenAvailable) try { ExportSchedule.schedule(this) } catch (error: Exception) {
                        prefs.edit().putString("status", "Не удалось запланировать следующий экспорт: ${error.message ?: error.javaClass.simpleName}").commit()
                        notifyResult("Не удалось запланировать следующий экспорт")
                    }
                }
            }
        }
        return true
    }
    override fun onStopJob(params: JobParameters): Boolean {
        running?.let { it.stopped = true; it.future?.cancel(true) }
        return ExportSchedule.prefs(this).getBoolean("enabled", false)
    }
    override fun onDestroy() { running?.let { it.stopped = true; it.future?.cancel(true) }; executor.shutdownNow(); super.onDestroy() }
}
