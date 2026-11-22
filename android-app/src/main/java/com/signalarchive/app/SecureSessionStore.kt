package com.signalarchive.app

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Platform boundary called by Python; plaintext session state never enters a file. */
class SecureSessionStore(
    context: Context,
    private val keyAlias: String = "telegram-observatory-session-v1",
    preferencesName: String = "encrypted_telegram_session",
) {
    private val preferences = context.applicationContext
        .getSharedPreferences(preferencesName, Context.MODE_PRIVATE)

    @Synchronized
    fun save(serialized: String) {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, secretKey())
        cipher.updateAAD(keyAlias.toByteArray(Charsets.UTF_8))
        val encrypted = cipher.doFinal(serialized.toByteArray(Charsets.UTF_8))
        check(preferences.edit()
            .putString("iv", Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            .putString("value", Base64.encodeToString(encrypted, Base64.NO_WRAP))
            .commit()) { "Unable to persist encrypted Telegram session" }
    }

    @Synchronized
    fun load(): String {
        val value = preferences.getString("value", null) ?: return ""
        val iv = preferences.getString("iv", null)
            ?: error("Encrypted Telegram session is incomplete")
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, secretKey(),
            GCMParameterSpec(128, Base64.decode(iv, Base64.NO_WRAP)))
        cipher.updateAAD(keyAlias.toByteArray(Charsets.UTF_8))
        return cipher.doFinal(Base64.decode(value, Base64.NO_WRAP)).toString(Charsets.UTF_8)
    }

    @Synchronized
    fun clear() {
        // Destroy the key first: stale ciphertext becomes unusable even if removing preferences fails.
        KeyStore.getInstance(KEYSTORE).apply { load(null) }.deleteEntry(keyAlias)
        check(preferences.edit().clear().commit()) { "Unable to remove encrypted Telegram session" }
    }

    private fun secretKey(): SecretKey {
        val keyStore = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (keyStore.getKey(keyAlias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE).apply {
            init(KeyGenParameterSpec.Builder(keyAlias,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build())
        }.generateKey()
    }

    private companion object {
        const val KEYSTORE = "AndroidKeyStore"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
    }
}
