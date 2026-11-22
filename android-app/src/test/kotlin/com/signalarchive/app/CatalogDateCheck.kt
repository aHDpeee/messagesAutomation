package com.signalarchive.app

fun main() {
    check(CatalogDate.normalize("2026-11-15T04:15:00+03:00") == "2026-11-15T01:15:00Z")
    check(CatalogDate.normalize("11/15/26 4:15 PM") == "2026-11-15T16:15:00Z")
    check(CatalogDate.normalize("2026-11-15") == "2026-11-15T00:00:00Z")
    check(CatalogDate.normalize("46000") == "2025-12-09T00:00:00Z")
    check(runCatching { CatalogDate.normalize("02/31/2026") }.isFailure)
    println("Catalog date checks passed")
}
