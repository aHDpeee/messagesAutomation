package com.signalarchive.app

import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest
import java.util.UUID

/** Preserve a closed Telegram engine before starting a different account. */
internal object WorkspaceArchive {
    private val snapshotFiles = listOf("account-settings.json", "chats.csv")
    private const val databaseName = "observatory.sqlite"
    private fun digest(file: File): String = file.inputStream().use { input ->
        val sha = MessageDigest.getInstance("SHA-256")
        val buffer = ByteArray(65536)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) break
            sha.update(buffer, 0, count)
        }
        sha.digest().joinToString("") { "%02x".format(it.toInt() and 255) }
    }

    /** Publish only after all recoverable account metadata has been written. */
    fun seal(directory: File) {
        val lines = snapshotFiles.map { name ->
            val file = File(directory, name)
            require(file.isFile && !Files.isSymbolicLink(file.toPath())) { "Неполная резервная копия" }
            "$name ${digest(file)}"
        }.toMutableList()
        val database = File(directory, databaseName)
        require(!Files.isSymbolicLink(database.toPath())) { "Неверный файл базы" }
        require(!database.exists() || database.isFile) { "Неверный файл базы" }
        lines.add("$databaseName ${if (database.isFile) digest(database) else "absent"}")
        val temp = File.createTempFile("snapshot-", ".tmp", directory)
        try {
            temp.outputStream().use { out -> out.write((lines.joinToString("\n") + "\n").toByteArray(Charsets.UTF_8)); out.fd.sync() }
            Files.move(temp.toPath(), File(directory, "snapshot.sha256").toPath(), StandardCopyOption.ATOMIC_MOVE)
        } finally { temp.delete() }
    }

    fun isSealed(directory: File): Boolean = runCatching {
        val marker = File(directory, "snapshot.sha256")
        if (!marker.isFile || Files.isSymbolicLink(marker.toPath()) || marker.length() > 1024) return false
        val entries = marker.readLines()
        if (entries.size != snapshotFiles.size + 1) return false
        val metadataValid = snapshotFiles.withIndex().all { (index, name) ->
            val file = File(directory, name)
            file.isFile && !Files.isSymbolicLink(file.toPath()) && entries[index] == "$name ${digest(file)}"
        }
        val database = File(directory, databaseName)
        metadataValid && !Files.isSymbolicLink(database.toPath()) &&
            (if (entries.last() == "$databaseName absent") !database.exists()
             else database.isFile && entries.last() == "$databaseName ${digest(database)}")
    }.getOrDefault(false)

    fun available(filesDir: File): List<File> {
        val root = File(filesDir, "telegram-workspaces")
        if (Files.isSymbolicLink(root.toPath())) return emptyList()
        return root.listFiles()?.filter {
            it.isDirectory && !Files.isSymbolicLink(it.toPath()) &&
                it.name.matches(Regex("workspace-[0-9a-f-]{36}")) && isSealed(it)
        }?.sortedByDescending { it.lastModified() } ?: emptyList()
    }

    /** Copy, don't consume, a verified snapshot into an atomic staging directory. */
    fun restoreEngine(filesDir: File, source: File) {
        require(available(filesDir).any { it.canonicalFile == source.canonicalFile }) { "Резервная копия недоступна или повреждена" }
        val target = File(filesDir, "telegram-engine")
        require(!target.exists() && !Files.isSymbolicLink(target.toPath())) { "Текущая база ещё открыта" }
        val staging = Files.createTempDirectory(filesDir.toPath(), "telegram-restore-").toFile()
        try {
            Files.walk(source.toPath()).use { paths -> paths.forEach { path ->
                val relative = source.toPath().relativize(path)
                val relativeName = relative.toString()
                if (relativeName.isEmpty() || relativeName in snapshotFiles || relativeName == "snapshot.sha256") return@forEach
                require(!Files.isSymbolicLink(path)) { "Недопустимая ссылка в резервной копии" }
                val destination = staging.toPath().resolve(relative)
                if (Files.isDirectory(path)) Files.createDirectories(destination)
                else {
                    require(Files.isRegularFile(path)) { "Недопустимый файл в резервной копии" }
                    Files.copy(path, destination)
                    java.io.FileOutputStream(destination.toFile(), true).use { it.fd.sync() }
                }
            } }
            val originalDatabase = File(source, databaseName)
            val stagedDatabase = File(staging, databaseName)
            check(if (originalDatabase.isFile) stagedDatabase.isFile && digest(originalDatabase) == digest(stagedDatabase) else !stagedDatabase.exists()) {
                "Не удалось проверить восстановленную базу"
            }
            check(isSealed(source)) { "Резервная копия изменилась во время восстановления" }
            Files.move(staging.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE)
        } finally { staging.deleteRecursively() }
    }

    fun archive(filesDir: File): File? {
        val root = filesDir.canonicalFile
        val source = File(root, "telegram-engine")
        if (!source.exists()) return null
        require(source.isDirectory && !Files.isSymbolicLink(source.toPath())) { "Неверная папка данных Telegram" }
        val backups = File(root, "telegram-workspaces")
        require(!Files.isSymbolicLink(backups.toPath())) { "Неверная папка резервных копий" }
        check(backups.isDirectory || backups.mkdir()) { "Не удалось создать папку резервных копий" }
        val destination = File(backups, "workspace-${UUID.randomUUID()}")
        // Same-filesystem atomic move: an interrupted switch never leaves a
        // half-copied SQLite database looking like a usable workspace.
        Files.move(source.toPath(), destination.toPath(), StandardCopyOption.ATOMIC_MOVE)
        return destination
    }
}
