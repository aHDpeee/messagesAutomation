package com.signalarchive.app

import java.time.Instant

fun main() {
    val now = Instant.parse("2026-11-22T12:00:00Z")
    val row = mapOf("type" to "group", "title" to "Test Group", "last_message_date" to "2026-11-21T12:00:00Z")
    check(CatalogSearch.matches(row, "@group test @active", now, 30))
    check(!CatalogSearch.matches(row, "@channel", now, 30))
    check(CatalogSearch.matches(row, "@channel @group @active", now, 30))
    check(!CatalogSearch.matches(row + ("type" to "chat"), "@channel @group @active", now, 30))
    check(!CatalogSearch.matches(row, "@active", now, 1))
    check(!CatalogSearch.matches(row - "last_message_date", "@active", now, 30))
    check(CatalogSearch.matches(row + ("username" to "person"), "person", now, 30))
    println("Catalog search checks passed")
}
