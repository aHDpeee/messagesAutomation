package com.signalarchive.app

/** Merge Telegram folder membership into CSV rows without allowing multiple blocks. */
internal object FolderImport {
    fun apply(existing: List<Map<String, String>>, incoming: List<Map<String, String>>, group: String, moveConflicts: Boolean): MutableList<MutableMap<String, String>> {
        require(group.isNotBlank() && group.length <= 100 && !group.equals("Ungrouped", true) && group.none { it.code < 32 }) { "Недопустимое название папки" }
        val target = BlockEdits.canonical(existing, group)
        val rows = existing.map { it.toMutableMap() }.toMutableList()
        val byId = rows.associateBy { it["id"].orEmpty() }.toMutableMap()
        for (item in incoming) {
            val id = item["id"].orEmpty()
            require(id.toLongOrNull()?.let { it != 0L } == true) { "Неверный ID чата" }
            val row = byId[id] ?: ChatCatalog.columns.associateWith { "" }.toMutableMap().also {
                it.putAll(item); it["id"] = id; it["link"] = TelegramLink.refreshed("", "", it["username"].orEmpty()); it["added_via"] = "Telegram folder"; it["added_at"] = java.time.Instant.now().toString(); rows.add(it); byId[id] = it
            }
            if (row["blocks"].isNullOrBlank() || row["blocks"].orEmpty().equals(target, true) || moveConflicts) row["blocks"] = target
        }
        return rows
    }
}
