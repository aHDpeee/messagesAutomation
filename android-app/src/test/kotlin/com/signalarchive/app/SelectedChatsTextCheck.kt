package com.signalarchive.app

fun main() {
    val rows = listOf(
        mapOf("title" to "Selected", "id" to "1", "type" to "chat", "blocks" to "Ungrouped", "username" to "example"),
        mapOf("title" to "Excluded", "id" to "2", "type" to "chat", "blocks" to ""),
        mapOf("title" to "Group\nname", "id" to "3", "type" to "group", "blocks" to "Work")
    )
    val text = SelectedChatsText.render(rows)
    check("Чаты для экспорта: 2" in text && "https://t.me/example" in text)
    check("Excluded" !in text && "Group name" in text)
    println("Selected chats text checks passed")
}
