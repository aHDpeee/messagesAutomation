package com.signalarchive.app

import java.nio.file.Files

fun main() {
    val dir = Files.createTempDirectory("signal-catalog-check").toFile()
    try {
        val store = ChatCatalog(dir.resolve("chats.csv"))
        fun row(id: String, title: String, group: String) = ChatCatalog.columns.associateWith { "" }.toMutableMap().apply {
            put("id", id); put("title", title); put("type", "group"); put("blocks", group)
        }
        val initial = listOf(row("-100123", "A, B", "Ungrouped"), row("12", "=Name", "Work, projects"))
        store.save(initial)
        val read = store.load()
        check(read.map { it["blocks"] } == listOf("Ungrouped", "Work, projects"))
        check(read.map { it["selected"] } == listOf("yes", "yes"))
        check(read[1]["title"] == "=Name")
        val old = dir.resolve("chats.csv").readBytes()
        val version = store.version()
        read[0]["blocks"] = ""
        store.save(read, expectedVersion = version)
        check(store.backups().single().readBytes().contentEquals(old))
        check(store.backups().single().name.matches(Regex("[0-9]{8}T[0-9]{12}Z-[0-9a-f]{8}\\.csv")))
        val legacy = dir.resolve("catalog-backups/20261121T120000123Z-12345678-1234-1234-1234-123456789abc.csv")
        legacy.writeBytes(old)
        check(legacy in store.backups())
        check(store.load()[0]["selected"] == "no")
        val latest = dir.resolve("chats.csv").readBytes()
        try { store.save(initial, expectedVersion = version); error("stale catalog overwritten") } catch (_: IllegalArgumentException) {}
        check(dir.resolve("chats.csv").readBytes().contentEquals(latest))
        val valid = dir.resolve("chats.csv").readBytes()
        val bad = read.map { it.toMutableMap() }
        bad[0]["id"] = "invalid"
        try { store.save(bad); error("invalid catalog saved") } catch (_: IllegalArgumentException) {}
        check(dir.resolve("chats.csv").readBytes().contentEquals(valid))
        try { ChatCatalog.parse("id,title\n1,test\n"); error("invalid columns accepted") } catch (_: IllegalArgumentException) {}
        try { ChatCatalog.parseImported(ChatCatalog.encode(emptyList()).toByteArray()); error("empty catalog imported") } catch (_: IllegalArgumentException) {}
        try { ChatCatalog.parseImported(byteArrayOf(0xc3.toByte(), 0x28)); error("invalid UTF-8 imported") } catch (_: IllegalArgumentException) {}
        check(ChatCatalog.parseImported(ChatCatalog.encode(initial).toByteArray()).size == 2)
        val differentlyCased = listOf(row("1", "First", "Work"), row("2", "Second", "work"))
        check(ChatCatalog.parse(ChatCatalog.encode(differentlyCased)).map { it["blocks"] } == listOf("Work", "Work"))
        check(ChatCatalog.decodeGroup("[\"   \"]") == "")
        val concurrentVersion = store.version()
        val start = java.util.concurrent.CountDownLatch(1)
        val successes = java.util.concurrent.atomic.AtomicInteger()
        val threads = listOf("First", "Second").map { name ->
            Thread {
                start.await()
                val candidate = store.load().map { it.toMutableMap() }.also { it[0]["title"] = name }
                if (runCatching { store.save(candidate, expectedVersion = concurrentVersion) }.isSuccess) successes.incrementAndGet()
            }.apply { start() }
        }
        start.countDown(); threads.forEach { it.join() }
        check(successes.get() == 1)
        check(store.load()[0]["title"] in listOf("First", "Second"))
        try { ChatCatalog.decodeGroup("[\"a\",\"b\"]"); error("multiple groups accepted") } catch (_: IllegalArgumentException) {}
        println("Chat catalog checks passed")
    } finally { dir.deleteRecursively() }
}
