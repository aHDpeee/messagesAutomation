package com.signalarchive.app

import java.time.Instant

fun main() {
    val now = Instant.parse("2026-11-22T12:00:00Z")
    check(ScheduleSummary.text(now, now.plusSeconds(7200), now.minusSeconds(3600)) == "Следующий экспорт ≈ через 2 ч")
    check(ScheduleSummary.text(now, now.plusSeconds(90), now.minusSeconds(86400)).contains("1 мин · последний — 1 дн. назад"))
    check(ScheduleSummary.text(now, now.plusSeconds(15), null).endsWith("15 с"))
    check(ScheduleSummary.text(now, now.minusSeconds(1), null) == "Экспорт ожидается")
    check(ScheduleSummary.text(now, null, now.plusSeconds(1)).isEmpty())
    println("Schedule summary checks passed")
}
