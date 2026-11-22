package com.signalarchive.app

import android.content.Context
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import android.net.Uri
import android.provider.DocumentsContract

internal object ArchiveNames {
    private val format = DateTimeFormatter.ofPattern("yyyy-MM-dd_HHmm").withZone(ZoneId.systemDefault())
    fun base(groups: Collection<String>, start: Instant, end: Instant): String {
        val names = groups.map { it.ifBlank { "Ungrouped" } }.distinct().sorted()
            .joinToString("+").replace(Regex("[^\\p{L}\\p{N}._+-]"), "_").take(70).trim('_', '.')
        return "Tegash_${names.ifBlank { "chats" }}_${format.format(start)}-${format.format(end)}"
    }
    @Synchronized fun reserve(context: Context, base: String): String {
        val prefs = context.getSharedPreferences("archive_names", Context.MODE_PRIVATE)
        val index = prefs.getInt(base, 0).coerceAtLeast(0)
        check(prefs.edit().putInt(base, index + 1).commit()) { "Не удалось сохранить номер архива" }
        return "${base}_${index.toString().padStart(3, '0')}.zip"
    }
    fun reserveInFolder(context: Context, tree: Uri, base: String): String {
        val children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree))
        val names = mutableSetOf<String>()
        val cursor = context.contentResolver.query(children, arrayOf(DocumentsContract.Document.COLUMN_DISPLAY_NAME), null, null, null)
            ?: error("Не удалось проверить имена архивов в папке")
        cursor.use { while (it.moveToNext()) names.add(it.getString(0).orEmpty()) }
        repeat(10_000) {
            val name = reserve(context, base)
            if (name !in names && "$name.part" !in names) return name
        }
        error("Не удалось выбрать свободное имя архива")
    }
}
