package com.signalarchive.app

import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneOffset

/** Normalizes common desktop and spreadsheet date values to UTC. */
internal object CatalogDate {
    private val calendar = Regex("^(\\d{1,4})[/-](\\d{1,2})[/-](\\d{1,4})(?:[ T]+(\\d{1,2}):(\\d{2})(?::(\\d{2}))?(?:\\.(\\d{1,6}))?\\s*([AaPp][Mm])?)?$")
    private val serial = Regex("^[1-9]\\d{4}(?:\\.\\d+)?$")
    fun normalize(value: String): String {
        val s = value.trim().removePrefix("'").trim()
        if (s.isEmpty()) return ""
        runCatching { return Instant.parse(s).toString() }
        runCatching { return OffsetDateTime.parse(s).toInstant().toString() }
        runCatching { return LocalDateTime.parse(s).toInstant(ZoneOffset.UTC).toString() }
        runCatching { return LocalDate.parse(s).atStartOfDay().toInstant(ZoneOffset.UTC).toString() }
        if (serial.matches(s)) {
            val seconds = (s.toDouble() * 86400).toLong()
            return LocalDate.of(1899, 12, 30).atStartOfDay().toInstant(ZoneOffset.UTC).plusSeconds(seconds).toString()
        }
        val m = calendar.matchEntire(s) ?: throw IllegalArgumentException("Неверная дата в CSV: $s")
        val yearFirst = s.substringBefore('/').substringBefore('-').length == 4
        var year = (if (yearFirst) m.groupValues[1] else m.groupValues[3]).toInt()
        if (year < 100) year += if (year < 69) 2000 else 1900
        val month = (if (yearFirst) m.groupValues[2] else m.groupValues[1]).toInt()
        val day = (if (yearFirst) m.groupValues[3] else m.groupValues[2]).toInt()
        var hour = m.groupValues[4].toIntOrNull() ?: 0
        val ampm = m.groupValues[8].lowercase()
        if (ampm.isNotEmpty()) { require(hour in 1..12) { "Неверное время в CSV" }; hour = hour % 12 + if (ampm == "pm") 12 else 0 }
        return try {
            LocalDateTime.of(year, month, day, hour, m.groupValues[5].toIntOrNull() ?: 0,
                m.groupValues[6].toIntOrNull() ?: 0, m.groupValues[7].padEnd(9, '0').take(9).toIntOrNull() ?: 0)
                .toInstant(ZoneOffset.UTC).toString()
        } catch (error: Exception) { throw IllegalArgumentException("Неверная дата в CSV: $s", error) }
    }
}
