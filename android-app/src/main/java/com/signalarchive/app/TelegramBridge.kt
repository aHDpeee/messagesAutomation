package com.signalarchive.app

import android.content.Context
import com.chaquo.python.Python
import com.chaquo.python.PyObject
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject

internal class TelegramJobException(val code: String, message: String) : IllegalStateException(message)

/** A single owner for the embedded Telegram event loop and encrypted session. */
internal object TelegramBridge {
    private var api: PyObject? = null
    private val accountSettings = listOf("export_state", "single_chat_period", "dialog_availability", "catalog_account", "pending_join_requests", "pending_join_cursor", "user_greeting")

    @Synchronized
    private fun instance(context: Context): PyObject {
        val app = context.applicationContext
        if (!Python.isStarted()) Python.start(AndroidPlatform(app))
        return api ?: Python.getInstance().getModule("tg_observatory.api")
            .callAttr("ObservatoryApi", app.filesDir.resolve("telegram-engine").absolutePath,
                null, SecureSessionStore(app)).also { api = it }
    }

    @Synchronized fun dispatch(context: Context, operation: String, params: JSONObject = JSONObject()): JSONObject {
        val request = JSONObject().put("schema_version", 1).put("operation", operation).put("params", params)
        val response = JSONObject(instance(context).callAttr("dispatch", request.toString()).toString())
        if (!response.optBoolean("ok")) throw IllegalStateException(response.optJSONObject("error")?.optString("message") ?: "Ошибка Telegram")
        return response.optJSONObject("data") ?: JSONObject()
    }

    /** Caller holds ExportLock. Preserve the closed engine before detaching the session. */
    @Synchronized fun prepareAccountSwitch(context: Context): String {
        val app = context.applicationContext
        api?.let {
            check(it.callAttr("close_if_idle").toBoolean()) { "Дождитесь завершения других операций Telegram и повторите." }
            api = null
        }
        val catalog = ChatCatalog(app.filesDir.resolve("chats.csv"))
        val (catalogRows, version) = catalog.loadVersioned()
        val session = SecureSessionStore(app)
        val oldSession = session.load()
        val archive = WorkspaceArchive.archive(app.filesDir)
        val backupId = archive?.name ?: "workspace-${java.util.UUID.randomUUID()}"
        val backupDir = archive ?: app.filesDir.resolve("telegram-workspaces").resolve(backupId).apply {
            check(mkdirs()) { "Не удалось создать резервную папку" }
        }
        if (oldSession.isNotEmpty()) {
            val backup = SecureSessionStore(app, "session-$backupId", "session-$backupId")
            backup.save(oldSession)
            check(backup.load() == oldSession) { "Не удалось проверить копию сессии" }
        }
        val oldCredentials = SecureCredentialStore(app).load()
        if (oldCredentials != null) {
            val credentialBackup = SecureSessionStore(app, "api-$backupId", "api-$backupId")
            val payload = JSONObject().put("id", oldCredentials.apiId).put("hash", oldCredentials.apiHash).toString()
            credentialBackup.save(payload)
            check(credentialBackup.load() == payload) { "Не удалось проверить копию данных API" }
        }
        val settings = JSONObject()
        for (name in accountSettings + "daily_export") {
            val values = JSONObject()
            for ((key, value) in app.getSharedPreferences(name, Context.MODE_PRIVATE).all) values.put(key, JSONObject.wrap(value))
            settings.put(name, values)
        }
        settings.put("has_session_backup", oldSession.isNotEmpty())
        settings.put("has_api_backup", oldCredentials != null)
        backupDir.resolve("account-settings.json").outputStream().use { output ->
            output.write(settings.toString().toByteArray(Charsets.UTF_8)); output.fd.sync()
        }
        // Keep the catalog with its corresponding engine and session, rather
        // than relying on a timestamped backup which cannot identify an account.
        backupDir.resolve("chats.csv").outputStream().use { output ->
            output.write(ChatCatalog.encode(catalogRows).toByteArray(Charsets.UTF_8)); output.fd.sync()
        }
        WorkspaceArchive.seal(backupDir)
        check(WorkspaceArchive.isSealed(backupDir)) { "Не удалось проверить резервную копию" }
        // Save creates a dated CSV backup before replacing the catalog.
        catalog.save(emptyList(), expectedVersion = version)
        val schedule = ExportSchedule.prefs(app)
        check(schedule.edit().putBoolean("enabled", false).commit()) { "Не удалось отключить расписание" }
        ExportSchedule.schedule(app)
        for (name in accountSettings) {
            check(app.getSharedPreferences(name, Context.MODE_PRIVATE).edit().clear().commit()) { "Не удалось подготовить данные нового аккаунта" }
        }
        session.clear()
        return backupId
    }

    /** Caller holds ExportLock; current workspace is backed up before replacement. */
    @Synchronized fun restoreAccount(context: Context, backupId: String) {
        val app = context.applicationContext
        val source = WorkspaceArchive.available(app.filesDir).firstOrNull { it.name == backupId }
            ?: error("Резервная копия недоступна или повреждена")
        val settings = JSONObject(source.resolve("account-settings.json").readText(Charsets.UTF_8))
        val rows = ChatCatalog.parse(source.resolve("chats.csv").readText(Charsets.UTF_8))
        val session = if (settings.optBoolean("has_session_backup"))
            SecureSessionStore(app, "session-$backupId", "session-$backupId").load().also { require(it.isNotEmpty()) { "Не удалось прочитать сессию" } } else ""
        val savedCredentials = if (settings.optBoolean("has_api_backup")) {
            val payload = JSONObject(SecureSessionStore(app, "api-$backupId", "api-$backupId").load())
            ApiCredentials(payload.getString("id"), payload.getString("hash"))
        } else null
        // Validate every preference type before changing the current workspace.
        val saved = (accountSettings + "daily_export").associateWith { name ->
            val values = settings.optJSONObject(name) ?: if (name == "user_greeting") JSONObject() else settings.getJSONObject(name)
            values.keys().asSequence().associateWith { key ->
                when (val value = values.get(key)) {
                    is String, is Boolean, is Int, is Long, is Float -> value
                    is org.json.JSONArray -> (0 until value.length()).map { value.getString(it) }.toSet()
                    else -> throw IllegalArgumentException("Неверный формат настроек копии")
                }
            }
        }
        prepareAccountSwitch(app)
        WorkspaceArchive.restoreEngine(app.filesDir, source)
        val catalog = ChatCatalog(app.filesDir.resolve("chats.csv"))
        catalog.save(rows, expectedVersion = catalog.version())
        for ((name, values) in saved) {
            val edit = app.getSharedPreferences(name, Context.MODE_PRIVATE).edit().clear()
            for ((key, value) in values) when (value) {
                is String -> edit.putString(key, value)
                is Boolean -> edit.putBoolean(key, value)
                is Int -> edit.putInt(key, value)
                is Long -> edit.putLong(key, value)
                is Float -> edit.putFloat(key, value)
                is Set<*> -> edit.putStringSet(key, value.filterIsInstance<String>().toSet())
            }
            if (name == "daily_export") edit.putBoolean("enabled", false)
            check(edit.commit()) { "Не удалось восстановить настройки" }
        }
        savedCredentials?.let { SecureCredentialStore(app).save(it) }
        if (session.isNotEmpty()) SecureSessionStore(app).save(session)
    }

    fun runJob(context: Context, operation: String, params: JSONObject, allowPartial: Boolean = false, progress: (String) -> Unit = {}): JSONObject {
        val id = dispatch(context, operation, params).getString("job_id")
        while (true) {
            if (Thread.currentThread().isInterrupted) {
                dispatch(context, "jobs.cancel", JSONObject().put("job_id", id))
                throw InterruptedException("Операция отменена")
            }
            val job = dispatch(context, "jobs.get", JSONObject().put("job_id", id))
            progress(job.optString("message"))
            when (job.optString("state")) {
                "completed" -> return job.optJSONObject("result") ?: JSONObject()
                "partial" -> if (allowPartial) return (job.optJSONObject("result") ?: JSONObject()).put("_partial", true) else throw IllegalStateException("Операция завершилась частично; проверьте результат и повторите.")
                "failed" -> {
                    val error = job.optJSONObject("error")
                    val message = if (error?.optString("code") == "flood_wait") {
                        val seconds = error.optLong("retry_after_seconds", 0)
                        if (seconds > 0) "Telegram попросил паузу. Повторите через $seconds сек." else "Telegram попросил паузу. Повторите позже."
                    } else if (error?.optString("code") == "invalid_code") {
                        "Неверный код Telegram. Проверьте его и попробуйте снова."
                    } else if (error?.optString("code") == "expired_code") {
                        "Срок действия кода истёк. Запросите новый код."
                    } else if (error?.optString("code") == "invalid_phone") {
                        "Проверьте номер телефона вместе с кодом страны."
                    } else if (error?.optString("code") == "phone_banned") {
                        "Telegram заблокировал этот номер телефона."
                    } else if (error?.optString("code") == "code_unavailable") {
                        "Telegram отказал в повторной доставке кода. Проверьте чат Telegram в приложении, где вы уже вошли. Ранее полученный код мог перестать действовать; новый запрос попробуйте позже."
                    } else if (error?.optString("code") == "official_app_required") {
                        "Telegram требует войти через официальное приложение. Проверьте вход в официальном Telegram и попробуйте позже."
                    } else if (error?.optString("code") == "login_rate_limited") {
                        "Telegram временно ограничил попытки входа. Подождите перед повторным запросом кода."
                    } else if (error?.optString("code") == "session_duplicated") {
                        "Telegram отклонил сессию. Сохраните данные и смените сессию в настройках подключения."
                    } else if (error?.optString("code") == "invalid_password") {
                        "Неверный пароль двухэтапной проверки Telegram."
                    } else if (error?.optString("code") == "invalid_api_credentials") {
                        "Проверьте API ID и API Hash на my.telegram.org."
                    } else if (error?.optString("code") == "authentication_required") {
                        "Сначала войдите в Telegram."
                    } else if (error?.optString("code") == "network_unavailable") {
                        "Не удалось связаться с Telegram. Проверьте соединение и повторите попытку."
                    } else if (error?.optString("code") == "members_unavailable") {
                        "Не удалось получить участников выбранных групп. Проверьте доступ и права в Telegram."
                    } else if (error?.optString("code") == "conversation_unavailable") {
                        "Чат недоступен в текущем аккаунте. Обновите список чатов и проверьте доступ; настройки каталога сохранены."
                    } else if (error?.optString("code") == "chat_private") {
                        "Чат приватный или недоступен этому аккаунту. Проверьте ссылку и доступ."
                    } else if (error?.optString("code") == "chat_unavailable") {
                        "Telegram не нашёл чат или имя пользователя. Проверьте ссылку и повторите."
                    } else if (error?.optString("code") == "invite_invalid") {
                        "Ссылка-приглашение недействительна или устарела."
                    } else if (error?.optString("code") == "chat_banned") {
                        "Аккаунт заблокирован в этом чате."
                    } else {
                        val fallback = error?.optString("message").orEmpty().ifBlank { "Ошибка Telegram" }
                        val kind = error?.optString("exception_type").orEmpty().takeIf { it.matches(Regex("[A-Za-z0-9_]{1,80}")) }
                        if (kind != null && fallback == "Operation failed") "Ошибка Telegram: $kind" else fallback
                    }
                    throw TelegramJobException(error?.optString("code").orEmpty(), message)
                }
                "cancelled" -> throw InterruptedException("Операция отменена")
            }
            try {
                Thread.sleep(250)
            } catch (interrupted: InterruptedException) {
                // Sleeping clears the interrupt flag. Stop the real Telegram
                // job before returning so it doesn't continue in the background.
                try { dispatch(context, "jobs.cancel", JSONObject().put("job_id", id)) }
                finally { Thread.currentThread().interrupt() }
                throw interrupted
            }
        }
    }
}
