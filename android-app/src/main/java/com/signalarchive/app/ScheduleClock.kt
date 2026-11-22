package com.signalarchive.app

import java.time.LocalTime
import java.time.ZonedDateTime

/** Resolve a local daily time using the system time zone and its DST rules. */
internal object ScheduleClock {
    fun next(now: ZonedDateTime, time: LocalTime): ZonedDateTime {
        var target = now.toLocalDate().atTime(time).atZone(now.zone)
        if (!target.isAfter(now)) target = now.toLocalDate().plusDays(1).atTime(time).atZone(now.zone)
        return target
    }
}
