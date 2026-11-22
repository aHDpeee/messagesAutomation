package com.signalarchive.app

import android.content.Context
import com.chaquo.python.Python
import org.json.JSONObject
import java.io.File

internal object MailSettings {
    private fun store(context: Context) = SecureSessionStore(context, "tegash-mail-v1", "encrypted_mail_settings")
    fun load(context: Context): JSONObject? = store(context).load().takeIf { it.isNotBlank() }?.let(::JSONObject)
    fun save(context: Context, value: JSONObject) { store(context).save(value.toString()) }
    fun send(context: Context, archive: File, filename: String) {
        val config = load(context) ?: error("Почта не настроена")
        check(Python.isStarted()) { "Сервис экспорта не запущен" }
        Python.getInstance().getModule("tg_observatory.mail_sender").callAttr("send", config.getString("host"), config.getInt("port"),
            config.getString("username"), config.getString("password"), config.getString("recipient"), archive.absolutePath, filename)
    }
}
