package com.signalarchive.app

import java.time.Instant
import java.time.OffsetDateTime

internal object CatalogSearch {
    fun matches(row: Map<String, String>, query: String, now: Instant, activeDays: Int): Boolean {
        val terms = query.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }
        val typeTokens = mapOf("@group" to "group", "@channel" to "channel", "@chat" to "chat")
        val kinds = terms.mapNotNull { typeTokens[it.lowercase()] }.toSet()
        if (kinds.isNotEmpty() && row["type"] !in kinds) return false
        val searchable = listOf("title", "username", "id", "blocks", "type").joinToString(" ") { row[it].orEmpty() }
        return terms.all { term ->
            when (term.lowercase()) {
                "@group", "@channel", "@chat" -> true
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
