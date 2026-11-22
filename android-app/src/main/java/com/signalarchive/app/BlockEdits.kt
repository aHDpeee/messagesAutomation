package com.signalarchive.app

/** Group membership remains in the catalog; changes never remove chat records. */
internal object BlockEdits {
    fun assign(rows: List<Map<String, String>>, id: String, group: String?): MutableList<MutableMap<String, String>> {
        require(rows.any { it["id"] == id }) { "Чат не найден" }
        val value = group?.let { canonical(rows, it) }.orEmpty()
        require(value.length <= 100 && value.none { it.code < 32 }) { "Неверное название блока" }
        return rows.map { it.toMutableMap().apply { if (this["id"] == id) this["blocks"] = value } }.toMutableList()
    }
    fun canonical(rows: List<Map<String, String>>, requested: String): String {
        val name = requested.trim()
        if (name.equals("Ungrouped", true)) return "Ungrouped"
        return rows.firstOrNull { it["blocks"].orEmpty().equals(name, true) }?.get("blocks") ?: name
    }
    fun rename(rows: List<Map<String, String>>, old: String, newName: String): MutableList<MutableMap<String, String>> {
        val name = newName.trim()
        require(name.isNotEmpty() && name.length <= 100 && name.none { it.code < 32 } && !name.equals("Ungrouped", true)) { "Неверное название блока" }
        require(rows.any { it["blocks"] == old }) { "Блок не найден" }
        require(rows.none { it["blocks"].orEmpty().equals(name, true) && it["blocks"] != old }) { "Блок с таким названием уже существует" }
        return rows.map { it.toMutableMap().apply { if (this["blocks"] == old) this["blocks"] = name } }.toMutableList()
    }
    fun remove(rows: List<Map<String, String>>, name: String): MutableList<MutableMap<String, String>> {
        require(name.isNotBlank() && !name.equals("Ungrouped", true)) { "Служебную группу нельзя удалить" }
        require(rows.any { it["blocks"] == name }) { "Блок не найден" }
        return rows.map { it.toMutableMap().apply { if (this["blocks"] == name) this["blocks"] = "" } }.toMutableList()
    }
}
