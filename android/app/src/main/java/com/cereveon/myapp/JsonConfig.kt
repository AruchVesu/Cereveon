package com.cereveon.myapp

import kotlinx.serialization.json.Json

/**
 * Single ``Json`` instance shared by every API client.
 *
 * Sprint 4.3.C migrated the client surface off hand-rolled
 * ``org.json.JSONObject`` parsing onto kotlinx-serialization.  Each
 * client used to hide its own JSON setup inside a private companion
 * object; centralising the config here keeps the wire-format
 * compatibility contract (snake_case fields, lenient null handling)
 * stable across every endpoint.
 *
 * Config rationale:
 *
 *   - ``ignoreUnknownKeys = true``: the backend can add new response
 *     fields in a future release without breaking older Android
 *     clients on the field.  Without this, any unknown key throws
 *     ``SerializationException`` and the call fails.
 *
 *   - ``coerceInputValues = true``: lenient coercion of JSON null →
 *     default value for non-nullable fields.  Matches what the
 *     previous hand-rolled parsers did when they called
 *     ``optString("…", "")`` / ``optInt("…", 0)``.
 *
 *   - ``encodeDefaults = false``: requests don't ship default values
 *     the server already assumes.  Smaller payloads, no behavioural
 *     change.
 *
 *   - ``isLenient = false``: reject malformed JSON loudly.  An
 *     upstream proxy or LLM-fallback path emitting non-JSON would
 *     otherwise be silently accepted.
 */
internal val ApiJson: Json = Json {
    ignoreUnknownKeys = true
    coerceInputValues = true
    encodeDefaults = false
    isLenient = false
}
