package com.signalarchive.app

import android.content.ContentResolver
import android.net.Uri
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.FilterInputStream
import java.io.InputStream
import java.util.zip.ZipException
import java.util.zip.ZipInputStream

/** Offline import of the desktop app's text CSV files; no network or media. */
internal object CsvArchiveImporter {
    data class Result(val imported: Int, val duplicates: Int, val files: Int)
    private const val LIMIT = 256L * 1024 * 1024

    fun import(resolver: ContentResolver, uri: Uri, db: ArchiveDb): Result {
        var total = 0L
        var imported = 0
        var duplicates = 0
        var files = 0
        val sql = db.writableDatabase
        sql.beginTransaction()
        try {
            resolver.openInputStream(uri)?.use { raw ->
                val input = BufferedInputStream(raw)
                input.mark(4)
                val zip = input.read() == 'P'.code && input.read() == 'K'.code
                input.reset()
                fun bounded(stream: InputStream) = object : FilterInputStream(stream) {
                    private fun count(n: Int) {
                        if (n > 0) {
                            total += n
                            if (total > LIMIT) throw IllegalArgumentException("Распакованный архив слишком большой")
                        }
                    }
                    override fun read(): Int = super.read().also { if (it >= 0) count(1) }
                    override fun read(buffer: ByteArray, offset: Int, length: Int): Int =
                        super.read(buffer, offset, length).also { count(it) }
                    override fun close() {} // ZIP reader owns the underlying entry stream.
                }
                fun consume(stream: InputStream) {
                    val reader = CsvReader(StrictUtf8.reader(stream))
                    val header = reader.next()?.map { it.trimStart('\uFEFF') } ?: return
                    if (!header.containsAll(listOf("chat_id", "id", "text"))) return
                    if (header.size != header.toSet().size) throw IllegalArgumentException("Повторяющиеся столбцы CSV")
                    files++
                    while (true) {
                        val values = reader.next() ?: break
                        if (values.size != header.size) throw IllegalArgumentException("Некорректная строка CSV")
                        val data = header.zip(values).toMap()
                        if (data["record_type"]?.let { it.isNotEmpty() && it != "message" } == true) continue
                        val chat = data["chat_id"].orEmpty()
                        val id = data["id"].orEmpty()
                        if (chat.isBlank() || id.isBlank()) continue
                        val title = data["chat_title"].orEmpty().ifBlank { chat }
                        val date = data["date"].orEmpty()
                        val sender = data["sender"].orEmpty()
                        val text = data["text"].orEmpty()
                        db.ensureSource(chat, title, data["chat_type"].orEmpty())
                        val inserted = db.insertMessage(chat, id, date, sender, text,
                            data["reply_to_message_id"].orEmpty().ifBlank { null }, JSONObject(data).toString())
                        if (inserted == -1L) duplicates++ else imported++
                    }
                }
                if (zip) {
                    ZipInputStream(input).use { archive ->
                        while (true) {
                            val entry = archive.nextEntry ?: break
                            val limited = bounded(archive)
                            if (!entry.isDirectory && entry.name.endsWith(".csv", ignoreCase = true)) consume(limited)
                            val buffer = ByteArray(8192)
                            while (limited.read(buffer) != -1) { /* count even ignored ZIP entries */ }
                            archive.closeEntry()
                        }
                    }
                } else consume(bounded(input))
            } ?: throw IllegalArgumentException("Не удалось открыть файл")
            if (files == 0) throw IllegalArgumentException("В файле нет CSV сообщений Tegash или Signal Archive")
            sql.setTransactionSuccessful()
        } catch (error: ZipException) {
            val hint = if (error.message?.contains("encrypt", ignoreCase = true) == true) "Зашифрованный ZIP пока не поддерживается" else "Повреждённый или неподдерживаемый ZIP"
            throw IllegalArgumentException(hint, error)
        } catch (error: java.nio.charset.CharacterCodingException) {
            throw IllegalArgumentException("CSV должен быть в кодировке UTF-8", error)
        } finally {
            sql.endTransaction()
        }
        return Result(imported, duplicates, files)
    }
}
