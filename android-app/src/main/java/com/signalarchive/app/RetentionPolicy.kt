package com.signalarchive.app

import java.io.InputStream
import java.security.MessageDigest
import java.time.Instant

internal object RetentionPolicy {
    fun eligible(savedTree: String, selectedTree: String, created: Instant?, cutoff: Instant): Boolean =
        savedTree == selectedTree && created != null && created < cutoff
    fun matches(input: InputStream, expectedSize: Long, expectedHash: String): Boolean {
        if (expectedSize < 0 || !expectedHash.matches(Regex("[0-9a-f]{64}"))) return false
        val digest = MessageDigest.getInstance("SHA-256"); var size = 0L; val buffer = ByteArray(65536)
        while (true) {
            if (Thread.currentThread().isInterrupted) throw InterruptedException()
            val n = input.read(buffer); if (n < 0) break
            size += n; if (size > expectedSize) return false
            digest.update(buffer, 0, n)
        }
        val actual = digest.digest().joinToString("") { "%02x".format(it.toInt() and 255) }
        return size == expectedSize && actual == expectedHash
    }
    fun sha256(input: InputStream): String {
        val digest = MessageDigest.getInstance("SHA-256"); val buffer = ByteArray(65536)
        while (true) {
            if (Thread.currentThread().isInterrupted) throw InterruptedException()
            val n = input.read(buffer); if (n < 0) break; digest.update(buffer, 0, n)
        }
        return digest.digest().joinToString("") { "%02x".format(it.toInt() and 255) }
    }
}
