package com.signalarchive.app

import android.content.ContentProvider
import android.content.ContentValues
import android.database.Cursor
import android.database.MatrixCursor
import android.net.Uri
import android.os.ParcelFileDescriptor
import android.provider.OpenableColumns
import java.io.File
import java.io.FileNotFoundException

/** Serves only explicitly shared, temporary QR images. */
class QrShareProvider : ContentProvider() {
    override fun onCreate() = true
    private fun file(uri: Uri): File {
        val name = uri.lastPathSegment.orEmpty()
        if (uri.pathSegments.size != 1 || !name.matches(Regex("login-[0-9a-f-]{36}\\.png"))) throw FileNotFoundException()
        val root = File(requireNotNull(context).cacheDir, "qr-share").canonicalFile
        val file = File(root, name).canonicalFile
        if (file.parentFile != root || !file.isFile) throw FileNotFoundException()
        return file
    }
    override fun getType(uri: Uri): String = "image/png"
    override fun openFile(uri: Uri, mode: String): ParcelFileDescriptor {
        if (mode != "r") throw FileNotFoundException()
        return ParcelFileDescriptor.open(file(uri), ParcelFileDescriptor.MODE_READ_ONLY)
    }
    override fun query(uri: Uri, projection: Array<out String>?, selection: String?, selectionArgs: Array<out String>?, sortOrder: String?): Cursor {
        val item = file(uri)
        val columns = projection ?: arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE)
        return MatrixCursor(columns).apply {
            addRow(columns.map { when (it) { OpenableColumns.DISPLAY_NAME -> "telegram-login.png"; OpenableColumns.SIZE -> item.length(); else -> null } })
        }
    }
    override fun insert(uri: Uri, values: ContentValues?): Uri? = throw UnsupportedOperationException()
    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?): Int = throw UnsupportedOperationException()
    override fun update(uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<out String>?): Int = throw UnsupportedOperationException()
}
