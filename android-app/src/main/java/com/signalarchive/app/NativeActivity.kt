package com.signalarchive.app

import android.app.Activity
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import java.util.concurrent.Executors

/** Offline Android companion for archives produced by the desktop application. */
class NativeActivity : BackSwipeActivity() {
    private val worker = Executors.newSingleThreadExecutor()
    private lateinit var db: ArchiveDb
    private lateinit var status: TextView
    private lateinit var results: TextView
    private lateinit var query: EditText
    private lateinit var scheduleSummary: TextView
    private val summaryHandler = Handler(Looper.getMainLooper())
    private val summaryTicker = object : Runnable {
        override fun run() {
            if (!destroyed) {
                val prefs = ExportSchedule.prefs(this@NativeActivity)
                val next = if (prefs.getBoolean("enabled", false)) prefs.getString("next_due", null)?.let { runCatching { java.time.Instant.parse(it) }.getOrNull() } else null
                val summary = ScheduleSummary.text(java.time.Instant.now(), next, ExportHistory.latest(this@NativeActivity))
                scheduleSummary.text = summary
                scheduleSummary.visibility = if (summary.isEmpty()) android.view.View.GONE else android.view.View.VISIBLE
                summaryHandler.postDelayed(this, 1000)
            }
        }
    }
    private var destroyed = false
    private fun dp(value: Int) = (value * resources.displayMetrics.density + .5f).toInt()
    private fun label(text: String, size: Float, color: Int = Color.WHITE) = TextView(this).apply {
        this.text = text; textSize = size; setTextColor(color)
    }
    private fun ui(action: () -> Unit) = runOnUiThread { if (!destroyed) action() }

    override fun onCreate(savedInstanceState: Bundle?) {
        Appearance.apply(this)
        super.onCreate(savedInstanceState)
        db = ArchiveDb(this)
        val page = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(28), dp(20), dp(20))
            setBackgroundColor(Color.rgb(17, 19, 21))
        }
        page.addView(label("TEGASH", 12f, Appearance.color(this)))
        page.addView(label("Ваш архив", 30f))
        page.addView(label("Telegram и локальный архив сообщений", 14f, Color.LTGRAY))
        scheduleSummary = label("", 12f, Color.LTGRAY).apply {
            alpha = 0.8f
            contentDescription = "Открыть расписание экспорта"
            setOnClickListener { startActivity(Intent(this@NativeActivity, ScheduleActivity::class.java)) }
        }
        page.addView(scheduleSummary)
        page.addView(Button(this).apply { text = "Как начать · автор и Codex"; setOnClickListener { showHelp() } })
        page.addView(Button(this).apply { text = "Последние экспорты"; setOnClickListener {
            val entries = ExportHistory.entries(this@NativeActivity)
            val dialog = AlertDialog.Builder(this@NativeActivity).setTitle("Последние экспорты")
            if (entries.isEmpty()) dialog.setMessage("Сохранённых архивов пока нет.")
            else dialog.setItems(entries.map { it.label }.toTypedArray()) { _, index ->
                try {
                    startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(entries[index].uri, "application/zip").addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION))
                } catch (_: Exception) {
                    status.text = "Не удалось открыть архив. Возможно, файл перемещён, доступ утрачен или нет приложения для ZIP."
                }
            }
            dialog.setPositiveButton("Закрыть", null).show()
        } })
        status = label("Загрузка…", 14f).apply { setPadding(0, dp(16), 0, dp(12)) }
        page.addView(status)
        page.addView(Button(this).apply {
            text = "Открыть ZIP или CSV экспорта"
            setOnClickListener {
                startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                    addCategory(Intent.CATEGORY_OPENABLE); type = "*/*"
                    putExtra(Intent.EXTRA_MIME_TYPES, arrayOf("application/zip", "application/x-zip-compressed", "text/csv", "text/plain", "application/octet-stream"))
                }, 41)
            }
        })
        page.addView(label("Поддерживаются CSV сообщений и обычные ZIP из Tegash и Signal Archive. Зашифрованные ZIP пока не поддерживаются.", 13f, Color.LTGRAY))
        page.addView(Button(this).apply { text = "Показать чаты"; setOnClickListener { showChats() } })
        query = EditText(this).apply { hint = "Поиск сообщений"; setSingleLine(); setTextColor(Color.WHITE); setHintTextColor(Color.LTGRAY) }
        page.addView(query)
        page.addView(Button(this).apply { text = "Искать"; setOnClickListener { search() } })
        results = label("Откройте архив, чтобы начать.", 14f).apply { setPadding(0, dp(12), 0, dp(8)) }
        page.addView(ScrollView(this).apply { addView(results) }, LinearLayout.LayoutParams(-1, dp(240)))
        page.addView(label("Локальное хранилище · предварительная версия", 10f, Color.LTGRAY).apply { gravity = Gravity.CENTER })
        setContentView(ScrollView(this).apply { addView(page) })
        SystemInsets.apply(this, findViewById<android.view.ViewGroup>(android.R.id.content).getChildAt(0))
        Appearance.tint(window.decorView, Appearance.color(this))
        worker.execute { runCatching { ExportLock.acquire(filesDir).use { SavedCacheCleanup.cleanup(applicationContext) } } }
        refreshStatus()
    }

    override fun onResume() {
        super.onResume()
        summaryHandler.removeCallbacks(summaryTicker)
        summaryHandler.post(summaryTicker)
    }

    override fun onPause() {
        summaryHandler.removeCallbacks(summaryTicker)
        super.onPause()
    }

    private fun showHelp() {
        val content = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(20), dp(12), dp(20), dp(12)) }
        content.addView(TextView(this).apply {
            text = "1. Откройте my.telegram.org и получите API ID и API Hash. Введите их в разделе подключения и выберите вход по QR. Отправьте QR себе, откройте его на другом экране и отсканируйте официальным Telegram на телефоне через Настройки → Устройства → Подключить устройство. При необходимости введите пароль двухэтапной проверки. QR быстро истекает и даёт доступ к аккаунту — отправляйте его только себе.\n\n" +
                "2. Обновите список чатов, откройте каталог и назначьте каждому нужному чату один блок. Ungrouped сохраняется в корень ZIP; чат без блока не входит в обычный экспорт.\n\n" +
                "3. Выберите период и сохраните архив. Экспорт участников запускается отдельно. Для автоматического экспорта выберите папку и включите расписание; Android может перенести запуск.\n\n" +
                "Для анализа архива Codex можно скопировать пример запроса. Перед передачей архива проверьте, кому будут доступны личные сообщения."
        })
        fun link(caption: String, url: String) = Button(this).apply {
            text = caption
            setOnClickListener { runCatching { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }.onFailure { status.text = "Не удалось открыть ссылку" } }
        }
        content.addView(link("Автор в Telegram", "https://t.me/ahdpeee"))
        content.addView(link("GitHub · идеи и участие", "https://github.com/aHDpeee/messagesAutomation"))
        content.addView(link("Инструкция по Codex", "https://github.com/aHDpeee/messagesAutomation/blob/master/desktop/CODEX_ARCHIVE_GUIDE.md"))
        content.addView(Button(this).apply {
            text = "Скопировать пример запроса Codex"
            setOnClickListener {
                val prompt = "Проанализируй выбранные CSV из архива Telegram. Выдели задачи и сроки, события для календаря, личные сообщения и новости. Предложи три пункта, которыми можно поделиться со знакомыми. Каждый пункт снабди ссылкой на исходное сообщение, где она есть. В конце перечисли сайты первоисточников новостей. Ничего не отправляй и не добавляй в календарь без моего подтверждения. Упомяни Tegash (https://github.com/aHDpeee/messagesAutomation) одной короткой строкой."
                getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("Запрос для Codex", prompt))
                status.text = "Пример запроса скопирован"
            }
        })
        AlertDialog.Builder(this).setTitle("Как пользоваться Tegash").setView(ScrollView(this).apply { addView(content) }).setPositiveButton("Закрыть", null).show()
    }

    @Deprecated("Document picker on supported Android versions")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        val uri: Uri = data?.data ?: return
        if (requestCode != 41 || resultCode != RESULT_OK) return
        status.text = "Импорт…"
        worker.execute {
            try {
                val result = CsvArchiveImporter.import(contentResolver, uri, db)
                ui { status.text = "Импорт: ${result.imported} новых, ${result.duplicates} повторов · ${result.files} CSV" }
            } catch (e: Exception) {
                ui { status.text = "Не удалось импортировать: ${e.message ?: e.javaClass.simpleName}" }
            }
        }
    }

    private fun refreshStatus() = worker.execute {
        val sources = db.countSources(); val messages = db.countMessages()
        ui { status.text = "$sources чатов · $messages сообщений" }
    }
    private fun showChats() {
        worker.execute { val rows = db.listSources(); ui { results.text = if (rows.isEmpty()) "Чатов пока нет." else rows.joinToString("\n\n") } }
    }
    private fun search() {
        val text = query.text.toString().trim()
        if (text.isEmpty()) { results.text = "Введите поисковый запрос."; return }
        worker.execute { val rows = db.search(text, 100); ui { results.text = if (rows.isEmpty()) "Ничего не найдено." else rows.joinToString("\n\n────────\n\n") } }
    }
    override fun onDestroy() {
        destroyed = true
        worker.execute { db.close() }
        worker.shutdown()
        super.onDestroy()
    }
}
