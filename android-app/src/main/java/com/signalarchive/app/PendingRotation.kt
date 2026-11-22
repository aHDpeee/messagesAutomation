package com.signalarchive.app

internal object PendingRotation {
    fun select(keys: List<String>, cursor: String?, limit: Int): List<String> {
        if (limit <= 0 || keys.isEmpty()) return emptyList()
        val ordered = keys.distinct().sorted()
        val first = cursor?.let { value -> ordered.indexOfFirst { it > value }.takeIf { it >= 0 } } ?: 0
        return (0 until minOf(limit, ordered.size)).map { ordered[(first + it) % ordered.size] }
    }
}
