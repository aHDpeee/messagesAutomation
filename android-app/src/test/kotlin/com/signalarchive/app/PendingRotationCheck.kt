package com.signalarchive.app

fun main() {
    val keys = (0..44).map { "%02d".format(it) }
    val first = PendingRotation.select(keys, null, 20)
    val second = PendingRotation.select(keys, first.last(), 20)
    val third = PendingRotation.select(keys, second.last(), 20)
    check((first + second + third).toSet() == keys.toSet())
    check(PendingRotation.select(listOf("a", "b"), "z", 20) == listOf("a", "b"))
    check(PendingRotation.select(emptyList(), null, 20).isEmpty())
    println("Pending rotation checks passed")
}
