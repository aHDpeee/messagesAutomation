package com.signalarchive.app

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.text.InputFilter
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.view.View
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.LocalDate
import java.time.Instant
import java.util.concurrent.Executors
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/** Explicit, user-started export of visible participants, without profile scraping. */
class MembersExportActivity : Activity() {
    private val worker = Executors.newSingleThreadExecutor()
    @Volatile private var destroyed = false
    private lateinit var status: TextView
    private lateinit var progress: ProgressBar
    private lateinit var allButton: Button
    private lateinit var selectedButton: Button
    private lateinit var manualButton: Button
    private lateinit var saveButton: Button
    private lateinit var cancelButton: Button
    @Volatile private var cancelRequested = false
    @Volatile private var exportThread: Thread? = null
    private lateinit var profiles: CheckBox
    private lateinit var plan: EditText
    private var pending: File? = null
    private var pendingIds: Set<String> = emptySet()
    private var pendingAccountId: String = ""
    private var working = false
    private var saving = false
    private val manualGroups = mutableMapOf<String, CheckBox>()
    private val singleGroupId: String? by lazy { intent.getStringExtra("group_id") }
    private fun ui(action: () -> Unit) = runOnUiThread { if (!destroyed) action() }

    override fun onCreate(savedInstanceState: Bundle?) {
        Appearance.apply(this)
        super.onCreate(savedInstanceState)
        val page = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(24, 24, 24, 24) }
        page.addView(TextView(this).apply { text = "Экспорт участников"; textSize = 24f })
        page.addView(TextView(this).apply { text = "Telegram может скрывать участников и поля. В ZIP попадут только доступные данные." })
        status = TextView(this).apply { text = "Войдите в Telegram и обновите список чатов." }
        page.addView(status)
        progress = ProgressBar(this).apply { visibility = View.GONE }
        page.addView(progress)
        profiles = CheckBox(this).apply {
            text = "Биографии и личные каналы (до 100 на группу, медленнее)"
            isChecked = getSharedPreferences("member_settings", MODE_PRIVATE).getBoolean("profiles", false)
            setOnCheckedChangeListener { _, checked -> getSharedPreferences("member_settings", MODE_PRIVATE).edit().putBoolean("profiles", checked).apply() }
        }
        page.addView(profiles)
        plan = EditText(this).apply { hint = "Метка плана (необязательно)"; filters = arrayOf(InputFilter.LengthFilter(200)); setSingleLine(true) }
        page.addView(plan)
        allButton = Button(this).apply { text = "Участники всех групп"; setOnClickListener { begin("all") } }
        selectedButton = Button(this).apply { text = "Участники групп, выбранных для экспорта"; setOnClickListener { begin("selected") } }
        manualButton = Button(this).apply { text = "Участники отмеченных ниже групп"; setOnClickListener { begin("manual") } }
        saveButton = Button(this).apply { text = "Сохранить ZIP"; isEnabled = false; setOnClickListener { save() } }
        cancelButton = Button(this).apply { text = "Остановить экспорт"; isEnabled = false; setOnClickListener { cancelRequested = true; exportThread?.interrupt(); isEnabled = false; status.text = "Останавливаем экспорт…" } }
        if (singleGroupId == null) { page.addView(allButton); page.addView(selectedButton); page.addView(manualButton) }
        else page.addView(Button(this).apply { text = "Получить участников этой группы"; setOnClickListener { if (!working) begin("single") } })
        page.addView(cancelButton); page.addView(saveButton)
        try {
            ChatCatalog(File(filesDir, "chats.csv")).load().filter { it["type"] == "group" && (singleGroupId == null || it["id"] == singleGroupId) }.forEach { row ->
                val id = row["id"].orEmpty()
                val title = row["title"].orEmpty() + " · " + row["blocks"].orEmpty().ifBlank { "без блока" }
                if (singleGroupId == null) {
                    val box = CheckBox(this).apply { text = title }
                    manualGroups[id] = box; page.addView(box)
                } else page.addView(TextView(this).apply { text = title })
            }
        } catch (error: Exception) { status.text = "Не удалось прочитать каталог: ${error.message}" }
        setContentView(ScrollView(this).apply { addView(page) })
        SystemInsets.apply(this, findViewById<android.view.ViewGroup>(android.R.id.content).getChildAt(0))
        Appearance.tint(window.decorView, Appearance.color(this))
        savedInstanceState?.let { saved ->
            profiles.isChecked = saved.getBoolean("profiles", profiles.isChecked)
            plan.setText(saved.getString("plan", ""))
            val checked = saved.getStringArrayList("checked_groups")?.toSet().orEmpty()
            manualGroups.forEach { (id, box) -> box.isChecked = id in checked }
            val name = saved.getString("pending")
            val file = name?.takeIf { it.matches(Regex("members-[0-9]+\\.zip")) }?.let { File(cacheDir, it) }
            val ids = saved.getStringArrayList("ids")
            if (file?.isFile == true && ids != null) {
                pending = file; pendingIds = ids.toSet(); pendingAccountId = saved.getString("account_id", ""); saveButton.isEnabled = true; status.text = if (saved.getBoolean("saving")) "Сохранение могло прерваться. Проверьте файл или сохраните архив повторно." else "Архив готов. Выберите место сохранения."
            } else if (saved.getBoolean("working")) status.text = "Экспорт был прерван при изменении экрана. Запустите его снова."
        }
    }

    private fun begin(scope: String) {
        try {
            val checked = manualGroups.filterValues { it.isChecked }.keys.toSet()
            val rows = ChatCatalog(File(filesDir, "chats.csv")).load().filter {
                it["type"] == "group" && when (scope) {
                    "all" -> true
                    "selected" -> !it["blocks"].isNullOrBlank()
                    "manual" -> it["id"] in checked
                    "single" -> it["id"] == singleGroupId
                    else -> false
                }
            }
            require(rows.isNotEmpty()) { "Нет подходящих групп. Обновите чаты и проверьте каталог." }
            val credentials = SecureCredentialStore(this).load() ?: error("Сначала войдите в Telegram")
            ExportNotifications.requestPermission(this)
            val groups = JSONArray()
            rows.forEach { row -> groups.put(JSONObject().put("id", row["id"]).put("block", row["blocks"].orEmpty())) }
            val params = JSONObject().put("api_id", credentials.apiId).put("api_hash", credentials.apiHash).put("groups", groups).put("include_profiles", profiles.isChecked).put("plan", plan.text.toString())
            allButton.isEnabled = false; selectedButton.isEnabled = false; manualButton.isEnabled = false; saveButton.isEnabled = false
            pending?.delete(); pending = null; pendingIds = emptySet(); working = true; cancelRequested = false; cancelButton.isEnabled = true; status.text = "Получаем участников…"; progress.visibility = View.VISIBLE
            ExportNotifications.progress(applicationContext, 0x534164, "Получаем участников…")
            worker.execute {
                exportThread = Thread.currentThread()
                var archive: File? = null
                var intermediate: File? = null
                try {
                    if (cancelRequested) throw InterruptedException("Экспорт отменён")
                    ExportLock.acquire(filesDir).use {
                    val progressId = ExportProgress.start("групп", rows.size)
                    try {
                    val accountParams = JSONObject().put("api_id", credentials.apiId).put("api_hash", credentials.apiHash)
                    val account = TelegramBridge.runJob(applicationContext, "account.status", accountParams)
                    check(account.optString("status") == "authenticated") { "Сначала войдите в Telegram." }
                    val accountId = account.optJSONObject("account")?.optString("telegram_id").orEmpty()
                    val stored = getSharedPreferences("catalog_account", MODE_PRIVATE).getString("telegram_id", null)
                    CatalogAccountBinding.validate(stored, accountId, hasExistingRows = true)
                    params.put("expected_account_id", accountId)
                    val result = TelegramBridge.runJob(applicationContext, "members.export_csv", params, allowPartial = true) { message ->
                        if (message.isNotBlank()) ui { status.text = message }
                        if (message.startsWith("Осталось групп:")) {
                            message.substringAfter(':').trim().toIntOrNull()?.let { ExportProgress.update(progressId, it) }
                            ExportNotifications.progress(applicationContext, 0x534164, message)
                        }
                    }
                    val source = IntermediateExports.checked(filesDir, result.getString("file_path")); intermediate = source
                    val zip = File.createTempFile("members-", ".zip", cacheDir); archive = zip
                    ZipOutputStream(zip.outputStream()).use { out ->
                        out.putNextEntry(ZipEntry("members.csv")); source.inputStream().use { DocumentExport.copy(it, out) }; out.closeEntry()
                    }
                    val completed = result.optJSONArray("completed_group_ids") ?: JSONArray()
                    val exportedIds = (0 until completed.length()).map { completed.getString(it) }.toSet()
                    val failedGroups = result.optInt("failed_groups")
                    val failed = result.optJSONArray("failed_group_ids") ?: JSONArray()
                    val failedIds = (0 until failed.length()).map { failed.getString(it) }.toSet()
                    val failedNames = rows.filter { it["id"] in failedIds }.joinToString(", ") { it["title"].orEmpty() }
                    if (cancelRequested || Thread.currentThread().isInterrupted) throw InterruptedException("Экспорт отменён")
                    ui {
                        working = false; cancelButton.isEnabled = false; allButton.isEnabled = true; selectedButton.isEnabled = true; manualButton.isEnabled = true; progress.visibility = View.GONE
                        if (cancelRequested) { zip.delete(); status.text = "Экспорт отменён" }
                        else { pending = zip; pendingIds = exportedIds; pendingAccountId = accountId; saveButton.isEnabled = true; status.text = "Получено участников: ${result.optInt("members_exported")}. " + (if (failedGroups > 0) "Недоступно групп: $failedGroups ($failedNames). Их данные в архив не включены. " else "") + "Доступность определяет Telegram. Сохраните ZIP." }
                    }
                    } finally { intermediate?.let { IntermediateExports.remove(listOf(it)); intermediate = null }; ExportProgress.finish(progressId) }
                    }
                } catch (error: Exception) {
                    archive?.delete()
                    ui { working = false; cancelButton.isEnabled = false; allButton.isEnabled = true; selectedButton.isEnabled = true; manualButton.isEnabled = true; progress.visibility = View.GONE; status.text = if (cancelRequested) "Экспорт отменён" else "Экспорт не завершён: ${error.message ?: error.javaClass.simpleName}" }
                } finally { exportThread = null; ExportNotifications.clear(applicationContext, 0x534164) }
            }
        } catch (error: Exception) { status.text = error.message ?: "Не удалось начать экспорт" }
    }
    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putBoolean("working", working)
        outState.putBoolean("saving", saving)
        outState.putBoolean("profiles", profiles.isChecked)
        outState.putString("plan", plan.text.toString())
        outState.putStringArrayList("checked_groups", ArrayList(manualGroups.filterValues { it.isChecked }.keys))
        pending?.let { outState.putString("pending", it.name); outState.putString("account_id", pendingAccountId); outState.putStringArrayList("ids", ArrayList(pendingIds)) }
    }

    private fun save() {
        if (pending == null) return
        startActivityForResult(Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
            .setType("application/zip").putExtra(Intent.EXTRA_TITLE, "Telegram_members_${LocalDate.now()}.zip"), 302)
    }

    @Deprecated("Document picker on supported Android versions")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        val uri = data?.data ?: return
        if (requestCode != 302 || resultCode != RESULT_OK) return
        if (data.flags and Intent.FLAG_GRANT_READ_URI_PERMISSION != 0) runCatching {
            contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        val source = pending ?: return
        val exportedIds = pendingIds
        saveButton.isEnabled = false; allButton.isEnabled = false; selectedButton.isEnabled = false; manualButton.isEnabled = false; working = true; saving = true
        worker.execute {
            try {
                val update = ExportLock.acquire(filesDir).use {
                DocumentExport.saveLocked(applicationContext, source, uri)
                runCatching {
                    check(pendingAccountId.isNotBlank() && pendingAccountId == getSharedPreferences("catalog_account", MODE_PRIVATE).getString("telegram_id", null)) { "Аккаунт изменился" }
                    val catalog = ChatCatalog(File(filesDir, "chats.csv"))
                    val (rows, version) = catalog.loadVersioned()
                    val stamp = Instant.now().toString()
                    rows.filter { it["id"] in exportedIds }.forEach { it["members_exported_at"] = stamp }
                    catalog.save(rows, expectedVersion = version)
                } }
                pending = null; pendingIds = emptySet()
                runCatching { ExportHistory.add(applicationContext, "Участники", uri.toString()) }
                ExportNotifications.completed(applicationContext, "Архив участников сохранён · нажмите, чтобы отправить", 0x534154, uri)
                ui { working = false; saving = false; allButton.isEnabled = true; selectedButton.isEnabled = true; manualButton.isEnabled = true; status.text = if (update.isSuccess) "ZIP сохранён" else "ZIP сохранён; дата в каталоге не обновлена. Обновите список и проверьте каталог."; android.widget.Toast.makeText(this, status.text, android.widget.Toast.LENGTH_LONG).show() }
            } catch (error: Exception) { ui { working = false; saving = false; allButton.isEnabled = true; selectedButton.isEnabled = true; manualButton.isEnabled = true; status.text = "Не удалось сохранить ZIP: ${error.message}. Выбранный файл может быть неполным; сохраните ещё раз."; saveButton.isEnabled = true } }
        }
    }

    override fun onDestroy() { destroyed = true; worker.shutdownNow(); super.onDestroy() }
}
