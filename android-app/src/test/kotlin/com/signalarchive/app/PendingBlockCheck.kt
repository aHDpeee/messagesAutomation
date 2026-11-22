package com.signalarchive.app

fun main() {
    val rows = mutableListOf(mutableMapOf("id" to "1", "blocks" to ""), mutableMapOf("id" to "2", "blocks" to "Work"))
    check(PendingBlock.apply(rows, "1", "work"))
    check(rows[0]["blocks"] == "Work")
    check(PendingBlock.apply(rows, "2", "Other"))
    check(rows[1]["blocks"] == "Work")
    check(!PendingBlock.apply(rows, "3", "Work"))
    check(runCatching { PendingBlock.apply(rows, "1", "x".repeat(101)) }.isFailure)
    println("Pending block checks passed")
}
