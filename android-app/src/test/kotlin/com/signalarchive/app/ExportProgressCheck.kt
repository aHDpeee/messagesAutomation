package com.signalarchive.app

fun main() {
    val first = ExportProgress.start("чатов", 4)
    ExportProgress.update(first, 2)
    check(ExportProgress.snapshot()?.remaining == 2)
    val second = ExportProgress.start("групп", 3)
    ExportProgress.finish(first)
    check(ExportProgress.snapshot()?.id == second)
    ExportProgress.update(second, 99)
    check(ExportProgress.snapshot()?.remaining == 3)
    ExportProgress.finish(second)
    check(ExportProgress.snapshot() == null)
    println("Export progress checks passed")
}
