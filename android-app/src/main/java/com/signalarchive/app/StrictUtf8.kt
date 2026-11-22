package com.signalarchive.app

import java.io.InputStream
import java.io.InputStreamReader
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets

internal object StrictUtf8 {
    fun reader(input: InputStream): InputStreamReader = InputStreamReader(input, StandardCharsets.UTF_8.newDecoder()
        .onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT))
}
