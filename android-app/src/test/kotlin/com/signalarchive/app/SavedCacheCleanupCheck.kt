package com.signalarchive.app

fun main() {
    val now = 10L * 86400 * 1000
    check(SavedCacheCleanup.eligible("members-123.zip", now - 7L * 86400 * 1000, now))
    check(!SavedCacheCleanup.eligible("members-123.zip", now - 6L * 86400 * 1000, now))
    check(!SavedCacheCleanup.eligible("../members-123.zip", 1, now))
    check(!SavedCacheCleanup.eligible("other-123.zip", 1, now))
    check(!SavedCacheCleanup.eligible("telegram-export-1.zip", now + 1, now))
    println("Saved cache cleanup checks passed")
}
