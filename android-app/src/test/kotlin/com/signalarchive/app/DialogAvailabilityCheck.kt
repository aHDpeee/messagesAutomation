package com.signalarchive.app

fun main() {
    check(!DialogAvailability.unavailable("1", null))
    check(!DialogAvailability.unavailable("1", setOf("1", "2")))
    check(DialogAvailability.unavailable("3", setOf("1", "2")))
    println("Dialog availability checks passed")
}
