package com.signalarchive.app

import android.app.Activity
import android.app.DatePickerDialog
import android.app.TimePickerDialog
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.view.View
import org.json.JSONObject
import java.io.File
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.concurrent.Executors

/** Explicit user-started export of selected Telegram chats into a single ZIP. */
class ExportActivity : BackSwipeActivity() {
    private val worker = Executors.newSingleThreadExecutor()
    private lateinit var status: TextView
    private lateinit var progress: ProgressBar
    private lateinit var start: EditText
    private lateinit var end: EditText
    private lateinit var startButton: Button
    private lateinit var saveButton: Button
    private lateinit var cancelButton: Button
    @Volatile private var cancelRequested = false
    @Volatile private var exportThread: Thread? = null
    private var rememberPeriod: CheckBox? = null
    private var pending: File? = null
    private var selectionKey: String = ""
    private var pendingEnd: String = ""
    private var pendingStart: String = ""
    private var pendingIds: List<String> = emptyList()
    private var pendingAccountId: String = ""
    private var pendingNameBase: String = ""
    private var working = false
    private var saving = false
    private var automaticStart: Instant? = null
    private val singleChatId: String? by lazy { intent.getStringExtra("chat_id") }
    private val blockName: String? by lazy { intent.getStringExtra("block_name") }
    private fun exportRows(): List<MutableMap<String, String>> {
        val rows = ChatCatalog(File(filesDir, "chats.csv")).load()
        return if (blockName != null) rows.filter { it["blocks"].orEmpty().equals(blockName, true) }
        else if (singleChatId == null) rows.filter { !it["blocks"].isNullOrBlank() }
        else rows.filter { it["id"] == singleChatId }
    }
    @Volatile private var destroyed = false
    private fun ui(block: () -> Unit) = runOnUiThread { if (!destroyed) block() }
    private fun dp(value: Int) = (value * resources.displayMetrics.density + .5f).toInt()

    override fun onCreate(savedInstanceState: Bundle?) {
        Appearance.apply(this)
        super.onCreate(savedInstanceState)
        val page = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(20), dp(28), dp(20), dp(20)); setBackgroundColor(Color.rgb(17,19,21)) }
        fun label(value: String, size: Float = 14f) = TextView(this).apply { text = value; textSize = size; setTextColor(Color.WHITE) }
        page.addView(label(if (blockName != null) "Экспорт блока $blockName" else if (singleChatId == null) "Экспорт Telegram" else "Экспорт одного чата", 26f))
        status = label("Период указан в местном часовом поясе. Медиа не скачиваются."); page.addView(status)
        progress = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply { visibility = View.GONE }
        page.addView(progress, LinearLayout.LayoutParams(-1, dp(12)))
        page.addView(label(if (singleChatId == null) "Сначала войдите в Telegram и обновите чаты. Выберите их группы в каталоге." else "Чат без группы будет сохранён в корне архива."))
        start = EditText(this).apply { hint = "С: ГГГГ-ММ-ДД ЧЧ:ММ"; setSingleLine(); setTextColor(Color.WHITE); setHintTextColor(Color.LTGRAY) }
        end = EditText(this).apply { hint = "По: ГГГГ-ММ-ДД ЧЧ:ММ"; setSingleLine(); setTextColor(Color.WHITE); setHintTextColor(Color.LTGRAY) }
        val today = LocalDate.now()
        val priorInstant = runCatching {
            val chats = exportRows()
            val key = selectionFingerprint(chats.map { it["id"].orEmpty() })
            ExportCheckpoint.start(this, chats.map { it["id"].orEmpty() }, key, Instant.now())
        }.getOrNull()
        val periodSettings = getSharedPreferences("single_chat_period", MODE_PRIVATE)
        val full = singleChatId != null && intent.getBooleanExtra("full", false)
        val remember = !full && singleChatId != null && periodSettings.getBoolean("$singleChatId:remember", false)
        val rememberedStart = if (remember) periodSettings.getString("$singleChatId:start", null) else null
        val rememberedEnd = if (remember) periodSettings.getString("$singleChatId:end", null) else null
        automaticStart = if (remember || full) null else priorInstant
        val dateTimeFormat = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")
        start.setText(if (full) "1970-01-02 00:00" else rememberedStart ?: (priorInstant?.atZone(ZoneId.systemDefault())?.toLocalDateTime() ?: LocalDateTime.now().minusDays(1)).format(dateTimeFormat)); end.setText(rememberedEnd ?: LocalDateTime.now().format(dateTimeFormat))
        // An explicitly edited start date means the beginning of that day,
        // even when it is the same day as the automatic checkpoint.
        start.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) { automaticStart = null }
            override fun afterTextChanged(s: Editable?) {}
        })
        fun addDate(field: EditText, caption: String) {
            val line = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
            line.addView(field, LinearLayout.LayoutParams(0, -2, 1f))
            line.addView(Button(this).apply {
                text = caption
                setOnClickListener {
                    val existing = runCatching { LocalDateTime.parse(field.text.toString().trim(), dateTimeFormat) }.getOrDefault(LocalDateTime.now())
                    val date = existing.toLocalDate()
                    DatePickerDialog(this@ExportActivity, { _, year, month, day ->
                        TimePickerDialog(this@ExportActivity, { _, hour, minute ->
                            if (field === start) automaticStart = null
                            field.setText(LocalDateTime.of(year, month + 1, day, hour, minute).format(dateTimeFormat))
                        }, existing.hour, existing.minute, true).show()
                    }, date.year, date.monthValue - 1, date.dayOfMonth).show()
                }
            })
            page.addView(line)
        }
        addDate(start, "Дата и время"); addDate(end, "Дата и время")
        if (singleChatId != null) {
            rememberPeriod = CheckBox(this).apply { text = "Запомнить этот период для чата"; setTextColor(Color.WHITE); isChecked = remember }
            page.addView(rememberPeriod)
        }
        startButton = Button(this).apply { text = "Получить сообщения и собрать ZIP"; setOnClickListener { begin() } }; page.addView(startButton)
        cancelButton = Button(this).apply { text = "Остановить экспорт"; isEnabled = false; setOnClickListener { cancelRequested = true; exportThread?.interrupt(); isEnabled = false; status.text = "Останавливаем экспорт…" } }; page.addView(cancelButton)
        page.addView(Button(this).apply { text = "К списку чатов"; setOnClickListener {
            startActivity(Intent(this@ExportActivity, CatalogActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT))
        } })
        saveButton = Button(this).apply { text = "Сохранить ZIP"; isEnabled = false; setOnClickListener { saveZip() } }; page.addView(saveButton)
        setContentView(ScrollView(this).apply { addView(page) })
        SystemInsets.apply(this, findViewById<android.view.ViewGroup>(android.R.id.content).getChildAt(0))
        Appearance.tint(window.decorView, Appearance.color(this))
        savedInstanceState?.let { saved ->
            start.setText(saved.getString("start", start.text.toString())); end.setText(saved.getString("end", end.text.toString()))
            automaticStart = saved.getString("automatic_start")?.let { runCatching { Instant.parse(it) }.getOrNull() }
            rememberPeriod?.isChecked = saved.getBoolean("remember", rememberPeriod?.isChecked == true)
            val name = saved.getString("pending")
            val file = name?.takeIf { it.matches(Regex("telegram-export-[0-9]+\\.zip")) }?.let { File(cacheDir, it) }
            val ids = saved.getStringArrayList("ids")
            val from = saved.getString("from"); val until = saved.getString("until"); val key = saved.getString("key")
            if (file?.isFile == true && ids != null && !from.isNullOrBlank() && !until.isNullOrBlank() && !key.isNullOrBlank()) {
                pending = file; pendingIds = ids; pendingStart = from; pendingEnd = until; selectionKey = key
                pendingAccountId = saved.getString("account_id", "")
                pendingNameBase = saved.getString("name_base", "")
                saveButton.isEnabled = true; status.text = if (saved.getBoolean("saving")) "Сохранение могло прерваться. Проверьте файл или сохраните архив повторно." else "Архив готов. Выберите место сохранения."
            } else if (saved.getBoolean("working")) status.text = "Экспорт был прерван при изменении экрана. Запустите его снова."
        }
        if (full && savedInstanceState == null) begin()
    }

    private fun begin() {
        try {
            val formatter = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")
            fun parse(text: String, endOfDay: Boolean): LocalDateTime = runCatching { LocalDateTime.parse(text.trim(), formatter) }.getOrElse {
                val date = LocalDate.parse(text.trim()); if (endOfDay) date.atTime(23, 59) else date.atStartOfDay()
            }
            val first = parse(start.text.toString(), false)
            val last = parse(end.text.toString(), true)
            require(!last.isBefore(first)) { "Конец периода раньше начала" }
            val zone = ZoneId.systemDefault()
            val now = Instant.now()
            val from = automaticStart?.takeIf { it < now && it.atZone(zone).toLocalDateTime().withSecond(0).withNano(0) == first } ?: first.atZone(zone).toInstant()
            val exclusiveEnd = minOf(last.plusMinutes(1).atZone(zone).toInstant(), now)
            require(from < exclusiveEnd) { "Период ещё не начался" }
            val chats = exportRows().map { GroupedArchive.Chat(it["id"].orEmpty(), it["title"].orEmpty(), it["blocks"].orEmpty().ifBlank { "Ungrouped" }, it["username"].orEmpty(), it["type"].orEmpty()) }
            require(chats.isNotEmpty()) { if (singleChatId == null) "Сначала назначьте группы чатам в каталоге" else "Чат больше не найден в каталоге" }
            val credential = SecureCredentialStore(this).load() ?: throw IllegalStateException("Сначала войдите в Telegram")
            singleChatId?.takeUnless { intent.getBooleanExtra("full", false) }?.let { id ->
                val editor = getSharedPreferences("single_chat_period", MODE_PRIVATE).edit().putBoolean("$id:remember", rememberPeriod?.isChecked == true)
                if (rememberPeriod?.isChecked == true) editor.putString("$id:start", first.format(formatter)).putString("$id:end", last.format(formatter))
                else editor.remove("$id:start").remove("$id:end")
                check(editor.commit()) { "Не удалось сохранить настройки периода" }
            }
            ExportNotifications.requestPermission(this)
            startButton.isEnabled = false; saveButton.isEnabled = false; cancelButton.isEnabled = true; pending?.delete(); pending = null; working = true; cancelRequested = false
            progress.visibility = View.VISIBLE; progress.isIndeterminate = true; progress.max = chats.size; progress.progress = 0
            ExportNotifications.progress(applicationContext, 0x534163, "Осталось чатов: ${chats.size}", 0, chats.size)
            val fingerprint = selectionFingerprint(chats.map { it.id })
            val file = File.createTempFile("telegram-export-", ".zip", cacheDir)
            worker.execute {
                exportThread = Thread.currentThread()
                val intermediates = mutableListOf<File>()
                var emptyCheckpointSaved = true
                var exportedAccountId = ""
                try {
                    if (cancelRequested) throw InterruptedException("Экспорт отменён")
                    val result = ExportLock.acquire(filesDir).use {
                      val progressId = ExportProgress.start("чатов", chats.size)
                      try {
                        val account = TelegramBridge.runJob(applicationContext, "account.status", JSONObject().put("api_id", credential.apiId).put("api_hash", credential.apiHash))
                        check(account.optString("status") == "authenticated") { "Сначала войдите в Telegram." }
                        val accountId = account.optJSONObject("account")?.optString("telegram_id").orEmpty()
                        exportedAccountId = accountId
                        val stored = getSharedPreferences("catalog_account", MODE_PRIVATE).getString("telegram_id", null)
                        CatalogAccountBinding.validate(stored, accountId, hasExistingRows = true)
                        GroupedArchive.create(file, chats, { chat ->
                        val params = JSONObject().put("dialog_id", chat.id.toLong()).put("format", "csv").put("fetch_source", true)
                            .put("api_id", credential.apiId).put("api_hash", credential.apiHash)
                            .put("expected_account_id", accountId)
                            .put("period_start", from.toString()).put("period_end", exclusiveEnd.toString())
                        try {
                            val result = TelegramBridge.runExportWithRetry(applicationContext, "messages.export", params) { note ->
                                if (note.startsWith("Пауза Telegram")) ui { status.text = note }
                            }
                            IntermediateExports.checked(filesDir, result.getString("file_path")).also(intermediates::add)
                        } catch (e: TelegramJobException) {
                            if (e.code == "empty_message_range") null else throw e
                        }
                    }, allowEmpty = true, prompts = BlockPrompts.forChats(applicationContext, accountId, chats)) { index, total, title ->
                        ExportProgress.update(progressId, total - index)
                        ExportNotifications.progress(applicationContext, 0x534163, "Осталось чатов: ${total - index}", index, total)
                        ui { progress.isIndeterminate = false; progress.progress = index; status.text = "Осталось чатов: ${total - index}" }
                    }.also { archive ->
                        // All selected ranges were checked. No ZIP is needed when
                        // they are empty, but future incremental exports should
                        // not repeatedly scan the same interval.
                        if (archive.chats == 0) {
                            if (cancelRequested || Thread.currentThread().isInterrupted) throw InterruptedException("Экспорт отменён")
                            emptyCheckpointSaved = ExportCheckpoint.advanceChats(applicationContext, chats.map { it.id }, fingerprint, from, exclusiveEnd)
                        }
                    }
                      } finally { IntermediateExports.remove(intermediates); intermediates.clear(); ExportProgress.finish(progressId) }
                    }
                    if (cancelRequested || Thread.currentThread().isInterrupted) throw InterruptedException("Экспорт отменён")
                    ui {
                        working = false; cancelButton.isEnabled = false; startButton.isEnabled = true; progress.visibility = View.GONE
                        if (cancelRequested) { file.delete(); status.text = "Экспорт отменён" }
                        else if (result.chats == 0) {
                            status.text = if (emptyCheckpointSaved) "За выбранный период новых сообщений не найдено." else "Новых сообщений нет, но не удалось сохранить дату проверки; проверьте период в следующий раз."
                            ExportNotifications.completed(applicationContext, if (emptyCheckpointSaved) "Новых сообщений нет" else "Проверьте дату следующего экспорта", 0x534153)
                        }
                        else { pending = file; selectionKey = fingerprint; pendingStart = from.toString(); pendingEnd = exclusiveEnd.toString(); pendingIds = chats.map { it.id }; pendingAccountId = exportedAccountId; pendingNameBase = ArchiveNames.base(chats.map { it.group }, from, exclusiveEnd); saveButton.isEnabled = true; status.text = "Готово: ${result.chats} чатов, ${result.messages} сообщений. Telegram может ограничивать доступную историю. Выберите место сохранения." }
                    }
                    if (result.chats > 0 && !cancelRequested) runCatching {
                        val uri = ExportNotifications.keepForSharing(applicationContext, file)
                        ExportNotifications.completed(applicationContext, "Архив готов · нажмите, чтобы поделиться", 0x534153, uri)
                    }
                } catch (e: Exception) {
                    file.delete(); ui { working = false; cancelButton.isEnabled = false; startButton.isEnabled = true; progress.visibility = View.GONE; status.text = if (cancelRequested) "Экспорт отменён" else "Экспорт не завершён: ${e.message ?: e.javaClass.simpleName}" }
                } finally { exportThread = null; ExportNotifications.clear(applicationContext, 0x534163) }
            }
        } catch (e: Exception) { status.text = "Проверьте период и настройки: ${e.message ?: e.javaClass.simpleName}" }
    }
    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString("start", start.text.toString()); outState.putString("end", end.text.toString()); outState.putBoolean("working", working)
        outState.putBoolean("saving", saving)
        outState.putString("automatic_start", automaticStart?.toString())
        rememberPeriod?.let { outState.putBoolean("remember", it.isChecked) }
        pending?.let { outState.putString("pending", it.name); outState.putString("key", selectionKey); outState.putString("from", pendingStart); outState.putString("until", pendingEnd); outState.putString("account_id", pendingAccountId); outState.putString("name_base", pendingNameBase); outState.putStringArrayList("ids", ArrayList(pendingIds)) }
    }
    private fun saveZip() {
        if (pending == null) return
        startActivityForResult(Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
            .setType("application/zip").putExtra(Intent.EXTRA_TITLE, ArchiveNames.reserve(this, pendingNameBase.ifBlank { "Tegash_export" })), 301)
    }
    @Deprecated("Document picker on supported Android versions")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        val uri = data?.data ?: return
        if (requestCode != 301 || resultCode != RESULT_OK) return
        if (data.flags and Intent.FLAG_GRANT_READ_URI_PERMISSION != 0) runCatching {
            contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        val source = pending ?: return
        saveButton.isEnabled = false; startButton.isEnabled = false; saving = true
        worker.execute {
            try {
                val checkpointSaved = ExportLock.acquire(filesDir).use {
                    DocumentExport.saveLocked(applicationContext, source, uri)
                    val current = getSharedPreferences("catalog_account", MODE_PRIVATE).getString("telegram_id", null)
                    pendingAccountId.isNotBlank() && pendingAccountId == current &&
                        runCatching { ExportCheckpoint.advanceChats(applicationContext, pendingIds, selectionKey, Instant.parse(pendingStart), Instant.parse(pendingEnd)) }.getOrDefault(false)
                }
                pending = null
                runCatching { ExportHistory.add(applicationContext, "Сообщения", uri.toString()) }
                ExportNotifications.completed(applicationContext, "Архив сообщений сохранён · нажмите, чтобы отправить", 0x534153, uri)
                ui { saving = false; startButton.isEnabled = true; status.text = if (checkpointSaved) "ZIP сохранён" else "ZIP сохранён, но дата экспорта не записана; проверьте период в следующий раз."; android.widget.Toast.makeText(this, status.text, android.widget.Toast.LENGTH_LONG).show(); saveButton.isEnabled = false }
            } catch (e: Exception) { ui { saving = false; startButton.isEnabled = true; status.text = "Не удалось сохранить ZIP: ${e.message}. Выбранный файл может быть неполным; сохраните ещё раз."; saveButton.isEnabled = true } }
        }
    }
    override fun onDestroy() { destroyed = true; worker.shutdownNow(); super.onDestroy() }

    private fun selectionFingerprint(ids: List<String>): String =
        java.security.MessageDigest.getInstance("SHA-256").digest(ids.sorted().joinToString(",").toByteArray()).joinToString("") { "%02x".format(it.toInt() and 255) }
}
