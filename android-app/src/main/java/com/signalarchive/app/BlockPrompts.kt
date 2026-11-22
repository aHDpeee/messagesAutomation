package com.signalarchive.app

import android.content.Context

internal object BlockPrompts {
    const val TEMPLATE = """Проанализируй сообщения из этой папки за указанный период.

1. Выдели задачи, сроки и ответственных, если они указаны.
2. Отдельно перечисли события для календаря и сообщения, на которые стоит ответить.
3. Кратко изложи новости и предложи три полезных пункта, которыми можно поделиться.
4. Для каждого вывода укажи ссылку на исходное сообщение, если она доступна. В конце перечисли ссылки на первоисточники новостей.

Не придумывай факты и отмечай неопределённость. Соблюдай конфиденциальность переписки."""
    private fun key(account: String, group: String) = "$account:${group.lowercase(java.util.Locale.ROOT)}"
    fun get(context: Context, account: String, group: String): String =
        context.getSharedPreferences("block_prompts", Context.MODE_PRIVATE).getString(key(account, group), "").orEmpty()
    fun save(context: Context, account: String, group: String, text: String) {
        require(text.length <= 32_000) { "Промпт слишком длинный" }
        check(context.getSharedPreferences("block_prompts", Context.MODE_PRIVATE).edit().putString(key(account, group), text).commit())
    }
    fun move(context: Context, account: String, group: String, replacement: String?) {
        val prefs = context.getSharedPreferences("block_prompts", Context.MODE_PRIVATE)
        val old = key(account, group); val edit = prefs.edit()
        if (replacement != null && key(account, replacement) == old) return
        if (replacement != null && prefs.contains(old)) edit.putString(key(account, replacement), prefs.getString(old, ""))
        edit.remove(old); check(edit.commit())
    }
    fun forChats(context: Context, account: String, chats: List<GroupedArchive.Chat>): Map<String, String> =
        chats.map { it.group }.distinct().associateWith { get(context, account, it) }
}
