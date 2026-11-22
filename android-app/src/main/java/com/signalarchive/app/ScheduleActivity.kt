package com.signalarchive.app

import android.app.Activity
import android.Manifest
import android.content.pm.PackageManager
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Build
import android.provider.DocumentsContract
import android.text.InputType
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import java.time.LocalTime

/** User chooses a folder before enabling automatic exports. */
class ScheduleActivity : BackSwipeActivity() {
    private lateinit var enabled: CheckBox
    private lateinit var time: EditText
    private lateinit var status: TextView
    private lateinit var retention: CheckBox
    private lateinit var days: EditText
    private lateinit var email: CheckBox
    private var tree: Uri? = null
    override fun onCreate(savedInstanceState: Bundle?) {
        Appearance.apply(this)
        super.onCreate(savedInstanceState)
        val prefs = ExportSchedule.prefs(this)
        tree = (savedInstanceState?.getString("tree") ?: prefs.getString("tree", null))?.let(Uri::parse)
        val body = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(24, 24, 24, 24) }
        body.addView(TextView(this).apply { text = "Автоматический экспорт"; textSize = 24f })
        body.addView(TextView(this).apply { text = "Раз в день при доступной сети. Android может перенести время запуска; экспорт выполняется в выбранную папку без медиа." })
        enabled = CheckBox(this).apply { text = "Включить ежедневный экспорт"; isChecked = prefs.getBoolean("enabled", false) }; body.addView(enabled)
        val mailConfigured = runCatching { MailSettings.load(this) != null }.getOrDefault(false)
        email = CheckBox(this).apply {
            text = if (mailConfigured) "Отправлять архив на почту" else "Настройте почту, чтобы включить отправку"
            isEnabled = mailConfigured; isChecked = mailConfigured && prefs.getBoolean("email_enabled", false)
        }; body.addView(email)
        body.addView(Button(this).apply { text = "Настроить почту"; setOnClickListener { startActivity(Intent(this@ScheduleActivity, MailSettingsActivity::class.java)) } })
        time = EditText(this).apply { hint = "Время ЧЧ:ММ"; setText(prefs.getString("time", "03:15")); setSingleLine() }; body.addView(time)
        retention = CheckBox(this).apply { text = "Удалять старые автоматические архивы"; isChecked = prefs.getBoolean("retention_enabled", false) }; body.addView(retention)
        days = EditText(this).apply { hint = "Хранить дней"; inputType = InputType.TYPE_CLASS_NUMBER; setText(prefs.getInt("retention_days", 30).toString()); setSingleLine() }; body.addView(days)
        body.addView(TextView(this).apply { text = "Удаляются только учтённые архивы, созданные приложением в выбранной папке. Другие файлы не затрагиваются." })
        status = TextView(this).apply {
            setOnClickListener {
                val selected = tree ?: return@setOnClickListener
                try {
                    val folder = DocumentsContract.buildDocumentUriUsingTree(selected, DocumentsContract.getTreeDocumentId(selected))
                    startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(folder, DocumentsContract.Document.MIME_TYPE_DIR)
                        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION))
                } catch (_: Exception) { text = "Не удалось открыть папку. Найдите её в файловом менеджере или выберите заново." }
            }
        }; body.addView(status)
        fun update() { status.text = "Папка: ${tree ?: "не выбрана"}${if (tree != null) " (нажмите, чтобы открыть)" else ""}\n${prefs.getString("status", "")}" }
        body.addView(Button(this).apply { text = "Выбрать папку"; setOnClickListener { startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT_TREE).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION), 501) } })
        body.addView(Button(this).apply { text = "Сохранить расписание"; setOnClickListener {
            try {
                val value = time.text.toString().trim()
                require(value.matches(Regex("(?:[01][0-9]|2[0-3]):[0-5][0-9]"))) { "Введите время в формате ЧЧ:ММ" }
                LocalTime.parse(value)
                val retentionDays = days.text.toString().toIntOrNull()
                require(retentionDays != null && retentionDays in 1..36500) { "Укажите срок хранения от 1 до 36500 дней" }
                if (enabled.isChecked) {
                    require(tree != null) { "Сначала выберите папку" }
                    require(contentResolver.persistedUriPermissions.any { it.uri == tree && it.isReadPermission && it.isWritePermission }) { "Доступ к папке потерян. Выберите папку заново." }
                    require(SecureCredentialStore(this@ScheduleActivity).load() != null) { "Сначала подключите Telegram" }
                }
                check(prefs.edit().putBoolean("enabled", enabled.isChecked).putString("time", value).putString("tree", tree?.toString()).putBoolean("retention_enabled", retention.isChecked).putInt("retention_days", retentionDays).putBoolean("email_enabled", email.isChecked).commit())
                try { ExportSchedule.schedule(this@ScheduleActivity) } catch (error: Exception) {
                    prefs.edit().putBoolean("enabled", false).commit(); throw error
                }
                if (enabled.isChecked && Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                    requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 502)
                }
                update(); status.append("\nНастройки сохранены")
            } catch (error: Exception) { status.text = error.message ?: "Не удалось сохранить настройки" }
        } })
        setContentView(ScrollView(this).apply { addView(body) })
        SystemInsets.apply(this, findViewById<android.view.ViewGroup>(android.R.id.content).getChildAt(0))
        Appearance.tint(window.decorView, Appearance.color(this))
        savedInstanceState?.let { saved ->
            enabled.isChecked = saved.getBoolean("enabled", enabled.isChecked)
            time.setText(saved.getString("time", time.text.toString()))
            retention.isChecked = saved.getBoolean("retention", retention.isChecked)
            days.setText(saved.getString("days", days.text.toString()))
        }
        update()
    }
    override fun onResume() {
        super.onResume()
        if (::email.isInitialized) {
            val configured = runCatching { MailSettings.load(this) != null }.getOrDefault(false)
            email.isEnabled = configured
            email.text = if (configured) "Отправлять архив на почту" else "Настройте почту, чтобы включить отправку"
            email.isChecked = configured && ExportSchedule.prefs(this).getBoolean("email_enabled", false)
        }
    }
    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString("tree", tree?.toString()); outState.putBoolean("enabled", enabled.isChecked)
        outState.putString("time", time.text.toString()); outState.putBoolean("retention", retention.isChecked); outState.putString("days", days.text.toString())
    }
    @Deprecated("Document picker on supported Android versions")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        val uri = data?.data ?: return
        if (requestCode != 501 || resultCode != RESULT_OK) return
        try {
            val required = Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
            require(data.flags and required == required) { "Нужен доступ к чтению и записи папки." }
            contentResolver.takePersistableUriPermission(uri, required)
            tree = uri
            status.text = "Папка: $uri (нажмите, чтобы открыть)\nСохраните настройки."
        } catch (error: Exception) { status.text = "Не удалось получить постоянный доступ к папке: ${error.message}" }
    }
}
