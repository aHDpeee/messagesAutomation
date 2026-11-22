package com.signalarchive.app

import java.net.URI

/** Accept only public Telegram web links from imported catalog data. */
internal object TelegramLink {
    private fun usernameLink(username: String): String? = username.removePrefix("@")
        .takeIf { it.matches(Regex("[A-Za-z0-9_]{1,32}")) }?.let { "https://t.me/$it" }

    /** Drop an automatically generated link if Telegram removed the username. */
    fun refreshed(oldUsername: String, oldLink: String, newUsername: String): String {
        usernameLink(newUsername)?.let { return it }
        val previous = usernameLink(oldUsername)
        return if (previous != null && oldLink.trim().trimEnd('/').equals(previous, ignoreCase = true)) "" else oldLink
    }

    fun from(row: Map<String, String>): String? {
        usernameLink(row["username"].orEmpty())?.let { return it }
        val raw = row["link"].orEmpty().trim()
        if (raw.any { it.code < 32 }) return null
        val uri = runCatching { URI(raw) }.getOrNull() ?: return null
        if (!uri.scheme.equals("https", true) || uri.userInfo != null || uri.port !in listOf(-1, 443)) return null
        if (uri.host?.lowercase() !in setOf("t.me", "www.t.me", "telegram.me")) return null
        if (uri.rawPath.isNullOrBlank() || uri.rawPath == "/") return null
        return uri.toASCIIString()
    }
}
