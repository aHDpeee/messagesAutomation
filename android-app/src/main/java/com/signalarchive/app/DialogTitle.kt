package com.signalarchive.app

/** Telegram may return a dialog with no display name, e.g. a deleted account. */
internal object DialogTitle {
    fun choose(title: String?, previous: String?, username: String?, id: String): String {
        fun valid(value: String?) = value?.trim()?.takeIf { it.isNotEmpty() && !it.equals("null", true) }
        return valid(title) ?: valid(previous) ?: valid(username)?.let { "@${it.removePrefix("@")}" } ?: "Чат $id"
    }
}
