package com.signalarchive.app

/** Apply the chosen block after approval without overwriting a later manual choice. */
internal object PendingBlock {
    fun apply(rows: List<MutableMap<String, String>>, chatId: String, requested: String): Boolean {
        val row = rows.firstOrNull { it["id"] == chatId } ?: return false
        val name = requested.trim()
        require(name.length <= 100 && name.none { it.code < 32 }) { "Неверное имя блока" }
        if (name.isNotEmpty() && row["blocks"].isNullOrBlank()) row["blocks"] = BlockEdits.canonical(rows, name)
        return true
    }
}
