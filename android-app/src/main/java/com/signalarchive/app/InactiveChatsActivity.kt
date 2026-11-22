package com.signalarchive.app

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.text.InputType
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import java.io.File
import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime

internal object Inactivity {
    fun date(value: String): Instant? = runCatching { Instant.parse(value) }.getOrNull()
        ?: runCatching { OffsetDateTime.parse(value).toInstant() }.getOrNull()
    fun isInactive(value: String, now: Instant, days: Int): Boolean =
        date(value)?.let { !it.isAfter(now.minus(Duration.ofDays(days.toLong()))) } ?: false
}

/** Dates are only as recent as the last dialog synchronization. */
class InactiveChatsActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        Appearance.apply(this)
        super.onCreate(savedInstanceState)
        val prefs = getSharedPreferences("catalog_settings", MODE_PRIVATE)
        val page = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(24, 24, 24, 24) }
        page.addView(TextView(this).apply { text = "Неактивные чаты"; textSize = 24f })
        page.addView(TextView(this).apply { text = "Даты обновляются при обновлении списка чатов Telegram. Отсутствующая дата не означает, что сообщений нет." })
        val days = EditText(this).apply { inputType = InputType.TYPE_CLASS_NUMBER; setText(prefs.getInt("inactive_days", 30).toString()); hint = "Дней без сообщений" }
        page.addView(days)
        val status = TextView(this); page.addView(status)
        val entries = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        fun render() {
            entries.removeAllViews()
            try {
                val threshold = days.text.toString().toIntOrNull()
                require(threshold != null && threshold in 1..36500) { "Введите число дней от 1 до 36500" }
                prefs.edit().putInt("inactive_days", threshold).apply()
                val rows = ChatCatalog(File(filesDir, "chats.csv")).load()
                val availability = getSharedPreferences("dialog_availability", MODE_PRIVATE)
                val account = getSharedPreferences("catalog_account", MODE_PRIVATE).getString("telegram_id", null)
                val snapshot = if (account != null && availability.getString("account_id", null) == account) availability.getStringSet("ids", null) else null
                val emptyIds = if (snapshot != null) availability.getStringSet("empty_ids", emptySet()).orEmpty() else emptySet()
                val unansweredIds = if (snapshot != null) availability.getStringSet("unanswered_bot_ids", emptySet()).orEmpty() else emptySet()
                val pending = PendingRequests.list(this)
                if (pending.isNotEmpty()) {
                    entries.addView(TextView(this).apply { text = "Ожидают одобрения (${pending.size})"; textSize = 18f; if (pending.any { TelegramLink.from(mapOf("link" to it.second.optString("reference"))) != null }) setTextColor(Color.RED); setPadding(0, 20, 0, 4) })
                    pending.forEach { (key, entry) ->
                        val reference = entry.optString("reference")
                        val link = TelegramLink.from(mapOf("link" to reference, "username" to if (reference.startsWith("@")) reference else ""))
                        val isLink = reference.startsWith("https://", ignoreCase = true) && link != null
                        entries.addView(TextView(this).apply {
                            text = entry.optString("title").ifBlank { entry.optString("reference") } + " · запрос отправлен"
                            if (isLink) setTextColor(Color.RED)
                            setPadding(0, 12, 0, 12)
                            setOnClickListener {
                                val dialog = AlertDialog.Builder(this@InactiveChatsActivity).setTitle("Запрос на вступление")
                                    .setMessage("После одобрения проверьте участие на экране Telegram. Запись также можно убрать вручную.")
                                    .setNeutralButton("Проверить") { _, _ -> startActivity(Intent(this@InactiveChatsActivity, TelegramActivity::class.java).putExtra("reference", reference)) }
                                    .setNegativeButton("Закрыть", null).setPositiveButton("Убрать запись") { _, _ -> PendingRequests.remove(this@InactiveChatsActivity, key); render() }
                                if (link != null) dialog.setView(Button(this@InactiveChatsActivity).apply {
                                    text = "Открыть в Telegram"
                                    setOnClickListener { runCatching { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(link))) }.onFailure { status.text = "Не удалось открыть ссылку Telegram" } }
                                })
                                dialog.show()
                            }
                        })
                    }
                }
                val unavailable = rows.filter { DialogAvailability.unavailable(it["id"].orEmpty(), snapshot) }
                val unavailableIds = unavailable.map { it["id"].orEmpty() }.toSet()
                val unknown = rows.filter { it["id"].orEmpty() !in unavailableIds && it["id"].orEmpty() !in emptyIds && Inactivity.date(it["last_message_date"].orEmpty()) == null }
                val inactive = rows.filter { it["id"].orEmpty() !in unavailableIds && it["id"].orEmpty() !in unansweredIds && Inactivity.isInactive(it["last_message_date"].orEmpty(), Instant.now(), threshold) }
                fun section(title: String, list: List<MutableMap<String, String>>) {
                    entries.addView(TextView(this).apply { text = "$title (${list.size})"; textSize = 18f; setPadding(0, 20, 0, 4) })
                    list.sortedBy { it["title"].orEmpty().lowercase() }.forEach { row ->
                        val link = TelegramLink.from(row)
                        entries.addView(TextView(this).apply {
                            text = "${row["title"]} · ${row["last_message_date"].orEmpty().ifBlank { "дата неизвестна" }}" + if (link != null) " ↗" else ""
                            setPadding(0, 12, 0, 12)
                            if (link != null) { setTextColor(Color.rgb(80, 160, 255)); setOnClickListener { runCatching { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(link))) } } }
                        })
                    }
                }
                section("Нет в последнем списке Telegram", unavailable)
                section("Боты без ответа на последнее сообщение", rows.filter { it["id"].orEmpty() in unansweredIds })
                section("Чаты без сообщений", rows.filter { it["id"].orEmpty() in emptyIds })
                section("Дата последнего сообщения неизвестна", unknown)
                section("Без сообщений не менее $threshold дней", inactive)
                status.text = "Обновлено из локального каталога"
            } catch (error: Exception) { status.text = error.message ?: "Не удалось прочитать каталог" }
        }
        page.addView(Button(this).apply { text = "Применить и обновить"; setOnClickListener { render() } })
        page.addView(ScrollView(this).apply { addView(entries) }, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(page); SystemInsets.apply(this, page); Appearance.tint(window.decorView, Appearance.color(this)); render()
    }
}
