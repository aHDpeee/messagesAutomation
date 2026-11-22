package com.signalarchive.app

import java.io.PushbackReader
import java.io.Reader

/** Streaming RFC 4180-style CSV reader supporting quoted, multiline fields. */
internal class CsvReader(input: Reader, private val consume: () -> Unit = {}) {
    private val reader = PushbackReader(input, 1)
    fun next(): List<String>? {
        val fields = mutableListOf<String>()
        val cell = StringBuilder()
        var quoted = false
        var closedQuote = false
        var started = false
        while (true) {
            val c = reader.read()
            if (c < 0) {
                if (quoted) throw IllegalArgumentException("Незакрытые кавычки в CSV")
                if (!started && fields.isEmpty() && cell.isEmpty()) return null
                fields.add(cell.toString().removeSuffix("\r")); return fields
            }
            consume()
            started = true
            when {
                quoted && c == '"'.code -> {
                    val next = reader.read()
                    if (next == '"'.code) { consume(); cell.append('"') }
                    else { quoted = false; closedQuote = true; if (next >= 0) reader.unread(next) }
                }
                quoted -> cell.append(c.toChar())
                c == '"'.code && cell.isEmpty() && !closedQuote -> quoted = true
                c == ','.code -> {
                    fields.add(cell.toString())
                    if (fields.size > 256) throw IllegalArgumentException("Слишком много столбцов CSV")
                    cell.setLength(0); closedQuote = false
                }
                c == '\n'.code -> { fields.add(cell.toString().removeSuffix("\r")); return fields }
                c == '\r'.code && closedQuote -> Unit
                closedQuote -> throw IllegalArgumentException("Недопустимый символ после кавычек CSV")
                c == '"'.code -> throw IllegalArgumentException("Недопустимые кавычки в CSV")
                else -> cell.append(c.toChar())
            }
            if (cell.length > 5 * 1024 * 1024) throw IllegalArgumentException("Поле CSV слишком велико")
        }
    }
}
