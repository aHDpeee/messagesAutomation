package com.signalarchive.app

import android.app.AlertDialog
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

class AboutActivity : BackSwipeActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        Appearance.apply(this)
        super.onCreate(savedInstanceState)
        val dp = resources.displayMetrics.density
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding((20 * dp).toInt(), (20 * dp).toInt(), (20 * dp).toInt(), (20 * dp).toInt())
            setBackgroundColor(Color.rgb(17, 19, 21))
        }
        content.addView(TextView(this).apply { text = "О Tegash"; textSize = 26f; setTextColor(Color.WHITE) })
        content.addView(TextView(this).apply {
            text = "Экспорт сообщений Telegram для личного архива. Проект развивается: идеи, тестирование и вклад приветствуются."
            textSize = 15f; setPadding(0, (16 * dp).toInt(), 0, (16 * dp).toInt())
        })
        content.addView(Button(this).apply {
            text = "Репозиторий и участие"
            setOnClickListener {
                AlertDialog.Builder(this@AboutActivity).setTitle("Репозиторий Tegash")
                    .setMessage("github.com/aHDpeee/messagesAutomation\n\nСообщайте об ошибках, предлагайте идеи и улучшения.")
                    .setNegativeButton("Закрыть", null).setPositiveButton("Открыть GitHub") { _, _ -> open("https://github.com/aHDpeee/messagesAutomation") }.show()
            }
        })
        content.addView(Button(this).apply { text = "Написать автору в Telegram"; setOnClickListener { open("https://t.me/ahdpeee") } })
        setContentView(content)
        SystemInsets.apply(this, content)
        Appearance.tint(window.decorView, Appearance.color(this))
    }
    private fun open(url: String) {
        runCatching { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }
            .onFailure { android.widget.Toast.makeText(this, "Не удалось открыть ссылку", android.widget.Toast.LENGTH_SHORT).show() }
    }
}
