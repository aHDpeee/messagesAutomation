package com.signalarchive.app

fun main() {
    check(TelegramLink.from(mapOf("username" to "@example")) == "https://t.me/example")
    check(TelegramLink.from(mapOf("link" to "https://t.me/+invite")) == "https://t.me/+invite")
    check(TelegramLink.from(mapOf("link" to "https://www.t.me/+invite")) == "https://www.t.me/+invite")
    check(TelegramLink.from(mapOf("link" to "https://t.me.evil.example/name")) == null)
    check(TelegramLink.from(mapOf("link" to "https://t.me@evil.example/name")) == null)
    check(TelegramLink.from(mapOf("link" to "javascript:alert(1)")) == null)
    check(TelegramLink.from(mapOf("link" to "https://t.me/")) == null)
    check(TelegramLink.refreshed("old", "https://t.me/old", "") == "")
    check(TelegramLink.refreshed("old", "https://t.me/old", "new") == "https://t.me/new")
    check(TelegramLink.refreshed("old", "https://t.me/+invite", "") == "https://t.me/+invite")
    println("Telegram link checks passed")
}
