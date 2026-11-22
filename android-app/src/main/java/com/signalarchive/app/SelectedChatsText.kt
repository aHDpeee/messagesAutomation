package com.signalarchive.app

/** Human-readable list of exactly the chats included in regular exports. */
internal object SelectedChatsText {
    fun render(rows: List<Map<String, String>>): String {
        fun clean(value: String) = value.replace(Regex("[\\r\\n\\t]+"), " ").trim()
        val selected = rows.filter { !it["blocks"].isNullOrBlank() }
            .sortedWith(compareBy({ it["blocks"].orEmpty().lowercase() }, { it["title"].orEmpty().lowercase() }))
        return buildString {
            append("Чаты для экспорта: ").append(selected.size).append('\n')
            selected.forEach { row ->
                val link = TelegramLink.from(row).orEmpty()
                append(clean(row["blocks"].orEmpty())).append(" | ").append(clean(row["title"].orEmpty()))
                    .append(" | ").append(clean(row["type"].orEmpty())).append(" | ").append(clean(row["id"].orEmpty()))
                if (link.isNotBlank()) append(" | ").append(clean(link))
                append('\n')
            }
        }
    }
}
