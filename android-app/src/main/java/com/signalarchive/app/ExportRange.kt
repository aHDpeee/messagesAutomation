package com.signalarchive.app

import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId

internal object ExportRange {
    fun start(date: LocalDate, zone: ZoneId, checkpoint: Instant?, now: Instant): Instant =
        checkpoint?.takeIf { it < now && it.atZone(zone).toLocalDate() == date }
            ?: date.atStartOfDay(zone).toInstant()
}
