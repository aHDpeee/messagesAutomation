package com.signalarchive.app

fun main() {
    val rows = listOf(mapOf("id" to "1", "blocks" to "Work"), mapOf("id" to "2", "blocks" to "Other"))
    val renamed = BlockEdits.rename(rows, "Work", "New")
    check(renamed[0]["blocks"] == "New" && rows[0]["blocks"] == "Work")
    check(runCatching { BlockEdits.rename(rows, "Work", "other") }.isFailure)
    check(BlockEdits.canonical(rows, "work") == "Work")
    check(BlockEdits.assign(rows, "1", "Ungrouped")[0]["blocks"] == "Ungrouped")
    check(BlockEdits.assign(rows, "1", null)[0]["blocks"] == "")
    check(runCatching { BlockEdits.assign(rows, "missing", "Work") }.isFailure)
    val removed = BlockEdits.remove(rows, "Work")
    check(removed.size == 2 && removed[0]["blocks"] == "" && removed[1]["blocks"] == "Other")
    check(runCatching { BlockEdits.remove(rows, "Ungrouped") }.isFailure)
    println("Block edit checks passed")
}
