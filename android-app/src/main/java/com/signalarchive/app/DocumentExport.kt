package com.signalarchive.app

import android.content.Context
import android.net.Uri
import java.io.File
import java.io.InputStream
import java.io.OutputStream

internal object DocumentExport {
    fun copy(input: InputStream, output: OutputStream) {
        val buffer = ByteArray(65536)
        while (true) {
            if (Thread.currentThread().isInterrupted) throw InterruptedException("Сохранение прервано")
            val count = input.read(buffer)
            if (count < 0) break
            output.write(buffer, 0, count)
        }
    }
    fun save(context: Context, source: File, uri: Uri) {
        ExportLock.acquire(context.filesDir).use { saveLocked(context, source, uri) }
    }
    /** Caller must hold ExportLock. */
    fun saveLocked(context: Context, source: File, uri: Uri) {
        val expectedSize = source.length()
        val expectedHash = source.inputStream().use(RetentionPolicy::sha256)
        // Explicitly truncate: providers may interpret the default "w" mode
        // without truncating an existing, longer document after a retry.
        context.contentResolver.openOutputStream(uri, "wt")?.use { output -> source.inputStream().use { copy(it, output) } }
            ?: error("Не удалось открыть файл для записи")
        val verified = context.contentResolver.openInputStream(uri)?.use { RetentionPolicy.matches(it, expectedSize, expectedHash) } ?: false
        check(verified) { "Не удалось проверить сохранённый файл" }
        runCatching { SavedCacheCleanup.record(context, source) }
    }
}
