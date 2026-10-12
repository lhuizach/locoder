package com.locoder.mobile

import android.util.Base64
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec
import org.json.JSONObject

/**
 * End-to-end encryption with `locoder serve`, for the encrypted tunnel. The
 * protocol is described in locoder/serve/e2e.py; the web app does the same
 * in JavaScript.
 */
object E2e {
    private val random = SecureRandom()

    fun hmac(key: ByteArray, message: ByteArray): ByteArray =
        Mac.getInstance("HmacSHA256").run {
            init(SecretKeySpec(key, "HmacSHA256"))
            doFinal(message)
        }

    fun deviceId(key: String): String =
        MessageDigest.getInstance("SHA-256").digest(key.toByteArray())
            .joinToString("") { "%02x".format(it) }.substring(0, 32)

    fun channel(key: String) = Channel(hmac("locoder e2e".toByteArray(), key.toByteArray()))

    private fun b64(bytes: ByteArray): String = Base64.encodeToString(bytes, Base64.NO_WRAP)
    private fun unb64(text: String): ByteArray = Base64.decode(text, Base64.DEFAULT)

    class Sealed(val envelope: JSONObject, val nonce: ByteArray)

    class Channel(secret: ByteArray) {
        private val c2s = SecretKeySpec(hmac(secret, "c2s".toByteArray()), "AES")
        private val s2c = SecretKeySpec(hmac(secret, "s2c".toByteArray()), "AES")

        fun sealRequest(sender: String, method: String, path: String, body: JSONObject?): Sealed {
            val message = JSONObject()
                .put("m", method)
                .put("p", path)
                .put("b", body ?: JSONObject.NULL)
                .put("t", System.currentTimeMillis())
            val nonce = ByteArray(12).also { random.nextBytes(it) }
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, c2s, GCMParameterSpec(128, nonce))
            cipher.updateAAD("locoder-req:$sender".toByteArray())
            val sealed = cipher.doFinal(message.toString().toByteArray())
            val envelope = JSONObject().put("v", 1).put("d", sender).put("n", b64(nonce)).put("c", b64(sealed))
            return Sealed(envelope, nonce)
        }

        /** Throws if it was tampered with or isn't for this request. */
        fun open(envelope: JSONObject, label: String, requestNonce: ByteArray): JSONObject {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, s2c, GCMParameterSpec(128, unb64(envelope.getString("n"))))
            cipher.updateAAD(label.toByteArray() + requestNonce)
            return JSONObject(String(cipher.doFinal(unb64(envelope.getString("c"))), Charsets.UTF_8))
        }
    }

    const val RESPONSE = "locoder-res:"
    const val EVENT = "locoder-evt:"
}
