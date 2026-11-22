package com.signalarchive.app

fun main() {
    val existing = listOf(mapOf("id" to "1", "title" to "A", "type" to "chat", "blocks" to "Other"))
    val incoming = listOf(mapOf("id" to "1", "title" to "A", "type" to "chat"), mapOf("id" to "2", "title" to "B", "type" to "channel", "username" to "publicchannel"))
    val kept = FolderImport.apply(existing, incoming, "Work", false)
    check(kept.first { it["id"] == "1" }["blocks"] == "Other")
    check(kept.first { it["id"] == "2" }["blocks"] == "Work")
    check(kept.first { it["id"] == "2" }["link"] == "https://t.me/publicchannel")
    check(FolderImport.apply(existing, incoming, "Work", true).first { it["id"] == "1" }["blocks"] == "Work")
    check(runCatching { FolderImport.apply(existing, incoming, "Ungrouped", true) }.isFailure)
    println("Folder import checks passed")
}
