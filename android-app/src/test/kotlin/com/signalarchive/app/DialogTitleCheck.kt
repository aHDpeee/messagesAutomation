package com.signalarchive.app

fun main() {
    check(DialogTitle.choose("", "Старое имя", "someone", "123") == "Старое имя")
    check(DialogTitle.choose("  ", "", "someone", "123") == "@someone")
    check(DialogTitle.choose(null, null, null, "123") == "Чат 123")
    check(DialogTitle.choose("Новое имя", "Старое имя", "", "123") == "Новое имя")
}
