package ai.chesscoach.app

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Abstraction over the JWT persistence layer.
 *
 * Defining storage as an interface allows [AuthRepository] to be tested with
 * a pure in-memory fake on the JVM, without requiring a real Android Keystore
 * or filesystem. Production code wires in [EncryptedTokenStorage].
 */
interface TokenStorage {
    /** Persist [token] to durable storage, replacing any previously stored value. */
    fun save(token: String)

    /** Return the stored token, or null if none has been saved or it was cleared. */
    fun load(): String?

    /** Remove the stored token. Subsequent [load] calls return null. */
    fun clear()
}

/**
 * Production [TokenStorage] backed by [EncryptedSharedPreferences].
 *
 * The token is encrypted at rest using AES-256-GCM via the Android Keystore,
 * and the shared-preferences key is encrypted with AES-256-SIV.
 *
 * The [EncryptedSharedPreferences] instance is created lazily on the first
 * access; subsequent accesses are instant. This class is safe to construct
 * from the main thread because Keystore operations are deferred.
 *
 * Requires API level 23+; the app's minSdk is 26, so this is always safe.
 *
 * @param context   Any [Context]; the application context is used internally.
 * @param prefsName Shared-preferences file name. Override in tests if needed.
 */
class EncryptedTokenStorage(
    context: Context,
    prefsName: String = "auth_prefs",
) : TokenStorage {

    private val prefs by lazy {
        val masterKey =
            MasterKey.Builder(context.applicationContext)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                .build()
        EncryptedSharedPreferences.create(
            context.applicationContext,
            prefsName,
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    override fun save(token: String) {
        prefs.edit().putString(KEY_TOKEN, token).apply()
    }

    override fun load(): String? = prefs.getString(KEY_TOKEN, null)

    override fun clear() {
        prefs.edit().remove(KEY_TOKEN).apply()
    }

    private companion object {
        const val KEY_TOKEN = "jwt_token"
    }
}
