package com.signalarchive.app

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONObject
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

data class ApiCredentials(val apiId: String, val apiHash: String)

/** Encrypts Telegram developer credentials with an Android Keystore AES-GCM key. */
class SecureCredentialStore(context: Context) {
    private val preferences = context.applicationContext
        .getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)

    fun save(credentials: ApiCredentials) {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, secretKey())
        val plaintext = JSONObject()
            .put("api_id", credentials.apiId)
            .put("api_hash", credentials.apiHash)
            .toString()
            .toByteArray(Charsets.UTF_8)
        val ciphertext = cipher.doFinal(plaintext)
        check(
            preferences.edit()
                .putString(KEY_IV, Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
                .putString(KEY_VALUE, Base64.encodeToString(ciphertext, Base64.NO_WRAP))
                .commit(),
        ) { "Unable to save encrypted Telegram API credentials" }
    }

    fun load(): ApiCredentials? {
        val ivText = preferences.getString(KEY_IV, null) ?: return null
        val valueText = preferences.getString(KEY_VALUE, null) ?: return null
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(
            Cipher.DECRYPT_MODE,
            secretKey(),
            GCMParameterSpec(128, Base64.decode(ivText, Base64.NO_WRAP)),
        )
        val plaintext = cipher.doFinal(Base64.decode(valueText, Base64.NO_WRAP))
        val json = JSONObject(plaintext.toString(Charsets.UTF_8))
        return ApiCredentials(json.getString("api_id"), json.getString("api_hash"))
    }

    fun clear() {
        preferences.edit().clear().commit()
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        keyStore.deleteEntry(KEY_ALIAS)
    }

    private fun secretKey(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build(),
        )
        return generator.generateKey()
    }

    private companion object {
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        const val KEY_ALIAS = "telegram-observatory-api-credentials"
        const val PREFERENCES = "encrypted_credentials"
        const val KEY_IV = "iv"
        const val KEY_VALUE = "encrypted_value"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
    }
}
