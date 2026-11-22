package com.signalarchive.app

import java.io.File
import java.io.StringReader
import java.nio.charset.StandardCharsets
import java.nio.charset.CodingErrorAction
import java.nio.ByteBuffer
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import org.json.JSONArray

/** Human-editable catalog compatible with desktop; membership alone enables export. */
internal class ChatCatalog(private val file: File) {
    companion object {
        private val catalogLock = Any()
        val columns = listOf("title", "type", "username", "link", "description", "added_via", "added_at", "last_message_date", "members_exported_at", "blocks", "id", "selected")
        private fun safe(value: String): String = if (value.trimStart().startsWith("=") || value.trimStart().startsWith("+") || value.trimStart().startsWith("-") || value.trimStart().startsWith("@") || value.startsWith("\t") || value.startsWith("\r") || value.startsWith("\n")) "'$value" else value
        private fun raw(value: String): String = if (value.startsWith("'") && safe(value.substring(1)) == value) value.substring(1) else value
        private fun quoted(value: String): String = "\"" + value.replace("\"", "\"\"") + "\""
        fun parseImported(bytes: ByteArray): MutableList<MutableMap<String, String>> {
            require(bytes.size <= 5_000_000) { "Файл каталога слишком большой" }
            val decoder = StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT)
            val text = try { decoder.decode(ByteBuffer.wrap(bytes)).toString() }
                catch (error: java.nio.charset.CharacterCodingException) { throw IllegalArgumentException("Каталог должен быть в UTF-8", error) }
            return parse(text).also { require(it.isNotEmpty()) { "Пустой каталог нельзя импортировать" } }
        }
        fun parse(text: String): MutableList<MutableMap<String, String>> {
            require(text.length <= 5_000_000) { "Файл каталога слишком большой" }
            val csv = CsvReader(StringReader(text.removePrefix("\uFEFF")))
            val head = csv.next() ?: throw IllegalArgumentException("Пустой каталог")
            require(head.size == columns.size && head.toSet() == columns.toSet()) { "Неверные столбцы CSV" }
            val rows = mutableListOf<MutableMap<String, String>>()
            val seen = mutableSetOf<String>()
            val groupNames = mutableMapOf<String, String>()
            while (true) {
                val row = csv.next() ?: break
                require(row.size == head.size) { "Неверная строка CSV" }
                val fields = head.zip(row.map(::raw)).toMap().toMutableMap()
                require(fields.values.all { it.length <= 16_384 }) { "Слишком длинное поле CSV" }
                val rawId = fields["id"].orEmpty().trim()
                require(rawId.matches(Regex("-?[0-9]{1,20}")) && rawId.toLongOrNull() != null && rawId.toLong() != 0L) { "Неверный ID" }
                val id = rawId.toLong().toString()
                require(seen.add(id)) { "Повторный ID" }
                fields["id"] = id
                require(fields["type"] in listOf("chat", "group", "channel", "bot")) { "Неверный тип чата" }
                require(fields["title"].orEmpty().isNotBlank()) { "Пустое название чата" }
                require(fields["selected"].orEmpty().trim().lowercase() in listOf("yes", "no", "true", "false", "1", "0")) { "Неверный признак выбора" }
                listOf("added_at", "last_message_date", "members_exported_at").forEach { fields[it] = CatalogDate.normalize(fields[it].orEmpty()) }
                val decoded = decodeGroup(fields["blocks"].orEmpty())
                val block = if (decoded.isEmpty()) "" else groupNames.getOrPut(decoded.lowercase(java.util.Locale.ROOT)) { decoded }
                fields["blocks"] = block
                fields["selected"] = if (block.isEmpty()) "no" else "yes"
                rows.add(fields)
            }
            return rows
        }
        fun decodeGroup(value: String): String {
            val text = value.trim()
            if (text.isEmpty()) return ""
            val name = (if (text.startsWith("[")) {
                val a = JSONArray(text)
                require(a.length() == 1 && a.opt(0) is String) { "Один чат может быть только в одной группе" }
                a.getString(0)
            } else {
                require(!text.contains(',')) { "Один чат может быть только в одной группе" }
                text
            }).trim()
            require(name.length <= 100 && name.none { it.code < 32 }) { "Неверное имя группы" }
            return if (name.equals("Ungrouped", true)) "Ungrouped" else name
        }
        fun encodeGroup(group: String): String = if (group.startsWith("[") || group.contains(',')) JSONArray().put(group).toString() else group
        fun encode(rows: List<Map<String, String>>): String = buildString {
            append(columns.joinToString(",") { quoted(it) }).append("\r\n")
            for (row in rows) {
                append(columns.joinToString(",") { name ->
                    val value = when (name) {
                        "blocks" -> safe(encodeGroup(row[name].orEmpty()))
                        "selected" -> if (row["blocks"].isNullOrBlank()) "no" else "yes"
                        "id" -> row[name].orEmpty()
                        else -> safe(row[name].orEmpty())
                    }
                    quoted(value)
                }).append("\r\n")
            }
        }
    }
    fun load(): MutableList<MutableMap<String, String>> = loadVersioned().first
    fun version(): String = synchronized(catalogLock) {
        if (!file.exists()) "missing" else java.security.MessageDigest.getInstance("SHA-256")
            .digest(file.readBytes()).joinToString("") { "%02x".format(it.toInt() and 255) }
    }
    fun loadVersioned(): Pair<MutableList<MutableMap<String, String>>, String> = synchronized(catalogLock) {
        val rows = if (file.exists()) parse(file.readText(StandardCharsets.UTF_8)) else mutableListOf()
        rows to version()
    }
    fun backups(): List<File> = File(file.parentFile, "catalog-backups").listFiles()
        ?.filter { it.isFile && !Files.isSymbolicLink(it.toPath()) && it.name.matches(Regex("[0-9]{8}T(?:[0-9]{12}Z-[0-9a-f]{8}|[0-9]{9}Z-[0-9a-f-]{36})\\.csv")) }
        ?.sortedByDescending { it.name } ?: emptyList()
    fun save(rows: List<Map<String, String>>, expectedVersion: String? = null): String = synchronized(catalogLock) {
        require(expectedVersion == null || version() == expectedVersion) { "Каталог изменился. Обновите список и повторите." }
        val content = encode(rows)
        parse(content) // Validate before touching the existing file.
        file.parentFile?.mkdirs()
        val tmp = File.createTempFile("catalog-", ".tmp", file.parentFile)
        try {
            tmp.outputStream().use { output -> output.write(content.toByteArray(StandardCharsets.UTF_8)); output.fd.sync() }
            if (file.exists()) {
                val dir = File(file.parentFile, "catalog-backups")
                check(dir.isDirectory || dir.mkdirs()) { "Не удалось создать каталог резервных копий" }
                val stamp = java.time.format.DateTimeFormatter.ofPattern("yyyyMMdd'T'HHmmssSSSSSS'Z'").withZone(java.time.ZoneOffset.UTC).format(java.time.Instant.now())
                val backup = File(dir, "$stamp-${java.util.UUID.randomUUID().toString().replace("-", "").take(8)}.csv")
                Files.copy(file.toPath(), backup.toPath())
                java.io.FileOutputStream(backup, true).use { it.fd.sync() }
            }
            require(expectedVersion == null || version() == expectedVersion) { "Каталог изменился во время сохранения. Обновите список и повторите." }
            Files.move(tmp.toPath(), file.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
        } finally { tmp.delete() }
        version()
    }
}
