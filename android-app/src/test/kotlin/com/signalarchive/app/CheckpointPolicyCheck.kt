package com.signalarchive.app

import java.time.Instant

fun main() {
    val now = Instant.parse("2026-11-21T12:00:00Z")
    val fallback = now.minusSeconds(86400)
    val older = now.minusSeconds(86400 * 5)
    val recent = now.minusSeconds(3600)
    check(CheckpointPolicy.start(listOf(older, recent, null), fallback, now) == older)
    check(CheckpointPolicy.start(listOf(recent, null), fallback, now) == fallback)
    check(CheckpointPolicy.start(listOf(now.plusSeconds(1)), fallback, now) == fallback)
    check(CheckpointPolicy.shouldAdvance(older, older, now))
    check(!CheckpointPolicy.shouldAdvance(older, recent, now)) // would leave a gap
    check(!CheckpointPolicy.shouldAdvance(recent, older, older.plusSeconds(1))) // old archive
    check(CheckpointPolicy.shouldAdvance(null, older, now))
    println("Checkpoint policy checks passed")
}
