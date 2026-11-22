package com.signalarchive.app

fun main() {
    val directory = kotlin.io.path.createTempDirectory("signal-lock-").toFile()
    try {
        ExportLock.acquire(directory).use {
            check(runCatching { ExportLock.acquire(directory) }.exceptionOrNull() is ExportBusyException)
        }
        ExportLock.acquire(directory).use { check(true) }
    } finally { directory.deleteRecursively() }
    println("Export lock checks passed")
}
