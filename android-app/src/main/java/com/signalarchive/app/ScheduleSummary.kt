package com.signalarchive.app

import java.time.Duration
import java.time.Instant

internal object ScheduleSummary {
    fun text(now: Instant, next: Instant?, last: Instant?): String {
        val parts = mutableListOf<String>()
        if (next != null) {
            val seconds = Duration.between(now, next).seconds
            parts += when {
                seconds <= 0 -> "Экспорт ожидается"
                seconds >= 3600 -> "Следующий экспорт ≈ через ${seconds / 3600} ч"
                seconds >= 60 -> "Следующий экспорт ≈ через ${seconds / 60} мин"
                else -> "Следующий экспорт ≈ через $seconds с"
            }
        }
        if (last != null && !last.isAfter(now)) {
            val days = Duration.between(last, now).toDays()
            if (days >= 1) parts += "последний — $days дн. назад"
        }
        return parts.joinToString(" · ")
    }
}
