package com.signalarchive.app

fun main() {
    CatalogAccountBinding.validate(null, "123", false)
    CatalogAccountBinding.validate("123", "123", true)
    check(runCatching { CatalogAccountBinding.validate(null, "123", true) }.isFailure)
    CatalogAccountBinding.validate(null, "123", true, acceptedUnbound = true)
    check(runCatching { CatalogAccountBinding.validate("123", "456", true) }.isFailure)
    check(runCatching { CatalogAccountBinding.validate(null, "0", false) }.isFailure)
    println("Catalog account binding checks passed")
}
