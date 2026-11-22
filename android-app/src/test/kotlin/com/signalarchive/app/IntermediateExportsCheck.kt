package com.signalarchive.app

import java.nio.file.Files

fun main() {
    val root = Files.createTempDirectory("signal-cleanup-").toFile()
    try {
        val inside = root.resolve("telegram-engine/exports/job/messages.csv").apply { parentFile.mkdirs(); writeText("data") }
        val outside = root.resolve("user.csv").apply { writeText("keep") }
        check(IntermediateExports.checked(root, inside.path) == inside.canonicalFile)
        check(runCatching { IntermediateExports.checked(root, outside.path) }.isFailure)
        val sibling = root.resolve("telegram-engine/exports-other/file.csv").apply { parentFile.mkdirs(); writeText("keep") }
        check(runCatching { IntermediateExports.checked(root, sibling.path) }.isFailure)
        IntermediateExports.remove(listOf(inside))
        check(!inside.exists() && outside.exists() && sibling.exists())
        println("Intermediate export cleanup checks passed")
    } finally { root.deleteRecursively() }
}
