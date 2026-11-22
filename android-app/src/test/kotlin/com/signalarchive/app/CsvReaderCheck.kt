package com.signalarchive.app

import java.io.StringReader

/** Runs without Android or external test libraries. */
fun main() {
    val reader = CsvReader(StringReader("a,b\r\n1,\"line 1\nline \"\"2\"\"\"\r\n3,\"\"\n"))
    check(reader.next() == listOf("a", "b"))
    check(reader.next() == listOf("1", "line 1\nline \"2\""))
    check(reader.next() == listOf("3", ""))
    check(reader.next() == null)
    check(runCatching { CsvReader(StringReader("a,\"broken")).next() }.isFailure)
    check(runCatching { CsvReader(StringReader("\"a\"x,b")).next() }.isFailure)
    println("CSV parser checks passed")
}
