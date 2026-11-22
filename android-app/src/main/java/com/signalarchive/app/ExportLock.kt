package com.signalarchive.app

import java.io.Closeable
import java.io.File
import java.io.RandomAccessFile
import java.nio.channels.FileLock
import java.nio.channels.OverlappingFileLockException

internal class ExportBusyException : IllegalStateException("Другой экспорт уже выполняется")

/** A single Telegram export at a time, including across app processes. */
internal class ExportLock private constructor(private val file: RandomAccessFile, private val lock: FileLock) : Closeable {
    override fun close() { try { lock.release() } finally { file.close() } }
    companion object {
        fun acquire(directory: File): ExportLock {
            val file = RandomAccessFile(File(directory, "export.lock"), "rw")
            try {
                val lock = try { file.channel.tryLock() } catch (_: OverlappingFileLockException) { null }
                if (lock == null) throw ExportBusyException()
                return ExportLock(file, lock)
            } catch (error: Throwable) { file.close(); throw error }
        }
    }
}
