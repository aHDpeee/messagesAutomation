package com.signalarchive.app

import android.app.AlertDialog
import android.os.Bundle
import android.text.InputType
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONObject

class MailSettingsActivity : BackSwipeActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        Appearance.apply(this); super.onCreate(savedInstanceState)
        val prefs = ExportSchedule.prefs(this)
        val previous = runCatching { MailSettings.load(this) }.getOrNull()
        val body = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(32, 24, 32, 24) }
        body.addView(TextView(this).apply { text = "Отправка на почту"; textSize = 24f })
        body.addView(TextView(this).apply { text = "Архивы содержат личную переписку. Автоматическая отправка выполняется только после включения. Используйте пароль приложения почтового сервиса. Поддерживается SMTP с TLS: порт 465 или 587; архив до 20 МиБ." })
        body.addView(TextView(this).apply { text = "Фильтр в почте: тема содержит [Tegash Archive]. При включённом сроке хранения письмо также получает заголовок Expires, но почтовые сервисы не обязаны удалять его автоматически. Настройте удаление по сроку в самой почте, если сервис поддерживает такие правила." })
        fun field(label: String, key: String, secret: Boolean = false): EditText = EditText(this).apply {
            hint = label; setSingleLine(); setText(previous?.optString(key, "").orEmpty())
            if (secret) inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            body.addView(this)
        }
        val host = field("SMTP сервер, например smtp.example.com", "host")
        val port = field("Порт: 465 или 587", "port").apply { inputType = InputType.TYPE_CLASS_NUMBER; if (text.isEmpty()) setText("465") }
        val username = field("Отправитель / логин", "username")
        val password = field("Пароль приложения", "password", true)
        val recipient = field("Получатель", "recipient")
        val enabled = CheckBox(this).apply { text = "Отправлять архивы при автоэкспорте"; isChecked = prefs.getBoolean("email_enabled", false) }; body.addView(enabled)
        body.addView(Button(this).apply { text = "Сохранить"; setOnClickListener {
            try {
                val h = host.text.toString().trim(); val p = port.text.toString().toIntOrNull()
                val u = username.text.toString().trim(); val pass = password.text.toString(); val to = recipient.text.toString().trim()
                require(h.matches(Regex("[A-Za-z0-9.-]{1,253}")) && p in listOf(465, 587) && u.isNotBlank() && pass.isNotBlank() && android.util.Patterns.EMAIL_ADDRESS.matcher(to).matches()) { "Проверьте сервер, порт, логин, пароль и адрес получателя" }
                MailSettings.save(this@MailSettingsActivity, JSONObject().put("host", h).put("port", p).put("username", u).put("password", pass).put("recipient", to))
                check(prefs.edit().putBoolean("email_enabled", enabled.isChecked).commit())
                android.widget.Toast.makeText(this@MailSettingsActivity, "Настройки сохранены", android.widget.Toast.LENGTH_SHORT).show(); finish()
            } catch (error: Exception) { AlertDialog.Builder(this@MailSettingsActivity).setMessage(error.message ?: "Не удалось сохранить настройки").setPositiveButton("Понятно", null).show() }
        } })
        setContentView(ScrollView(this).apply { addView(body) }); SystemInsets.apply(this, findViewById<android.view.ViewGroup>(android.R.id.content).getChildAt(0)); Appearance.tint(window.decorView, Appearance.color(this))
    }
}
