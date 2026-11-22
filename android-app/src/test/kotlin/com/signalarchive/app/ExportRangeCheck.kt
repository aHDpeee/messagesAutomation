package com.signalarchive.app

import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId

fun main() {
    val zone = ZoneId.of("Europe/Moscow")
    val checkpoint = Instant.parse("2026-11-21T18:47:30Z")
    val now = Instant.parse("2026-11-22T10:00:00Z")
    check(ExportRange.start(LocalDate.parse("2026-11-21"), zone, checkpoint, now) == checkpoint)
    check(ExportRange.start(LocalDate.parse("2026-11-20"), zone, checkpoint, now) == Instant.parse("2026-11-19T21:00:00Z"))
    check(ExportRange.start(LocalDate.parse("2026-11-21"), zone, now.plusSeconds(1), now) == Instant.parse("2026-11-20T21:00:00Z"))
    check(ExportRange.start(LocalDate.parse("2026-11-21"), zone, null, now) == Instant.parse("2026-11-20T21:00:00Z"))
    println("Export range checks passed")
}
