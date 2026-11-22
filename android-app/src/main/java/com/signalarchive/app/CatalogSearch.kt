package com.signalarchive.app

import java.time.Instant
import java.time.OffsetDateTime

internal object CatalogSearch {
    fun matches(row: Map<String, String>, query: String, now: Instant, activeDays: Int): Boolean {
        val terms = query.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }
        val searchable = listOf("title", "username", "id", "blocks", "type").joinToString(" ") { row[it].orEmpty() }
        return terms.all { term ->
            when (term.lowercase()) {
                "@group" -> row["type"] == "group"
                "@channel" -> row["type"] == "channel"
                "@chat" -> row["type"] == "chat"
                "@active" -> {
                    val text = row["last_message_date"].orEmpty()
                    val date = runCatching { Instant.parse(text) }.getOrNull() ?: runCatching { OffsetDateTime.parse(text).toInstant() }.getOrNull()
                    date != null && date > now.minusSeconds(activeDays.toLong() * 86400)
                }
                else -> searchable.contains(term, ignoreCase = true)
            }
        }
    }
}
