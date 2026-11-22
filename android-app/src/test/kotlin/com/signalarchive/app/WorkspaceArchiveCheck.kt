package com.signalarchive.app

import java.nio.file.Files

fun main() {
    val root = Files.createTempDirectory("workspace-check").toFile()
    try {
        check(WorkspaceArchive.archive(root) == null)
        val engine = root.resolve("telegram-engine").apply { mkdir() }
        engine.resolve("observatory.sqlite").writeText("old account")
        val archived = requireNotNull(WorkspaceArchive.archive(root))
        check(!engine.exists())
        check(archived.resolve("observatory.sqlite").readText() == "old account")
        check(!WorkspaceArchive.isSealed(archived))
        archived.resolve("account-settings.json").writeText("{}")
        archived.resolve("chats.csv").writeText("title,id\n")
        WorkspaceArchive.seal(archived)
        check(WorkspaceArchive.isSealed(archived))
        archived.resolve("observatory.sqlite").writeText("damaged")
        check(!WorkspaceArchive.isSealed(archived))
        archived.resolve("observatory.sqlite").writeText("old account")
        check(WorkspaceArchive.isSealed(archived))
        check(archived in WorkspaceArchive.available(root))
        WorkspaceArchive.restoreEngine(root, archived)
        check(engine.resolve("observatory.sqlite").readText() == "old account")
        check(!engine.resolve("snapshot.sha256").exists())
        check(runCatching { WorkspaceArchive.restoreEngine(root, archived) }.isFailure)
        check(engine.resolve("observatory.sqlite").readText() == "old account")
        engine.deleteRecursively()
        archived.resolve("chats.csv").writeText("changed")
        check(!WorkspaceArchive.isSealed(archived))
        engine.mkdir()
        engine.resolve("observatory.sqlite").writeText("new account")
        val second = requireNotNull(WorkspaceArchive.archive(root))
        check(second != archived && archived.resolve("observatory.sqlite").readText() == "old account")
        check(second.resolve("observatory.sqlite").readText() == "new account")
        println("Workspace archive checks passed")
    } finally { root.deleteRecursively() }
}
