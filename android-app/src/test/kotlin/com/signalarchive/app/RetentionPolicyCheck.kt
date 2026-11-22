package com.signalarchive.app

import java.time.Instant

fun main() {
    val cutoff = Instant.parse("2026-10-01T00:00:00Z")
    check(RetentionPolicy.eligible("tree", "tree", cutoff.minusSeconds(1), cutoff))
    check(!RetentionPolicy.eligible("other", "tree", cutoff.minusSeconds(1), cutoff))
    check(!RetentionPolicy.eligible("tree", "tree", cutoff, cutoff))
    check(!RetentionPolicy.eligible("tree", "tree", null, cutoff))
    val bytes = "original archive".toByteArray()
    val hash = bytes.inputStream().use(RetentionPolicy::sha256)
    check(RetentionPolicy.matches(bytes.inputStream(), bytes.size.toLong(), hash))
    check(!RetentionPolicy.matches("changed archive!".byteInputStream(), bytes.size.toLong(), hash))
    check(!RetentionPolicy.matches(bytes.inputStream(), bytes.size.toLong() - 1, hash))
    try {
        Thread.currentThread().interrupt()
        check(runCatching { RetentionPolicy.sha256(bytes.inputStream()) }.exceptionOrNull() is InterruptedException)
    } finally { Thread.interrupted() }
    println("Retention policy checks passed")
}
