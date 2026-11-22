package com.signalarchive.app

/** A missing snapshot means availability is unknown, never implicitly unavailable. */
internal object DialogAvailability {
    fun unavailable(id: String, snapshot: Set<String>?): Boolean = snapshot != null && id !in snapshot
}
