package com.signalarchive.app

import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.OutputStream

fun main() {
    val source = ByteArray(150_000) { (it % 251).toByte() }
    val destination = ByteArrayOutputStream()
    DocumentExport.copy(source.inputStream(), destination)
    check(source.contentEquals(destination.toByteArray()))
    val broken = object : OutputStream() { override fun write(value: Int) { throw IOException("disk full") } }
    check(runCatching { DocumentExport.copy(source.inputStream(), broken) }.exceptionOrNull() is IOException)
    try {
        Thread.currentThread().interrupt()
        check(runCatching { DocumentExport.copy(source.inputStream(), ByteArrayOutputStream()) }.exceptionOrNull() is InterruptedException)
    } finally { Thread.interrupted() }
    println("Document export checks passed")
}
