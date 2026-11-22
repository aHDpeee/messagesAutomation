package com.signalarchive.app

import java.io.File
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/** Writes one portable archive; caller fetches messages using the authenticated client. */
internal object GroupedArchive {
    data class Chat(val id: String, val title: String, val group: String, val username: String = "", val kind: String = "")
    data class Result(val chats: Int, val messages: Int)
    private val fields = listOf("record_type", "chat_id", "chat_title", "chat_type", "id", "date", "edit_date", "sender_id", "sender_name", "sender_username", "post_author", "sender", "reply_to_msg_id", "reply_to_message_id", "message_link", "text")
    private fun quote(v: String) = "\"" + v.replace("\"", "\"\"") + "\""
    private fun safe(v: String): String = if (v.trimStart().startsWith("=") || v.trimStart().startsWith("+") || v.trimStart().startsWith("-") || v.trimStart().startsWith("@") || v.startsWith("\t") || v.startsWith("\r") || v.startsWith("\n")) "'$v" else v
    private fun messageLink(chat: Chat, id: String): String {
        if (chat.kind !in setOf("group", "channel")) return ""
        val username = chat.username.removePrefix("@")
        if (username.matches(Regex("[A-Za-z0-9_]{1,32}"))) return "https://t.me/$username/$id"
        if (chat.id.startsWith("-100") && chat.id.drop(4).all { it.isDigit() } && chat.id.length > 4)
            return "https://t.me/c/${chat.id.drop(4)}/$id"
        return ""
    }
    private fun safeComponent(name: String, max: Int): String =
        name.replace(Regex("[^\\p{L}\\p{N} ._-]"), "_").trim(' ', '.').take(max)
            .let { if (it.lastOrNull()?.isHighSurrogate() == true) it.dropLast(1) else it }.trimEnd(' ', '.')
    private fun folder(name: String): String {
        val normalized = safeComponent(name, 70).ifBlank { "Group" }
        val hash = MessageDigest.getInstance("SHA-256").digest(name.toByteArray(StandardCharsets.UTF_8)).take(4).joinToString("") { "%02x".format(it.toInt() and 255) }
        return "${normalized}_$hash/"
    }
    private fun fileName(chat: Chat): String {
        val title = safeComponent(chat.title, 60)
        return if (title.isEmpty()) "chat_${chat.id}.csv" else "chat_${chat.id}_${title}.csv"
    }
    fun create(destination: File, chats: List<Chat>, fetchCsv: (Chat) -> File?, allowEmpty: Boolean = false, prompts: Map<String, String> = emptyMap(), progress: (Int, Int, String) -> Unit = { _, _, _ -> }): Result {
        require(chats.isNotEmpty()) { "Нет выбранных чатов" }
        require(chats.map { it.id }.toSet().size == chats.size) { "Повторный ID чата" }
        var total = 0; var written = 0
        try {
            destination.outputStream().use { raw ->
                ZipOutputStream(raw).use { zip ->
                    val writtenGroups = mutableSetOf<String>()
                    for ((index, chat) in chats.withIndex()) {
                        if (Thread.currentThread().isInterrupted) throw InterruptedException("Экспорт отменён")
                        require(chat.id.matches(Regex("-?[0-9]{1,20}")) && chat.id.toLongOrNull() != null && chat.id.toLong() != 0L) { "Неверный ID чата" }
                        require(chat.group.isNotBlank()) { "Не назначена группа чата" }
                        progress(index, chats.size, chat.title)
                        val input = fetchCsv(chat) ?: continue
                        val prefix = if (chat.group.equals("Ungrouped", true)) "" else folder(chat.group)
                        input.inputStream().use { file ->
                            val csv = CsvReader(InputStreamReader(file, StandardCharsets.UTF_8))
                            val header = csv.next()?.map { it.removePrefix("\uFEFF") } ?: throw IllegalArgumentException("Пустой экспорт")
                            require(header.toSet().containsAll(listOf("dialog_id", "message_id", "date_utc", "text")) && header.size == header.toSet().size) { "Неверный формат экспорта Telegram" }
                            zip.putNextEntry(ZipEntry(prefix + fileName(chat)))
                            val out = OutputStreamWriter(zip, StandardCharsets.UTF_8)
                            fun line(values: List<String>) { out.write(values.joinToString(",") { quote(it) }); out.write("\r\n") }
                            out.write("\uFEFF"); line(fields)
                            while (true) {
                                if (Thread.currentThread().isInterrupted) throw InterruptedException("Экспорт отменён")
                                val values = csv.next() ?: break
                                require(values.size == header.size) { "Неверная строка CSV Telegram" }
                                val data = header.zip(values).toMap()
                                require(data["dialog_id"] == chat.id) { "Экспорт относится к другому чату" }
                                val messageId = data["message_id"].orEmpty()
                                require(messageId.matches(Regex("[0-9]{1,20}"))) { "Неверный ID сообщения" }
                                val item = mapOf("record_type" to "message", "chat_id" to chat.id, "chat_title" to safe(chat.title),
                                    "chat_type" to safe(data["dialog_kind"].orEmpty().replace("private", "chat")), "id" to messageId,
                                    "date" to data["date_utc"].orEmpty(), "edit_date" to data["edit_date_utc"].orEmpty(),
                                    "sender_id" to data["sender_id"].orEmpty(), "sender_name" to safe(data["sender_name"].orEmpty()),
                                    "sender_username" to safe(data["sender_username"].orEmpty()), "post_author" to safe(data["post_author"].orEmpty()),
                                    "sender" to safe(data["sender_name"].orEmpty().ifBlank { data["sender_username"].orEmpty().ifBlank { data["post_author"].orEmpty() } }),
                                    "reply_to_msg_id" to data["reply_to_message_id"].orEmpty(), "reply_to_message_id" to data["reply_to_message_id"].orEmpty(), "message_link" to messageLink(chat, messageId), "text" to safe(data["text"].orEmpty()))
                                line(fields.map { item[it].orEmpty() }); total++
                            }
                            val footer = mapOf("record_type" to "project", "text" to "Exported with Tegash by ahdpeee and Codex. Ideas and contributions welcome. https://github.com/aHDpeee/messagesAutomation")
                            line(fields.map { footer[it].orEmpty() }); out.flush(); zip.closeEntry(); written++; writtenGroups.add(chat.group)
                        }
                    }
                    for (group in writtenGroups) {
                        val prompt = prompts[group].orEmpty()
                        if (prompt.isBlank()) continue
                        val prefix = if (group.equals("Ungrouped", true)) "" else folder(group)
                        zip.putNextEntry(ZipEntry(prefix + "AI_PROMPT.txt"))
                        zip.write(prompt.toByteArray(StandardCharsets.UTF_8)); zip.closeEntry()
                    }
                }
            }
            if (written == 0 && allowEmpty) {
                destination.delete()
                return Result(0, 0)
            }
            require(written > 0) { "За выбранный период сообщений не найдено" }
            return Result(written, total)
        } catch (error: Throwable) { destination.delete(); throw error }
    }
}
