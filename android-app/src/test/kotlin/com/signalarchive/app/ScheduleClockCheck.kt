package com.signalarchive.app

import java.time.LocalTime
import java.time.ZoneId
import java.time.ZonedDateTime

fun main() {
    val zone = ZoneId.of("Europe/Berlin")
    val morning = ZonedDateTime.of(2026, 3, 28, 9, 0, 0, 0, zone)
    check(ScheduleClock.next(morning, LocalTime.of(10, 30)).toLocalDate() == morning.toLocalDate())
    val after = ZonedDateTime.of(2026, 3, 28, 12, 0, 0, 0, zone)
    val dst = ScheduleClock.next(after, LocalTime.of(2, 30))
    check(dst.toLocalDate().dayOfMonth == 29 && dst.hour == 3) // DST gap is advanced safely.
    val exactly = ZonedDateTime.of(2026, 3, 30, 10, 30, 0, 0, zone)
    check(ScheduleClock.next(exactly, LocalTime.of(10, 30)).toLocalDate().dayOfMonth == 31)
    println("Schedule clock checks passed")
}
