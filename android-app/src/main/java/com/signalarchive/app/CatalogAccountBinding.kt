package com.signalarchive.app

/** Keep a user-edited or imported catalog from being silently merged across accounts. */
internal object CatalogAccountBinding {
    fun validate(stored: String?, current: String, hasExistingRows: Boolean, acceptedUnbound: Boolean = false) {
        require(current.matches(Regex("[0-9]{1,20}")) && current.toLongOrNull()?.let { it > 0 } == true) {
            "Telegram не подтвердил аккаунт."
        }
        if (stored == null && hasExistingRows && !acceptedUnbound) {
            throw IllegalStateException("Каталог уже содержит чаты, но его аккаунт не подтверждён. Сохраните копию каталога и используйте отдельное хранилище для входа, чтобы не смешать аккаунты.")
        }
        if (stored != null && stored != current) {
            throw IllegalStateException("Каталог принадлежит другому аккаунту Telegram. Используйте отдельное хранилище, чтобы не смешать чаты.")
        }
    }
}
