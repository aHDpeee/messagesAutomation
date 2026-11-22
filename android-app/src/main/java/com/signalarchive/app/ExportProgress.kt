package com.signalarchive.app

import java.util.UUID

/** Process-local progress; a stale job cannot clear a newer job's indicator. */
internal object ExportProgress {
    data class State(val id: String, val label: String, val remaining: Int, val total: Int)
    @Volatile private var current: State? = null
    @Synchronized fun start(label: String, total: Int): String {
        val id = UUID.randomUUID().toString()
        current = State(id, label, total.coerceAtLeast(0), total.coerceAtLeast(0))
        return id
    }
    @Synchronized fun update(id: String, remaining: Int) {
        val value = current ?: return
        if (value.id == id) current = value.copy(remaining = remaining.coerceIn(0, value.total))
    }
    @Synchronized fun finish(id: String) { if (current?.id == id) current = null }
    fun snapshot(): State? = current
}
