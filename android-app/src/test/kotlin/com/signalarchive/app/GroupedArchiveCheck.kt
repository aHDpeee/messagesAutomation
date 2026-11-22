package com.signalarchive.app

import java.io.File
import java.util.zip.ZipFile

fun main() {
    val root = kotlin.io.path.createTempDirectory("signal-grouped-").toFile()
    try {
        val source = File(root, "source.csv")
        source.writeText("dialog_id,message_id,date_utc,text,media,post_author,reply_to_message_id\r\n-10,7,2026-01-01T12:00:00Z,hello,secret-media,Author,5\r\n")
        val output = File(root, "out.zip")
        val result = GroupedArchive.create(output, listOf(GroupedArchive.Chat("-10", "Group", "Work", "example_group", "group")), { source })
        check(result.chats == 1 && result.messages == 1)
        ZipFile(output).use { zip ->
            val entry = zip.entries().asSequence().single()
            check(entry.name.startsWith("Work_") && entry.name.endsWith("/chat_-10_Group.csv"))
            val text = zip.getInputStream(entry).bufferedReader().readText()
            check("hello" in text && "secret-media" !in text && "record_type" in text)
            check("https://t.me/example_group/7" in text)
            check("Author" in text && "\"5\",\"5\"" in text)
        }
        val withPrompt = File(root, "with-prompt.zip")
        GroupedArchive.create(withPrompt, listOf(GroupedArchive.Chat("-10", "Group", "Work", "example_group", "group")), { source }, prompts = mapOf("Work" to "Сделай краткую сводку"))
        ZipFile(withPrompt).use { zip ->
            val prompt = zip.entries().asSequence().single { it.name.endsWith("/AI_PROMPT.txt") }
            check(zip.getInputStream(prompt).bufferedReader().readText() == "Сделай краткую сводку")
        }
        val rootPrompt = File(root, "root-prompt.zip")
        GroupedArchive.create(rootPrompt, listOf(GroupedArchive.Chat("-10", "Group", "Ungrouped")), { source }, prompts = mapOf("Ungrouped" to "Промпт"))
        ZipFile(rootPrompt).use { zip -> check(zip.getEntry("AI_PROMPT.txt") != null) }
        val failed = File(root, "failed.zip")
        runCatching { GroupedArchive.create(failed, listOf(GroupedArchive.Chat("-11", "Other", "Ungrouped")), { source }) }.onSuccess { error("wrong chat accepted") }
        check(!failed.exists())
        val safeName = File(root, "safe.zip")
        GroupedArchive.create(safeName, listOf(GroupedArchive.Chat("-10", "../Unsafe\\title", "Ungrouped")), { source })
        ZipFile(safeName).use { zip ->
            val name = zip.entries().asSequence().single().name
            check('/' !in name && '\\' !in name && name.startsWith("chat_-10_"))
        }
        val unicode = File(root, "unicode.zip")
        val supplementaryLetter = "\uD801\uDC00"
        GroupedArchive.create(unicode, listOf(GroupedArchive.Chat("-10", "a".repeat(59) + supplementaryLetter, "b".repeat(69) + supplementaryLetter)), { source })
        ZipFile(unicode).use { zip ->
            val name = zip.entries().asSequence().single().name
            check('\uFFFD' !in name && name.toByteArray(Charsets.UTF_8).size < 255)
        }
        val empty = File(root, "empty.zip")
        val noMessages = GroupedArchive.create(empty, listOf(GroupedArchive.Chat("-11", "Other", "Ungrouped")), { null }, allowEmpty = true)
        check(noMessages.chats == 0 && noMessages.messages == 0 && !empty.exists())
        val cancelled = File(root, "cancelled.zip")
        try {
            Thread.currentThread().interrupt()
            check(runCatching { GroupedArchive.create(cancelled, listOf(GroupedArchive.Chat("-10", "Group", "Work")), { source }) }.exceptionOrNull() is InterruptedException)
            check(!cancelled.exists())
        } finally { Thread.interrupted() }
    } finally { root.deleteRecursively() }
    println("Grouped archive checks passed")
}
