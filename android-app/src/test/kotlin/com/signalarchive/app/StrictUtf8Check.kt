package com.signalarchive.app

fun main() {
    check(StrictUtf8.reader("Привет".byteInputStream()).readText() == "Привет")
    check(runCatching { StrictUtf8.reader(byteArrayOf(0xc3.toByte(), 0x28).inputStream()).readText() }.isFailure)
    println("Strict UTF-8 checks passed")
}
