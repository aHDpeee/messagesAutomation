package com.signalarchive.app

import java.time.Instant

internal object CheckpointPolicy {
    fun start(checkpoints: List<Instant?>, fallback: Instant, now: Instant): Instant =
        checkpoints.map { it?.takeIf { value -> value < now } ?: fallback }.minOrNull() ?: fallback

    fun shouldAdvance(previous: Instant?, start: Instant, end: Instant): Boolean =
        (previous == null || previous >= start) && (previous == null || previous < end)
}
