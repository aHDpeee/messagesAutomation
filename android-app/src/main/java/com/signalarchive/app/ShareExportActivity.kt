package com.signalarchive.app

import android.app.Activity
import android.content.ClipData
import android.content.Intent
import android.os.Bundle
import android.widget.Toast

/** Opens the Android share chooser only when the user taps a completed export. */
class ShareExportActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val uri = intent.data
        if (uri == null || uri.scheme != "content") {
            Toast.makeText(this, "Архив недоступен", Toast.LENGTH_LONG).show()
            finish(); return
        }
        val share = Intent(Intent.ACTION_SEND).setType("application/zip")
            .putExtra(Intent.EXTRA_STREAM, uri)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            .apply { clipData = ClipData.newRawUri("Архив", uri) }
        try { startActivity(Intent.createChooser(share, "Отправить архив")) }
        catch (_: Exception) { Toast.makeText(this, "Не удалось открыть меню отправки", Toast.LENGTH_LONG).show() }
        finish()
    }
}
